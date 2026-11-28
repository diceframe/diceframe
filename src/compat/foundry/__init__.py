"""FVTT compatibility adapters (B3+).

Scene conversion lives in :mod:`src.compat.foundry.scene_adapter`; it is a pure
domain conversion with a read-only dependency on the frozen scene contract and
never touches the engine, webui or rulesets.
"""

from .report import ConversionIssue, ConversionLevel, ConversionReport
from .scene_adapter import (
    CODE_SCENE_MALFORMED,
    CODE_VERSION_UNSUPPORTED,
    NAMESPACE_UUID,
    NAMESPACE_V13,
    NAMESPACE_V14,
    FoundrySceneError,
    detect_foundry_version,
    export_scene,
    import_scene,
)

__all__ = [
    "CODE_SCENE_MALFORMED",
    "CODE_VERSION_UNSUPPORTED",
    "ConversionIssue",
    "ConversionLevel",
    "ConversionReport",
    "FoundrySceneError",
    "NAMESPACE_UUID",
    "NAMESPACE_V13",
    "NAMESPACE_V14",
    "detect_foundry_version",
    "export_scene",
    "import_scene",
]