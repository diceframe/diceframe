"""检定渠道策略：同一情境事实只能进入一条渠道。

模型可以同时输出情境 DC、``advantage``/``disadvantage`` 与环境 ``modifier``。
若三者表达的是同一个情境事实，服务端全量执行就会造成“同一不利事实被重复
计入”的三重惩罚。本模块把规则边界表达为三条互不重叠的渠道：

- ``target``（DC）：任务本身的客观难度。
- ``advantage_mode``：行动者相对情境造成的掷骰方式变化。
- ``modifier``：独立、明确、可解释的临时数值修正（默认 0）。

同一事实只能进入一条渠道；不同且明确的事实可以合法叠加（例如任务本身困难
DC 15 + 独立光照惩罚 -2）。本模块是纯函数策略层：不依赖规则集、不依赖
GameInstance、不做掷骰，也不改动服务端自己计算的属性/技能加值。每次修正都会
追加稳定的机器可读 note，并把被丢弃的原值记入 ``dropped`` 供 UI 与日志审计。

缺依据的处理边界（不越权改写规则机制）：

- 非零 ``modifier`` 没有 ``modifier_reason`` 时无法证明它不是重复计入，按 0
  处理（``modifier_without_reason``）。
- ``advantage``/``disadvantage`` 没有 ``advantage_reason`` **只追加审计提示**
  ``advantage_without_reason``，绝不改写掷骰方式：项目方案只要求非零
  ``modifier`` 必须给出理由，缺少依据不足以证明该优势/劣势与其它渠道重复，
  静默降级会误伤模型已声明的合法优势。没有理由的掷骰方式变化也不参与同源比较。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_ADVANTAGE_MODES = {"advantage", "disadvantage"}
# 短于该长度的“理由”无法证明两个渠道说的是同一件事，不参与同源折叠。
_MIN_REASON_LENGTH = 4
# 明细 DC/劣势与同类修正同时出现时的“明显重复”阈值（按设计固定为 ±5）。
_STACKING_THRESHOLD = 5
# 理由归一：去空白与标点后比较（CJK 字符在 Unicode \w 内，会被保留）。
_PUNCTUATION = re.compile(r"[\s\W_]+", re.UNICODE)


@dataclass(frozen=True)
class CheckChannels:
    """渠道归一后的最终取值与审计信息。

    ``notes`` 既包含真正改写数值/渠道的修正，也包含 ``advantage_without_reason``
    这类“仅审计、不改写”的提示；``dropped`` 记录被丢弃的原值（不是替换后的
    值），最终采用的取值即在本 dataclass 的 ``target`` / ``modifier`` /
    ``advantage_mode`` 字段上。
    """

    target: int | None
    modifier: int
    advantage_mode: str
    dc_reason: str
    advantage_reason: str
    modifier_reason: str
    notes: tuple[str, ...]
    dropped: dict[str, int | str]


def _as_int(value: object, default: int) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float, str)):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
    return default


def _normalize_reason(value: object) -> str:
    """理由归一：strip + casefold + 去空白/标点，用于判断“同一事实”。

    归一后短于 ``_MIN_REASON_LENGTH``（含空理由，例如没有 ``advantage_reason``
    的掷骰方式变化）时调用方会跳过该渠道：理由太短或缺失都无法证明两个渠道
    说的是同一件事，因此它既不参与同源折叠，也不被折叠掉。
    """
    text = str(value or "").strip().casefold()
    if not text:
        return ""
    return _PUNCTUATION.sub("", text)


def _clamped_baseline(baseline_dc: int | None, dc_cap: int) -> int | None:
    if baseline_dc is None:
        return None
    cap = max(1, _as_int(dc_cap, 1))
    return max(1, min(cap, _as_int(baseline_dc, cap)))


def normalize_check_channels(
    *,
    target: int | None,
    modifier: int,
    advantage_mode: str,
    baseline_dc: int | None,
    dc_cap: int,
    supports_advantage: bool,
    dc_reason: str = "",
    advantage_reason: str = "",
    modifier_reason: str = "",
) -> CheckChannels:
    """消解重复惩罚，返回最终渠道取值与审计信息。

    d100 等不使用模型 DC 的骰制应传 ``target=None``：此时 DC 渠道不参与
    同源比较，``baseline_dc`` 也不参与。``target`` 的 1..``dc_cap`` 钳制仍由
    调用方（``normalize_check_specs``）负责，本函数只把被折叠回基线的 DC
    钳制到 ``[1, dc_cap]``。
    """
    notes: list[str] = []
    dropped: dict[str, int | str] = {}
    dc_reason = str(dc_reason or "").strip()
    advantage_reason = str(advantage_reason or "").strip()
    modifier_reason = str(modifier_reason or "").strip()

    # (a) 规则不支持或取值非法 → 归一为普通检定（沿用 planner 既有行为，不记 note）。
    mode = str(advantage_mode or "")
    if mode not in _ADVANTAGE_MODES or not supports_advantage:
        mode = ""

    # (b) 非零环境修正必须有独立、可解释的理由。
    if modifier and not modifier_reason:
        dropped["modifier"] = modifier
        modifier = 0
        notes.append("modifier_without_reason")

    # (c) 掷骰方式变化缺少依据：只追加审计提示，绝不降级。
    #     项目方案只要求非零 modifier 必须给出理由；advantage/disadvantage 是模型
    #     已作出的合法判定，缺少 advantage_reason 不足以证明它与其它渠道重复，
    #     静默改写为 normal 会越权改写规则机制并误伤合法优势。
    if mode and not advantage_reason:
        notes.append("advantage_without_reason")

    # (d) 同源折叠：同一归一化理由只保留优先级最高的一条渠道。
    #     优先级固定为 advantage/disadvantage > DC > modifier。没有理由（或理由
    #     过短）的渠道不参与比较，也不会因为这条规则被改写。
    channels: list[tuple[str, str]] = []
    if mode:
        channels.append(("advantage", _normalize_reason(advantage_reason)))
    if target is not None:
        channels.append(("dc", _normalize_reason(dc_reason)))
    if modifier:
        channels.append(("modifier", _normalize_reason(modifier_reason)))
    owner_by_reason: dict[str, str] = {}
    for channel, reason in channels:
        if len(reason) < _MIN_REASON_LENGTH:
            continue
        owner = owner_by_reason.get(reason)
        if owner is None:
            owner_by_reason[reason] = channel
            continue
        notes.append(
            "same_fact_as_advantage" if owner == "advantage" else "same_fact_as_dc"
        )
        if channel == "dc":
            if target is not None:
                dropped["target"] = target
            target = _clamped_baseline(baseline_dc, dc_cap)
        else:
            dropped["modifier"] = modifier
            modifier = 0

    # (e) 明显重复惩罚/奖励兜底（不依赖理由文本）：同一情境既抬 DC 又给大额同类修正。
    clamped_baseline = _clamped_baseline(baseline_dc, dc_cap)
    if target is not None and clamped_baseline is not None:
        if (
            mode == "disadvantage"
            and target > clamped_baseline
            and modifier <= -_STACKING_THRESHOLD
        ):
            dropped["modifier"] = modifier
            modifier = 0
            notes.append("stacked_penalty")
        elif (
            mode == "advantage"
            and target < clamped_baseline
            and modifier >= _STACKING_THRESHOLD
        ):
            dropped["modifier"] = modifier
            modifier = 0
            notes.append("stacked_bonus")

    # (f) 属性/技能加值由服务端从角色卡计算，本函数从不触碰。
    return CheckChannels(
        target=target,
        modifier=modifier,
        advantage_mode=mode,
        dc_reason=dc_reason,
        advantage_reason=advantage_reason,
        modifier_reason=modifier_reason,
        notes=tuple(notes),
        dropped=dropped,
    )
