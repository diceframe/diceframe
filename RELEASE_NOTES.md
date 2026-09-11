# DiceFrame v2.5.6

> 补丁版。修复世界书编辑器保存已有条目必然失败的问题（`sqlite3.OperationalError: near "order": syntax error`）。升级无需迁移。

## 中文

### 修复内容

- **世界书编辑无法保存**：编辑已有条目后保存，后端 `UPDATE` 语句把 SQL 保留字 `order` / `group` 当列名裸写，触发 `sqlite3.OperationalError: near "order": syntax error`，接口返回 500，界面上表现为保存按钮没有反应。现在 SET 子句的列名统一加引号（列名来自后端白名单，值仍参数化）。
- **影响范围**：仅「编辑已有条目」（`PUT /api/lorebook/{id}`）。新建条目、导入、AI 生成、复制世界书都不受影响，因此新建能成功、保存会失败。
- **回归测试**：新增 store 级与路由级各一条；把修复临时退回时两条都会复现同样的报错。

### 升级提示

- **无存档迁移**：只改 SQL 语句构造，不涉及数据结构。世界书数据未受影响——失败的保存从未写入数据库。
- 建议升级重要战役前备份完整 `data/` 目录。

### 下载与校验

- **普通 Windows 用户**：`DiceFrame-v2.5.6-windows-portable.zip`
- **源码运行用户**：`DiceFrame-v2.5.6-windows.zip`
- **托管 Docker 更新**：`DiceFrame-v2.5.6-docker-update-linux-amd64.zip`
- 下载后请使用 Release 中的 `SHA256SUMS` 校验文件。

## English

### Fix

- **Editing a lorebook entry could not be saved**: saving an existing entry produced `sqlite3.OperationalError: near "order": syntax error` because the backend's `UPDATE` statement used the SQL reserved words `order` / `group` as bare column names. The endpoint returned 500 and the UI's save button appeared to do nothing. Column names in the `SET` clause are now quoted (they come from a backend allowlist; values remain parameterized).
- **Scope**: only editing an existing entry (`PUT /api/lorebook/{id}`). Creating entries, imports, AI generation and worldbook copy were unaffected — which is why creating worked while saving failed.
- **Regression coverage**: one store-level and one route-level test; temporarily reverting the fix reproduces the same error in both.

### Upgrade notes

- **No save migration**: this changes SQL construction only, not the data model. Lorebook data was never corrupted — failed saves never wrote to the database.
- Back up the complete `data/` directory before upgrading important campaigns.

### Downloads and verification

- **Regular Windows users**: `DiceFrame-v2.5.6-windows-portable.zip`
- **Source-run users**: `DiceFrame-v2.5.6-windows.zip`
- **Managed Docker update**: `DiceFrame-v2.5.6-docker-update-linux-amd64.zip`
- Verify downloads with the `SHA256SUMS` file attached to the Release.
