"""Game command executors for QQ bot adapters."""

from __future__ import annotations

from typing import Any


class QQGameCommandsMixin:
    async def _send_status(self, group_id: str, platform_user_id: str, game_key: str, actor: str) -> None:
        data = await self.api.characters(game_key, actor)
        player = next((item for item in data.get("players", []) if item.get("user_id") == actor), None)
        if not player:
            await self._send_group_text(group_id, "未找到当前角色。")
            return
        sheet = player.get("character_sheet", {})
        items = [item.get("name", "") if isinstance(item, dict) else str(item) for item in sheet.get("inventory", [])]
        key_items = [item.get("name", "") if isinstance(item, dict) else str(item) for item in sheet.get("key_items", [])]
        resources = sheet.get("resources", {}) if isinstance(sheet.get("resources"), dict) else {}
        resource_lines: list[str] = []
        for key, value in resources.items():
            if isinstance(value, dict):
                resource_lines.append(f"{value.get('label') or key} {value.get('current', 0)}/{value.get('max', 0)}")
        # 属性（带规则 display_name）
        attrs = sheet.get("attributes", {}) if isinstance(sheet.get("attributes"), dict) else {}
        rule_attrs = data.get("rule_attrs") if isinstance(data.get("rule_attrs"), list) else []
        attr_lines: list[str] = []
        seen: set[str] = set()
        for attr in rule_attrs:
            if not isinstance(attr, dict):
                continue
            key = str(attr.get("key") or "")
            if not key:
                continue
            seen.add(key)
            name = str(attr.get("display_name") or attr.get("name") or key)
            attr_lines.append(f"{name} {attrs.get(key, 0)}")
        for key, value in attrs.items():
            if key in seen:
                continue
            attr_lines.append(f"{key} {value}")
        # 技能
        skills = sheet.get("skills", []) if isinstance(sheet.get("skills"), list) else []
        skill_lines: list[str] = []
        for s in skills:
            if isinstance(s, dict):
                name = str(s.get("name") or "").strip()
                if not name:
                    continue
                skill_lines.append(f"{name} {s.get('value', '')}")
            elif isinstance(s, str) and s.strip():
                skill_lines.append(s.strip())
        # 任务（game 级，需额外调 game_detail 拿 plot_tracker）
        quest_lines: list[str] = []
        try:
            detail = await self.api.game_detail(game_key, actor)
            quests = (detail.get("plot_tracker") or {}).get("quests") or {}
            if isinstance(quests, dict):
                active = [q for q in quests.values() if isinstance(q, dict) and q.get("status") == "active"]
                completed = sorted(
                    [q for q in quests.values() if isinstance(q, dict) and q.get("status") == "completed"],
                    key=lambda q: q.get("round_updated", 0), reverse=True,
                )[:5]
                for q in active:
                    title = str(q.get("title") or "").strip()
                    if not title:
                        continue
                    progress = str(q.get("progress") or "").strip()
                    quest_lines.append(f"[进行] {title}" + (f"（{progress}）" if progress else ""))
                for q in completed:
                    title = str(q.get("title") or "").strip()
                    if title:
                        quest_lines.append(f"[完成] {title}")
        except Exception:
            self.logger.warning("状态卡拉取任务列表失败", exc_info=True)
        lines: list[str] = [
            f"HP {sheet.get('hp', 0)}/{sheet.get('max_hp', 0)}  金币 {sheet.get('gold', 0)}",
        ]
        lines.extend(resource_lines[:4])
        if attr_lines:
            lines.append("")
            lines.append("-- 属性 --")
            lines.extend(attr_lines)
        if skill_lines:
            lines.append("")
            lines.append("-- 技能 --")
            lines.extend(skill_lines[:12])
        if quest_lines:
            lines.append("")
            lines.append("-- 任务 --")
            lines.extend(quest_lines[:12])
        lines.append("")
        lines.append(f"背包：{'、'.join(items[:8]) or '无'}")
        lines.append(f"关键物品：{'、'.join(key_items[:6]) or '无'}")
        await self._send_group_card(
            group_id,
            title=str(player.get("character_name") or actor),
            subtitle="角色状态",
            lines=lines,
            fallback=(
                f"{player.get('character_name') or actor}  HP {sheet.get('hp', 0)}/{sheet.get('max_hp', 0)}  "
                f"金币 {sheet.get('gold', 0)}  背包：{'、'.join(items) or '无'}"
            ),
        )

    async def _send_attributes_card(self, group_id: str, game_key: str, actor: str) -> None:
        data = await self.api.characters(game_key, actor)
        player = next((item for item in data.get("players", []) if item.get("user_id") == actor), None)
        if not player:
            await self._send_group_text(group_id, "未找到当前角色。")
            return
        sheet = player.get("character_sheet", {})
        attrs = sheet.get("attributes", {}) if isinstance(sheet.get("attributes"), dict) else {}
        pool = int(sheet.get("level_up_points", 0) or 0)
        rule_attrs = data.get("rule_attrs") if isinstance(data.get("rule_attrs"), list) else []
        lines: list[str] = []
        seen: set[str] = set()
        for attr in rule_attrs:
            if not isinstance(attr, dict):
                continue
            key = str(attr.get("key") or "")
            if not key:
                continue
            seen.add(key)
            name = str(attr.get("display_name") or attr.get("name") or key)
            lines.append(f"{name} {attrs.get(key, 0)}")
        for key, value in attrs.items():
            if key in seen:
                continue
            lines.append(f"{key} {value}")
        lines.append("")
        lines.append(f"可分配点数：{pool}")
        if pool > 0:
            lines.append("发送：@我 加 属性名 数值")
        else:
            lines.append("暂无可分配点数（升级获得）")
        await self._send_group_card(
            group_id,
            title=str(player.get("character_name") or actor),
            subtitle="属性 · 加点",
            lines=lines,
            fallback=("  ".join(ln for ln in lines if ln) or f"{player.get('character_name') or actor} 属性"),
        )

    async def _allocate_attribute(self, group_id: str, game_key: str, actor: str, attr_name: str, delta: int) -> None:
        data = await self.api.characters(game_key, actor)
        player = next((item for item in data.get("players", []) if item.get("user_id") == actor), None)
        if not player:
            await self._send_group_text(group_id, "未找到当前角色。")
            return
        sheet = player.get("character_sheet", {})
        attrs = sheet.get("attributes", {}) if isinstance(sheet.get("attributes"), dict) else {}
        pool = int(sheet.get("level_up_points", 0) or 0)
        rule_attrs = data.get("rule_attrs") if isinstance(data.get("rule_attrs"), list) else []
        target_key = ""
        target_name = ""
        for attr in rule_attrs:
            if not isinstance(attr, dict):
                continue
            key = str(attr.get("key") or "")
            display = str(attr.get("display_name") or "")
            name = str(attr.get("name") or "")
            if attr_name == key or attr_name == display or attr_name == name or (display and display.startswith(attr_name)):
                target_key = key
                target_name = display or name or key
                break
        if not target_key and attr_name in attrs:
            target_key = attr_name
            target_name = attr_name
        if not target_key:
            await self._send_group_text(group_id, f"未找到属性“{attr_name}”，发送 @我 加点 查看可加属性。")
            return
        if delta <= 0:
            await self._send_group_text(group_id, "加点数值必须大于 0。")
            return
        if delta > pool:
            await self._send_group_text(group_id, f"点数不足：剩余 {pool} 点，尝试加 {delta} 点。")
            return
        old_val = int(attrs.get(target_key, 0) or 0)
        new_val = old_val + delta
        result = await self.api.update_character(game_key, actor, {"attributes": {target_key: new_val}})
        if not result.get("ok"):
            await self._send_group_text(group_id, f"加点失败：{result.get('error') or '未知错误'}")
            return
        remaining = pool - delta
        await self._send_group_card(
            group_id,
            title=str(player.get("character_name") or actor),
            subtitle="加点成功",
            lines=[
                f"{target_name} {old_val} -> {new_val}",
                f"剩余可分配：{remaining}",
            ],
            fallback=f"{target_name} {old_val}->{new_val}，剩余 {remaining} 点",
        )

    async def _send_recap_group(self, group_id: str, group: dict[str, Any]) -> None:
        game_key = str(group.get("game_key") or "")
        gm_uid = str(group.get("gm_uid") or "")
        if not game_key or not gm_uid:
            await self._send_group_text(group_id, "当前群还没有可读取的前情。")
            return
        detail = await self.api.game_detail(game_key, gm_uid)
        await self._send_group_text(group_id, self._recap_text(detail))

    async def _send_recap_private(self, platform_user_id: str, game_key: str, actor: str) -> None:
        detail = await self.api.game_detail(game_key, actor)
        await self._send_private_text(platform_user_id, self._recap_text(detail))

    async def _send_map_group(self, group_id: str, group: dict[str, Any]) -> None:
        game_key = str(group.get("game_key") or "")
        gm_uid = str(group.get("gm_uid") or "")
        if not game_key:
            await self._send_group_text(group_id, "当前群还没有绑定游戏，暂时没有地图。")
            return
        data = await self.api.map(game_key, gm_uid)
        lines = self._map_lines(data)
        await self._send_group_card(
            group_id,
            title="场景地图",
            subtitle=str(data.get("current_scene") or "地点概览"),
            lines=lines,
            fallback=self._map_text(lines),
        )

    async def _set_away_group(self, group_id: str, platform_user_id: str, group: dict[str, Any],
                              text: str, *, away: bool, actor_uid: str = "") -> None:
        game_key = str(group.get("game_key") or "")
        if not game_key:
            await self._send_group_text(group_id, "当前群聊还没有绑定游戏。")
            return
        player = self.store.player(group_id, platform_user_id)
        actor_uid = actor_uid or str((player or {}).get("user_id") or "")
        target_uid, target_name = await self._away_target(group_id, group, text, actor_uid)
        if not target_uid:
            await self._send_group_text(group_id, "没有找到要切换状态的角色。自己暂离可发 @我 暂离；GM 可发 @我 暂离 角色名。")
            return
        if target_uid != actor_uid and not self._can_advance(platform_user_id, group):
            await self._send_group_text(group_id, f"@{platform_user_id} 只能切换自己的暂离状态；GM 或授权账号可指定角色。")
            return
        api_actor = actor_uid if target_uid == actor_uid else str(group.get("gm_uid") or actor_uid)
        result = await self.api.set_player_away(game_key, api_actor, target_uid, away=away)
        if not result.get("ok"):
            await self._send_group_text(group_id, str(result.get("error") or "暂离状态切换失败。"))
            return
        name = str(result.get("character_name") or target_name or target_uid)
        if away:
            await self._send_group_text(group_id, f"{name} 已暂离：暂时不阻塞回合，剧情中默认跟随队伍，不主动做重大决定。")
        else:
            await self._send_group_text(group_id, f"{name} 已回来：之后会重新参与待行动列表。")

    async def _away_target(self, group_id: str, group: dict[str, Any], text: str, actor_uid: str) -> tuple[str, str]:
        query = self._away_target_query(text)
        if not query:
            if actor_uid:
                name = self._roster_name_by_uid(group, actor_uid)
                return actor_uid, name
            return "", ""
        matches = self._match_roster_character(group.get("roster", []), query)
        if len(matches) != 1:
            group = await self._refresh_group_roster(group_id, group)
            matches = self._match_roster_character(group.get("roster", []), query)
        if len(matches) == 1:
            return str(matches[0].get("user_id") or ""), str(matches[0].get("character_name") or "")
        return "", ""

    async def _advance_group(self, group_id: str, platform_user_id: str, group: dict[str, Any], text: str) -> None:
        if not self._can_advance(platform_user_id, group):
            await self._send_group_text(group_id, f"@{platform_user_id} 只有本局 GM 或设置里授权的账号可以推进。")
            return
        game_key = str(group.get("game_key") or "")
        gm_uid = str(group.get("gm_uid") or "")
        if not game_key or not gm_uid:
            await self._send_group_text(group_id, "当前群聊还没有绑定可推进的游戏。")
            return
        await self._send_group_text(group_id, "收到推进指令，GM 正在思考中，生成下一段剧情…")
        result = await self.api.advance(game_key, gm_uid, force=self._advance_force(text))
        gm_response = str(result.get("narration") or "")
        state_changes: list = []
        round_number = 0
        quick_actions: list[str] | None = None
        try:
            detail = await self.api.game_detail(game_key, gm_uid)
            round_number = int(detail.get("round_number") or 0)
            quick_actions = detail.get("quick_actions")
            recap = detail.get("recap") or {}
            rounds = recap.get("recent_rounds") or []
            if rounds and isinstance(rounds[-1], dict):
                latest = rounds[-1]
                gm_response = str(latest.get("gm_response") or gm_response).strip()
                sc = latest.get("state_changes")
                if isinstance(sc, list):
                    state_changes = sc
            if round_number:
                self._web_sync_last_round[game_key] = round_number
                self._web_sync_seen_actions.setdefault(game_key, set()).clear()
        except Exception:
            self.logger.warning("推进后拉取回合详情失败，回退用 advance 返回", exc_info=True)
        await self._send_round_summary_card(
            group_id,
            round_number,
            gm_response,
            state_changes,
            roll=result.get("roll"),
            pending_payments=result.get("pending_payments"),
            quick_actions=quick_actions,
        )

    def _can_advance(self, platform_user_id: str, group: dict[str, Any]) -> bool:
        user = str(platform_user_id or "").strip()
        if user and user == str(group.get("gm_platform_id") or "").strip():
            return True
        configured = getattr(self.config, "advance_allowed_users", ()) if self.config else ()
        return user in {str(item).strip() for item in configured if str(item).strip()}

    async def _send_private_log_group(self, group_id: str, platform_user_id: str, game_key: str, actor: str) -> None:
        try:
            sent = await self._send_private_log_private(platform_user_id, game_key, actor, announce=False)
        except Exception:
            self.logger.warning("QQ 私聊角色感知失败", exc_info=True)
            sent = False
        await self._send_group_text(group_id, "已私聊你最近的角色感知。" if sent else "暂时无法私聊你，请检查是否允许接收临时会话。")

    async def _send_private_log_private(self, platform_user_id: str, game_key: str, actor: str,
                                        announce: bool = True) -> bool:
        data = await self.api.private_log(game_key, actor)
        messages = data.get("messages") if isinstance(data.get("messages"), list) else []
        recent = messages[-6:]
        if recent:
            lines = [
                f"R{item.get('round', '?')}：{str(item.get('text') or '').strip()}"
                for item in recent
                if str(item.get("text") or "").strip()
            ]
        else:
            lines = ["暂无专属于你的角色感知。"]
        await self._send_private_card(
            platform_user_id,
            title="角色感知",
            subtitle="仅你可见",
            lines=lines,
            fallback="角色感知：\n" + "\n".join("　　" + line for line in lines),
        )
        return True

    async def _send_payment_list_group(self, group_id: str, platform_user_id: str, game_key: str, actor: str) -> None:
        try:
            sent = await self._send_payment_list_private(platform_user_id, game_key, actor, announce=False)
        except Exception:
            self.logger.warning("QQ 私聊支付列表失败", exc_info=True)
            sent = False
        await self._send_group_text(group_id, "已私聊你待处理的支付请求。" if sent else "暂时无法私聊你，请检查是否允许接收临时会话。")

    async def _send_payment_list_private(self, platform_user_id: str, game_key: str, actor: str,
                                         announce: bool = True) -> bool:
        payments = await self._pending_payments(game_key, actor)
        if not payments:
            await self._send_private_text(platform_user_id, "当前没有待你确认的支付请求。")
            return True
        lines = [self._payment_line(item, idx) for idx, item in enumerate(payments, 1)]
        lines.append("确认：确认支付 或 确认支付 2")
        lines.append("拒绝：拒绝支付 或 拒绝支付 2")
        await self._send_private_card(
            platform_user_id,
            title="待确认支付",
            subtitle="仅处理你的角色",
            lines=lines,
            fallback="待确认支付：\n" + "\n".join("　　" + line for line in lines),
        )
        return True

    async def _resolve_payment_group(self, group_id: str, platform_user_id: str, game_key: str,
                                     actor: str, accepted: bool, text: str) -> None:
        message = await self._resolve_payment(game_key, actor, accepted, text)
        await self._send_group_text(group_id, f"@{platform_user_id} {message}")

    async def _resolve_payment_private(self, platform_user_id: str, game_key: str,
                                       actor: str, accepted: bool, text: str) -> None:
        message = await self._resolve_payment(game_key, actor, accepted, text)
        await self._send_private_text(platform_user_id, message)

    async def _resolve_payment(self, game_key: str, actor: str, accepted: bool, text: str) -> str:
        payments = await self._pending_payments(game_key, actor)
        if not payments:
            return "当前没有待你确认的支付请求。"
        index = self._payment_index(text)
        if index < 1 or index > len(payments):
            return f"没有第 {index} 笔待支付；发送“支付”查看列表。"
        payment = payments[index - 1]
        result = await self.api.resolve_payment(game_key, actor, str(payment.get("id") or ""), accepted)
        if result.get("ok") is False:
            return str(result.get("error") or "支付处理失败")
        amount = int(payment.get("amount", 0) or 0)
        return f"已{'确认' if accepted else '拒绝'}支付 {amount} 金币。"

    async def _pending_payments(self, game_key: str, actor: str) -> list[dict[str, Any]]:
        detail = await self.api.game_detail(game_key, actor)
        payments = detail.get("pending_payments") if isinstance(detail.get("pending_payments"), list) else []
        return [
            item for item in payments
            if isinstance(item, dict) and str(item.get("uid") or "") == actor and item.get("status") == "pending"
        ]
