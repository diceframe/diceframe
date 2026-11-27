"""A feat may grant an active capability, and combat must be able to see it.

Feats were passive-only: `FeatDefinition` had parameters, passive effects and a
choice, but not `capabilities`, which is where every subclass active ability
lives.  Nothing in the projection needed the owner to be a class feature -- it
reads `id`, `capabilities` and `parameters` -- so the fix is a structural
protocol rather than a second projection.

Two things this file exists to keep true:

* the grantor of a capability is the declaration it is written in, never a
  field in the payload;
* a feat id never stands in for a class feature id.  They share one string
  space today, and mixing them into `owned_ids` would let a capability's
  `requires_feature_id` be satisfied by a feat that grants nothing.
"""

from __future__ import annotations

from copy import deepcopy

import pytest

from src.rulesets.dnd2024.combat.view import CombatViewMixin
from src.rulesets.dnd2024.features.combat import CombatContext, combat_capabilities
from src.rulesets.dnd2024.features.feat_models import FeatDefinitionError, parse_feat
from src.rulesets.dnd2024.features.models import Dnd2024ClassFeatureCatalog
from src.rulesets.dnd2024.runtime import Dnd2024Runtime

LOCALE = "en"


def _feat(**overrides):
    base = {
        "schema_version": 1,
        "kind": "feat",
        "id": "probe_feat",
        "category": "origin",
        "source_ref": "srd-5.2.1:p87:probe",
        "automation_level": "guided",
        "content_pack": "srd-5.2.1",
    }
    base.update(overrides)
    return base


def _capability(**overrides):
    base = {
        "id": "probe_step",
        "cost": {"bonus_action": 1},
        "actions": [{"kind": "dash"}],
    }
    base.update(overrides)
    return base


@pytest.fixture(scope="module")
def bundle():
    return Dnd2024Runtime().load_bundle(LOCALE)


# ---------------------------------------------------------------------------
# 1. 模型：能力挂在专长上，授予者是专长自己
# ---------------------------------------------------------------------------


def test_a_feat_can_declare_an_active_capability() -> None:
    definition = parse_feat(_feat(capabilities=[_capability()]))
    assert [capability.id for capability in definition.capabilities] == ["probe_step"]
    assert definition.capabilities[0].feature_id == "probe_feat"


def test_the_payload_may_not_claim_a_grantor() -> None:
    # 与子职侧不同：子职同样忽略载荷里的 feature_id，但那是因为没人写过。
    # 专长侧显式拒绝——归属不能由没人校验的字段决定。
    with pytest.raises(FeatDefinitionError) as captured:
        parse_feat(_feat(capabilities=[_capability(feature_id="someone_else")]))
    assert "feature_id" in str(captured.value)


def test_an_unknown_action_kind_fails_at_load() -> None:
    with pytest.raises(FeatDefinitionError):
        parse_feat(_feat(capabilities=[_capability(actions=[{"kind": "teleport"}])]))


def test_a_feat_without_capabilities_carries_an_empty_tuple() -> None:
    assert parse_feat(_feat()).capabilities == ()


# ---------------------------------------------------------------------------
# 2. 投影：输入是结构协议，专长与子职同场
# ---------------------------------------------------------------------------


def _views(bundle, *owners, labels=None, feature_owner_ids=None):
    catalog = Dnd2024ClassFeatureCatalog.from_bundle(bundle)
    return combat_capabilities(
        catalog,
        owners,
        CombatContext.from_economy({}, ""),
        {},
        include_unavailable=True,
        extra_labels=labels,
        feature_owner_ids=feature_owner_ids,
    )


def test_the_projection_accepts_a_feat_as_an_owner(bundle) -> None:
    definition = parse_feat(_feat(capabilities=[_capability()]))
    views = _views(bundle, definition)
    assert [view.id for view in views] == ["probe_step"]
    assert views[0].feature_id == "probe_feat"


def test_two_grantors_with_the_same_capability_id_each_get_a_view(bundle) -> None:
    one = parse_feat(_feat(id="probe_one", capabilities=[_capability()]))
    two = parse_feat(_feat(id="probe_two", capabilities=[_capability()]))
    views = _views(bundle, one, two)
    # 投影不去重：去重会让其中一个授予者的定价永远读不到。
    assert sorted(view.feature_id for view in views) == ["probe_one", "probe_two"]


def test_labels_come_from_the_declaration_not_the_bundle(bundle) -> None:
    # 两个布局（每实体一个文件 / catalog 里的一行）必须都能给出 label，而
    # ``bundle.get("feat", ...)`` 只看得到前者。
    from src.rulesets.dnd2024.features.feat_capabilities import capability_labels

    definition = parse_feat(
        _feat(capabilities=[_capability()], labels={"probe_step": "Probe Step"}),
    )
    assert capability_labels([definition]) == {"probe_step": "Probe Step"}
    assert _views(
        bundle, definition, labels=capability_labels([definition]),
    )[0].name == "Probe Step"


def test_without_a_label_the_capability_id_is_shown(bundle) -> None:
    definition = parse_feat(_feat(capabilities=[_capability()]))
    assert _views(bundle, definition)[0].name == "probe_step"


def test_a_feat_id_does_not_satisfy_a_feature_prerequisite(bundle) -> None:
    # ``requires_feature_id`` 问的是"有没有那条特性"。专长 id 与特性 id 共用一个
    # 字符串空间，混进同一个集合就会互相顶替。
    definition = parse_feat(
        _feat(capabilities=[_capability(requires_feature_id="probe_feat")]),
    )
    blocked = _views(bundle, definition, feature_owner_ids=frozenset())
    assert "requires" in (blocked[0].blocked_reason or "")

    # 显式把该 id 当成特性放进去才解除；resolver 传的是 active 特性的 id，
    # 所以专长永远不会走这条路。
    allowed = _views(bundle, definition, feature_owner_ids=frozenset({"probe_feat"}))
    assert "requires" not in (allowed[0].blocked_reason or "")


# ---------------------------------------------------------------------------
# 3. resolver：只把真正授予能力的专长带进 owned
# ---------------------------------------------------------------------------


def _resolver_with(bundle, *feats: dict):
    from src.rulesets.dnd2024.features import Dnd2024ClassFeatureResolver

    class _BundleView:
        """A bundle whose feat entities are replaced, everything else forwarded."""

        def get(self, kind, ident=None):
            return bundle.get(kind, ident)

        def list(self, kind):
            if kind == "feat":
                return list(feats)
            return bundle.list(kind)

        def __getattr__(self, name):
            return getattr(bundle, name)

    return Dnd2024ClassFeatureResolver(_BundleView())


def test_only_feats_that_grant_capabilities_enter_the_owner_list(bundle) -> None:
    resolver = _resolver_with(
        bundle,
        _feat(id="probe_active", capabilities=[_capability()]),
        _feat(id="probe_passive"),
    )
    owners = resolver._owned_feat_capability_owners({
        "features": {"feat_refs": ["feat:probe_active", "feat:probe_passive"]},
    })
    assert [owner.id for owner in owners] == ["probe_active"]


def test_a_feat_the_character_does_not_own_contributes_nothing(bundle) -> None:
    resolver = _resolver_with(
        bundle, _feat(id="probe_active", capabilities=[_capability()]),
    )
    assert resolver._owned_feat_capability_owners({"features": {"feat_refs": []}}) == ()
    assert resolver.declared_capability(
        {"features": {"feat_refs": []}}, "probe_step",
    ) is None


def test_declared_capability_finds_a_feat_granted_capability(bundle) -> None:
    resolver = _resolver_with(
        bundle, _feat(id="probe_active", capabilities=[_capability()]),
    )
    found = resolver.declared_capability(
        {"features": {"feat_refs": ["feat:probe_active"]}}, "probe_step",
    )
    assert found is not None and found.feature_id == "probe_active"


# ---------------------------------------------------------------------------
# 4. 加载期：能力 id 全局唯一，否则"按 id 申报"没有唯一答案
# ---------------------------------------------------------------------------


def test_two_grantors_sharing_a_capability_id_fail_at_load(bundle) -> None:
    from src.rulesets.dnd2024.features.models import ClassFeatureError

    # patient_defense 已由武僧声明；申报端只按 id 找，重复即无唯一答案。
    # 校验发生在 resolver 构造期，所以构造本身就要在 with 里。
    with pytest.raises(ClassFeatureError) as captured:
        _resolver_with(
            bundle,
            _feat(id="probe_clash", capabilities=[_capability(id="patient_defense")]),
        )
    assert "patient_defense" in str(captured.value)


# ---------------------------------------------------------------------------
# 5. 产品路径：战斗视图必须把角色拥有的专长带到特性边界
# ---------------------------------------------------------------------------


def test_the_actor_view_carries_the_feat_refs() -> None:
    # 没有这一步，专长的能力在战斗里永远"不可用"——投影根本不知道角色有专长。
    character = CombatViewMixin._feature_character({
        "features": {"feat_refs": ["feat:alert"], "subclass_ref": ""},
    })
    assert character["features"]["feat_refs"] == ["feat:alert"]
