# -*- coding: utf-8 -*-
"""平台字数口径与安全截断。

平台（小红书/公众号）按 **UTF-16 码元** 计数，并额外多算组合 emoji：
- 汉字 = 1，英文/数字 = 1
- 😀 这类扩展区 emoji = 2（UTF-16 代理对）
- 1️⃣ 这类组合序号 = 3 个码元，平台再多算 2（实测口径）

所以 `len(text)` 会低估，肉眼字数也会低估。所有预检统一走这里。
"""

import re

COMBO_RE = re.compile(r"[0-9]\uFE0F?\u20E3")


def platform_len(text):
    """平台口径字数：UTF-16 码元数 + 组合 emoji 额外计数。"""
    t = text or ""
    units = len(t.encode("utf-16-le")) // 2
    combos = len(COMBO_RE.findall(t))
    return units + combos * 2


def clip_units(text, limit):
    """按平台口径截到 limit 以内，不切断代理对（emoji 不会被切成半个）。"""
    if limit <= 0:
        return ""
    used = 0
    for i, ch in enumerate(text or ""):
        u = len(ch.encode("utf-16-le")) // 2
        if used + u > limit:
            return (text or "")[:i]
        used += u
    return text or ""


def smart_truncate(text, limit, ellipsis="…"):
    """超限时按「整段 → 句末 → 硬截」三级回退，保留段落结构，不切半个 emoji。

    返回截断后的文本；未超限时原样返回（只 strip 首尾空白）。
    """
    t = (text or "").strip()
    if platform_len(t) <= limit:
        return t

    budget = limit - platform_len(ellipsis)
    if budget <= 0:
        return ""

    # ① 整段保留：能放下的段落全部留下，超出的不要
    kept, used = [], 0
    for para in t.split("\n"):
        add = platform_len(para) + (1 if kept else 0)   # 段间换行算 1 个字
        if used + add > budget:
            break
        kept.append(para)
        used += add
    if kept:
        return "\n".join(kept).rstrip() + ellipsis

    # ② 首段就超限：按句末标点回退，读起来不会断在半句话上。
    #    边界保留量低于预算 40% 时宁可不退（退太多等于白截），改走硬截。
    clipped = clip_units(t, budget)
    for ch in ("。", "！", "？", "；", ".", "!", "?", ";"):
        pos = clipped.rfind(ch)
        if pos >= budget * 0.4:
            return clipped[:pos + 1] + ellipsis

    # ③ 实在没有句末标点：硬截（clip_units 已保证不切半个 emoji）
    return clipped + ellipsis
