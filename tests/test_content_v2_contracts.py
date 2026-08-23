import pytest

from src.content import ResourceRef, apply_locale_overlay, mechanics_snapshot, resolve_locale


def test_resource_ref_round_trip_supports_namespaced_owner():
    ref = ResourceRef.parse("plugin:my-pack:item:moon_blade")
    assert ref.owner == "plugin:my-pack"
    assert str(ref) == "plugin:my-pack:item:moon_blade"


def test_locale_overlay_rejects_mechanics_fields():
    with pytest.raises(ValueError):
        apply_locale_overlay({"damage": "1d8"}, {"damage": "2d8"})


def test_locale_fallback_and_snapshot_ignore_display_fields():
    assert resolve_locale({"en": {"name": "Sword"}}, "en-US", "zh-CN")["name"] == "Sword"
    assert mechanics_snapshot({"damage": "1d8", "name": "A"}) == mechanics_snapshot({"damage": "1d8", "name": "B"})
