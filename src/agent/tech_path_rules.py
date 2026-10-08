"""场景 → 技术路线规则。设计文档 §4.4 的 15 条编码版。

规则维护:FAE 反馈后人工调整 keywords / preferred_models。
"""


TECH_PATH_RULES: list[dict] = [
    {
        "keywords": ["户外", "强光", "反光", "户外强光"],
        "tech_path": "双目结构光/双目主动",
        "preferred_models": ["Gemini 335L", "Gemini 335", "Gemini 345Lg", "Gemini 435Le"],
        "reason": "主动双目对环境光鲁棒,户外/强光优势明显",
    },
    {
        "keywords": ["高精度近距", "Azure Kinect 替代", "近距", "1m 内高精度"],
        "tech_path": "ToF",
        "preferred_models": ["Femto Bolt", "Femto Mega"],
        "reason": "ToF 近距精度高,Femto 系列设计为 Azure Kinect 替代",
    },
    {
        "keywords": ["大型 AMR", "长距离", "户外移动机器人"],
        "tech_path": "双目长基线",
        "preferred_models": ["Gemini 435Le", "Gemini 335L"],
        "reason": "长基线对应大测距范围,以太网/PoE 适合大型 AMR",
    },
    {
        "keywords": ["平面 2D 扫描", "避障", "导航", "激光雷达"],
        "tech_path": "激光雷达",
        "preferred_models": ["MS200p", "MS500", "MS600"],
        "reason": "2D LiDAR 适合平面 SLAM 与避障",
    },
    {
        "keywords": ["人脸 3D 扫描", "人脸建模", "面部扫描"],
        "tech_path": "高精度 ToF / 单目结构光",
        "preferred_models": ["Femto Bolt", "Astra+"],
        "reason": "近距高精度场景,ToF 或单目结构光合适",
    },
    {
        "keywords": ["工业检测", "物流体积", "体积测量"],
        "tech_path": "双目 + Global Shutter RGB",
        "preferred_models": ["Gemini 335L", "Femto Mega"],
        "reason": "Global Shutter 防运动模糊,适合工业流水线",
    },
    {
        "keywords": ["3D 扫描", "3D扫描", "三维扫描", "扫描建模", "三维重建", "点云重建", "物体扫描", "旋转台"],
        "tech_path": "近距 3D 扫描/建模",
        "preferred_models": ["Femto Bolt", "Femto Mega", "Astra 2", "Gemini 2 L"],
        "reason": "扫描建模需要稳定深度/点云和近中距离覆盖,优先评估 ToF 或近距结构光/主动双目路线",
    },
    {
        "keywords": ["机械臂", "手眼", "抓取", "外接立方体", "包络", "路径规划", "喷涂"],
        "tech_path": "机械臂 RGB-D 感知",
        "preferred_models": ["Femto Mega", "Femto Bolt", "Gemini 335L", "Gemini 335"],
        "reason": "机械臂感知/抓取/喷涂路径规划通常需要 RGB-D、点云和手眼标定验证",
    },
    {
        "keywords": ["室内 AGV", "室内导航", "搬运机器人"],
        "tech_path": "单目结构光/双目",
        "preferred_models": ["Astra 2", "Gemini 2"],
        "reason": "成本敏感的室内场景",
    },
    {
        "keywords": ["透明物体", "低反", "周转箱", "黑色物体"],
        "tech_path": "双目主动(IR 投射器)",
        "preferred_models": ["Gemini 335", "Gemini 336"],
        "reason": "主动 IR 投射改善低反/无纹理物体识别",
    },
    {
        "keywords": ["GMSL", "GMSL2"],
        "tech_path": "GMSL 接口系列",
        "preferred_models": ["Gemini 345Lg", "Gemini 335Lg"],
        "reason": "GMSL 接口型号,适合车规场景",
    },
    {
        "keywords": ["PoE", "工业以太网", "网络供电"],
        "tech_path": "PoE/以太网接口",
        "preferred_models": ["Gemini 335Le", "Gemini 435Le"],
        "reason": "PoE 单线供电+数据,布线友好",
    },
    {
        "keywords": ["Body Tracking", "骨架识别", "人体姿态"],
        "tech_path": "单目结构光(Body SDK 支持)",
        "preferred_models": ["Femto Bolt", "Femto Mega"],
        "reason": "Femto 系列 SDK 支持 Body Tracking",
    },
    {
        "keywords": ["ROS 2", "ROS2 优先"],
        "tech_path": "SDK V2 兼容范围",
        "preferred_models": ["Gemini 335L", "Gemini 335", "Femto Bolt"],
        "reason": "Gemini 330 系列 SDK V2 一线支持",
    },
    {
        "keywords": ["多机同步", "多机", "3 台以上"],
        "tech_path": "硬件同步系列",
        "preferred_models": ["Gemini 335L", "Gemini 335Le"],
        "reason": "8-pin 同步接口,支持外部触发和多机硬同步",
    },
    {
        "keywords": ["Jetson", "嵌入式", "Orin Nano"],
        "tech_path": "USB 3.0 + SDK Jetson 验证",
        "preferred_models": ["Gemini 335L", "Femto Bolt"],
        "reason": "规格书明确 Jetson 验证型号",
    },
    {
        "keywords": ["IP65", "IP67", "户外防护"],
        "tech_path": "防护等级达标系列",
        "preferred_models": ["Gemini 335L", "Gemini 435Le"],
        "reason": "防护等级 IP65 及以上",
    },
]


def match_tech_paths(scenarios: list[str]) -> list[dict]:
    """对每个场景关键词去规则表里匹配,返回去重后的命中规则。"""
    seen: set[str] = set()
    out: list[dict] = []
    for s in scenarios:
        for rule in TECH_PATH_RULES:
            if any(kw in s or s in kw for kw in rule["keywords"]):
                key = rule["tech_path"]
                if key not in seen:
                    seen.add(key)
                    out.append(rule)
    return out
