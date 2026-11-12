# -*- coding: utf-8 -*-
"""生成 intersection.mp4 场景分析复现 Word 文档。运行: python generate_word_report.py"""
import json, os
from docx import Document
from docx.shared import Pt
from docx.oxml.ns import qn

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)
doc = Document()
st = doc.styles['Normal']
st.font.name = 'SimSun'
st.element.rPr.rFonts.set(qn('w:eastAsia'), 'SimSun')
st.font.size = Pt(10.5)

data = json.load(open(os.path.join(BASE, 'scene_analysis_full_0001_0008.json'), encoding='utf-8'))

def p(t): doc.add_paragraph(t)

doc.add_heading('intersection.mp4 前视视频场景分析与仿真重建复现文档', 0)
p('用途：供同事复现本次“前视行车视频 → 结构化仿真场景数据”的完整分析流程。内容包括：任务提示词（JSON 格式规范已省略）、输入信息、任务步骤、输出说明、输出的自然语言描述、运行环境与复现步骤。')

doc.add_heading('一、任务提示词（JSON 输出格式规范已省略）', 1)
p('完整提示词见工程根目录 analysis_prompt.txt。其中【九、输出格式】一节为大篇幅 JSON Schema 规范，本文档予以省略；JSON 的具体字段结构与取值规范请直接参考实际输出文件 scene_analysis_full_0001_0008.json（该文件即严格遵循第九节 Schema 的合规实例）。以下为提示词其余部分：')
lines = open(os.path.join(ROOT, 'analysis_prompt.txt'), encoding='utf-8').read().splitlines()
start_omit = next(i for i, l in enumerate(lines) if l.startswith('【九、输出格式】'))
end_omit = next(i for i, l in enumerate(lines) if l.startswith('【十、一致性检查】'))
for line in lines[:start_omit]:
    if line.strip(): p(line.rstrip())
p('【九、输出格式】（本节 JSON Schema 篇幅较长，已省略——请以实际输出文件 intersection/scene_analysis_full_0001_0008.json 为格式参考实例。）')
for line in lines[end_omit:]:
    if line.strip(): p(line.rstrip())

doc.add_heading('二、输入信息', 1)
for t in [
 '视频文件：intersection.mp4（前视行车记录仪，旧金山城区 Franklin St × Jackson St 信号交叉口，下坡接近）。',
 '视频帧率 fps：10；时长：10.1 s；分辨率：1280x864。',
 '抽帧：每 0.5 s 一帧，共 8 帧 frames/frame_0001.png~frame_0008.png，对应 t=0.0~3.5 s（仅分析视频前 3.5 s）。',
 '相机安装位置/高度/俯仰/内参/畸变：均缺失（已在 quality_report.missing_inputs 标注）。',
 '自车尺寸/初始速度、CAN 车速、GPS/IMU 数据：均缺失。',
 '目标仿真平台/输出标准：未指定；输出为通用结构化 JSON，可转 OpenDRIVE/OpenSCENARIO。',
 '采样时间间隔：0.5 s。',
]: p('- ' + t)

doc.add_heading('三、任务步骤', 1)
for t in [
 '1. 逐帧观察：依次读取 8 帧图像，盘点可见道路结构（车道、停车带、标线、斑马线、停止线）、路口要素（信号灯、路名牌、标志牌）、交通参与者与路侧物体。',
 '2. 跨帧关联与 ID 分配：对车辆、行人、信号灯等目标建立稳定 ID（vehicle_001~011、pedestrian_001~005、signal_001~005），并记录各目标首现/末现帧。',
 '3. 坐标系与尺度约定：以 t=0 自车坐标系为参考（X 前、Y 左、Z 上）；静止物体几何统一表达在 t=0 自车系，运动目标轨迹点表达在各自时刻自车系；尺度由停放车辆相对位移与标准车道宽假设标定（单目无标定，存在系统性尺度误差）。',
 '4. 道路与路口拓扑建模：建立 road_001~004（单向主干道 + 横向 Jackson St）、lane_001~014（进出口各 5 条车道：3 条行车道 + 两侧停车带统一按 driving 车道建模；横向道路各 2 条车道；同一道路内 lane 编号自右侧开始按物理右→左排布）、junction_001（四岔信号路口：5 条直行 lane_connection、停止线、4 条斑马线、4 个冲突区、通行权规则）。',
 '5. 信号与标志时间线：整理车用信号灯（绿灯全程 0~3.5 s，phase_001）、行人信号灯（红手 → 闪烁倒计时）、路名牌/禁止右转/ONE WAY/停车标志等装置。',
 '6. 自车运动估计：自车以约 11 m/s 匀速在中间车道 lane_003 直行，建立 8 点轨迹（x=11t），t≈2.3 s 越过停止线，t=2.5~3.5 s 处于路口内。',
 '7. 参与者轨迹与运动学估计：对 3 辆运动车辆估计位置/速度/加速度模型（vehicle_001 约 13 m/s、vehicle_002 约 12 m/s、vehicle_003 约 10 m/s 制动）；8 辆停放车辆按静止目标建模；5 名行人分别建模（2 人右侧人行道走远、1 人斑马线速度不可测、2 人左侧驻足）。',
 '8. 交互与关键事件识别：识别 interaction_001~003（SUV 超越停放货车、自车与右侧相邻车道 MPV 并行、SUV 与行人潜在冲突）与 event_001~003（自车绿灯过路口、前车临近货车制动、行人驻足未过街）。',
 '9. 仿真场景参数化：生成 simulation_scenario_001（信号交叉口直行通过），包含初始状态、触发条件、动作、终止条件、评价指标与参数取值范围，目标格式 OpenSCENARIO/OpenDRIVE。',
 '10. 一致性检查：执行提示词第十节 10 项检查（ID 稳定、运动连续性、车道与横向位置一致、TTC 计算条件、单位 SI、estimated 数据附置信度与范围等）。',
 '11. 生成最终 JSON 并用 python json.load 校验合法性。',
 '12. 运行本脚本生成复现文档。',
]: p(t)

doc.add_heading('四、输出说明', 1)
p('1) 过程记录：trace.txt —— 逐帧观察、目标汇总与结论推演的完整分析轨迹。')
p('2) 最终输出：scene_analysis_full_0001_0008.json —— 8 帧（t=0.0~3.5 s）合并结果，合法 JSON；顶层九节：metadata / environment / road / ego / actors / interactions / events / simulation_scenarios / quality_report。JSON 字段规范见 analysis_prompt.txt 第九节，本文档省略。')
p('3) 本文档：intersection_scene_analysis_report.docx（由 generate_word_report.py 生成）。')
p('关键数据一览（参与者，共 %d 个）：' % len(data['actors']))
t = doc.add_table(rows=1, cols=5); t.style = 'Table Grid'
hdr = t.rows[0].cells
hdr[0].text='ID'; hdr[1].text='类型/颜色'; hdr[2].text='首现(s)'; hdr[3].text='末现(s)'; hdr[4].text='说明'
notes = {
 'vehicle_001':'左车道红色小车，约13 m/s，同向车队前导，绿灯通过路口',
 'vehicle_002':'右车道深色MPV，约12 m/s，自车右侧相邻车道并行（横向间距约3.3 m），绿灯通过路口',
 'vehicle_003':'右车道黑色SUV，约10 m/s，全程制动灯亮，临近路口超越停放货车，制动原因不明',
 'vehicle_004':'路口前右侧路缘停放的白色厢式货车（对右前视野构成主要遮挡）',
 'vehicle_005':'左侧路缘停放黑色轿车',
 'vehicle_006':'画面左缘停放灰色轿车（部分可见）',
 'vehicle_007':'右侧路缘停放银色轿跑',
 'vehicle_008':'右侧路缘停放深色轿车（位于vehicle_007后方，部分可见）',
 'vehicle_009':'右侧路缘停放白色SUV',
 'vehicle_010':'路口外左侧停放白色轿车',
 'vehicle_011':'路口外右侧停放银色轿车',
 'pedestrian_001':'右侧人行道行人，约1.3 m/s 走远',
 'pedestrian_002':'右侧人行道行人，约1.3 m/s 与pedestrian_001同行',
 'pedestrian_003':'路口外右侧斑马线行人，速度与走向不可判定',
 'pedestrian_004':'左侧转角驻足成人（蓝色外套），靠近停止线，未过街',
 'pedestrian_005':'左侧消防栓附近驻足成人（深色衣着），未过街',
}
for a in data['actors']:
    r = t.add_row().cells
    r[0].text = a['actor_id']
    r[1].text = '%s / %s' % (a.get('actor_type'), a.get('color'))
    r[2].text = str(a.get('first_visible_s'))
    r[3].text = str(a.get('last_visible_s'))
    r[4].text = notes.get(a['actor_id'], ('静止' if a.get('is_static') else '运动'))
p('关键事件一览：')
t = doc.add_table(rows=1, cols=6); t.style = 'Table Grid'
hdr = t.rows[0].cells
hdr[0].text='event_id'; hdr[1].text='类型'; hdr[2].text='开始(s)'; hdr[3].text='峰值(s)'; hdr[4].text='结束(s)'; hdr[5].text='严重度/优先级'
for e in data['events']:
    r = t.add_row().cells
    r[0].text = e['event_id']; r[1].text = e['event_type']
    r[2].text = str(e['start_time_s']); r[3].text = str(e['peak_time_s']); r[4].text = str(e['end_time_s'])
    r[5].text = '%s / %s' % (e['severity'], e['reconstruction_priority'])
p('仿真场景一览：')
for s in data['simulation_scenarios']:
    p('- %s：%s' % (s['scenario_name'], s['description']))

doc.add_heading('五、输出的自然语言描述', 1)
for t in [
 '总体：视频为旧金山城区前视行车记录仪画面（分析窗口为前 3.5 s），自车沿 -8% 下坡的单向三车道 Franklin St 以约 11 m/s 直行，在稳定绿灯期间通过与 Jackson St 的信号交叉口。',
 '环境：白天阴天，散射光无明显眩光；路面干燥；能见度估计约 1000 m（范围 500~2000 m）。',
 '道路：单向三车道（各约 3.3 m）+ 两侧约 2.2 m 停车带；停车带统一按 driving 车道建模并参与编号（自车进口 road_001 与对向出口 road_003 各为 5 条车道，lane 编号自右侧开始按物理右→左排布，构成一致）；白色虚线分道；路口前设停止线（距自车初始位置约 25 m）、近远两侧及左右两侧共 4 条斑马线；Jackson St 为横向单向道路（自右向左），按顺时针编号进口侧为 road_004、出口侧为 road_002，各两车道。',
 '交通控制：主方向车用信号灯（signal_001~003）全程绿灯（phase_001）；行人信号灯 1.0~2.5 s 红手、3.0~3.5 s 闪烁倒计时；另有 Jackson/Franklin 路名牌、禁止右转标志、ONE WAY 标志与横向道路停车标志。',
 '自车（ego）：中间车道 lane_003 匀速约 11 m/s 直行，t≈2.3 s 越过停止线，t=2.5~3.5 s 处于路口内，经 connection_002 驶出路口；全程无制动、无转向。',
 'vehicle_001（左车道红色小车）：约 13 m/s，自车左前方前导，t=0 时位于停止线前约 5 m，绿灯通过路口。',
 'vehicle_002（右车道深色 MPV）：约 12 m/s，位于自车右侧相邻车道（lane_002，横向间距约 3.3 m）并行行驶，绿灯通过路口；因不同车道，不计算 TTC/THW。',
 'vehicle_003（右车道黑色 SUV）：约 10 m/s 且全程制动灯点亮，在路口前沿右车道超越停放厢式货车（vehicle_004，最小横向间距约 1.5 m）后通过路口，制动原因无法从画面判定。',
 '行人：pedestrian_001/002 在右侧人行道以约 1.3 m/s 走远；pedestrian_003 位于路口外右侧斑马线区域（速度不可测）；pedestrian_004/005 在左侧转角驻足未过街（行人信号为红手/倒计时）。',
 '关键事件：event_001 自车绿灯直行通过路口（0~3.5 s，优先级 high）；event_002 黑色 SUV 临近停放货车制动（medium）；event_003 行人驻足未过街（low）。无碰撞、无冲突升级。',
 '仿真场景：simulation_scenario_001 —— 信号交叉口直行通过（含前导车队与停放货车遮挡），给出自车与各参与者初始状态、绿灯触发条件、SpeedAction 动作、终止条件、8 项评价指标（信号合规、最小距离、TTC、PET、最大减速度、最大横向加速度、可行驶区域、出口车道）以及 6 个可参数化变量的取值范围，目标格式 OpenSCENARIO/OpenDRIVE。',
 '质量与局限：单目未标定视频存在系统性尺度歧义，距离/速度均为估计值（带范围与置信度）；相机参数、自车 CAN/GPS 数据缺失；仅分析全片 10.1 s 的前 3.5 s（8 帧）；出口道路横断面按进口侧类比推定、横向街道车道数为估计值（远距离几何未验证）、转向连接不可见；总体置信度 0.45。建议补充相机标定、CAN/GPS、LiDAR 与完整信号周期视频。',
]: p(t)

doc.add_heading('六、运行环境与复现步骤', 1)
p('运行环境：')
for t in [
 '操作系统：Windows（PowerShell 终端）。',
 'Python 3.10.8；python-docx 1.2.0（生成本 Word 文档）。',
 '分析方式：Trae IDE 智能体（视觉语言模型）逐帧读取 frames/ 下图像进行分析；分析轨迹记录于 trace.txt，最终一次性生成结构化 JSON。',
 '抽帧命令（ffmpeg，可选）：ffmpeg -i intersection.mp4 -vf "fps=2" frames/frame_%04d.png',
]: p('- ' + t)
p('复现步骤：')
for t in [
 '1. 准备 intersection.mp4，按上述命令抽帧得到 frames/frame_0001~0008.png。',
 '2. 在 Trae 中打开本工程目录，将 analysis_prompt.txt 作为提示词发送给智能体，附加说明：逐帧分析 frame_0001~0008 并按第九节 Schema 输出 scene_analysis_full_0001_0008.json。',
 '3. 校验 JSON 合法性：python -c "import json;json.load(open(\'scene_analysis_full_0001_0008.json\',encoding=\'utf-8\'))"。',
 '4. 运行 python generate_word_report.py 生成本复现文档 intersection_scene_analysis_report.docx。',
]: p(t)

doc.save(os.path.join(BASE, 'intersection_scene_analysis_report.docx'))
print('Word report saved:', os.path.join(BASE, 'intersection_scene_analysis_report.docx'))
