# -*- coding: utf-8 -*-
"""生成「前视视频 → 结构化仿真场景数据」的通用 Word 复现报告。

用法:
  python generate_report.py <analysis_json> <task_meta_json> <output_docx> [--prompt <analysis_prompt.txt>]

输入:
  analysis_json   最终结构化 JSON（遵循 analysis_prompt.txt 第九节 Schema）
  task_meta_json  任务元数据，字段模板:
  {
    "task_name": "intersection",
    "video_file": "intersection.mp4",
    "video_fps": 10,
    "duration_s": 10.1,
    "resolution": "1280x864",
    "extract_fps": 2,
    "frame_dir": "media/input/intersection",
    "frame_count": 8,
    "sampling_interval_s": 0.5,
    "analysis_window_s": [0.0, 3.5],
    "provided_inputs": ["目标仿真平台：OpenSCENARIO/OpenDRIVE"],
    "missing_inputs": ["相机内参", "自车尺寸"],
    "steps": ["1. 逐帧观察……", "2. 跨帧关联……"],
    "output_files": {"json": "media/output/xxx.json", "trace": "temp_file/xxx/trace.txt"},
    "natural_language_summary": ["总体：……", "道路：……"],
    "environment_notes": ["操作系统：Windows", "Python 3.12 + python-docx"],
    "reproduce_steps": ["1. 抽帧……", "2. 分析……"]
  }
"""
import argparse
import json
import os
import sys

try:
    from docx import Document
    from docx.shared import Pt
    from docx.oxml.ns import qn
except ImportError:
    sys.exit("[generate_report] 缺少 python-docx，请先执行: python -m pip install python-docx")


def main():
    parser = argparse.ArgumentParser(description="生成场景分析 Word 复现报告")
    parser.add_argument("analysis_json", help="最终结构化 JSON 路径")
    parser.add_argument("task_meta_json", help="任务元数据 JSON 路径")
    parser.add_argument("output_docx", help="输出 docx 路径")
    parser.add_argument("--prompt", default=None,
                        help="analysis_prompt.txt 路径，默认取工作区 skills/analysis_prompt.txt")
    args = parser.parse_args()

    with open(args.analysis_json, encoding="utf-8") as f:
        data = json.load(f)
    with open(args.task_meta_json, encoding="utf-8") as f:
        meta = json.load(f)

    task = meta.get("task_name") or os.path.splitext(
        os.path.basename(args.analysis_json))[0]
    prompt_path = args.prompt or os.path.join(
        os.getcwd(), "skills", "analysis_prompt.txt")
    out_json_name = os.path.basename(args.analysis_json)

    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "SimSun"
    style.element.get_or_add_rPr()
    style.element.rPr.rFonts.set(qn("w:eastAsia"), "SimSun")
    style.font.size = Pt(10.5)

    def p(text):
        doc.add_paragraph(str(text))

    doc.add_heading("%s 前视视频场景分析与仿真重建复现文档" % task, 0)
    p("用途：供同事复现本次“前视行车视频 → 结构化仿真场景数据”的完整分析流程。"
      "内容包括：任务提示词（JSON 格式规范已省略）、输入信息、任务步骤、输出说明、"
      "生成场景的自然语言描述、运行环境与复现步骤。")

    # 一、任务提示词（第九节省略）
    doc.add_heading("一、任务提示词（JSON 输出格式规范已省略）", 1)
    if os.path.isfile(prompt_path):
        p("完整提示词见 %s。其中【九、输出格式】为大篇幅 JSON Schema 规范，本文档予以省略；"
          "JSON 的具体字段结构与取值规范请直接参考实际输出文件 %s"
          "（该文件即严格遵循第九节 Schema 的合规实例）。以下为提示词其余部分："
          % (prompt_path, out_json_name))
        with open(prompt_path, encoding="utf-8") as f:
            lines = f.read().splitlines()
        try:
            start = next(i for i, l in enumerate(lines) if l.startswith("【九、输出格式】"))
            end = next(i for i, l in enumerate(lines) if l.startswith("【十、一致性检查】"))
        except StopIteration:
            start, end = None, None
        if start is not None:
            for line in lines[:start]:
                if line.strip():
                    p(line.rstrip())
            p("【九、输出格式】（本节 JSON Schema 篇幅较长，已省略——"
              "请以实际输出文件 %s 为格式参考实例。）" % out_json_name)
            for line in lines[end:]:
                if line.strip():
                    p(line.rstrip())
        else:
            for line in lines:
                if line.strip():
                    p(line.rstrip())
    else:
        p("提示词文件 %s 未找到，本节省略。" % prompt_path)

    # 二、输入信息
    doc.add_heading("二、输入信息", 1)
    basics = [
        "视频文件：%s" % meta.get("video_file", "未知"),
        "视频帧率 fps：%s；时长：%s s；分辨率：%s" % (
            meta.get("video_fps", "未知"), meta.get("duration_s", "未知"),
            meta.get("resolution", "未知")),
        "抽帧：%s fps，共 %s 帧，目录 %s" % (
            meta.get("extract_fps", 2), meta.get("frame_count", "未知"),
            meta.get("frame_dir", "media/input")),
    ]
    window = meta.get("analysis_window_s")
    if window:
        basics.append("分析时间窗：%s~%s s；采样时间间隔：%s s" % (
            window[0], window[1], meta.get("sampling_interval_s", "未知")))
    if meta.get("provided_inputs"):
        basics.append("已提供输入：" + "；".join(meta["provided_inputs"]))
    if meta.get("missing_inputs"):
        basics.append("缺失输入（已记入 quality_report.missing_inputs）："
                      + "；".join(meta["missing_inputs"]))
    for item in basics:
        p("- " + item)

    # 三、任务步骤
    doc.add_heading("三、任务步骤", 1)
    for item in meta.get("steps", []):
        p(item)

    # 四、输出说明
    doc.add_heading("四、输出说明", 1)
    out_files = meta.get("output_files", {})
    idx = 1
    p("%d) 最终结构化 JSON：%s —— 顶层九节：metadata / environment / road / ego / "
      "actors / interactions / events / simulation_scenarios / quality_report；"
      "JSON 字段规范见 analysis_prompt.txt 第九节，本文档省略。"
      % (idx, out_files.get("json", args.analysis_json)))
    idx += 1
    if out_files.get("trace"):
        p("%d) 过程记录：%s —— 逐帧观察、目标汇总与结论推演的完整分析轨迹。"
          % (idx, out_files["trace"]))
        idx += 1
    p("%d) 本文档：%s。" % (idx, os.path.basename(args.output_docx)))

    actors = data.get("actors", [])
    if actors:
        p("关键数据一览（参与者，共 %d 个）：" % len(actors))
        table = doc.add_table(rows=1, cols=5)
        table.style = "Table Grid"
        hdr = table.rows[0].cells
        hdr[0].text = "ID"
        hdr[1].text = "类型/颜色"
        hdr[2].text = "首现(s)"
        hdr[3].text = "末现(s)"
        hdr[4].text = "静/动"
        for actor in actors:
            row = table.add_row().cells
            row[0].text = str(actor.get("actor_id", ""))
            row[1].text = "%s / %s" % (actor.get("actor_type"), actor.get("color"))
            row[2].text = str(actor.get("first_visible_s"))
            row[3].text = str(actor.get("last_visible_s"))
            row[4].text = "静止" if actor.get("is_static") else "运动"

    events = data.get("events", [])
    if events:
        p("关键事件一览：")
        table = doc.add_table(rows=1, cols=6)
        table.style = "Table Grid"
        hdr = table.rows[0].cells
        hdr[0].text = "event_id"
        hdr[1].text = "类型"
        hdr[2].text = "开始(s)"
        hdr[3].text = "峰值(s)"
        hdr[4].text = "结束(s)"
        hdr[5].text = "严重度/优先级"
        for event in events:
            row = table.add_row().cells
            row[0].text = str(event.get("event_id", ""))
            row[1].text = str(event.get("event_type", ""))
            row[2].text = str(event.get("start_time_s"))
            row[3].text = str(event.get("peak_time_s"))
            row[4].text = str(event.get("end_time_s"))
            row[5].text = "%s / %s" % (event.get("severity"),
                                       event.get("reconstruction_priority"))

    for scenario in data.get("simulation_scenarios", []):
        p("- 仿真场景 %s：%s —— %s" % (scenario.get("scenario_id"),
                                       scenario.get("scenario_name"),
                                       scenario.get("description")))

    quality = data.get("quality_report", {})
    if quality.get("overall_confidence") is not None:
        p("总体置信度：%s；limitations 共 %d 条。" % (
            quality.get("overall_confidence"),
            len(quality.get("limitations", []))))

    # 五、自然语言描述
    doc.add_heading("五、输出的自然语言描述", 1)
    for item in meta.get("natural_language_summary", []):
        p(item)

    # 六、运行环境与复现步骤
    doc.add_heading("六、运行环境与复现步骤", 1)
    if meta.get("environment_notes"):
        p("运行环境：")
        for item in meta["environment_notes"]:
            p("- " + item)
    if meta.get("reproduce_steps"):
        p("复现步骤：")
        for item in meta["reproduce_steps"]:
            p(item)

    out_dir = os.path.dirname(os.path.abspath(args.output_docx))
    os.makedirs(out_dir, exist_ok=True)
    doc.save(args.output_docx)
    print("Word report saved:", os.path.abspath(args.output_docx))


if __name__ == "__main__":
    main()
