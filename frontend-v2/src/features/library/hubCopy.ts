import type { Locale } from '@/i18n'

export type ContentHubCopy = {
  kicker: string
  title: string
  intro: string
  startFree: string
  openStudio: string
  adventureWorkbench: string
  importScript: string
  worldsSection: string
  worldsHint: string
  lorebooksSection: string
  lorebooksHint: string
  primaryLorebook: string
  independentLorebook: string
  globalLorebook: string
  unnamedLorebook: string
  boundWorlds: string
  unlistedWorld: string
  adventuresSection: string
  adventuresHint: string
  sourcesSection: string
  sourcesHint: string
  open: string
  editWorld: string
  useWorld: string
  manageBooks: string
  startAdventure: string
  manageSources: string
  manageAdventures: string
  empty: string
  refresh: string
  loading: string
  unavailable: string
  worldCount: string
  adventureCount: string
  sourceCount: string
  installed: string
  disabled: string
  unknown: string
  contentPack: string
  adventureModule: string
  noWorlds: string
  noLorebooks: string
  noAdventures: string
  noSources: string
  sourceHint: string
  builtIn: string
  user: string
  noMatches: string
  notPlayable: string
  contentAll: string
  adventuresFilter: string
  worldsFilter: string
  lorebooksFilter: string
  sourcesFilter: string
  allRulesets: string
  allAdventureSources: string
  clearFilters: string
  contentLanguageAll: string
  searchPlaceholder: string
  ruleset: string
  worldPolicy: string
  source: string
  version: string
  fixedWorld: string
  portableWorld: string
  agnosticWorld: string
  unknownRuntime: string
  chooseWorld: string
  openCreateHint: string
}

const zh: ContentHubCopy = {
  primaryLorebook: '主资料册', independentLorebook: '独立资料册', globalLorebook: '全局资料册', unnamedLorebook: '未命名资料册', boundWorlds: '所属世界：', unlistedWorld: '未列入世界库',
  kicker: 'CONTENT LIBRARY', title: '内容库', intro: '浏览世界、资料册、冒险和内容包。选择内容后，再进入对应的编辑或开局流程。',
  startFree: '开始自由游戏', openStudio: '打开创作工坊', adventureWorkbench: '冒险工作台', importScript: '导入剧本',
  worldsSection: '世界', worldsHint: '世界定义故事背景，并可提供默认规则与资料来源；资料册提供人物、地点和背景知识，开局时可按需选择。', lorebooksSection: '资料册', lorebooksHint: '资料册保存人物、地点和背景知识，可在创建游戏时逐册选择。',
  adventuresSection: '冒险包', adventuresHint: '冒险包提供可推进的剧情节点，不会替换你选择的世界和资料册。',
  sourcesSection: '内容包', sourcesHint: '内容包负责安装和提供资料；插件页只管理运行时插件。',
  open: '打开', editWorld: '编辑世界', useWorld: '用这个世界开局', manageBooks: '管理资料册', startAdventure: '开始冒险', manageSources: '管理内容包', manageAdventures: '查看全部冒险',
  empty: '还没有可显示的内容。', refresh: '刷新', loading: '正在加载…', unavailable: '暂时无法加载内容。',
  worldCount: '{count} 个世界', adventureCount: '{count} 个冒险', sourceCount: '{count} 个内容包', installed: '已安装', disabled: '已停用', unknown: '内容不可读',
  contentPack: '内容包', adventureModule: '冒险模组', noWorlds: '还没有世界。', noLorebooks: '还没有资料册。', noAdventures: '还没有冒险包。', noSources: '还没有安装内容包。',
  sourceHint: '内容包可以包含冒险、资料册、角色模板和资源；是否能开始冒险由具体冒险和规则兼容性决定。', builtIn: '内置', user: '我的创作', noMatches: '没有匹配项。', notPlayable: '暂不可用', contentAll: '全部内容', adventuresFilter: '冒险包', worldsFilter: '世界', lorebooksFilter: '资料册', sourcesFilter: '内容包', allRulesets: '全部规则', allAdventureSources: '全部冒险来源', clearFilters: '清除筛选', contentLanguageAll: '全部语言', searchPlaceholder: '搜索世界、冒险、资料册或内容包', ruleset: '规则', worldPolicy: '世界策略', source: '来源', version: '版本', fixedWorld: '使用指定世界', portableWorld: '可搭配其他世界', agnosticWorld: '不限世界', unknownRuntime: '规则需求未声明', chooseWorld: '请选择推荐世界', openCreateHint: '请进入创建游戏处理规则与世界兼容性',
}

const en: ContentHubCopy = {
  primaryLorebook: 'Primary lorebook', independentLorebook: 'Independent lorebook', globalLorebook: 'Global lorebook', unnamedLorebook: 'Untitled lorebook', boundWorlds: 'World: ', unlistedWorld: 'Not listed in the world library',
  kicker: 'CONTENT LIBRARY', title: 'Content library', intro: 'Browse worlds, lorebooks, adventures, and content packages. Open the matching workspace when you are ready to edit or play.',
  startFree: 'Start free play', openStudio: 'Open creator studio', adventureWorkbench: 'Adventure workbench', importScript: 'Import script',
  worldsSection: 'Worlds', worldsHint: 'Worlds define the setting and may provide default rules and sources; lorebooks add selectable characters, places, and background knowledge.', lorebooksSection: 'Lorebooks', lorebooksHint: 'Lorebooks hold characters, places, and background knowledge and can be selected per game.',
  adventuresSection: 'Adventure packages', adventuresHint: 'Adventure packages provide playable story nodes without replacing your world or lorebooks.',
  sourcesSection: 'Content packages', sourcesHint: 'Content packs install and provide data; the Plugins page manages runtime plugins.',
  open: 'Open', editWorld: 'Edit world', useWorld: 'Use this world', manageBooks: 'Manage lorebooks', startAdventure: 'Start adventure', manageSources: 'Manage content packages', manageAdventures: 'View all adventures',
  empty: 'There is no content to show yet.', refresh: 'Refresh', loading: 'Loading…', unavailable: 'Content could not be loaded.',
  worldCount: '{count} worlds', adventureCount: '{count} adventures', sourceCount: '{count} content packages', installed: 'Installed', disabled: 'Disabled', unknown: 'Unreadable content',
  contentPack: 'Content pack', adventureModule: 'Adventure module', noWorlds: 'No worlds yet.', noLorebooks: 'No lorebooks yet.', noAdventures: 'No adventure packages yet.', noSources: 'No content packages installed yet.',
  sourceHint: 'A content pack may contain adventures, lorebooks, character templates, and assets. Playability depends on the adventure and ruleset compatibility.', builtIn: 'Built-in', user: 'User', noMatches: 'No matching content.', notPlayable: 'Unavailable', contentAll: 'All content', adventuresFilter: 'Adventures', worldsFilter: 'Worlds', lorebooksFilter: 'Lorebooks', sourcesFilter: 'Content packages', allRulesets: 'All rulesets', allAdventureSources: 'All adventure sources', clearFilters: 'Clear filters', contentLanguageAll: 'All languages', searchPlaceholder: 'Search worlds, adventures, lorebooks, or content packages', ruleset: 'Ruleset', worldPolicy: 'World policy', source: 'Source', version: 'Version', fixedWorld: 'Uses specified world', portableWorld: 'Can be paired with other worlds', agnosticWorld: 'No world restriction', unknownRuntime: 'Ruleset requirements not declared', chooseWorld: 'Choose the recommended world', openCreateHint: 'Open Create game to resolve the required rules and world',
}

const values: Record<string, ContentHubCopy> = { 'zh-CN': zh, en, de: en, ja: en, ru: en }

export function contentHubCopy(locale: Locale | string): ContentHubCopy {
  return values[String(locale)] || en
}
