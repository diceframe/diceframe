"""Web-to-QQ synchronization helpers."""

from __future__ import annotations


class QQWebSyncMixin:
    async def _poll_web_notifications(self) -> None:
        """轮询 web 端游戏进度：实时转发网页新提交的行动，推进时发叙事+状态变动。"""
        for group_id, group in list(self.store.groups.items()):
            game_key = str(group.get("game_key") or "")
            gm_uid = str(group.get("gm_uid") or "")
            if not game_key or not gm_uid:
                continue
            try:
                detail = await self.api.game_detail(game_key, gm_uid)
            except Exception:
                self.logger.warning("Web 同步轮询 game_detail 失败: %s", game_key, exc_info=True)
                continue
            if not isinstance(detail, dict):
                continue
            recap = detail.get("recap") or {}
            seen = self._web_sync_seen_actions.setdefault(game_key, set())

            pending = recap.get("pending_actions") if isinstance(recap.get("pending_actions"), list) else []
            for action in pending:
                if not isinstance(action, dict):
                    continue
                sig = str(action.get("signature") or "")
                if not sig or sig in seen:
                    continue
                seen.add(sig)
                try:
                    await self._send_web_action_to_group(group_id, action)
                except Exception:
                    self.logger.warning("Web 同步实时转发行动失败: group=%s sig=%s", group_id, sig, exc_info=True)

            current_round = int(detail.get("round_number") or 0)
            last = self._web_sync_last_round.get(game_key)
            if last is None:
                self._web_sync_last_round[game_key] = current_round
                self.logger.info("Web 同步基线: game=%s round=%d（之后网页推进新轮将转发到群）", game_key, current_round)
                continue
            if current_round <= last or self._group_action_inflight.get(game_key):
                continue
            self._web_sync_last_round[game_key] = current_round
            self.logger.info("Web 同步检测到新轮: game=%s round=%d，转发到群 %s", game_key, current_round, group_id)
            rounds = recap.get("recent_rounds") if isinstance(recap.get("recent_rounds"), list) else []
            if not rounds:
                continue
            latest = rounds[-1] if isinstance(rounds[-1], dict) else {}
            try:
                await self._send_web_round_to_group(group_id, latest, current_round, seen, quick_actions=detail.get("quick_actions"))
            except Exception:
                self.logger.warning("Web 同步转发到群失败: group=%s round=%s", group_id, current_round, exc_info=True)
            seen.clear()

    async def _send_web_action_to_group(self, group_id: str, action: dict) -> None:
        """网页玩家行动实时转发：纯文本「角色名：行动」，像群友发言。"""
        if str(action.get("source") or "") == "group":
            return
        name = str(action.get("character_name") or "冒险者")
        text = str(action.get("text") or "").strip()
        if not text:
            return
        await self._send_group_text(group_id, f"{name}：{text}")

    async def _send_web_round_to_group(self, group_id: str, round_entry: dict, round_number: int, seen: set[str], *, quick_actions: list[str] | None = None) -> None:
        """轮次推进时：补发未被实时转发的行动 + 合并卡。"""
        actions = round_entry.get("actions") or []
        for action in actions:
            if not isinstance(action, dict):
                continue
            sig = str(action.get("signature") or "")
            if sig and sig not in seen:
                seen.add(sig)
                await self._send_web_action_to_group(group_id, action)
        await self._send_round_summary_card(
            group_id,
            round_number,
            str(round_entry.get("gm_response") or ""),
            round_entry.get("state_changes"),
            quick_actions=quick_actions,
        )
