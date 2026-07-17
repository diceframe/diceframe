# 发布与打包

本文档给维护者使用，说明如何生成 GitHub Release 附件。

## 本地生成 Windows 包

发布前先确认版本号：

```text
src/version.py
```

然后在干净的公开仓库目录运行：

```powershell
python scripts\build_release.py
```

生成物会放到：

```text
dist/DiceFrame-v版本-windows.zip
```

如果要生成“解压即用”的 Windows 便携包，运行：

```powershell
python scripts\build_portable.py
```

生成物会放到：

```text
dist/DiceFrame-v版本-windows-portable.zip
```

这个 zip 包包含：

- Python 后端源码
- Vue 前端源码
- 已编译好的 `static-v2/`
- `web_ui.bat`
- Docker 文件、模板、插件、用户文档

便携包额外包含：

- `DiceFrame.exe`
- Windows 嵌入式 Python
- 已安装好的后端运行依赖

便携包用户不需要安装 Python，也不需要安装 Node.js。

不会包含：

- `data/`
- `.env`
- 日志
- 测试目录
- `node_modules`
- 本地 IDE 与辅助工具配置目录

如果只是本地试包，当前工作区有未提交改动时可以运行：

```powershell
python scripts\build_release.py --allow-dirty
```

正式发布不要用 `--allow-dirty`。

## GitHub 自动生成

仓库包含 `.github/workflows/release.yml`。推送 `v*` 标签后，GitHub Actions 会自动：

1. 安装 Python 和 Node.js。
2. 构建前端。
3. 生成 `DiceFrame-v版本-windows.zip`。
4. 把 zip 挂到对应 GitHub Release。
5. Windows runner 会额外生成 `DiceFrame-v版本-windows-portable.zip`。

命令示例：

```powershell
git tag v0.1.0
git push origin v0.1.0
```

Release 正文就是应用内“更新日志”的来源。发布后请到 GitHub Release 页面填写更新内容。
