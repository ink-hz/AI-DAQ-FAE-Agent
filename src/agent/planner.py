"""planner.py — v0.3 Planner: dataclass + 规则 anchor 层 + LLM 层。

A3: dataclass + anchor_rules + planner_rule_fallback(规则兜底)。
A4: plan() 主入口 — LLM(GLM fast)+ 规则裁决融合;crash/empty → rule fallback;
    规则也空 → catch-all experience(决策 #5)。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Literal

from src.agent.schema import RequestSchema  # 复用 v0.2 schema

Capability = Literal[
    "catalog", "spec", "sdk", "selection",
    "experience", "troubleshoot", "risk_compliance",
]

QuestionType = Literal[
    "unknown",
    "catalog_overview",
    "spec_fact",
    "sdk_howto",
    "selection",
    "field_troubleshooting",
    "integration_design",
    "case_reference",
    "boundary",
]

_ALL_CAPABILITIES: tuple[Capability, ...] = (
    "catalog", "spec", "sdk", "selection",
    "experience", "troubleshoot", "risk_compliance",
)


@dataclass
class PlannerInput:
    user_message: str
    history_schema: RequestSchema | None = None
    recent_messages: list[dict] = field(default_factory=list)  # 简化:dict 而非 Message 类
    channel: Literal["fae", "ecom"] = "fae"


@dataclass
class PlanResult:
    primary_capability: Capability
    extra_capabilities: list[Capability]   # 不含 primary
    capability_queries: dict[Capability, str]  # 每模块独立 query
    short_circuit: bool
    confidence: Literal["high", "low"]
    alternatives: list[Capability]
    needs_clarify: bool
    clarify_questions: list[str]            # ≤ 2
    refused: bool
    refusal_text: str | None
    question_type: QuestionType = "unknown"
    evidence_requirements: list[str] = field(default_factory=list)
    answer_contract: list[str] = field(default_factory=list)
    context_required: bool = False

    def __post_init__(self) -> None:
        # 互斥校验
        if self.needs_clarify and self.refused:
            raise ValueError("needs_clarify and refused must not both be True")
        # primary 不能也出现在 extras
        if self.primary_capability in self.extra_capabilities:
            raise ValueError(f"primary {self.primary_capability} appears in extras")
        # clarify_questions ≤ 2
        if len(self.clarify_questions) > 2:
            raise ValueError("clarify_questions must be ≤ 2 items")


# === 规则 anchor 层 ===

# 关键词到 capability 的映射(over-include 原则)
# 复用 v0.2 schema_extractor 中已有的 _PRODUCT_ANCHOR_KEYWORDS / _SPEC_TRIGGER_PHRASES / _TROUBLESHOOT_PHRASES
# 不要重新定义,直接 import + 复用

_SDK_KEYWORDS: tuple[str, ...] = (
    "SDK", "API", "C#", "C++", "Python", "ROS", "Android",
    "安卓", "安卓系统",
    "Linux", "Windows", "Java", "Node.js", "JavaScript",
    "驱动", "调用", "接口", "源码", "示例代码",
    "OpenNI", "OpenCV", "ROS2",
    "AlignFilter", "D2C", "对齐",
)

_CATALOG_KEYWORDS: tuple[str, ...] = (
    "哪些相机", "有哪些", "型号列表", "你们的产品", "产品矩阵",
    "全系列", "什么相机", "都有什么",
)

_SELECTION_KEYWORDS: tuple[str, ...] = (
    "推荐", "选哪", "用哪款", "适合", "应该用", "怎么选",
    "选型", "选个型", "选择", "用什么相机", "哪种相机",
    "哪款相机", "更合适", "比较好", "好一些", "更好",
    "哪个好", "哪个更好", "哪一个好", "二选一", "对比",
    "比较一下", "相比", "对标", "有合适", "合适相机",
    "合适产品", "用哪个型号", "哪个型号",
)

_STRONG_SELECTION_INTENT_KEYWORDS: tuple[str, ...] = (
    "选型", "选个型", "用哪款", "用哪种", "用什么相机",
    "哪种相机", "哪款相机", "哪个型号", "用哪个型号",
    "有合适", "合适相机", "合适产品", "二选一",
)

_CANDIDATE_COMPARISON_MARKERS: tuple[str, ...] = (
    "还是", "相比", "对比", "二选一", "哪个", "哪款", "哪一个",
)

_CANDIDATE_COMPARISON_DECISION_MARKERS: tuple[str, ...] = (
    "好", "合适", "推荐", "选择", "选", "优先", "更稳", "更适合",
)

_PRODUCT_DIFFERENCE_MARKERS: tuple[str, ...] = (
    "区别", "差异", "不同", "不一样", "有什么区别", "有何不同",
)

_CANDIDATE_SET_SELECTION_MARKERS: tuple[str, ...] = (
    "方案", "候选", "相比较", "比较", "对比", "优先", "不用", "要不要",
)

_APPLICATION_GOAL_MARKERS: tuple[str, ...] = (
    "目的", "需求", "用在", "用于", "想要", "计划", "主要是",
    "应用场景", "使用场景", "想用", "希望用", "准备用", "需要用",
)

_APPLICATION_TASK_MARKERS: tuple[str, ...] = (
    "识别", "检测", "测量", "测距", "定位", "重建", "抓取",
    "拍照", "采集", "高度", "体积", "尺寸", "避障", "导航",
    "建模", "扫描", "关节点", "喷涂", "路径规划",
)

_SCENARIO_ENVIRONMENT_MARKERS: tuple[str, ...] = (
    "室内", "室外", "户外", "强光", "暗室", "紫外", "反光",
    "机械臂", "机器人", "流水线", "工位", "末端", "喷涂",
    "环境", "导轨", "架子", "移动", "水箱", "水下", "上方",
    "从上往下", "采集装置", "安装位置",
)

_SCENARIO_GEOMETRY_MARKERS: tuple[str, ...] = (
    "距离", "工作距离", "测量距离", "拍摄距离", "cm", "mm", "厘米",
    "毫米", "米", "尺寸", "厚度", "宽", "长", "FOV", "视野",
)

_SCENARIO_OBJECT_MARKERS: tuple[str, ...] = (
    "物体", "目标", "工件", "表面", "材质", "反光", "尺寸",
    "厚度", "边缘", "车厢", "人体", "苗", "果实",
    "饲料", "料槽", "槽", "牛", "鲍鱼", "连杆", "晶圆",
    "PCB", "二维码", "金属壁",
)

_COMPLIANCE_DOMAIN_KEYWORDS: tuple[str, ...] = (
    "防爆", "认证", "合规", "防护等级", "IP 等级", "IP等级",
    "防水", "防尘", "盐雾", "高温", "低温", "温度范围",
)

_COMPLIANCE_SELECTION_KEYWORDS: tuple[str, ...] = (
    "厂家", "推荐", "建议", "处理", "改装", "合作", "方案", "外壳",
)

_EXPERIENCE_KEYWORDS: tuple[str, ...] = (
    "FAE 经验", "现场", "实际部署", "案例", "经验", "客户反馈",
)

_FIELD_SYMPTOM_KEYWORDS: tuple[str, ...] = (
    "不理想", "不太理想", "不准", "不稳定", "波动", "抖动",
    "异常", "失败", "超时", "报错", "不出图", "黑屏", "花屏",
    "采集不到图像", "没有图像", "无图像", "没图像",
    "看不到画面", "没有画面", "无画面", "没画面",
    "深度为0", "深度为 0", "深度数据为0", "深度数据为 0",
    "无效深度", "黑色阴影", "花花绿绿", "丢帧", "卡顿",
    "滤波", "参数都加满", "效果差", "识别不了", "识别不准",
    "连接不上", "设备不识别", "读不到", "打不开",
    "没反应", "没有反应", "无反应", "网口灯不亮", "灯不亮",
    "缺少插件", "缺插件",
)

_AFTER_SALES_TROUBLESHOOT_KEYWORDS: tuple[str, ...] = (
    "售后", "返厂", "寄回", "寄修", "维修", "检测", "检查",
)

_AFTER_SALES_SYMPTOM_KEYWORDS: tuple[str, ...] = (
    "采集不到图像", "没有图像", "无图像", "没图像",
    "不出图", "黑屏", "打不开", "设备不识别", "连接不上",
    "报错", "故障", "坏了", "异常",
)

_STATUS_INDICATOR_KEYWORDS: tuple[str, ...] = (
    "指示灯", "亮黄灯", "黄灯", "红灯", "蓝灯", "绿灯",
    "亮灯", "闪灯", "闪烁", "常亮", "灯不亮",
)

_STATUS_INDICATOR_QUESTION_KEYWORDS: tuple[str, ...] = (
    "什么原因", "怎么回事", "什么情况", "原因", "情况", "异常",
)

_HOST_ENVIRONMENT_MARKERS: tuple[str, ...] = (
    "换电脑", "换了一台电脑", "另一台电脑", "之前那台", "这台电脑",
    "原电脑", "电脑上", "主机",
)

_HOST_ENVIRONMENT_SYMPTOM_MARKERS: tuple[str, ...] = (
    "看不到", "没有画面", "无画面", "没画面", "缺少插件", "缺插件",
    "插件", "驱动", "打不开",
)

_MATERIAL_CHALLENGE_KEYWORDS: tuple[str, ...] = (
    "反光", "高反", "透明", "半透明", "薄膜", "塑料薄膜",
    "黑色", "吸光", "镜面", "金属", "材质",
)

_MATERIAL_CHALLENGE_ACTION_KEYWORDS: tuple[str, ...] = (
    "解决", "处理", "怎么办", "影响", "识别", "深度", "空洞",
    "不准", "不稳定", "是否可以", "能不能", "可以吗",
)

_SDK_LANGUAGE_AVAILABILITY_MARKERS: tuple[str, ...] = (
    "只会", "支持这个语言", "支持哪些语言", "支持哪种语言",
    "哪些机器", "哪些型号", "有那些", "有哪些", "型号还没定",
    "哪款", "哪种相机",
)

_INTEGRATION_DESIGN_KEYWORDS: tuple[str, ...] = (
    "直接处理输出",
    "处理输出",
    "直接输出结果",
    "输出结果",
    "端侧处理",
    "板端处理",
    "边缘计算",
    "主机处理",
    "跑算法",
    "算法推理",
    "系统集成",
    "集成方案",
    "工业协议",
    "FPGA",
    "GMSL",
    "串行器",
    "解串器",
)

_PRODUCT_MODIFICATION_KEYWORDS: tuple[str, ...] = (
    "去掉外壳", "去外壳", "拆外壳", "拆掉外壳", "拆开外壳", "去壳", "拆壳",
    "去掉结构件", "拆结构件", "改装", "拆解", "拆掉", "去掉镜头", "更换镜头",
    "自行更换", "拆下", "破坏外壳",
)

_MODIFICATION_PART_KEYWORDS: tuple[str, ...] = (
    "外壳", "壳", "结构件", "镜头", "滤光片", "盖板", "外罩",
)

_MODIFICATION_VERB_KEYWORDS: tuple[str, ...] = (
    "去掉", "去除", "拆掉", "拆下", "拆开", "卸掉", "卸下", "移除", "拆除", "破坏",
)

_MEASUREMENT_FEASIBILITY_METRIC_KEYWORDS: tuple[str, ...] = (
    "精度", "误差", "准确度", "重复精度", "测量精度",
)

_MEASUREMENT_FEASIBILITY_CONTEXT_KEYWORDS: tuple[str, ...] = (
    "相机距离", "被测", "工件", "铝型材", "金属", "塑料",
    "尺寸", "长", "宽", "高度", "厚度", "直径", "测量",
    "需求", "方案", "办法",
)

_MEASUREMENT_FEASIBILITY_DISTANCE_REQUIRES: tuple[str, ...] = (
    "米", "mm", "毫米", "cm", "厘米",
)

_DISTANCE_REQUIREMENT_FEASIBILITY_KEYWORDS: tuple[str, ...] = (
    "需求", "办法", "方案", "满足", "不行", "达不到", "能不能",
    "能否", "可以", "做不到", "怎么处理", "怎么解决",
)

_CALIBRATION_INTERFERENCE_ACTION_KEYWORDS: tuple[str, ...] = (
    "标定", "校准", "手眼标定", "外参标定", "内参标定",
)

_CALIBRATION_INTERFERENCE_SOURCE_KEYWORDS: tuple[str, ...] = (
    "结构光", "红外", "IR", "投射", "投影", "散斑", "激光",
)

_CALIBRATION_INTERFERENCE_QUESTION_KEYWORDS: tuple[str, ...] = (
    "干扰", "影响", "冲突", "关掉", "关闭", "打开",
)

_ENVIRONMENT_OPERATING_CONTEXT_KEYWORDS: tuple[str, ...] = (
    "野外", "户外", "室外", "室内", "外面", "天气", "环境",
    "工况", "现场", "长时间", "连续", "全天", "运行",
)

_ENVIRONMENT_OPERATING_BOUNDARY_KEYWORDS: tuple[str, ...] = (
    "环境温度", "温度", "高温", "低温", "发热", "散热",
    "长时间", "连续", "小时", "8小时", "八小时", "全天",
)

_ENVIRONMENT_OPERATING_DECISION_KEYWORDS: tuple[str, ...] = (
    "合适", "适合", "推荐", "建议", "能不能", "能否", "可以",
    "可不可以", "能用", "使用", "用", "产品", "型号", "选",
)

_NETWORK_CONFIG_OBJECT_KEYWORDS: tuple[str, ...] = (
    "IP", "ip", "静态IP", "静态 ip", "固定IP", "固定 ip",
    "DHCP", "dhcp", "网络地址", "设备地址", "网段", "子网",
    "网关", "以太网地址",
)

_NETWORK_CONFIG_ACTION_KEYWORDS: tuple[str, ...] = (
    "怎么改", "修改", "更改", "设置", "配置", "改成", "改的",
    "写入", "自动写", "默认", "恢复", "查看", "指定",
)

_CONTEXT_REFERENCE_KEYWORDS: tuple[str, ...] = (
    "这款", "这两款", "上面", "上述", "前面", "刚刚", "刚才",
    "这个", "那个", "这种", "默认", "满足需求",
    "黄框",
)

_ATTACHMENT_CONTEXT_PATTERNS: tuple[str, ...] = (
    "图片里", "图片中", "图里", "图中", "截图", "照片里",
    "照片中", "视频里", "视频中", "如图", "见图", "黄框",
)

_CONTEXT_FRAGMENT_QUESTION_MARKERS: tuple[str, ...] = (
    "下降多少",
    "变化多少",
    "差多少",
    "有数据支持吗",
    "数据支持吗",
    "有数据吗",
    "有实测吗",
    "具体多少",
)

_COUNTERPART_CONTEXT_KEYWORDS: tuple[str, ...] = (
    "对标", "竞品", "替代产品", "替代型号", "同类产品",
)

_COUNTERPART_GENERIC_TERMS: tuple[str, ...] = (
    "产品", "型号", "相机", "摄像头", "设备", "方案", "这个", "那个",
    "这种", "这款", "哪款", "哪个", "吗", "的",
)

_EXPLICIT_PRODUCT_FAMILY_KEYWORDS: tuple[str, ...] = (
    "Gemini", "gemini", "Femto", "femto", "Astra", "astra",
    "Zora", "zora", "Mega", "mega", "Bolt", "bolt",
    "MS200", "MS500", "MS600", "ms200", "ms500", "ms600",
)

_STREAM_PROFILE_ALIGNMENT_KEYWORDS: tuple[str, ...] = (
    "D2C", "对齐", "profile", "Profile", "内参", "外参",
)

_MULTISENSOR_SYNC_SIGNAL_KEYWORDS: tuple[str, ...] = (
    "同步", "时间同步", "时间戳", "硬件时间戳", "打戳", "授时",
    "VSYNC", "vsync", "Trigger", "trigger", "触发",
    "PPS", "PTP", "gPTP", "GPRMC", "NMEA",
)

_MULTISENSOR_SYNC_SCOPE_KEYWORDS: tuple[str, ...] = (
    "多传感器", "多设备", "跨设备", "不同外设", "其他设备",
    "激光雷达", "雷达", "深度相机", "RGB 相机", "RGB相机",
    "2D工业相机", "2D 工业相机", "IMU", "主控", "电脑",
)

_MULTISENSOR_SYNC_SENSOR_TERMS: tuple[str, ...] = (
    "相机", "深度相机", "RGB", "激光雷达", "雷达", "IMU",
    "外设", "传感器", "主控", "电脑",
)

_HARDWARE_INTERFACE_OBJECT_KEYWORDS: tuple[str, ...] = (
    "USB", "Type-C", "Type-A", "SBU", "DC口", "DC 口",
    "M12", "M8", "RJ45", "X-coded", "X coded", "网口",
    "电源插口", "电源接口", "适配器", "线材", "线缆",
    "数据线", "线束", "延长线", "转接线",
    "转接头", "接头", "连接器", "接线", "引脚", "pin", "Pin",
    "网线", "以太网", "Ethernet", "PoE", "供电线", "电源线",
    "交换机", "PoE交换机", "PoE 交换机", "HUB", "Hub", "hub",
    "同步器", "12V", "24V", "光纤", "光口", "光模块", "光电转换",
)

_HARDWARE_INTERFACE_ACTION_KEYWORDS: tuple[str, ...] = (
    "包含", "确认", "支持", "需要", "需不需要", "能否", "能不能",
    "可以", "尺寸", "规格", "接口", "什么接口", "配", "连接", "接",
    "能到", "最长", "多长", "保障", "稳定",
    "型号", "要求", "推荐", "链接", "搜索", "关键词", "购买",
    "长一点", "更长", "两百米", "200米", "200m", "200 m",
    "抗干扰", "干扰", "屏蔽", "电磁", "EMI", "输出", "传输",
)

_POWER_DATA_INTEGRATION_KEYWORDS: tuple[str, ...] = (
    "供电", "传输数据", "数据传输", "传数据",
)

_POWER_DATA_INTEGRATION_QUESTION_KEYWORDS: tuple[str, ...] = (
    "怎么", "如何", "方式", "方案", "接口", "接线",
)

_POINT_CLOUD_INSPECTION_OBJECT_KEYWORDS: tuple[str, ...] = (
    "点云", "点云图", "PointCloud", "point cloud", "Point Cloud",
)

_POINT_CLOUD_INSPECTION_ACTION_KEYWORDS: tuple[str, ...] = (
    "在哪看", "在哪里看", "哪看", "怎么看", "查看", "显示",
    "没看到", "看不到", "间隙", "空洞",
)

_CALIBRATION_PARAMETER_KEYWORDS: tuple[str, ...] = (
    "内参", "外参", "畸变", "手眼标定", "标定板", "相机标定",
    "标定方法", "偏移", "重投影",
)

_CALIBRATION_PARAMETER_ACTION_KEYWORDS: tuple[str, ...] = (
    "读取", "得到", "获取", "参数", "方法", "结果", "离谱",
    "特殊", "分辨率", "标定",
)

_USER_PROVIDED_CONFIRMED_PREMISE_KEYWORDS: tuple[str, ...] = (
    "产品经理回复", "研发确认", "研发回复", "FAE确认", "FAE 回复",
    "客户确认", "回复称", "确认是", "已确认",
)

_ACCESSORY_INFRASTRUCTURE_OBJECT_KEYWORDS: tuple[str, ...] = (
    "交换机", "PoE交换机", "PoE 交换机", "网线", "数据线",
    "线缆", "线束", "延长线", "转接线", "同步器", "Hub", "HUB",
)

_ACCESSORY_INFRASTRUCTURE_ACTION_KEYWORDS: tuple[str, ...] = (
    "型号", "要求", "推荐", "链接", "搜索", "关键词", "购买",
    "多长", "最长", "长一点", "更长", "两百米", "200米", "200m",
    "200 m", "需要", "需不需要", "能不能", "可以吗",
)

_CASCADE_INTEGRATION_KEYWORDS: tuple[str, ...] = (
    "级联", "串联", "多机", "多设备", "多相机", "同步采集",
    "同步触发", "外触发", "硬件触发",
)

_MATERIAL_DURABILITY_KEYWORDS: tuple[str, ...] = (
    "前盖", "玻璃", "镜片", "硬度", "强度", "耐磨", "抗造",
    "耐冲击", "恶劣工况", "沙尘", "砂尘", "刮擦", "划伤",
)


def anchor_rules(user_message: str) -> set[Capability]:
    """规则匹配:返回触发的 capability 集合。

    Over-include 原则:多关键词命中就多加 capability;risk_compliance 默认始终在。
    """
    msg = user_message
    caps: set[Capability] = {"risk_compliance"}  # 默认横切

    # 复用 v0.2 schema_extractor 现有规则集
    from src.agent.schema_extractor import (
        _PRODUCT_ANCHOR_KEYWORDS,
        _SPEC_TRIGGER_PHRASES,
        _TROUBLESHOOT_PHRASES,
    )

    if any(k in msg for k in _PRODUCT_ANCHOR_KEYWORDS):
        caps.add("spec")
    if any(p in msg for p in _SPEC_TRIGGER_PHRASES):
        caps.add("spec")
    if any(p in msg for p in _TROUBLESHOOT_PHRASES):
        caps.add("troubleshoot")
    if _is_field_troubleshooting_message(msg):
        caps.update({"troubleshoot", "spec", "sdk", "experience"})
    if _is_after_sales_troubleshooting_message(msg):
        caps.update({"troubleshoot", "risk_compliance"})
    if _is_status_indicator_troubleshooting_message(msg):
        caps.update({"troubleshoot", "spec", "experience"})
    if _is_material_challenge_message(msg):
        caps.update({"troubleshoot", "spec", "experience", "selection"})
    if _is_integration_design_message(msg):
        caps.update({"sdk", "spec"})
    if _is_product_modification_message(msg):
        caps.update({"spec", "risk_compliance"})
    if _is_multisensor_time_sync_message(msg):
        caps.update({"sdk", "spec", "experience", "troubleshoot"})
    if _is_cascade_integration_message(msg):
        caps.update({"sdk", "spec", "experience"})
    if _is_stream_profile_alignment_message(msg):
        caps.update({"sdk", "spec"})
    if _is_hardware_interface_integration_message(msg):
        caps.add("spec")
        if _is_alternative_interface_selection_message(msg):
            caps.add("selection")
    if _is_accessory_infrastructure_message(msg):
        caps.update({"spec", "selection"})
    if _is_material_durability_message(msg):
        caps.add("spec")
    if _is_user_provided_confirmed_premise(msg):
        caps.update({"spec", "experience"})
    if _is_measurement_feasibility_message(msg):
        caps.update({"selection", "spec"})
    if _is_distance_requirement_feasibility_message(msg):
        caps.update({"selection", "spec"})
    if _is_calibration_interference_message(msg):
        caps.update({"sdk", "spec"})
    if _is_environment_operating_message(msg):
        caps.update({"selection", "spec"})
    if _is_network_configuration_message(msg):
        caps.update({"sdk", "spec"})
    if _is_point_cloud_inspection_message(msg):
        caps.update({"sdk", "spec", "troubleshoot"})
    if _is_calibration_parameter_message(msg):
        caps.update({"sdk", "spec"})
    if any(k in msg for k in _SDK_KEYWORDS):
        caps.add("sdk")
    if _is_sdk_language_availability_message(msg):
        caps.update({"sdk", "selection", "catalog"})
    if any(k in msg for k in _CATALOG_KEYWORDS):
        caps.add("catalog")
    if any(k in msg for k in _SELECTION_KEYWORDS):
        caps.add("selection")
        if _is_product_fit_selection_message(msg):
            caps.update({"catalog", "spec"})
    if _is_candidate_comparison_message(msg):
        caps.add("selection")
    if _is_candidate_set_selection_message(msg):
        caps.add("selection")
    if any(keyword in msg for keyword in _COUNTERPART_CONTEXT_KEYWORDS):
        caps.update({"selection", "spec", "experience"})
    if _is_application_goal_message(msg):
        caps.add("selection")
    if _is_scenario_constraint_selection_message(msg):
        caps.update({"selection", "spec"})
    if _is_compliance_domain_message(msg):
        caps.add("spec")
        if _is_compliance_selection_message(msg):
            caps.add("selection")
    if any(k in msg for k in _EXPERIENCE_KEYWORDS):
        caps.add("experience")

    return caps


def _is_field_troubleshooting_message(message: str) -> bool:
    """Detect broad field-effect symptoms, not exact eval sentences."""
    return (
        any(keyword in message for keyword in _FIELD_SYMPTOM_KEYWORDS)
        or _is_status_indicator_troubleshooting_message(message)
        or _is_host_environment_troubleshooting_message(message)
        or _is_material_challenge_message(message)
        or _is_after_sales_troubleshooting_message(message)
    )


def _is_after_sales_troubleshooting_message(message: str) -> bool:
    """Detect after-sales requests that still need first-pass field diagnosis."""
    return (
        any(keyword in message for keyword in _AFTER_SALES_TROUBLESHOOT_KEYWORDS)
        and any(keyword in message for keyword in _AFTER_SALES_SYMPTOM_KEYWORDS)
    )


def _is_status_indicator_troubleshooting_message(message: str) -> bool:
    """Detect status-light symptoms without requiring a concrete model anchor."""
    return (
        any(keyword in message for keyword in _STATUS_INDICATOR_KEYWORDS)
        and any(keyword in message for keyword in _STATUS_INDICATOR_QUESTION_KEYWORDS)
    )


def _is_host_environment_troubleshooting_message(message: str) -> bool:
    """Detect host-specific failures such as one PC showing images while another does not."""
    return (
        any(keyword in message for keyword in _HOST_ENVIRONMENT_MARKERS)
        and any(keyword in message for keyword in _HOST_ENVIRONMENT_SYMPTOM_MARKERS)
    )


def _is_material_challenge_message(message: str) -> bool:
    """Detect reflective/transparent/dark-material issues requiring FAE guidance."""
    return (
        any(keyword in message for keyword in _MATERIAL_CHALLENGE_KEYWORDS)
        and any(keyword in message for keyword in _MATERIAL_CHALLENGE_ACTION_KEYWORDS)
    )


def _is_sdk_language_availability_message(message: str) -> bool:
    """Detect language-support range questions before a model is selected."""
    has_language = any(_contains_keyword(message, keyword) for keyword in _SDK_KEYWORDS)
    if not has_language:
        return False
    return any(keyword in message for keyword in _SDK_LANGUAGE_AVAILABILITY_MARKERS)


def _is_integration_design_message(message: str) -> bool:
    """Detect integration-boundary questions that need platform capability facts."""
    return any(keyword in message for keyword in _INTEGRATION_DESIGN_KEYWORDS)


def _is_product_modification_message(message: str) -> bool:
    """Detect product-modification questions (remove housing, swap lens, disassemble).

    These need a risk_compliance position first: standard products are
    factory-calibrated sealed units, so modifying them risks calibration drift,
    sealing/heat loss, and voids warranty. The answer must state that position
    up front, not bury "not recommended / warranty void" in a footnote and end
    with a clarifying question.
    """
    return any(keyword in message for keyword in _PRODUCT_MODIFICATION_KEYWORDS) or (
        any(part in message for part in _MODIFICATION_PART_KEYWORDS)
        and any(verb in message for verb in _MODIFICATION_VERB_KEYWORDS)
    )


def _is_multisensor_time_sync_message(message: str) -> bool:
    """Detect cross-device timing designs requiring more than model specs.

    This covers a class of FAE questions where users mix camera trigger lines,
    lidar PPS/PTP, IMU timestamps, host stamping, and SDK time domains. A useful
    answer needs product/spec facts plus SDK and field evidence; a pure spec
    lookup often produces unsourced but plausible engineering prose.
    """
    has_timing_signal = any(_contains_keyword(message, keyword) for keyword in _MULTISENSOR_SYNC_SIGNAL_KEYWORDS)
    if not has_timing_signal:
        return False
    if any(_contains_keyword(message, keyword) for keyword in _MULTISENSOR_SYNC_SCOPE_KEYWORDS):
        return True
    matched_sensor_terms = {
        keyword
        for keyword in _MULTISENSOR_SYNC_SENSOR_TERMS
        if _contains_keyword(message, keyword)
    }
    return len(matched_sensor_terms) >= 2


def _is_cascade_integration_message(message: str) -> bool:
    """Detect device cascade / multi-device integration capability questions."""
    return any(_contains_keyword(message, keyword) for keyword in _CASCADE_INTEGRATION_KEYWORDS)


def _is_stream_profile_alignment_message(message: str) -> bool:
    """Detect depth/color profile and alignment questions requiring SDK evidence."""
    has_depth_color_pair = "深度" in message and "彩色" in message
    has_profile_term = any(keyword in message for keyword in _STREAM_PROFILE_ALIGNMENT_KEYWORDS)
    has_resolution_or_alignment = any(
        keyword in message
        for keyword in ("分辨率", "对齐", "D2C", "profile", "Profile", "内参", "外参")
    )
    return (has_depth_color_pair and has_resolution_or_alignment) or has_profile_term


def _contains_keyword(message: str, keyword: str) -> bool:
    return keyword in message or keyword.lower() in message.lower()


def _is_hardware_interface_integration_message(message: str) -> bool:
    """Detect cable/power/connector boundary questions.

    These are not ordinary model specs: a useful FAE answer should separate
    confirmed product facts from connector/cable engineering boundaries and
    propose verification, instead of immediately asking for a model.
    """
    if _is_power_data_integration_message(message):
        return True
    return (
        any(_contains_keyword(message, keyword) for keyword in _HARDWARE_INTERFACE_OBJECT_KEYWORDS)
        and any(keyword in message for keyword in _HARDWARE_INTERFACE_ACTION_KEYWORDS)
    )


def _is_accessory_infrastructure_message(message: str) -> bool:
    """Detect switch/cable/sync accessory infrastructure questions."""
    return (
        any(_contains_keyword(message, keyword) for keyword in _ACCESSORY_INFRASTRUCTURE_OBJECT_KEYWORDS)
        and any(keyword in message for keyword in _ACCESSORY_INFRASTRUCTURE_ACTION_KEYWORDS)
    )


def _is_material_durability_message(message: str) -> bool:
    """Detect housing/lens/glass durability questions requiring spec boundaries."""
    hits = sum(1 for keyword in _MATERIAL_DURABILITY_KEYWORDS if keyword in message)
    return hits >= 2


def _is_measurement_feasibility_message(message: str) -> bool:
    """Detect open measurement-feasibility questions that need selection tradeoffs."""
    has_metric = any(keyword in message for keyword in _MEASUREMENT_FEASIBILITY_METRIC_KEYWORDS)
    if not has_metric:
        return False
    has_scene_context = any(keyword in message for keyword in _MEASUREMENT_FEASIBILITY_CONTEXT_KEYWORDS)
    has_distance_or_size = any(keyword in message for keyword in _MEASUREMENT_FEASIBILITY_DISTANCE_REQUIRES)
    return has_scene_context and has_distance_or_size


def _is_distance_requirement_feasibility_message(message: str) -> bool:
    """Detect distance requirements that need feasibility and alternative paths."""
    has_distance = any(keyword in message for keyword in _MEASUREMENT_FEASIBILITY_DISTANCE_REQUIRES)
    has_feasibility_request = any(keyword in message for keyword in _DISTANCE_REQUIREMENT_FEASIBILITY_KEYWORDS)
    return has_distance and has_feasibility_request


def _is_calibration_interference_message(message: str) -> bool:
    """Detect calibration questions involving active illumination interference."""
    return (
        any(keyword in message for keyword in _CALIBRATION_INTERFERENCE_ACTION_KEYWORDS)
        and any(_contains_keyword(message, keyword) for keyword in _CALIBRATION_INTERFERENCE_SOURCE_KEYWORDS)
        and any(keyword in message for keyword in _CALIBRATION_INTERFERENCE_QUESTION_KEYWORDS)
    )


def _is_environment_operating_message(message: str) -> bool:
    """Detect environmental operating-boundary and long-run suitability questions."""
    has_environment = any(keyword in message for keyword in _ENVIRONMENT_OPERATING_CONTEXT_KEYWORDS)
    has_boundary = (
        any(keyword in message for keyword in _ENVIRONMENT_OPERATING_BOUNDARY_KEYWORDS)
        or bool(re.search(r"\d+(?:\.\d+)?\s*(?:度|℃|°C|小时|h|H)", message))
    )
    has_decision = any(keyword in message for keyword in _ENVIRONMENT_OPERATING_DECISION_KEYWORDS)
    return has_environment and has_boundary and has_decision


def _is_network_configuration_message(message: str) -> bool:
    """Detect IP address configuration questions without confusing IP rating."""
    if "IP等级" in message or "IP 等级" in message or "防护等级" in message:
        return False
    return (
        any(_contains_keyword(message, keyword) for keyword in _NETWORK_CONFIG_OBJECT_KEYWORDS)
        and any(keyword in message for keyword in _NETWORK_CONFIG_ACTION_KEYWORDS)
    )


def _is_point_cloud_inspection_message(message: str) -> bool:
    """Detect Viewer/SDK entry-point questions for point-cloud inspection."""
    return (
        any(_contains_keyword(message, keyword) for keyword in _POINT_CLOUD_INSPECTION_OBJECT_KEYWORDS)
        and any(keyword in message for keyword in _POINT_CLOUD_INSPECTION_ACTION_KEYWORDS)
    )


def _is_calibration_parameter_message(message: str) -> bool:
    """Detect SDK/calibration parameter questions without treating data images as attachments."""
    return (
        any(keyword in message for keyword in _CALIBRATION_PARAMETER_KEYWORDS)
        and any(keyword in message for keyword in _CALIBRATION_PARAMETER_ACTION_KEYWORDS)
    )


def _is_alternative_interface_selection_message(message: str) -> bool:
    """Detect interface asks where the useful answer should include conversion/alternative paths."""
    return (
        any(keyword in message for keyword in ("光纤", "光口", "光模块", "光电转换"))
        and any(keyword in message for keyword in ("输出", "传输", "支持", "有", "接口"))
    )


def _is_power_data_integration_message(message: str) -> bool:
    """Detect generic power + data transport integration asks."""
    return (
        "供电" in message
        and any(keyword in message for keyword in ("传输数据", "数据传输", "传数据", "数据"))
        and any(keyword in message for keyword in _POWER_DATA_INTEGRATION_QUESTION_KEYWORDS)
    )


def _is_user_provided_confirmed_premise(message: str) -> bool:
    """Detect when the user brings a confirmed premise that should be explained with boundaries."""
    return any(keyword in message for keyword in _USER_PROVIDED_CONFIRMED_PREMISE_KEYWORDS)


def _is_explicit_selection_message(message: str) -> bool:
    """Detect explicit candidate-selection requests, independent of scenario risks."""
    normalized = (
        message
        .replace("推荐范围内", "")
        .replace("推荐范围", "")
        .replace("相机推荐范围", "")
    )
    return (
        any(keyword in normalized for keyword in _SELECTION_KEYWORDS)
        or _is_candidate_comparison_message(normalized)
        or _is_candidate_set_selection_message(normalized)
    )


def _is_strong_selection_intent_message(message: str) -> bool:
    """Detect asks that explicitly want product choice, not generic advice."""
    normalized = (
        message
        .replace("推荐范围内", "")
        .replace("推荐范围", "")
        .replace("相机推荐范围", "")
    )
    if any(keyword in normalized for keyword in _STRONG_SELECTION_INTENT_KEYWORDS):
        return True
    if _is_candidate_comparison_message(normalized) or _is_candidate_set_selection_message(normalized):
        return True
    return (
        "推荐" in normalized
        and any(keyword in normalized for keyword in ("相机", "型号", "产品", "方案", "哪款", "哪种"))
    )


def _is_candidate_comparison_message(message: str) -> bool:
    """Detect candidate tradeoff requests without relying on exact product names.

    Real FAE questions often phrase selection as "A 还是 B 好一些" rather
    than "推荐哪款". This is a selection request because the user expects
    a tradeoff, not just two independent specification lookups.
    """
    return (
        any(marker in message for marker in _CANDIDATE_COMPARISON_MARKERS)
        and any(marker in message for marker in _CANDIDATE_COMPARISON_DECISION_MARKERS)
    ) or _is_product_difference_message(message)


def _is_product_difference_message(message: str) -> bool:
    """Detect "A 和 B 区别是什么" difference questions as comparisons.

    A difference question is a comparison even without a decision word like
    "好/推荐": the FAE answer must lead with the decisive dimensions that
    distinguish the products, not bury "core specs not comparable" in a note
    and headline model codes/connectors as if they were the core difference.
    Requires two product/model hints so a lone "区别" does not over-trigger.
    """
    if not any(marker in message for marker in _PRODUCT_DIFFERENCE_MARKERS):
        return False
    model_hits = len(re.findall(r"\d{3,4}[A-Za-z]*", message))
    return model_hits >= 2 or (
        _has_explicit_product_text_anchor(message) and model_hits >= 1
    )


def _is_candidate_set_selection_message(message: str) -> bool:
    """Detect multi-candidate tradeoff questions that are not phrased as A 还是 B."""
    has_candidate_language = any(marker in message for marker in _CANDIDATE_SET_SELECTION_MARKERS)
    has_product_or_model_hint = (
        _has_explicit_product_text_anchor(message)
        or bool(re.search(r"\b\d{3,4}[A-Za-z]*\b", message))
    )
    has_tradeoff_action = any(marker in message for marker in ("比较", "相比较", "优先", "不用", "选择", "方案"))
    return has_candidate_language and has_product_or_model_hint and has_tradeoff_action


def _is_product_fit_selection_message(message: str) -> bool:
    """Detect open 'is there a suitable camera/product' asks that need catalog scan."""
    return any(keyword in message for keyword in ("有合适", "合适相机", "合适产品", "哪个型号"))


def _is_application_goal_message(message: str) -> bool:
    """Detect domain application goals that need solution selection even without "推荐"."""
    return (
        any(keyword in message for keyword in _APPLICATION_GOAL_MARKERS)
        and any(keyword in message for keyword in _APPLICATION_TASK_MARKERS)
    )


def _is_scenario_constraint_selection_message(message: str) -> bool:
    """Detect dense application constraints that imply product/solution selection.

    Real FAE inputs often list environment, distance, object and installation
    constraints without explicitly saying "recommend a camera". This is still a
    selection/feasibility question because the user expects a product boundary
    judgment, not a single spec lookup.
    """
    has_environment = any(keyword in message for keyword in _SCENARIO_ENVIRONMENT_MARKERS)
    has_geometry = (
        any(keyword in message for keyword in _SCENARIO_GEOMETRY_MARKERS)
        or _has_numeric_geometry_signal(message)
    )
    has_object_or_task = (
        any(keyword in message for keyword in _SCENARIO_OBJECT_MARKERS)
        or any(keyword in message for keyword in _APPLICATION_TASK_MARKERS)
    )
    return has_environment and has_geometry and has_object_or_task


def _has_numeric_geometry_signal(message: str) -> bool:
    """Detect metric units written with Latin unit symbols in real FAE chats."""
    return bool(
        re.search(
            r"\d+(?:\.\d+)?\s*(?:m/s|m每s|m每秒|米/秒|米每秒|m|M|cm|CM|mm|MM|厘米|毫米|米)",
            message,
        )
    )


def _is_compliance_domain_message(message: str) -> bool:
    return any(keyword in message for keyword in _COMPLIANCE_DOMAIN_KEYWORDS)


def _is_compliance_selection_message(message: str) -> bool:
    return _is_compliance_domain_message(message) and any(
        keyword in message for keyword in _COMPLIANCE_SELECTION_KEYWORDS
    )


def _requires_context_resolution(message: str) -> bool:
    return (
        any(keyword in message for keyword in _CONTEXT_REFERENCE_KEYWORDS)
        or _is_attachment_context_reference(message)
        or _is_context_dependent_fragment(message)
        or _is_contextless_counterpart_request(message)
    )


def _is_attachment_context_reference(message: str) -> bool:
    """Detect missing chat attachments while allowing ordinary phrases like 标定板图片."""
    if any(pattern in message for pattern in _ATTACHMENT_CONTEXT_PATTERNS):
        return True
    return bool(re.search(r"(图片|图|照片|视频).{0,8}(里|中|上|下|黄框|标注)", message))


def _is_context_dependent_fragment(message: str) -> bool:
    """Detect short follow-up fragments that have no standalone subject.

    These are common in raw chat exports. Without prior turn context, retrieving
    a similar QA can produce a polished but wrong answer. The right protocol is
    to ask for the missing object or comparison condition.
    """
    text = message.strip()
    if len(text) > 32:
        return False
    if not any(marker in text for marker in _CONTEXT_FRAGMENT_QUESTION_MARKERS):
        return False
    from src.agent.schema_extractor import (
        _PRODUCT_ANCHOR_KEYWORDS,
        _SPEC_TRIGGER_PHRASES,
    )

    if any(keyword in text for keyword in _PRODUCT_ANCHOR_KEYWORDS):
        return False
    if any(keyword in text for keyword in _SPEC_TRIGGER_PHRASES):
        return False
    return True


def _is_contextless_counterpart_request(message: str) -> bool:
    """Detect counterpart questions without the target competitor/product."""
    text = message.strip()
    if not any(keyword in text for keyword in _COUNTERPART_CONTEXT_KEYWORDS):
        return False
    return not (
        _has_explicit_product_text_anchor(text)
        or _has_named_counterpart_anchor(text)
        or _has_standalone_domain_context(text)
    )


def _has_named_counterpart_anchor(message: str) -> bool:
    """Whether a counterpart question names a concrete comparison target.

    This intentionally detects broad anchors rather than exact products:
    - Latin/digit competitor names such as "LEAP MOTION2";
    - Chinese names adjacent to "对标", excluding generic words like "产品".
    """
    text = message.strip()
    if re.search(r"[A-Za-z][A-Za-z0-9 _+\-]{1,32}", text):
        return True
    match = re.search(r"对标\s*([\u4e00-\u9fffA-Za-z0-9 _+\-]{2,24})", text)
    if not match:
        return False
    candidate = re.split(r"[的和与,，。？?\s]", match.group(1).strip(), maxsplit=1)[0]
    if len(candidate) < 2:
        return False
    return all(term not in candidate for term in _COUNTERPART_GENERIC_TERMS)


def _has_explicit_product_text_anchor(message: str) -> bool:
    return any(_contains_keyword(message, keyword) for keyword in _EXPLICIT_PRODUCT_FAMILY_KEYWORDS)


def _has_product_subject_anchor(schema: RequestSchema | None, message: str) -> bool:
    if schema is not None and getattr(schema, "products", None):
        return True
    return _has_explicit_product_text_anchor(message)


def _has_standalone_domain_context(message: str) -> bool:
    """Whether a deictic message still contains enough standalone scenario signal."""
    return (
        _is_field_troubleshooting_message(message)
        or _has_named_counterpart_anchor(message)
        or _is_measurement_feasibility_message(message)
        or _is_sdk_language_availability_message(message)
        or _is_accessory_infrastructure_message(message)
        or _is_application_goal_message(message)
        or _is_scenario_constraint_selection_message(message)
        or _is_distance_requirement_feasibility_message(message)
        or _is_calibration_interference_message(message)
        or _is_calibration_parameter_message(message)
        or _is_environment_operating_message(message)
        or _is_network_configuration_message(message)
        or _is_point_cloud_inspection_message(message)
    )


def _requires_subject_clarification(message: str, schema: RequestSchema | None = None) -> bool:
    """Return true when resolving would require an absent subject from context.

    This is not a refusal. It prevents polished answers from binding a follow-up
    fragment such as "那个/这种/默认" to an arbitrary product or evidence hit.
    """
    if _has_product_subject_anchor(schema, message):
        return False
    if _is_context_dependent_fragment(message) or _is_contextless_counterpart_request(message):
        return True
    if any(keyword in message for keyword in _CONTEXT_REFERENCE_KEYWORDS) or _is_attachment_context_reference(message):
        if _has_standalone_domain_context(message):
            return False
        from src.agent.schema_extractor import _SPEC_TRIGGER_PHRASES

        if (
            _is_hardware_interface_integration_message(message)
            or any(_contains_keyword(message, keyword) for keyword in _SDK_KEYWORDS)
            or any(keyword in message for keyword in _SPEC_TRIGGER_PHRASES)
        ):
            return True
        if _schema_has_subject_anchor(schema):
            return False
        return True
    return False


def _requires_product_anchor_for_model_specific_specs(
    message: str,
    schema: RequestSchema | None = None,
) -> bool:
    """Product material/mechanical durability asks need a concrete model anchor."""
    return _is_material_durability_message(message) and not _has_product_subject_anchor(schema, message)


def _clarify_question_for_profile(message: str, schema: RequestSchema | None = None) -> str:
    if _requires_product_anchor_for_model_specific_specs(message, schema):
        return "请补充具体相机型号或系列,材料/镜片/结构参数不同型号不能混用。"
    if _is_contextless_counterpart_request(message):
        return "请补充需要对标的竞品品牌/型号和最关注的指标。"
    if _is_hardware_interface_integration_message(message):
        return "请补充您说的“那个/这个”对应的具体相机型号或接口位置。"
    return "请补充这句话对应的具体对象、型号或上一轮场景。"


def _infer_question_type(message: str, caps: set[Capability]) -> QuestionType:
    if _is_strong_selection_intent_message(message) and "selection" in caps:
        return "selection"
    if _is_field_troubleshooting_message(message):
        return "field_troubleshooting"
    if _is_integration_design_message(message):
        return "integration_design"
    if _is_product_modification_message(message):
        return "integration_design"
    if _is_multisensor_time_sync_message(message):
        return "integration_design"
    if _is_cascade_integration_message(message):
        return "integration_design"
    if _is_hardware_interface_integration_message(message):
        return "integration_design"
    if _is_accessory_infrastructure_message(message):
        return "integration_design"
    if _is_calibration_interference_message(message):
        return "integration_design"
    if _is_environment_operating_message(message):
        return "selection"
    if _is_network_configuration_message(message):
        return "sdk_howto"
    if _is_point_cloud_inspection_message(message):
        return "sdk_howto"
    if _is_calibration_parameter_message(message):
        return "sdk_howto"
    if _is_sdk_language_availability_message(message) and "sdk" in caps:
        return "sdk_howto"
    if _is_explicit_selection_message(message) and "selection" in caps:
        return "selection"
    if _is_measurement_feasibility_message(message):
        return "selection"
    if _is_scenario_constraint_selection_message(message):
        return "selection"
    if _is_distance_requirement_feasibility_message(message):
        return "selection"
    if _is_compliance_selection_message(message) and "selection" in caps:
        return "selection"
    if _is_application_goal_message(message) and "selection" in caps:
        return "selection"
    if "sdk" in caps:
        return "sdk_howto"
    if "selection" in caps:
        return "selection"
    if "catalog" in caps:
        return "catalog_overview"
    if "spec" in caps:
        return "spec_fact"
    return "unknown"


def _infer_evidence_requirements(
    question_type: QuestionType,
    caps: set[Capability],
    *,
    context_required: bool,
) -> list[str]:
    requirements: list[str] = []
    if question_type == "field_troubleshooting":
        requirements.extend([
            "model_or_setup_scope",
            "first_pass_diagnostics",
            "do_not_treat_missing_evidence_as_negative_support",
        ])
    if question_type == "integration_design":
        requirements.extend([
            "integration_boundary",
            "do_not_treat_missing_evidence_as_negative_support",
        ])
    if "sdk" in caps:
        requirements.append("sdk_specific_support")
    if "spec" in caps:
        requirements.append("model_specific_spec_or_boundary")
    if "selection" in caps:
        requirements.append("scenario_constraints_and_tradeoffs")
    if question_type == "selection" and "selection" in caps:
        requirements.append("measurement_or_scenario_feasibility_boundary")
    if context_required:
        requirements.append("resolve_context_reference_or_state_unavailable")
    return _dedupe(requirements)


def _infer_answer_contract(question_type: QuestionType, caps: set[Capability]) -> list[str]:
    contract: list[str] = ["separate_confirmed_from_unknown"]
    if question_type == "field_troubleshooting":
        contract.extend([
            "first_pass_diagnostics",
            "ask_only_after_initial_diagnosis",
            "hypotheses_before_root_cause",
        ])
    if question_type == "integration_design":
        contract.append("bounded_answer_before_clarifying")
    if "sdk" in caps:
        contract.append("mark_code_as_executable_or_pseudocode")
    if "selection" in caps:
        contract.append("rank_candidates_by_constraints_and_risk")
    if question_type == "selection":
        contract.append("bounded_answer_before_clarifying")
    contract.append("no_negative_from_missing_evidence")
    return _dedupe(contract)


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item and item not in seen:
            result.append(item)
            seen.add(item)
    return result


def _profile_defaults(
    user_message: str,
    caps: set[Capability],
    *,
    schema: RequestSchema | None = None,
) -> dict:
    subject_clarification_required = _requires_subject_clarification(user_message, schema)
    context_required = subject_clarification_required
    if (
        context_required
        and _schema_has_subject_anchor(schema)
        and not subject_clarification_required
    ):
        context_required = False
    requires_clarify = (
        subject_clarification_required
        or _requires_product_anchor_for_model_specific_specs(user_message, schema)
    )
    question_type = _infer_question_type(user_message, caps)
    evidence_requirements = _infer_evidence_requirements(
        question_type,
        caps,
        context_required=context_required,
    )
    answer_contract = _infer_answer_contract(question_type, caps)
    if _is_hardware_interface_integration_message(user_message):
        evidence_requirements = _dedupe(evidence_requirements + ["hardware_interface_boundary"])
        answer_contract = _dedupe(answer_contract + ["allow_bounded_general_engineering_answer"])
    if _is_accessory_infrastructure_message(user_message):
        evidence_requirements = _dedupe(evidence_requirements + ["hardware_interface_boundary"])
        answer_contract = _dedupe(answer_contract + ["allow_bounded_general_engineering_answer"])
    if _is_cascade_integration_message(user_message):
        evidence_requirements = _dedupe(evidence_requirements + ["multi_device_or_cascade_boundary"])
        answer_contract = _dedupe(answer_contract + ["bounded_answer_before_clarifying"])
    if _is_calibration_interference_message(user_message):
        evidence_requirements = _dedupe(evidence_requirements + ["calibration_interference_boundary"])
        answer_contract = _dedupe(answer_contract + ["allow_bounded_general_engineering_answer"])
    if _is_multisensor_time_sync_message(user_message):
        evidence_requirements = _dedupe(evidence_requirements + ["multisensor_time_sync_boundary"])
        answer_contract = _dedupe(answer_contract + ["allow_bounded_general_engineering_answer"])
    if _is_environment_operating_message(user_message):
        evidence_requirements = _dedupe(evidence_requirements + ["environment_operating_boundary"])
        answer_contract = _dedupe(answer_contract + ["allow_bounded_general_engineering_answer"])
    if _is_network_configuration_message(user_message):
        evidence_requirements = _dedupe(evidence_requirements + ["network_configuration_boundary"])
        answer_contract = _dedupe(answer_contract + ["allow_bounded_general_engineering_answer"])
    if _is_point_cloud_inspection_message(user_message):
        evidence_requirements = _dedupe(evidence_requirements + ["point_cloud_inspection_boundary"])
        answer_contract = _dedupe(answer_contract + ["allow_bounded_general_engineering_answer"])
    if _is_calibration_parameter_message(user_message):
        evidence_requirements = _dedupe(evidence_requirements + ["calibration_parameter_boundary"])
        answer_contract = _dedupe(answer_contract + ["allow_bounded_general_engineering_answer"])
    if _is_material_durability_message(user_message):
        evidence_requirements = _dedupe(evidence_requirements + ["material_durability_boundary"])
        answer_contract = _dedupe(answer_contract + ["allow_bounded_general_engineering_answer"])
    if _is_product_modification_message(user_message):
        evidence_requirements = _dedupe(evidence_requirements + ["product_modification_risk_boundary"])
        answer_contract = _dedupe(answer_contract + [
            "state_modification_risk_position",
            "allow_bounded_general_engineering_answer",
        ])
    if _is_user_provided_confirmed_premise(user_message):
        evidence_requirements = _dedupe(evidence_requirements + ["user_provided_confirmed_context"])
        answer_contract = _dedupe(answer_contract + ["use_user_provided_premise_with_boundary"])
    if (
        question_type == "selection"
        and (_is_scenario_constraint_selection_message(user_message) or _is_product_fit_selection_message(user_message))
    ):
        answer_contract = _dedupe(answer_contract + ["allow_bounded_general_engineering_answer"])
    if _requires_product_anchor_for_model_specific_specs(user_message, schema):
        answer_contract = _dedupe(answer_contract + ["requires_product_anchor_for_model_specific_specs"])
    return {
        "question_type": question_type,
        "evidence_requirements": evidence_requirements,
        "answer_contract": answer_contract,
        "context_required": context_required,
        "requires_clarify": requires_clarify,
        "clarify_question": _clarify_question_for_profile(user_message, schema),
    }


def planner_rule_fallback(
    user_message: str,
    channel: Literal["fae", "ecom"] = "fae",
) -> PlanResult:
    """LLM 失败时用的纯规则版 Planner。

    primary = 规则集合里第一个非 risk 的 capability(按 _ALL_CAPABILITIES 顺序优先级)。
    如果规则只触发 risk_compliance → catch-all 到 experience(决策 #5)。
    confidence 一律 "low"(规则兜底不应自信)。
    """
    caps = anchor_rules(user_message)
    non_risk = [c for c in _ALL_CAPABILITIES if c in caps and c != "risk_compliance"]
    profile = _profile_defaults(user_message, caps)

    if profile["requires_clarify"] or (profile["context_required"] and not non_risk):
        return PlanResult(
            primary_capability=non_risk[0] if non_risk else "experience",
            extra_capabilities=[c for c in non_risk[1:] if c != non_risk[0]] + ["risk_compliance"] if non_risk else ["risk_compliance"],
            capability_queries={non_risk[0] if non_risk else "experience": user_message},
            short_circuit=False,
            confidence="low",
            alternatives=[],
            needs_clarify=True,
            clarify_questions=[profile["clarify_question"]],
            refused=False,
            refusal_text=None,
            question_type=profile["question_type"],
            evidence_requirements=profile["evidence_requirements"],
            answer_contract=profile["answer_contract"],
            context_required=True,
        )

    if not non_risk:
        # 规则啥都没触发 → catch-all 到 experience(决策 #5)
        primary: Capability = "experience"
        extras: list[Capability] = ["risk_compliance"]
    elif profile["question_type"] == "field_troubleshooting" and "troubleshoot" in caps:
        primary = "troubleshoot"
        extras = [
            c for c in ("spec", "sdk", "experience", "selection", "catalog", "risk_compliance")
            if c in caps and c != primary
        ]
    elif (
        profile["question_type"] == "integration_design"
        and _is_hardware_interface_integration_message(user_message)
        and "spec" in caps
    ):
        primary = "spec"
        extras = [
            c for c in ("sdk", "selection", "catalog", "experience", "troubleshoot", "risk_compliance")
            if c in caps and c != primary
        ]
    elif profile["question_type"] == "integration_design" and "sdk" in caps:
        primary = "sdk"
        extras = [
            c for c in ("spec", "selection", "catalog", "experience", "troubleshoot", "risk_compliance")
            if c in caps and c != primary
        ]
    elif profile["question_type"] == "sdk_howto" and "sdk" in caps:
        primary = "sdk"
        extras = [
            c for c in ("spec", "selection", "catalog", "experience", "troubleshoot", "risk_compliance")
            if c in caps and c != primary
        ]
    elif profile["question_type"] == "selection" and "selection" in caps:
        primary = "selection"
        extras = [
            c for c in ("catalog", "spec", "sdk", "experience", "troubleshoot", "risk_compliance")
            if c in caps and c != primary
        ]
    else:
        primary = non_risk[0]
        extras = [c for c in non_risk[1:]] + ["risk_compliance"]

    # 规则版不出 needs_clarify 也不出 refused — 那是 LLM 才能判
    return PlanResult(
        primary_capability=primary,
        extra_capabilities=extras,
        capability_queries={primary: user_message},  # 最简:原问当 query
        short_circuit=False,  # 规则版不短路
        confidence="low",
        alternatives=[],
        needs_clarify=False,
        clarify_questions=[],
        refused=False,
        refusal_text=None,
        question_type=profile["question_type"],
        evidence_requirements=profile["evidence_requirements"],
        answer_contract=profile["answer_contract"],
        context_required=profile["context_required"],
    )


# === LLM 层 (A4) ===

_PLANNER_SYSTEM = (
    "你是 Orbbec FAE Agent 的 Planner。"
    "严格按要求输出 JSON,不要解释,不要输出任何 JSON 以外的内容。"
)


def plan(
    input: PlannerInput,
    *,
    llm_provider: str,
    llm_api_key: str,
    llm_base_url: str | None,
    model_fast: str,
    max_tokens: int = 800,
    temperature: float = 0.0,
) -> PlanResult:
    """Planner 主入口:LLM (GLM fast) + 规则兜底融合。

    决策 #5:LLM crash / empty + 规则也空 → catch-all 到 experience。
    """
    from src.agent.llm_client import complete_json, LLMJsonError

    # 1. 跑规则 anchor
    rule_caps = anchor_rules(input.user_message)
    rule_caps.update(_schema_anchor_caps(input.history_schema))

    # 2. 跑 LLM
    prompt_user = _build_prompt(input)
    try:
        raw = complete_json(
            provider=llm_provider,
            api_key=llm_api_key,
            base_url=llm_base_url or "",
            model=model_fast,
            system=_PLANNER_SYSTEM,
            user=prompt_user,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        plan_result = _parse_llm_output(raw)
    except (LLMJsonError, ValueError, KeyError, Exception) as exc:
        # LLM crash / JSON 非法 → rule fallback
        _trace_planner_fallback(reason=str(exc), used_llm=True)
        return planner_rule_fallback(input.user_message, channel=input.channel)

    # 3. 规则 vs LLM 矛盾裁决:
    #    a) LLM 漏 capability 但规则触发 → 强制加入 extras(over-include)
    #    b) LLM refused=true 但规则有 non-risk capability 命中 → 以规则为准
    #    c) needs_clarify+refused 都 true → 以 refused 为准(已在 _parse_llm_output 处理)
    plan_result = _reconcile_with_rules(plan_result, rule_caps)
    plan_result = _ensure_profile(
        plan_result,
        input.user_message,
        rule_caps,
        schema=input.history_schema,
    )

    # 4. trace
    _trace_planner_success(plan_result)

    return plan_result


def _build_prompt(input: PlannerInput) -> str:
    """读 prompts/planner.md 模板,填入用户消息 / channel / 历史。"""
    template_path = Path(__file__).parent.parent.parent / "prompts" / "planner.md"
    template = template_path.read_text(encoding="utf-8")

    recent_block = "(无历史)"
    if input.recent_messages:
        lines = []
        for m in input.recent_messages[-3:]:  # 最近 3 轮
            role = m.get("role", "?")
            content = m.get("content", "")
            lines.append(f"- {role}: {content}")
        recent_block = "\n".join(lines)

    return template.format(
        USER_MESSAGE=input.user_message,
        CHANNEL=input.channel,
        RECENT_MESSAGES_BLOCK=recent_block,
    )


def _parse_llm_output(raw) -> PlanResult:
    """LLM JSON → PlanResult。字段缺时给默认值。"""
    raw = _coerce_plan_payload(raw)
    primary = raw.get("primary_capability") or "experience"  # 缺 primary → catch-all
    # 确保 primary 是合法 capability
    if primary not in _ALL_CAPABILITIES:
        primary = "experience"

    extras_raw = raw.get("extra_capabilities") or []
    # 去重 + 确保是合法 capability
    extras: list[Capability] = [
        c for c in extras_raw if c in _ALL_CAPABILITIES and c != primary
    ]
    if "risk_compliance" not in extras and primary != "risk_compliance":
        extras.append("risk_compliance")  # 横切默认

    needs_clarify = bool(raw.get("needs_clarify", False))
    refused = bool(raw.get("refused", False))
    # 互斥 — LLM 出错时硬裁决:refused 胜
    if needs_clarify and refused:
        needs_clarify = False

    clarify_qs = list(raw.get("clarify_questions") or [])
    if len(clarify_qs) > 2:
        clarify_qs = clarify_qs[:2]  # 截断

    alternatives_raw = raw.get("alternatives") or []
    alternatives: list[Capability] = [
        c for c in alternatives_raw if c in _ALL_CAPABILITIES
    ]

    question_type = raw.get("question_type") or "unknown"
    if question_type not in QuestionType.__args__:  # type: ignore[attr-defined]
        question_type = "unknown"

    return PlanResult(
        primary_capability=primary,
        extra_capabilities=extras,
        capability_queries=dict(raw.get("capability_queries") or {}),
        short_circuit=bool(raw.get("short_circuit", False)),
        confidence=raw.get("confidence") or "low",
        alternatives=alternatives,
        needs_clarify=needs_clarify,
        clarify_questions=clarify_qs,
        refused=refused,
        refusal_text=raw.get("refusal_text"),
        question_type=question_type,
        evidence_requirements=_string_list(raw.get("evidence_requirements")),
        answer_contract=_string_list(raw.get("answer_contract")),
        context_required=bool(raw.get("context_required", False)),
    )


def _coerce_plan_payload(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                return item
    raise ValueError(f"planner payload must be object, got {type(raw).__name__}")


def _string_list(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _ensure_profile(
    plan: PlanResult,
    user_message: str,
    rule_caps: set[Capability],
    *,
    schema: RequestSchema | None = None,
) -> PlanResult:
    defaults = _profile_defaults(user_message, rule_caps, schema=schema)
    non_risk_rule = rule_caps - {"risk_compliance"}
    question_type = plan.question_type
    if defaults["question_type"] in {"field_troubleshooting", "integration_design", "selection"}:
        question_type = defaults["question_type"]
    elif defaults["question_type"] == "sdk_howto" and "sdk" in rule_caps:
        question_type = defaults["question_type"]
    elif question_type == "unknown":
        question_type = defaults["question_type"]
    evidence_requirements = _dedupe(plan.evidence_requirements + defaults["evidence_requirements"])
    answer_contract = _dedupe(plan.answer_contract + defaults["answer_contract"])
    context_required = plan.context_required or defaults["context_required"]
    needs_clarify = plan.needs_clarify
    clarify_questions = list(plan.clarify_questions)
    current_turn_has_standalone_anchor = (
        _has_product_subject_anchor(schema, user_message)
        or _has_standalone_domain_context(user_message)
    )
    if (
        current_turn_has_standalone_anchor
        and not defaults["requires_clarify"]
        and non_risk_rule
    ):
        context_required = False
        needs_clarify = False
        clarify_questions = []
    if context_required and _schema_has_subject_anchor(schema) and not defaults["requires_clarify"]:
        context_required = False
    if needs_clarify and _schema_has_subject_anchor(schema) and non_risk_rule:
        needs_clarify = False
        clarify_questions = []
    if defaults["requires_clarify"] and not plan.refused:
        needs_clarify = True
        clarify_questions = [defaults["clarify_question"]]
    elif context_required and not non_risk_rule and not plan.refused:
        needs_clarify = True
        if not clarify_questions:
            clarify_questions = [defaults["clarify_question"]]
    primary = plan.primary_capability
    extras = list(plan.extra_capabilities)
    if question_type == "field_troubleshooting" and "troubleshoot" in rule_caps and primary != "troubleshoot":
        if primary != "risk_compliance":
            extras.append(primary)
        primary = "troubleshoot"
    if (
        question_type == "integration_design"
        and _is_hardware_interface_integration_message(user_message)
        and "spec" in rule_caps
        and primary != "spec"
    ):
        if primary != "risk_compliance":
            extras.append(primary)
        primary = "spec"
    elif question_type == "integration_design" and "sdk" in rule_caps and primary != "sdk":
        if primary != "risk_compliance":
            extras.append(primary)
        primary = "sdk"
    if question_type == "sdk_howto" and "sdk" in rule_caps and primary != "sdk":
        if primary != "risk_compliance":
            extras.append(primary)
        primary = "sdk"
    if question_type == "selection" and "selection" in rule_caps and primary != "selection":
        if primary != "risk_compliance":
            extras.append(primary)
        primary = "selection"
    extras = _dedupe([cap for cap in extras if cap != primary])
    if (
        question_type == plan.question_type
        and evidence_requirements == plan.evidence_requirements
        and answer_contract == plan.answer_contract
        and context_required == plan.context_required
        and primary == plan.primary_capability
        and extras == plan.extra_capabilities
        and needs_clarify == plan.needs_clarify
        and clarify_questions == plan.clarify_questions
    ):
        return plan
    return PlanResult(
        primary_capability=primary,
        extra_capabilities=extras,
        capability_queries=plan.capability_queries,
        short_circuit=plan.short_circuit,
        confidence=plan.confidence,
        alternatives=plan.alternatives,
        needs_clarify=needs_clarify,
        clarify_questions=clarify_questions,
        refused=plan.refused,
        refusal_text=plan.refusal_text,
        question_type=question_type,
        evidence_requirements=evidence_requirements,
        answer_contract=answer_contract,
        context_required=context_required,
    )


def _reconcile_with_rules(plan: PlanResult, rule_caps: set[Capability]) -> PlanResult:
    """规则裁决:over-include + refused 覆盖。"""
    non_risk_rule = rule_caps - {"risk_compliance"}

    # b) LLM refused=true 但规则有 non-risk 命中 → 规则胜
    if plan.refused and non_risk_rule:
        # 规则觉得不该拒 → 不 refused,confidence=low
        primary: Capability = next(
            (c for c in _ALL_CAPABILITIES if c in non_risk_rule), "experience"
        )
        new_extras: list[Capability] = [
            c for c in _ALL_CAPABILITIES
            if c in non_risk_rule and c != primary
        ]
        if "risk_compliance" not in new_extras:
            new_extras.append("risk_compliance")
        return PlanResult(
            primary_capability=primary,
            extra_capabilities=new_extras,
            capability_queries=plan.capability_queries,
            short_circuit=plan.short_circuit,
            confidence="low",
            alternatives=plan.alternatives,
            needs_clarify=False,
            clarify_questions=plan.clarify_questions,
            refused=False,
            refusal_text=None,
            question_type=plan.question_type,
            evidence_requirements=plan.evidence_requirements,
            answer_contract=plan.answer_contract,
            context_required=plan.context_required,
        )

    # a) over-include:规则触发但 LLM 漏的 → 加入 extras
    if plan.refused:
        return plan  # 越界保持(non_risk_rule 为空时走这里)

    missing: set[Capability] = non_risk_rule - {plan.primary_capability} - set(plan.extra_capabilities)
    if missing:
        new_extras_list = list(plan.extra_capabilities) + sorted(missing)
        return PlanResult(
            primary_capability=plan.primary_capability,
            extra_capabilities=new_extras_list,
            capability_queries=plan.capability_queries,
            short_circuit=plan.short_circuit,
            confidence=plan.confidence,
            alternatives=plan.alternatives,
            needs_clarify=plan.needs_clarify,
            clarify_questions=plan.clarify_questions,
            refused=plan.refused,
            refusal_text=plan.refusal_text,
            question_type=plan.question_type,
            evidence_requirements=plan.evidence_requirements,
            answer_contract=plan.answer_contract,
            context_required=plan.context_required,
        )

    return plan


def _schema_has_subject_anchor(schema: RequestSchema | None) -> bool:
    if schema is None:
        return False
    return bool(getattr(schema, "products", None) or getattr(schema, "scenario", None))


def _schema_anchor_caps(schema: RequestSchema | None) -> set[Capability]:
    """Use schema extraction as an input to planning, not only raw text rules."""
    caps: set[Capability] = set()
    if schema is None:
        return caps
    if getattr(schema, "products", None):
        caps.add("spec")
    text_parts: list[str] = []
    for field_name in ("products", "scenario", "constraints", "platforms", "technical_components"):
        text_parts.extend(str(item) for item in (getattr(schema, field_name, None) or []))
    if text_parts:
        caps.update(anchor_rules(" ".join(text_parts)))
    return caps


def _trace_planner_fallback(reason: str, used_llm: bool) -> None:
    """trace 标 fallback_used + reason。trace 失败不影响 planner。"""
    try:
        from src.agent.tracing import current_trace_ctx
        ctx = current_trace_ctx()
        if ctx is None:
            return
        span = ctx.current_span()
        if span is not None:
            span.set_metadata(used_llm=used_llm, fallback_used=True, fallback_reason=reason)
    except Exception:
        pass  # trace 失败不能影响 planner


def _trace_planner_success(plan: PlanResult) -> None:
    """trace 标 success metadata。trace 失败不影响 planner。"""
    try:
        from src.agent.tracing import current_trace_ctx
        ctx = current_trace_ctx()
        if ctx is None:
            return
        span = ctx.current_span()
        if span is not None:
            n_caps = 1 + len(plan.extra_capabilities)
            span.set_metadata(
                used_llm=True,
                fallback_used=False,
                capabilities_count=n_caps,
                primary=plan.primary_capability,
                refused=plan.refused,
                needs_clarify=plan.needs_clarify,
                question_type=plan.question_type,
                evidence_requirements=plan.evidence_requirements,
                answer_contract=plan.answer_contract,
                context_required=plan.context_required,
            )
    except Exception:
        pass
