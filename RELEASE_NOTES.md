# DiceFrame v2.1.0

## 中文

DiceFrame 2.1.0 带来更完整的冒险地图、按需生成的剧情概览，以及可连接浏览器、在线服务和本地服务的语音朗读系统。

### 地图与内容包

- **地图背景**：内置奇幻区域、神秘小镇和赛博城市三套背景；地图可拖动、缩放和展开，背景与标记使用同一坐标空间。
- **冒险级选择**：创建冒险时可选择地图背景，GM 也能在游玩页随时更换；旧存档会获得安全的默认背景。
- **内容包地图素材**：内容包可分发地图定义、地点、图标和背景，制作工具能够一并打包相关素材。
- **移动端沉浸布局**：侧栏、GM 控制栏和地图工作区适配手机安全区域，不再为底部浏览器菜单预留多余空间。

### 剧情概览

- 游玩页新增共享的“剧情概览”入口，点击时根据当前公开时间线生成概览。
- 生成结果直接显示在消息区域，所有在场玩家均可查看，不依赖收团或暂停流程。

### 语音朗读

- **统一 TTS 引擎**：保留零配置的浏览器系统音色，并支持 OpenAI 兼容接口和 GPT-SoVITS。
- **本地服务连接**：可连接 Kokoro-FastAPI 等本地 OpenAI 兼容服务，不需要把模型塞进 DiceFrame 安装包。
- **音色预设与个人音色**：新增 `voice-pack` 插件类型、角色音色映射、个人音色管理和授权参考音频校验。
- **更清晰的设置页**：语音设置整理为“引擎与连接”“角色音色映射”“自动朗读与语速”三张子卡。
- **缓存与访问边界**：服务端语音带有容量受限的磁盘缓存；游戏内只朗读公开时间线文本。

### 其他改进

- 世界书删除与用户模板同步，条目编辑可以回写模板。
- 新增用户头像管理、角色头像选择和角色卡批量删除。
- 插件类型与权限描述改为集中配置，地图与语音贡献使用统一校验。

## English

DiceFrame 2.1.0 adds richer adventure maps, on-demand shared story recaps, and a text-to-speech system that can use browser voices, online APIs, or local services.

### Maps and content packs

- **Map backgrounds**: includes fantasy region, occult town, and cyber city scenes. Maps remain pannable, zoomable, and expandable, with backgrounds and markers sharing one coordinate space.
- **Per-adventure selection**: choose a background while creating an adventure or change it later from the GM play view. Existing saves receive a safe default.
- **Content-pack map assets**: content packs can provide map definitions, locations, icons, and backgrounds, and the authoring tool packages those assets together.
- **Mobile immersive layout**: sidebars, GM controls, and the map workspace now respect phone safe areas without reserving unnecessary space for browser chrome.

### Story recap

- The play page now has a shared recap action that generates an overview from the current public timeline on demand.
- The result appears in the message area for everyone in the session and does not depend on ending or pausing the game.

### Text-to-speech

- **Unified TTS engines**: keeps zero-configuration browser voices and adds OpenAI-compatible and GPT-SoVITS providers.
- **Local service support**: connect to services such as Kokoro-FastAPI without bundling large models in DiceFrame.
- **Voice presets and personal voices**: adds the `voice-pack` plugin type, per-role voice mapping, personal voice management, and validation for licensed reference audio.
- **Clearer settings**: voice controls are organized into Engine & connection, Role voice mapping, and Auto-read & speed cards.
- **Caching and access boundaries**: server audio uses a size-bounded disk cache, and in-game synthesis is restricted to public timeline text.

### Other improvements

- Lorebook deletion now keeps user templates in sync, and entry edits can update their templates.
- Adds user-avatar management, portrait selection, and bulk character-card deletion.
- Centralizes plugin type and permission descriptors, with unified validation for map and voice contributions.
