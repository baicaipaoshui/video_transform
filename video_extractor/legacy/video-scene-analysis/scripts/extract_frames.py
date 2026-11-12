# -*- coding: utf-8 -*-
"""按指定帧率（默认 2fps）对视频抽帧，默认原分辨率；超预算才自动降采样。

用法:
  python extract_frames.py <video_path> [--fps 2] [--out media/input/<stem>]
                           [--prefix frame] [--max-width 0]

输出:
  图像   <out>/<prefix>_%04d.png   （第 n 帧对应 t = (n-1)/fps 秒）
  stdout 一行 JSON 元信息（时长/源帧率/分辨率/帧数/每帧体积/是否自动降采样）

自动降采样策略（防 API 413，重要）:
  view_image 会把 PNG 以 base64 内联进模型 API 请求；chat API 无状态，
  在窗图像每轮模型调用都会整体重发。因此存在两条硬约束：
    1) 单文件上限：QwenPaw capping formatter 对 >2MB 的本地媒体直接替换为
       占位文本（qwenpaw/providers/capping_formatter.py, MAX_INLINE_MEDIA_BYTES），
       超过 2MB 的帧根本无法送入模型；
    2) 请求体上限：网关对单次请求体大小有上限（实测 ~18MB 通过、~27MB 触发
       HTTP 413 RequestTooLarge），在窗图像累计体积必须留出余量。
  流程：先按原分辨率抽帧 → 统计帧数与每帧体积 → 仅当超限才整体等比降采样
  （最多迭代 3 次直到满足预算）。超限被降采样时，细节补充应使用
  全分辨率 ROI 裁剪（从原视频裁目标区域，见 SKILL.md 步骤 2）。

依赖: ffmpeg 与 ffprobe（优先 PATH；找不到 ffprobe 时自动尝试 ffmpeg 同目录）。
"""
import argparse
import glob
import json
import math
import os
import shutil
import subprocess
import sys
from fractions import Fraction

# ── 体积预算（依据见模块 docstring）────────────────────────────────
# 单帧硬上限：略低于框架 2MB 内联上限，确保任何帧都能送入模型。
PER_FRAME_HARD_BYTES = 2_000_000
# 在窗媒体累计预算（磁盘字节）：≈16MB base64。实测网关 ~18MB base64 通过、
# ~27MB 失败，取 16MB 给提示词/文本/工具结果留余量，同时尽量不压缩画质。
RESIDENT_MEDIA_DISK_BUDGET = 12_000_000
# 最坏情况下同时驻留在模型上下文里的帧数（≤4 帧/批的工作流 + 最近尾部）。
MAX_RESIDENT_FRAMES = 16
# 每轮降采样的安全系数（PNG 体积≈正比于像素数，留 8% 余量防内容差异）。
DOWNSCALE_SAFETY = 0.92
# 降采样迭代上限。
MAX_DOWNSCALE_ITERS = 3


def _bin(name):
    """定位 ffmpeg/ffprobe 可执行文件。"""
    found = shutil.which(name)
    if found:
        return found
    ff = shutil.which("ffmpeg")
    if ff:
        ext = ".exe" if os.name == "nt" else ""
        sibling = os.path.join(os.path.dirname(ff), name + ext)
        if os.path.exists(sibling):
            return sibling
    sys.exit("[extract_frames] 找不到 %s，请安装 ffmpeg 或加入 PATH" % name)


def probe(video):
    """ffprobe 探测视频元信息。"""
    ffprobe = _bin("ffprobe")
    cmd = [ffprobe, "-v", "error", "-print_format", "json",
           "-show_format", "-show_streams", video]
    out = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
    info = json.loads(out.decode("utf-8", "replace"))
    vstream = next((s for s in info.get("streams", [])
                    if s.get("codec_type") == "video"), None)
    if vstream is None:
        sys.exit("[extract_frames] 未找到视频流: %s" % video)

    def rate(key):
        raw = vstream.get(key) or "0"
        try:
            frac = Fraction(raw)
            return float(frac) if frac.denominator else 0.0
        except (ValueError, ZeroDivisionError):
            return 0.0

    duration = float(info.get("format", {}).get("duration")
                     or vstream.get("duration") or 0)
    return {
        "duration_s": round(duration, 3),
        "src_fps": round(rate("avg_frame_rate") or rate("r_frame_rate"), 3),
        "width_px": int(vstream.get("width") or 0),
        "height_px": int(vstream.get("height") or 0),
        "codec": vstream.get("codec_name"),
    }


def extract(video, out_dir, prefix, fps, scale_width=None):
    """抽帧（可选等比缩放到 scale_width 宽，高取偶数）。覆盖同名旧帧。"""
    vf = "fps=%g" % fps
    if scale_width:
        vf += ",scale=%d:-2" % scale_width
    pattern = os.path.join(out_dir, prefix + "_%04d.png")
    ffmpeg = _bin("ffmpeg")
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
           "-i", video, "-vf", vf, pattern]
    subprocess.check_call(cmd)


def measure(out_dir, prefix):
    """返回 (帧文件列表, 各帧字节数列表)。"""
    frames = sorted(glob.glob(os.path.join(out_dir, prefix + "_*.png")))
    return frames, [os.path.getsize(f) for f in frames]


def main():
    # Windows 控制台默认 cp1252，无法输出中文 JSON 字段，统一切 UTF-8
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    parser = argparse.ArgumentParser(
        description="按固定 fps 抽帧（默认 2fps），默认原分辨率，超预算自动降采样")
    parser.add_argument("video", help="输入视频文件路径")
    parser.add_argument("--fps", type=float, default=2.0, help="抽帧帧率，默认 2")
    parser.add_argument("--out", default=None,
                        help="输出目录，默认 media/input/<视频名>")
    parser.add_argument("--prefix", default="frame", help="帧文件名前缀，默认 frame")
    parser.add_argument("--max-width", type=int, default=0,
                        help="手动强制限宽（等比缩小，高取偶数）；默认 0 = 不手动缩放，"
                             "由自动预算逻辑决定")
    args = parser.parse_args()

    video = os.path.abspath(args.video)
    if not os.path.isfile(video):
        sys.exit("[extract_frames] 视频不存在: %s" % video)
    if args.fps <= 0:
        sys.exit("[extract_frames] fps 必须为正数")
    stem = os.path.splitext(os.path.basename(video))[0]

    out_dir = (os.path.abspath(args.out) if args.out
               else os.path.join(os.getcwd(), "media", "input", stem))
    os.makedirs(out_dir, exist_ok=True)

    meta = probe(video)

    # 1) 抽帧：手动限宽优先，否则原分辨率
    manual_w = None
    if args.max_width and args.max_width > 0 and meta["width_px"] > args.max_width:
        manual_w = args.max_width
    extract(video, out_dir, args.prefix, args.fps, scale_width=manual_w)

    frames, sizes = measure(out_dir, args.prefix)
    if not frames:
        sys.exit("[extract_frames] 抽帧失败：未生成任何帧")
    cur_w = manual_w or meta["width_px"]

    # 2) 自动预算检查：仅当超限才降采样
    resident = min(len(frames), MAX_RESIDENT_FRAMES)
    auto_w = None
    reasons = []
    avg = sum(sizes) / len(sizes)
    mx = max(sizes)
    if mx > PER_FRAME_HARD_BYTES:
        reasons.append("单帧最大 %.2fMB 超过框架 2MB 内联上限" % (mx / 1e6))
    if avg * resident > RESIDENT_MEDIA_DISK_BUDGET:
        reasons.append("驻窗预算超限：avg %.2fMB x %d 帧 > %.0fMB"
                       % (avg / 1e6, resident, RESIDENT_MEDIA_DISK_BUDGET / 1e6))

    iters = 0
    while reasons and iters < MAX_DOWNSCALE_ITERS:
        per_target = min(PER_FRAME_HARD_BYTES * 0.95,
                         RESIDENT_MEDIA_DISK_BUDGET / resident)
        factor = math.sqrt(per_target / avg) * DOWNSCALE_SAFETY
        cur_w = max(320, int(cur_w * factor) // 2 * 2)
        extract(video, out_dir, args.prefix, args.fps, scale_width=cur_w)
        frames, sizes = measure(out_dir, args.prefix)
        if not frames:
            sys.exit("[extract_frames] 降采样重抽失败：未生成任何帧")
        auto_w = cur_w
        iters += 1
        avg = sum(sizes) / len(sizes)
        mx = max(sizes)
        reasons = []
        if mx > PER_FRAME_HARD_BYTES:
            reasons.append("重抽后单帧最大 %.2fMB 仍超 2MB" % (mx / 1e6))
        if avg * resident > RESIDENT_MEDIA_DISK_BUDGET:
            reasons.append("重抽后驻窗预算仍超限")
    if reasons:
        sys.exit("[extract_frames] 降采样 %d 次后仍超预算：%s"
                 % (MAX_DOWNSCALE_ITERS, "；".join(reasons)))

    result = {
        "video": video,
        "video_name": stem,
        "duration_s": meta["duration_s"],
        "src_fps": meta["src_fps"],
        "width_px": meta["width_px"],
        "height_px": meta["height_px"],
        "codec": meta["codec"],
        "extract_fps": args.fps,
        "frame_count": len(frames),
        "avg_frame_bytes": int(round(sum(sizes) / len(sizes))),
        "max_frame_bytes": int(max(sizes)),
        "manual_max_width": manual_w,
        "auto_downscaled": auto_w is not None,
        "auto_downscale_reason": "；".join(reasons) if reasons else
                                 ("超限自动降采样" if auto_w else "未超限，保持原分辨率"),
        "analysis_width_px": cur_w,
        "out_dir": out_dir,
        "frame_pattern": args.prefix + "_%04d.png",
        "first_frame": os.path.basename(frames[0]),
        "last_frame": os.path.basename(frames[-1]),
        "first_frame_time_s": 0.0,
        "last_frame_time_s": round((len(frames) - 1) / args.fps, 3),
        "time_mapping": "%s_NNNN.png -> t = (N-1)/%g s" % (args.prefix, args.fps),
    }
    if auto_w:
        result["roi_hint"] = (
            "帧已降采样；需要细节时用全分辨率 ROI 裁剪："
            "ffmpeg -ss <t> -i <video> -frames:v 1 -vf crop=W:H:X:Y "
            "temp_file/<task>/roi/<name>.png（坐标按原始 %dx%d 计）"
            % (meta["width_px"], meta["height_px"]))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
