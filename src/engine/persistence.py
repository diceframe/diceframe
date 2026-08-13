"""存档持久化逻辑 —— 从 GameRegistry 拆出的读写/恢复方法（P2-G Step 2）。

GameRegistry 保留薄委托（公共方法 + 被外部调用的 _save_path），调用方零改动。
纯私有方法（_chatlog_path/_append_chatlog/_truncate_chatlog/_restore_chatlog）
仅本模块内部使用，不设委托。循环 import 由 game_instance.py 文件末尾
`from src.engine import persistence` 触发（届时 GameInstance/GameRegistry 已定义）。
"""

from __future__ import annotations

import io
import json
import logging
import time
import zipfile
from pathlib import Path
from typing import Any, Callable

from src.engine import game_instance
from src.engine.game_instance import GameInstance, GameRegistry, GameState
from src.engine.health import record_health_event

logger = logging.getLogger("trpg")


def _save_path(registry: GameRegistry, game_key: tuple) -> Path:
    parts = [str(x) for x in game_key]
    if any(not part or "/" in part or "\\" in part or part in {".", ".."} for part in parts):
        raise ValueError(f"非法 game_key 存档路径: {game_key}")
    key_str = registry._KEY_SEPARATOR.join(parts)
    path = registry.save_dir / key_str / "state.json"
    base = registry.save_dir.resolve()
    parent = path.parent.resolve()
    if base != parent and base not in parent.parents:
        raise ValueError(f"非法 game_key 存档路径: {game_key}")
    return path


async def save(registry: GameRegistry, instance: GameInstance) -> None:
    """写入存档: 完整 log 追加进 chatlog.jsonl（增量），核心态写 state.json。

    锁权衡（P2-K）：本方法**不持 instance._lock** 写盘，避免 IO 期间阻塞回合
    状态修改。to_dict() 是纯内存读、耗时微秒级，半更新窗口极窄，风险可接受。
    若未来改高频写盘或发现状态撕裂，可仅对 to_dict() 段持锁（它不 await、无
    死锁），但须确认所有 save 调用点不在 _lock 内（如 games.py/round_processor）。
    """
    sp = _save_path(registry, instance.game_key)
    sp.parent.mkdir(parents=True, exist_ok=True)
    backup = sp.with_name("state.backup.json")

    _append_chatlog(registry, instance)
    data = instance.to_dict()
    tmp = sp.with_name("state.tmp.json")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    if sp.exists():
        sp.replace(backup)
    tmp.replace(sp)


def _chatlog_path(sp: Path) -> Path:
    return sp.with_name("chatlog.jsonl")


def _append_chatlog(registry: GameRegistry, instance: GameInstance) -> None:
    """把自上次保存以来的新 log 增量追加进 chatlog.jsonl。

    历史完整保存在 jsonl（逐行追加，O(1)），核心态 state.json 只留最近上下文，
    避免上万回合时每次全量重写大文件。增量通过 last_saved_log_count 跟踪。
    rollback 会使 log 缩短：此时截断 chatlog 末尾的死条目，保持文件与内存一致。
    swipe 不改 log 长度，chatlog 末尾会残留旧版本，由 _restore_chatlog 用 core_log
    对齐修正——save 时无需为 swipe 重写整个文件。
    """
    sp = _save_path(registry, instance.game_key)
    sp.parent.mkdir(parents=True, exist_ok=True)
    chatlog = _chatlog_path(sp)
    if instance.last_saved_log_count > len(instance.log):
        # log 缩短（rollback 弹出了末尾条目）：截断 chatlog 到当前长度，丢弃死条目
        _truncate_chatlog(chatlog, len(instance.log))
        instance.mark_log_persisted()
    if instance.last_saved_log_count >= len(instance.log):
        return
    new_entries = instance.log[instance.last_saved_log_count:]
    if not new_entries:
        return
    lines = "\n".join(
        json.dumps(entry, ensure_ascii=False) for entry in new_entries
    ) + "\n"
    # 追加写入（O(1)，不重写历史）
    with open(chatlog, "a", encoding="utf-8") as fh:
        fh.write(lines)
    instance.mark_log_persisted()


def _truncate_chatlog(chatlog: Path, keep: int) -> None:
    """截断 chatlog.jsonl 到前 keep 行（rollback 后清理死条目）。"""
    if not chatlog.exists():
        return
    lines = [l for l in chatlog.read_text(encoding="utf-8").splitlines() if l.strip()]
    if len(lines) <= keep:
        return
    kept = lines[:keep]
    tmp = chatlog.with_name("chatlog.tmp.jsonl")
    tmp.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
    tmp.replace(chatlog)


async def load(registry: GameRegistry, game_key: tuple) -> GameInstance | None:
    """加载存档，优先 state.json，回退到 backup。兼容旧版 , 分隔存档目录。"""
    sp = _save_path(registry, game_key)
    backup = sp.with_name("state.backup.json")

    if not sp.exists():
        for old_sep in (",", "|"):
            old_key_str = old_sep.join(str(x) for x in game_key)
            old_sp = registry.save_dir / old_key_str / "state.json"
            old_backup = registry.save_dir / old_key_str / "state.backup.json"
            if old_sp.exists() or old_backup.exists():
                sp = old_sp
                backup = old_backup
                break

    recovered_from_backup = False
    if not sp.exists():
        if not backup.exists():
            return None
        sp = backup
        recovered_from_backup = True
        logger.warning("主存档不存在，使用备份: %s", sp)

    try:
        data = json.loads(sp.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.exception("存档 JSON 损坏: %s", sp)
        if backup.exists() and sp != backup:
            data = json.loads(backup.read_text(encoding="utf-8"))
            recovered_from_backup = True
        else:
            return None

    instance = GameInstance.from_dict(data)
    if recovered_from_backup:
        record_health_event(
            instance,
            component="save",
            code="SAVE_RECOVERED_FROM_BACKUP",
            severity="warning",
            title="已从备份存档恢复",
            message="主存档缺失或损坏，系统已加载 state.backup.json。",
            impact="最近一次保存后的少量进度可能未恢复。",
            fallback="backup_state",
            repair_hint="建议检查 data/saves 目录权限、磁盘空间和 state.json 格式。",
        )
    _restore_chatlog(registry, instance, sp)
    registry.register(instance)
    logger.info("存档已加载: %s, round=%d", game_key, instance.round_number)
    return instance


def _restore_chatlog(registry: GameRegistry, instance: GameInstance, sp: Path) -> None:
    """把 chatlog.jsonl 的完整历史拼回 instance.log；老存档自动迁移。

    core_log（state.json 的 log[-100:]）是权威的最近窗口：save 时它直接来自内存
    log，一定反映 rollback/swipe 后的最新状态。chatlog 是 append-only 的完整历史，
    但 rollback 会留下死条目、swipe 会留下旧版本——二者都在 chatlog 末尾。因此以
    core_log[0] 为锚点对齐：找到它在 chatlog 中的位置，保留之前的更早历史作前缀，
    末尾用 core_log 替换（丢弃其后的死条目/旧版）。
    """
    chatlog = _chatlog_path(sp)
    core_log = list(instance.log)
    history: list = []
    if chatlog.exists():
        try:
            for line in chatlog.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    history.append(json.loads(line))
                except json.JSONDecodeError:
                    logger.warning("chatlog.jsonl 含无效行，已跳过: %s", chatlog)
        except OSError:
            logger.exception("读取 chatlog.jsonl 失败: %s", chatlog)
    if not history and core_log:
        # 老存档自动迁移：把核心态里的 log 写入 chatlog.jsonl
        _append_chatlog(registry, instance)
        history = list(core_log)
    elif core_log and history:
        # 以 core_log[0] 为锚点对齐：chatlog 中锚点之前的更早历史保留，之后用 core_log
        # 替换。这丢弃了 chatlog 末尾因 rollback 残留的死条目、因 swipe 残留的旧版本。
        first_core = core_log[0]
        anchor = -1
        for i in range(len(history) - 1, -1, -1):
            if history[i] == first_core:
                anchor = i
                break
        if anchor >= 0:
            history = history[:anchor] + core_log
        else:
            # core_log[0] 不在 chatlog（swipe 改写了窗口边界轮且被非推进 save 写入
            # state.json，chatlog 仍是原版）：保留 chatlog 中早于 core_log 窗口的更早
            # 历史（按条数取前缀），末尾用 core_log 权威覆盖，避免丢失 r1..r(N-100)。
            prefix = len(history) - len(core_log)
            history = (history[:prefix] if prefix > 0 else []) + core_log
    elif core_log:
        history = core_log
    instance.restore_log_history(history)


async def recover_all(registry: GameRegistry) -> list[GameInstance]:
    """启动时恢复未完成对局；待幸运选择保持可处理，其余对局暂停。"""
    recovered: list[GameInstance] = []
    if not registry.save_dir.exists():
        return recovered

    for entry in registry.save_dir.iterdir():
        if not entry.is_dir():
            continue
        if not (entry / "state.json").exists() and \
           not (entry / "state.backup.json").exists():
            continue
        try:
            parts = entry.name.split(registry._KEY_SEPARATOR)
            if len(parts) < 3:
                for old_sep in ("|", ","):
                    parts = entry.name.split(old_sep)
                    if len(parts) >= 3:
                        break
            game_key = tuple(parts[:3])
            instance = await load(registry, game_key)
            if instance and instance.state != GameState.ENDED:
                kept_luck_pending = (
                    instance.state == GameState.ACTIVE_JUDGMENT
                    and instance.round_checks_prepared
                    and instance.pending_luck_checks()
                )
                if not kept_luck_pending:
                    instance.state = GameState.PAUSED
                else:
                    # P2-F：重启后待幸运决定的局保持可处理，标记供前端提示
                    #（幸运超时定时器不跨重启；玩家手动决定或 GM decline 即可推进）。
                    instance.pending_luck_after_recovery = True
                recovered.append(instance)
        except Exception:
            logger.exception("恢复存档失败: %s", entry.name)

    logger.info("存档恢复完成: %d 个对局", len(recovered))
    return recovered


async def import_save_zip(
    registry: GameRegistry,
    payload: bytes,
    *,
    platform: str = "web",
    account_id: str = "web_bot",
    scene_image_importer: Callable[[bytes], dict[str, Any]] | None = None,
    map_background_importer: Callable[[bytes], dict[str, Any]] | None = None,
) -> dict:
    """导入导出的存档 zip（state.json + 可选历史/头图），作为新对局恢复。

    自动生成唯一新 game_key，不覆盖现有对局。关键点：
    - state.json 内的 game_key 改写为新值，否则 load 后 instance.game_key 仍是导出方
      原值，register 会把导入实例串到原始对局的内存槽（本机导出再导入即覆盖原对局）。
    - 写盘后立即 load+register，使新对局在 list_games 中可见，不必等重启 recover_all。
    安全校验：仅接受 state.json / chatlog.jsonl 顶层文件，防路径穿越。
    返回 game_key 为 list（由路由层用公开 | 分隔符字符串化）。
    """
    if len(payload) > game_instance.MAX_SAVE_PACKAGE_BYTES:
        return {"ok": False, "error": "存档包不能超过 50 MB"}
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as zf:
            infos = zf.infolist()
            raw_names = [info.filename for info in infos]
            names = set(raw_names)
            if len(raw_names) != len(names):
                return {"ok": False, "error": "存档包包含重复文件"}
            if "state.json" not in names:
                return {"ok": False, "error": "存档包缺少 state.json"}
            # 先信任 ZIP 中央目录做资源预检，再解压成内存，防止高压缩比存档耗尽内存。
            limits = {
                "state.json": game_instance.MAX_SAVE_STATE_BYTES,
                "chatlog.jsonl": game_instance.MAX_SAVE_CHATLOG_BYTES,
                "scene-image.asset": 8 * 1024 * 1024,
                "map-background.asset": 8 * 1024 * 1024,
            }
            unpacked_size = 0
            for info in infos:
                if info.flag_bits & 0x1:
                    return {"ok": False, "error": "存档包不支持加密文件"}
                limit = limits.get(info.filename, 0)
                if info.file_size > limit:
                    return {"ok": False, "error": f"{info.filename} 解压后过大"}
                unpacked_size += info.file_size
            if unpacked_size > game_instance.MAX_SAVE_UNPACKED_BYTES:
                return {"ok": False, "error": "存档包解压后过大"}
            state_data = zf.read("state.json")
            try:
                state_json = json.loads(state_data.decode("utf-8-sig"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return {"ok": False, "error": "state.json 不是有效 JSON"}
            if not isinstance(state_json, dict) or "game_key" not in state_json:
                return {"ok": False, "error": "state.json 缺少 game_key"}
            # 仅接受顶层已知文件，防止解压路径穿越/任意写入
            allowed = {"state.json", "chatlog.jsonl", "scene-image.asset", "map-background.asset"}
            if any(name for name in names if name not in allowed or "/" in name or "\\" in name or ".." in name):
                return {"ok": False, "error": "存档包包含非法文件"}
            chatlog_data = zf.read("chatlog.jsonl") if "chatlog.jsonl" in names else b""
            scene_image_data = zf.read("scene-image.asset") if "scene-image.asset" in names else b""
            map_background_data = zf.read("map-background.asset") if "map-background.asset" in names else b""
    except zipfile.BadZipFile:
        return {"ok": False, "error": "存档包不是有效的 zip"}
    except Exception as exc:
        logger.exception("导入存档解析失败")
        return {"ok": False, "error": f"存档包解析失败：{exc}"}

    scene_reference = state_json.get("scene_image")
    if isinstance(scene_reference, dict) and scene_reference.get("kind") == "save_asset":
        if scene_reference.get("path") != "scene-image.asset" or not scene_image_data:
            return {"ok": False, "error": "存档包缺少冒险头图资产"}
        if scene_image_importer is None:
            return {"ok": False, "error": "当前环境不支持导入冒险头图资产"}
        imported = scene_image_importer(scene_image_data)
        if not imported.get("ok") or not imported.get("scene_image"):
            return {"ok": False, "error": str(imported.get("error") or "冒险头图导入失败")}
        state_json["scene_image"] = imported["scene_image"]

    map_reference = state_json.get("map_background")
    if isinstance(map_reference, dict) and map_reference.get("kind") == "save_asset":
        if map_reference.get("path") != "map-background.asset" or not map_background_data:
            return {"ok": False, "error": "存档包缺少地图背景资产"}
        if map_background_importer is None:
            return {"ok": False, "error": "当前环境不支持导入地图背景资产"}
        imported = map_background_importer(map_background_data)
        if not imported.get("ok") or not imported.get("map_background"):
            return {"ok": False, "error": str(imported.get("error") or "地图背景导入失败")}
        state_json["map_background"] = imported["map_background"]

    # 生成唯一新 game_key：import_<毫秒时间戳>
    new_key = (platform, f"import_{int(time.time() * 1000)}", account_id)
    sp = _save_path(registry, new_key)
    sp.parent.mkdir(parents=True, exist_ok=True)
    # 改写 game_key 为新值再落盘，使存档目录、instance.game_key、公开 game_key 三者一致
    state_json["game_key"] = list(new_key)
    sp.write_text(json.dumps(state_json, ensure_ascii=False, indent=2), encoding="utf-8")
    if chatlog_data:
        sp.with_name("chatlog.jsonl").write_bytes(chatlog_data)
    # 立即加载并注册进内存，否则 list_games（只遍历内存）看不到导入的对局
    instance = await load(registry, new_key)
    rounds = instance.round_number if instance else -1
    logger.info("已导入存档为新对局: %s (round=%d)", sp.parent.name, rounds)
    return {"ok": True, "game_key": list(new_key)}


async def save_all_active(registry: GameRegistry) -> None:
    for instance in registry.list_active():
        try:
            await save(registry, instance)
        except Exception:
            logger.exception("保存失败: %s", instance.game_key)
            record_health_event(
                instance,
                component="save",
                code="SAVE_FAILED",
                severity="error",
                title="存档失败",
                message="当前游戏仍在内存中，但服务器重启后可能丢失最近进度。",
                impact="重启后可能回到旧回合。",
                fallback="memory_only",
                repair_hint="检查 data/saves 权限、磁盘空间和 JSON 文件是否被占用。",
            )
