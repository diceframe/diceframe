# DiceFrame v3.1.2

## 中文

### 孤注一掷（修复）

- 修正了部分情况下「申请孤注一掷 / 接受失败 / 接受风险并重掷」无法提交的问题；这些选择现在会按当前席位的身份正常生效，主持人告知风险与拒绝申请的行为不变。
- 相关提示也更清楚了：不再是裸的状态码，会给出具体原因并按界面语言显示（zh-CN / en；社区语言暂回退英文）。

### 资料册与内容包（修复）

- 内容包详情页此前直接读取包内文件，绕过了可见性判定，玩家能读到守秘人专属条目的正文。现在按观看者视角过滤：玩家只看到自己可见的条目，条目计数与被隐藏的条数也如实告知。
- 视角判定：房主凭据优先；配置了访问密码时，非房主一律按玩家视角处理（fail closed）；未配置访问密码时普通请求仍按房主处理，带 `user=` 或席位凭据的请求按玩家处理。

### 界面（修复）

- 内容包列表在更新进行中会禁用**所有**更新按钮。此前只有正在更新的那个按钮变灰，点另一个包看起来像没反应。

### 升级说明

- 本版本不新增存档 schema 迁移。
- de/ja/ru 界面下本版新增文案暂时回退英文。

## English

### Push the roll (fix)

- Fixed a case where "Request Push", "Accept failure" and "Accept risk and re-roll" could not be submitted; those choices now take effect under the current seat's identity, while offering the risk and rejecting a request stay Keeper-only.
- The related messages are clearer too: a refusal states a reason and follows the UI language instead of showing a bare status code (zh-CN / en; community locales fall back to English for now).

### Lorebooks and content packs (fix)

- The module detail page read pack files directly and bypassed the visibility check, so players could read keeper-only entry bodies. It now filters by viewer perspective: players see only the entries visible to them, and the entry count plus the number of hidden entries match that view.
- Perspective rules: owner credentials win; with an access password configured, non-owners are fail-closed player views; without one, plain requests stay GM while requests marked as a share (`user=` or a seat token) are player views.

### Interface (fix)

- The pack list now disables **every** update button while an update runs. Previously only the running button was greyed out, so clicking another pack looked dead.

### Upgrade notes

- This release adds no save-schema migration.
- New UI strings fall back to English under de/ja/ru for now.

## Downloads

- **普通 Windows 用户 / Windows portable users**: `DiceFrame-v3.1.2-windows-portable.zip`
- **源码运行用户 / Source-run users**: `DiceFrame-v3.1.2-windows.zip`
- **托管 Docker 更新 / Managed Docker update**: `DiceFrame-v3.1.2-docker-update-linux-amd64.zip`
- Release 同时提供以上压缩包及 `SHA256SUMS` 校验文件。
