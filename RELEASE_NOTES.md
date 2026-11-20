# DiceFrame v3.3.3

## 中文

### D&D 2024

- **战斗行动与死亡豁免**：暂时无法行动时，战斗面板会显示等待提示。没有可用战斗法术时，法术栏会明确提示。行动因战斗状态变化被拒绝后，面板会自动刷新。死亡角色不能再行动。死亡豁免判定角色死亡或稳定后，回合会推进，或在队伍无法继续行动时结束战斗。
- **猎人子职**：巨兽杀手、破阵者、卓越猎人猎物和卓越猎人防御现在会参与实际战斗结算。猎人学识为施加印记的角色提供目标的抗性、免疫和易伤数据。休息结算支持更换「猎人猎物」和「防御战术」的选项。
- **战斗遮蔽**：云雾术和黑暗术会生成遮蔽区域。攻击掷骰会根据双方的位置和感官判定能否看见对方，再计算优势与劣势。本次接入范围是攻击掷骰。
- **AI 托管**：重试行动时按当前先攻回合判定行动者，支持同一角色在一轮中的额外回合。

### 房主管理

- 本地向量模型的目录刷新、安装、卸载和索引重建统一要求房主权限。设置页复用一次模型文件校验结果。
- Docker 手动重建只更新指定版本的标签。正式版发布仍会更新 `latest`。

从 3.3.2 升级无需新增的存档迁移。升级前请备份完整的 `data/`。

## English

### D&D 2024

- **Combat actions and death saves**: The panel shows when players need to wait and when the spell tab has no available combat spells. It refreshes after an action is rejected because the combat state changed. Deceased characters can no longer act. A death save that leaves a character dead or stable advances the turn, or ends the encounter if the party can no longer act.
- **Hunter subclass**: Colossus Slayer, Horde Breaker, Superior Hunter’s Prey, and Superior Hunter’s Defense now resolve in combat. Hunter’s Lore gives the character who placed the mark access to the target’s resistances, immunities, and vulnerabilities. Rest resolution supports changing Hunter’s Prey and Defensive Tactics choices.
- **Combat obscurement**: Fog Cloud and Darkness create obscured areas. Attack rolls use both creatures’ positions and senses to determine visibility, advantage, and disadvantage. This release applies obscurement to attack rolls.
- **AI seats**: Action retries follow the current initiative turn, including extra turns in the same round.

### Host administration

- Refreshing the local embedding model catalog, installing or uninstalling models, and rebuilding the index require owner access. Settings reuses the same model file verification results.
- Manual Docker image rebuilds update only the selected version tag. Stable releases continue to update `latest`.

Upgrading from 3.3.2 requires no new save migration. Back up the entire `data/` directory before upgrading.

## Downloads

- Windows 安装包 / installer · `DiceFrame-v3.3.3-windows-setup.exe`
- Windows 便携版 / portable · `DiceFrame-v3.3.3-windows-portable.zip`
- 源码包 / source package · `DiceFrame-v3.3.3-windows.zip`
- 托管 Docker 更新包 / managed Docker update · `DiceFrame-v3.3.3-docker-update-linux-amd64.zip`
- 校验和 / checksums · `SHA256SUMS`
