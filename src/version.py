"""Application version metadata."""

from __future__ import annotations

APP_NAME = "DiceFrame"
__version__ = "2.0.2-beta.1"
DEFAULT_UPDATE_REPOSITORY = "diceframe/diceframe"


def version_below(minimum: str, current: str) -> bool:
    """语义化比较：current 是否低于 minimum（如 "1.9.12" 低于 "1.9.13"）。

    仅用于展示级提示（插件最低版本），不构成任何加载/安装门控。
    beta/rc 后缀按主版本号比较：current "1.9.12-beta.1" 视为满足 minimum "1.9.12"。
    """
    def parts(value: str) -> tuple[int, ...]:
        out = []
        for chunk in str(value).strip().lstrip("vV").split("."):
            try:
                out.append(int(chunk.split("-")[0] or 0))
            except ValueError:
                out.append(0)
        return tuple(out)

    return parts(minimum) > parts(current)


def needs_core_update(min_app_version: str) -> bool:
    """展示级判断：插件声明的 min_app_version 是否高于当前核心版本。

    集中定义，供 PluginHost.public_detail 与市场索引共用；只做提示，不阻断加载/安装。
    """
    minimum = str(min_app_version or "").strip()
    return bool(minimum) and version_below(minimum, __version__)
