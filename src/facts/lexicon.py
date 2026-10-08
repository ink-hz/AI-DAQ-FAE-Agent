"""跨语言/跨表述 token 等价词表——单一定义,矩阵消费方共用。

消费方:param_match(参数块反查)、selection_filter(硬约束过滤)。
新增等价组在这里登记,禁止在消费方内各写一份(协议显式化原则)。
"""
from __future__ import annotations

# 接口类:约束/参数块里出现组内任一说法,矩阵行文本命中组内任一说法即匹配
INTERFACE_GROUPS: dict[str, tuple[str, ...]] = {
    "以太网": ("ethernet", "以太网", "rj45", "网口"),
    "ethernet": ("ethernet", "以太网", "rj45", "网口"),
    "网口": ("ethernet", "以太网", "rj45", "网口"),
    "rj45": ("ethernet", "以太网", "rj45", "网口"),
    "poe": ("poe",),
    "gmsl": ("gmsl",),
    "m12": ("m12",),
    "type-c": ("type-c", "type‑c", "typec"),
    "usb": ("usb",),
}

# 快门类
SHUTTER_GROUPS: tuple[tuple[str, ...], ...] = (
    ("global shutter", "全局快门"),
    ("rolling shutter", "卷帘快门", "卷帘"),
)

# 参数块反查用:等价组 -> 矩阵字段
SPEC_TOKEN_FIELDS: tuple[tuple[tuple[str, ...], str], ...] = (
    *((group, "shutter_type") for group in SHUTTER_GROUPS),
    (("ethernet", "以太网", "网口", "rj45"), "data_interface"),
    (("gmsl",), "data_interface"),
    (("m12",), "data_interface"),
    (("type-c", "type‑c", "typec"), "data_interface"),
    (("usb",), "data_interface"),
)
