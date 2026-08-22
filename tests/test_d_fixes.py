"""D 批次修复验证测试：B2 难度叠加、D9 命中检定、B1 存档原子性。"""
import asyncio

from src.engine.combat import resolve_attack
from src.engine.game_instance import GameInstance, GameRegistry


def _attack_check(verdict: str, roll: int) -> dict:
    return {
        "check_id": f"attack-{roll}",
        "actor_uid": "fighter",
        "kind": "attack",
        "dice": "d20",
        "roll": roll,
        "total": roll,
        "verdict": verdict,
        "is_critical": verdict == "大成功",
        "is_fumble": verdict == "大失败",
    }


def test_difficulty_no_compound():
    """E3: 硬核难度多次受击不会让 HP 滚雪球增加（B2 修复）。"""
    target = {"character_name": "哥布林", "hp": 100, "max_hp": 100, "armor": 0}
    for _ in range(3):
        resolve_attack("战士", target, {"name": "剑", "damage": 5},
                       attr_value=14, combat_model="hp_based", difficulty="硬核",
                       check_result=_attack_check("成功", 10))
    assert target["hp"] < 100  # 受击后 HP 应单调递减，不滚雪球


def test_lethal_narrative_can_miss():
    """lethal_narrative 直接消费失败 CheckResult，dmg=0。"""
    target = {"character_name": "教徒", "hp": 20, "max_hp": 20, "armor": 0}
    result = resolve_attack("调查员", target, {"name": "手枪", "damage": 8},
                            attr_value=14, combat_model="lethal_narrative",
                            check_result=_attack_check("失败", 90))
    assert result.damage == 0
    assert result.target_hp_after == 20


def test_lethal_narrative_hit():
    """lethal_narrative 直接消费成功 CheckResult，有伤害。"""
    target = {"character_name": "教徒", "hp": 20, "max_hp": 20, "armor": 0}
    result = resolve_attack("调查员", target, {"name": "手枪", "damage": 8},
                            attr_value=14, combat_model="lethal_narrative",
                            check_result=_attack_check("普通成功", 10))
    assert result.damage > 0
    assert result.target_hp_after < 20


def test_save_load_roundtrip(tmp_path):
    """E1: save 后 load 能恢复状态（验证原子写不损坏）。"""
    reg = GameRegistry(tmp_path)
    inst = GameInstance(game_key=("test", "g1", "bot"))
    inst.world_id = "test_world"
    inst.world_name = "测试世界"
    inst.scene = "酒馆"
    inst.round_number = 5
    asyncio.run(reg.save(inst))
    loaded = asyncio.run(reg.load(("test", "g1", "bot")))
    assert loaded is not None
    assert loaded.scene == "酒馆"
    assert loaded.round_number == 5
    assert loaded.world_name == "测试世界"


def test_save_backup_exists(tmp_path):
    """E1: save 后存在 backup 文件（原子写三步法的回退依据）。"""
    reg = GameRegistry(tmp_path)
    inst = GameInstance(game_key=("test", "g2", "bot"))
    inst.scene = "场景1"
    asyncio.run(reg.save(inst))
    # 第二次 save 应生成 backup
    inst.scene = "场景2"
    asyncio.run(reg.save(inst))
    backup = tmp_path / "test#g2#bot" / "state.backup.json"
    assert backup.exists()
