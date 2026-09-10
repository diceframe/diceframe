# DiceFrame v2.5.5-beta.1

> Windows 便携版前端加载修复测试版。

## 中文

### 修复内容

- 修复部分 Windows 系统将 `.js` 静态资源标记为 `text/plain`，导致浏览器严格 MIME 检查拒绝执行脚本、页面只显示背景的问题。
- 为 JavaScript、CSS、JSON、HTML 和 SVG 前端资源设置稳定的响应类型，不再依赖 Windows 注册表中的 MIME 关联。
- 增加回归测试，模拟系统返回错误 MIME 类型并验证模块脚本仍以 `text/javascript` 提供。

### 下载与校验

- **普通 Windows 用户**：`DiceFrame-v2.5.5-beta.1-windows-portable.zip`
- **源码运行用户**：`DiceFrame-v2.5.5-beta.1-windows.zip`
- **托管 Docker 更新**：`DiceFrame-v2.5.5-beta.1-docker-update-linux-amd64.zip`
- 下载后请使用 Release 中的 `SHA256SUMS` 校验文件。

## English

### Fix

- Fixes a blank UI on Windows systems that associate `.js` files with `text/plain`, causing browsers to reject module scripts during strict MIME checking.
- Serves JavaScript, CSS, JSON, HTML, and SVG frontend assets with stable content types independent of Windows registry MIME associations.
- Adds regression coverage that simulates an incorrect system MIME mapping and verifies module scripts are still served as `text/javascript`.

### Downloads and verification

- **Regular Windows users**: `DiceFrame-v2.5.5-beta.1-windows-portable.zip`
- **Source-run users**: `DiceFrame-v2.5.5-beta.1-windows.zip`
- **Managed Docker update**: `DiceFrame-v2.5.5-beta.1-docker-update-linux-amd64.zip`
- Verify downloads with the `SHA256SUMS` file attached to the Release.
