# PR754 本地修复报告

**范围与 Heads**

- 日期：2026-11-16；独占 worktree：[pr-754](file:///C:/Users/Administrator/code/diceframe-review/review-20261116/pr-754)。仅修改此 worktree。
- 分支：`fix/platform-754`，从干净 detached HEAD 创建。
- 原始 PR head／本地 `review/754`：`a295953da1f3b64e5353a4372efbf7acee460db3`。
- 已提交且完成下述验证的代码 head：`797a43e5e68e6ff53519d3f5e15390bef710279e`。本报告另作后续本地文档提交；最终分支 head 可用 `git rev-parse fix/platform-754` 查询。
- 提供的 CI 日志对应 merge head：`514dcdbc599c4e2ac1ab7a499881bd742c662e34`，日志记载将上述 PR head 合入 `6c1788ab109582453522e2400323210da85ff5c9`，不是本次修复 head。
- 未 fetch、push、合并 GitHub PR、调用 Claude/OpenCode，也未修改其他 worktree 或安装依赖。已读工作区及仓库 AGENTS、架构相关章节、工程规则（含 §15）与 ADR-0013。

**1. 组安装 Rename／Journal 崩溃窗口**

- 原代码在 `staging → target` rename 后才将插件 ID 写入 `published`。首次安装没有 backup，若 rename 后进程中断，重启恢复既不删该 target，也不提交整个组，导致只安装部分插件。
- 初始源码上运行真实 ZIP 安装故障注入，第一次／第二次 rename 后直接抛继承 `BaseException` 的 `PowerLoss`，绕开普通异常回滚。两个首次安装场景均复现残留 target；初始 8 个安装回归共 **5 failed / 3 passed**，见 [platform754-before.log](file:///C:/Users/Administrator/code/diceframe-review/review-20261116/pr-754/platform754-before.log)。
- 修复：[host.py](file:///C:/Users/Administrator/code/diceframe-review/review-20261116/pr-754/src/plugin_host/host.py) 的 `install_group_from_zips` 在每次 rename **之前**持久化发布意图；`phase=published` 仍是整组提交标记，未到达它就回滚整个组。
- 同时兼容旧 journal：`backup_moved/publishing` 阶段 staging 已消耗，即使 `published` 漏记，也作为候选 target 清除。恢复前将每项 `discard_target/restore_backup` 回滚计划落盘，避免恢复 backup 后再次崩溃，下次误删已经恢复的旧包。
- 回归：[test_plugin_install_crash_recovery.py](file:///C:/Users/Administrator/code/diceframe-review/review-20261116/pr-754/tests/test_plugin_install_crash_recovery.py) 覆盖第 1／2 包发布、新装／覆盖、新／旧 journal、重复重启，以及恢复 backup rename 后再中断。无提交标记时结果只能是全无新包或完整旧版本。

**2. 覆盖升级旧 Receipt 导致永久 Digest Mismatch**

- 原 `_backfill_install_receipt` 遇到已有 `install.json` 即 return。已安装 v1、发布 v2 后在写收据前崩溃，恢复留下 v1 digest，discover 将 v2 标记为 `failed / installed manifest digest mismatch`；单包与组安装均复现于初始源码。
- 修复：`_backfill_install_receipt` 先核对 journal plugin ID 与目标完整 manifest digest，再核对现有 receipt 的 ID／版本／digest／artifact hash／source。只有完全一致才免写；旧收据按已提交目标刷新，而不是按文件存在性跳过。目标 manifest 被修改则拒绝补写，保留现场。
- 单包恢复原本捕获收据写入异常后仍删除整个 journal，本次改为保留失败记录重试；单包／组恢复均先成功持久化 receipt，再清理恢复证据。尚未发布的 `prepared/staged` 覆盖现场则保留旧包及旧收据。
- 回归同上安装测试文件：单包／组 v1→v2 覆盖后在 commit journal 落盘处中断，连续重启两次均为 active v2，receipt digest 与新 manifest 相等且 artifact hash 正确；收据写入 OSError 保留 journal；目标 manifest 冲突不覆盖收据／用户内容，消除冲突后可完成恢复。

**3. 密码保护分享入口遗漏 Gameplay／Rulebooks**

- 原 [access_control.py](file:///C:/Users/Administrator/code/diceframe-review/review-20261116/pr-754/src/webui/access_control.py) 的 `share_endpoint_kind` 不识别已注册的 gameplay view／actions／slots 与 game-scoped plugin rulebooks。存在全局访问密码时，合法 seat token 也无法进入分享认证链，最终 401。
- 修复仅增加精确方法及路径深度白名单：`GET gameplay-view`、`POST gameplay-actions/{action}`、`GET gameplay-slots/{slot}`、`GET plugins/{plugin_id}/rulebooks[/{book_id}]`。不放行全局插件管理／错误方法／额外路径。
- [test_gameplay_share_access.py](file:///C:/Users/Administrator/code/diceframe-review/review-20261116/pr-754/tests/test_gameplay_share_access.py) 通过真实 aiohttp 路由与真实密码认证中间件验证五个端点，合法 seat token + room token 返回 200，服务接收到真实 p1／非 GM 身份，而不是伪造 query 中的 GM。
- 同时验证无 token／错误 token／跨游戏 token／旋转失效 token 被拒绝，room password 不能被 seat token 绕过，关闭玩家访问后拒绝请求；全局 plugin/admin 边界未扩大。Owner／GM／Bot 相关既有回归也通过。

**4. SequentialActionComposer 重试更换 UUID**

- 原 [SequentialActionComposer.vue](file:///C:/Users/Administrator/code/diceframe-review/review-20261116/pr-754/frontend-v2/src/features/play/SequentialActionComposer.vue) 每次 submit 都生成新 `intent_id`，服务端已提交而响应丢失时，重试无法命中既有幂等收据。
- 修复：组件保留一次提交的深拷贝 DTO 和 identity；同一游戏／actor／mode／steps 的不确定失败重试复用该 DTO 和 ID；成功、编辑动作或改变上下文后开始新命令。发送期间锁定编辑与重复提交，回退 ID 也不再只依赖毫秒时间戳。
- [SequentialActionComposer.test.ts](file:///C:/Users/Administrator/code/diceframe-review/review-20261116/pr-754/frontend-v2/tests/SequentialActionComposer.test.ts) 模拟“服务已执行一次但第一次响应丢失”，第二次提交 payload／ID 完全相同，副作用计数仍为 1；成功后再次提交相同动作获得新 ID。另覆盖 draft／game／actor／mode／target 改变及发送期间禁用编辑，共 8 项通过。
- 顺带修正该测试原先 `beforeEach` 隐式返回 mock 函数的问题：Vitest 会将返回函数当 cleanup 执行，导致拒绝／未决 promise 测试在 teardown 额外调用 mock。

**5. CI ImportError 的实际原因与处理**

- [ci-754.log](file:///C:/Users/Administrator/code/diceframe-review/review-20261116/ci-754.log) 第 759–768、2092 行明确是 collection ImportError：`parse_ruleset_runtime_declaration` 不能从 `src.plugin_host.requirements` 导入，并非日志中的 Ruff／Mypy 错误。初始 worktree 的 collect-only 原样复现，见 [ci754-collection-before.log](file:///C:/Users/Administrator/code/diceframe-review/review-20261116/pr-754/ci754-collection-before.log)。
- 不只是缺少 re-export：该 head 根本没有 parser 实现，也没有测试随后引用的 `src.rulesets.plugin_runtime_source`、`PluginHost.set_ruleset_runtime_listener` 或 `RulesetRuntimeRegistry.unregister`，宿主 descriptor 不支持 `ruleset-runtime` 类型。单补导出无法修好测试，搬入整套注册桥则会混入依赖 PR 的功能。
- `git log/show` 核实原 [test_plugin_ruleset_runtimes.py](file:///C:/Users/Administrator/code/diceframe-review/review-20261116/pr-754/tests/test_plugin_ruleset_runtimes.py) 是 `0f0f58e7d33dd38cef1cc0871e84bd6fe4c2b880` 为默认 RNG 修复一次性带入的 496 行后续功能测试，与本分支实现不匹配。
- 本次完整改写这份测试，以 **本分支已有** `RemoteRulesetRuntimeAdapter + StdioRuntimeTransport + ruleset_gameplay.submit_intent` 真实服务链覆盖原修复目的。省略 `rng_factory` 使用生产默认，真实子进程还原 RNG state 并掷骰；依次提交 first、first 重试、second，子进程只结算两次、权威状态只推进到 v2，最后关闭并回收子进程。
- 未添加伪造 parser／空实现，也未用 skip 或 importorskip 隐藏这份错误。后续 PR 的完整插件注册契约仍应在拥有其实现的分支验证，本报告不将其声称为 PR754 已支持。

**验证命令与结果**

- 共享 Python：[python.exe](file:///C:/Users/Administrator/code/diceframe/.venv/Scripts/python.exe)，Python 3.12 环境；未 pip install。现有 node 依赖来自 [review-20261113/node_modules](file:///C:/Users/Administrator/code/diceframe-review/review-20261113/frontend-v2/node_modules)，仅在本 worktree 建立被忽略的 junction，不修改依赖源或 lockfile。
- 最终后端：**401 passed / 316 warnings**。警告为既有 aiohttp AppKey／RequestKey 建议等；见 [platform754-final-backend.log](file:///C:/Users/Administrator/code/diceframe-review/review-20261116/pr-754/platform754-final-backend.log)。

```bash
PY=/c/Users/Administrator/code/diceframe/.venv/Scripts/python.exe
"$PY" -m pytest tests/test_plugin*.py tests/test_gameplay*.py \
  tests/test_sequential_actions.py tests/test_resource_recovery_routes.py \
  tests/test_game_query_routes_http.py tests/test_remote_ruleset_adapter.py \
  tests/test_stdio_runtime_transport.py tests/test_seat_token_hardening.py \
  tests/test_share_gm_seat_guard.py tests/test_web_server_bot_auth.py -q
```

- 前端：**3 files / 23 tests passed**，覆盖 SequentialActionComposer、RulesetPlayHost、GameplayImportBoundary；见 [platform754-frontend.log](file:///C:/Users/Administrator/code/diceframe-review/review-20261116/pr-754/platform754-frontend.log)。

```bash
cd frontend-v2
node node_modules/vitest/vitest.mjs run tests/SequentialActionComposer.test.ts \
  tests/RulesetPlayHost.test.ts tests/GameplayImportBoundary.test.ts
node node_modules/vue-tsc/bin/vue-tsc.js --noEmit -p tsconfig.app.json
node node_modules/eslint/bin/eslint.js src/features/play/SequentialActionComposer.vue \
  tests/SequentialActionComposer.test.ts
```

- Vue TypeScript、两份变更前端文件 ESLint：通过。Ruff `src/` + 三份变更 Python 测试：通过。配置内 `python -m mypy`：**102 source files 无错误**。`git diff --check`：通过。日志见 [platform754-typecheck.log](file:///C:/Users/Administrator/code/diceframe-review/review-20261116/pr-754/platform754-typecheck.log)、[platform754-eslint.log](file:///C:/Users/Administrator/code/diceframe-review/review-20261116/pr-754/platform754-eslint.log)、[platform754-ruff.log](file:///C:/Users/Administrator/code/diceframe-review/review-20261116/pr-754/platform754-ruff.log)、[platform754-mypy.log](file:///C:/Users/Administrator/code/diceframe-review/review-20261116/pr-754/platform754-mypy.log)。
- 全仓 collect-only 的原 PR754 ImportError 已消失，但共享 venv 缺少 `pypdf`，仍在外部冒险导入测试 collection 报 `ModuleNotFoundError`；见 [platform754-collection.log](file:///C:/Users/Administrator/code/diceframe-review/review-20261116/pr-754/platform754-collection.log)。未为本次任务安装依赖。仅为核验其余测试收集，执行 `python -m pytest --collect-only --ignore=tests/test_external_adventure_import.py -q`，成功收集 **9178 tests**；见 [platform754-collection-available.log](file:///C:/Users/Administrator/code/diceframe-review/review-20261116/pr-754/platform754-collection-available.log)。这不是全量运行或全量 CI 通过。

**边界与限制**

- 涉及安装恢复、receipt integrity、分享权限与多人命令幂等；不改游戏 mechanics、存档 schema、公开插件类型或 AI 配置格式。不引入第三方依赖。
- 故障注入验证的是进程中断语义（真实 ZIP／rename 路径，BaseException 绕开 Exception cleanup），不是断电／文件系统 fsync 顺序或真实主机强杀测试。
- 组件重试身份保存在当前已挂载组件内；未增加跨页面刷新／卸载后的持久重试队列，也未声称覆盖这种恢复场景。未做浏览器视觉验收或完整前端生产构建。
- 未跑全仓测试执行；缺少 pypdf 的全量 collection 限制如上，未掩盖或修改其他任务的依赖／测试。
- 最终 changed-file list 已逐项检查：仅安装 owner、访问控制 owner、SequentialActionComposer 及各自回归、误带入的 RNG 测试修正和本报告；测试日志及依赖 junction 不提交。所有提交仅保留本地，未 push／merge。
