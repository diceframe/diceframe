"""Size ratchet for active-logic source files that are already hotspots.

ENGINEERING_RULES §21.5.1 treats more than 1500 lines in an active-logic file
as a review signal, not as a failure, and files inside the ordinary range are
not tracked here at all.  This baseline only keeps the known hotspots from
getting worse while they are being split.

Each entry records the file's current line count.  A hotspot may drift by at
most 100 lines around it: growing further has to be paid for with an
extraction, and a split that removes more has to lower the entry in the same
change.  That is how the baseline ratchets down.
"""

from __future__ import annotations

#: One entry per hotspot, ``path -> line count``.
SIZE_BASELINE: dict[str, int] = {
    "frontend-v2/src/features/admin/SettingsView.vue": 2308,
    "frontend-v2/src/features/creator/CreatorAssistantPanel.vue": 1635,
    "frontend-v2/src/features/lorebook/LorebookView.vue": 1778,
    "frontend-v2/src/features/play/PlayView.vue": 2124,
    "frontend-v2/src/features/rulesets/dnd2024/combat/Dnd2024CombatPanel.vue": 1772,
    "src/commands/round_processor.py": 1917,
    "src/engine/economy.py": 1639,
    "src/plugin_host/host.py": 2299,
    "src/rulesets/dnd2024/combat/check_windows.py": 1854,
    "src/rulesets/dnd2024/combat/engine.py": 3267,
    "src/rulesets/dnd2024/combat/resolution.py": 3744,
    "src/rulesets/dnd2024/combat/validation.py": 3285,
    "src/rulesets/dnd2024/features/resolver.py": 1871,
    "src/rulesets/dnd2024/progression/advancement.py": 2127,
    "src/rulesets/dnd2024/resting/engine.py": 1718,
    "src/rulesets/dnd2024/runtime.py": 1997,
    "src/webui/api.py": 4079,
    "src/webui/services/characters.py": 1961,
}
