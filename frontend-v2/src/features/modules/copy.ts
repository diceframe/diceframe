import type { Locale } from '@/i18n'

export type ModuleCategory = 'adventure' | 'resource' | 'unknown'

type ModuleCopy = {
  contentProfile: string
  adventureLabel: string
  resourceLabel: string
  playableAdventures: string
  resourcePack: string
  noFixedStory: string
  resourceContributions: string
  resourceBrowse: string
  packLabel: string
  inventoryUnknown: string
  installedAdventures: string
  disabledAdventures: string
  adventureSummary: (count: number) => string
  resourceSummary: (count: number) => string
}

const copies: Record<Locale, ModuleCopy> = {
  'zh-CN': {
    contentProfile: '内容配置',
    adventureLabel: '冒险内容包', resourceLabel: '资源内容包', playableAdventures: '可玩冒险', resourcePack: '资源内容包',
    noFixedStory: '不含固定剧情；查看资料并创建自己的内容副本。', resourceContributions: '包含资料',
    resourceBrowse: '包内资料 · 不含固定剧情 · 查看资料',
    packLabel: '内容包', inventoryUnknown: '内容清单暂不可读；请检查包状态后重试。', installedAdventures: '已安装冒险', disabledAdventures: '已禁用，启用后在详情中选择冒险。',
    adventureSummary: count => `包含 ${count} 个冒险 · 在详情中选择具体冒险`,
    resourceSummary: count => `包含 ${count} 项资料 · 不含固定剧情 · 查看资料`,
  },
  en: {
    contentProfile: 'Content profile',
    adventureLabel: 'Adventure content package', resourceLabel: 'Resource content package', playableAdventures: 'Playable adventures', resourcePack: 'Resource content package',
    noFixedStory: 'No fixed story included. Browse resources and create your own copies.', resourceContributions: 'Included content',
    resourceBrowse: 'Pack content · No fixed story · Browse resources',
    packLabel: 'Content pack', inventoryUnknown: 'Content inventory is unreadable. Check the package status and retry.', installedAdventures: 'Installed adventures', disabledAdventures: 'Disabled. Enable the pack to choose an adventure in its details.',
    adventureSummary: count => `${count} adventure${count === 1 ? '' : 's'} included · Choose one in the details`,
    resourceSummary: count => `${count} resource${count === 1 ? '' : 's'} included · No fixed story · Browse resources`,
  },
  ja: {
    contentProfile: 'コンテンツプロファイル',
    adventureLabel: '冒険コンテンツパッケージ', resourceLabel: 'リソースコンテンツパッケージ', playableAdventures: 'プレイ可能な冒険', resourcePack: 'リソースコンテンツパッケージ',
    noFixedStory: '固定シナリオはありません。資料を確認して自分用のコピーを作成できます。', resourceContributions: '収録コンテンツ',
    resourceBrowse: 'パック内資料 · 固定シナリオなし · 資料を見る',
    packLabel: 'コンテンツパック', inventoryUnknown: '内容一覧を読み取れません。パックの状態を確認して再試行してください。', installedAdventures: 'インストール済みの冒険', disabledAdventures: '無効です。有効にして詳細から冒険を選択してください。',
    adventureSummary: count => `冒険 ${count} 件を収録 · 詳細で冒険を選択`,
    resourceSummary: count => `資料 ${count} 件を収録 · 固定シナリオなし · 資料を見る`,
  },
  de: {
    contentProfile: 'Inhaltsprofil',
    adventureLabel: 'Abenteuer-Inhaltspaket', resourceLabel: 'Ressourcen-Inhaltspaket', playableAdventures: 'Spielbare Abenteuer', resourcePack: 'Ressourcen-Inhaltspaket',
    noFixedStory: 'Keine feste Geschichte enthalten. Ressourcen ansehen und eigene Kopien erstellen.', resourceContributions: 'Enthaltene Inhalte',
    resourceBrowse: 'Paketinhalt · Keine feste Geschichte · Ressourcen ansehen',
    packLabel: 'Inhaltspaket', inventoryUnknown: 'Inhaltsverzeichnis nicht lesbar. Paketstatus prüfen und erneut versuchen.', installedAdventures: 'Installierte Abenteuer', disabledAdventures: 'Deaktiviert. Paket aktivieren und in den Details ein Abenteuer wählen.',
    adventureSummary: count => `${count} Abenteuer enthalten · In den Details wählen`,
    resourceSummary: count => `${count} Ressourcen enthalten · Keine feste Geschichte · Ressourcen ansehen`,
  },
  ru: {
    contentProfile: 'Профиль содержимого',
    adventureLabel: 'Пакет приключений', resourceLabel: 'Пакет ресурсов', playableAdventures: 'Доступные приключения', resourcePack: 'Пакет ресурсов',
    noFixedStory: 'Фиксированного сюжета нет. Просмотрите ресурсы и создайте свои копии.', resourceContributions: 'Содержимое пакета',
    resourceBrowse: 'Материалы пакета · Без фиксированного сюжета · Просмотреть ресурсы',
    packLabel: 'Пакет содержимого', inventoryUnknown: 'Не удалось прочитать содержимое. Проверьте пакет и повторите попытку.', installedAdventures: 'Установленные приключения', disabledAdventures: 'Отключено. Включите пакет и выберите приключение в его описании.',
    adventureSummary: count => `Приключений в пакете: ${count} · Выберите в описании`,
    resourceSummary: count => `Ресурсов в пакете: ${count} · Без фиксированного сюжета · Просмотреть ресурсы`,
  },
}

export function moduleCopy(locale: Locale): ModuleCopy {
  return copies[locale] || copies.en
}

/**
 * The category label follows the declared `content_profile`, which is the
 * install contract (an adventure module must use catalog delivery). Whether a
 * pack carries adventures is reported separately by `moduleInventory`: a plain
 * content pack may ship adventures too, and that must not relabel it.
 */
export function moduleCategory(value: { content_profile?: string | null }): ModuleCategory {
  if (value.content_profile === 'adventure-module') return 'adventure'
  if (value.content_profile === 'content-pack') return 'resource'
  return 'unknown'
}

export function moduleCategoryLabel(value: { content_profile?: string | null }, copy: ModuleCopy): string {
  const category = moduleCategory(value)
  if (category === 'adventure') return copy.adventureLabel
  if (category === 'resource') return copy.resourceLabel
  return copy.packLabel
}

export type ModuleInventory = 'adventures' | 'resources' | 'unknown'

/**
 * What the installed pack actually contains. Disabled packs still count their
 * declared adventures; an unreadable inventory is `unknown`, never "empty".
 */
export function moduleInventory(value: { adventure_count?: number | null; adventures?: unknown[]; inventory_status?: string }): ModuleInventory {
  if (value.inventory_status === 'unknown') return 'unknown'
  const adventures = Array.isArray(value.adventures) ? value.adventures.length : 0
  if (Number(value.adventure_count) > 0 || adventures > 0) return 'adventures'
  return value.adventure_count == null ? 'unknown' : 'resources'
}

const contentKindLabels: Record<string, Record<Locale, string>> = {
  npc: { 'zh-CN': 'NPC', en: 'NPCs', ja: 'NPC', de: 'NSC', ru: 'NPC' },
  item: { 'zh-CN': '道具', en: 'Items', ja: 'アイテム', de: 'Gegenstände', ru: 'Предметы' },
  spell: { 'zh-CN': '法术', en: 'Spells', ja: '呪文', de: 'Zauber', ru: 'Заклинания' },
  class: { 'zh-CN': '职业', en: 'Classes', ja: 'クラス', de: 'Klassen', ru: 'Классы' },
  character_template: { 'zh-CN': '角色模板', en: 'Character templates', ja: 'キャラクターテンプレート', de: 'Charaktervorlagen', ru: 'Шаблоны персонажей' },
  world: { 'zh-CN': '世界', en: 'Worlds', ja: 'ワールド', de: 'Welten', ru: 'Миры' },
  world_template: { 'zh-CN': '世界模板', en: 'World templates', ja: 'ワールドテンプレート', de: 'Weltenvorlagen', ru: 'Шаблоны миров' },
  lorebook: { 'zh-CN': '资料册', en: 'Lorebooks', ja: 'ロアブック', de: 'Lorebücher', ru: 'Книги знаний' },
  adventure: { 'zh-CN': '冒险资源', en: 'Adventure resources', ja: '冒険リソース', de: 'Abenteuerressourcen', ru: 'Ресурсы приключений' },
  map_definition: { 'zh-CN': '地图定义', en: 'Map definitions', ja: 'マップ定義', de: 'Kartendefinitionen', ru: 'Определения карт' },
  map_location: { 'zh-CN': '地图地点', en: 'Map locations', ja: 'マップ地点', de: 'Kartenorte', ru: 'Места на карте' },
  map_icon: { 'zh-CN': '地图图标', en: 'Map icons', ja: 'マップアイコン', de: 'Kartensymbole', ru: 'Значки карт' },
  map_scene: { 'zh-CN': '地图背景', en: 'Map scenes', ja: 'マップシーン', de: 'Kartenszenen', ru: 'Сцены карт' },
}

export function contentKindLabel(kind: string, locale: Locale): string {
  return contentKindLabels[kind]?.[locale] || kind
}

// 安装深校验的机器可读 blocker（module_validation）翻成用户能懂的话；
// 未识别的 blocker 原样透出，绝不吞掉或改写语义。
//
// 两种 blocker 都表示「缺规则运行时」：
//   ruleset_runtime_missing:fu:core>=1        —— manifest 的 requires.rulesets
//   adventure_runtime_missing:press_start:fu:core>=1 —— 冒险包自己声明的运行时
// runtime id 允许带冒号（namespace:id），所以按 `>=` 切分而不是按冒号切。
export function missingRuntimeFromBlocker(blocker: string): string {
  const value = String(blocker || '')
  const ruleset = /^ruleset_runtime_missing:(.+)>=(\d+)$/.exec(value)
  if (ruleset) return ruleset[1]
  const adventure = /^adventure_runtime_missing:([^:]+):(.+)>=(\d+)$/.exec(value)
  if (adventure) return adventure[2]
  return ''
}

export function formatModuleBlocker(blocker: string, locale: Locale): string {
  const ruleset = /^ruleset_runtime_missing:(.+)>=(\d+)$/.exec(String(blocker || ''))
  if (ruleset) {
    const [, runtimeId, minimum] = ruleset
    return locale === 'zh-CN'
      ? `这个内容包需要规则运行时 ${runtimeId}（≥ ${minimum}），请先安装并启用对应的规则插件再导入。`
      : `This content package requires ruleset runtime ${runtimeId} (>= ${minimum}). Install and enable the matching ruleset plugin first.`
  }
  const match = /adventure_runtime_missing:([^:]+):(.+)>=(\d+)/.exec(String(blocker || ''))
  if (!match) return blocker
  const [, adventure, runtimeId, minimum] = match
  return locale === 'zh-CN'
    ? `冒险「${adventure}」需要规则运行时 ${runtimeId}（≥ ${minimum}），请先安装对应的规则运行时插件再导入。`
    : `Adventure "${adventure}" requires ruleset runtime ${runtimeId} (>= ${minimum}). Install the matching ruleset-runtime plugin first.`
}

const compendiumEn = {
  open: 'Import FVTT compendium',
  title: 'Import a Foundry VTT compendium pack',
  hint: 'Import a compendium source file (JSON / JSONL / YAML) into your content library. Preview the plan, choose an action per Book, then commit. Owner only; mechanics are never exchanged and scripts are never executed.',
  limits: 'Request body limit 8 MiB. Scene definitions in the pack are reported only — import them into a game from the tactical scene page.',
  chooseFile: 'Pack source file',
  packRef: 'Import identity',
  packRefHint: 'Keeps this pack matched to the same Books when you import it again after edits. Letters, digits and _ . : - only.',
  packRefInvalid: 'The identity may only contain letters, digits and _ . : - (1–120 characters, starting with a letter or digit).',
  preview: 'Preview',
  previewing: 'Previewing…',
  commit: 'Commit import',
  committing: 'Committing…',
  rePreview: 'Preview again',
  startOver: 'Import another file',
  fileTooLarge: 'The file exceeds the 8 MiB request limit.',
  emptyFile: 'Choose a pack source file first.',
  planTitle: 'Import plan',
  planHint: 'Each Book below is one plan item. New Books are created; an existing Book needs an explicit action.',
  actionCreate: 'New Book',
  actionUpdate: 'Update',
  actionUnchanged: 'Unchanged',
  actionUnsupported: 'Unsupported',
  decisionLabel: 'Action',
  decisionPlaceholder: 'Choose an action…',
  decisionUpdate: 'Update existing',
  decisionDuplicate: 'Keep both (duplicate)',
  decisionSkip: 'Skip',
  decisionRequired: 'Every existing Book needs an explicit action before committing.',
  existingName: 'Existing',
  serverModified: 'edited on the server since the last import',
  serverUnknown: 'server edit state unknown',
  entriesAdd: 'add',
  entriesUpdate: 'update',
  entriesRemove: 'remove',
  reportTitle: 'Conversion report',
  reportHint: 'Unmapped documents stay in this report only; nothing is executed.',
  noWarnings: 'No conversion warnings.',
  receiptTitle: 'Import receipt',
  receiptCreated: 'Import committed. The Books are now in your content library.',
  receiptPartial: 'Some items were not imported. Review the rows below.',
  statusCreated: 'Created',
  statusUpdated: 'Updated',
  statusDuplicated: 'Duplicated',
  statusSkipped: 'Skipped',
  statusUnchanged: 'Unchanged',
  statusError: 'Failed',
  statusNotAttempted: 'Not attempted',
  staleTitle: 'Preview expired',
  staleHint: 'The library changed since the preview. Preview again to review the new plan.',
  warningLabels: {
    FOUNDRY_COMPENDIUM_UNSUPPORTED: 'Unmapped document (kept in the report only)',
    FOUNDRY_COMPENDIUM_SCRIPT_IGNORED: 'Executable content ignored, never run',
    FOUNDRY_COMPENDIUM_PAGE_UNSUPPORTED: 'Journal page type has no text entry',
    FOUNDRY_COMPENDIUM_EMBEDDED_DATA_REJECTED: 'Embedded data: asset dropped',
    LOREBOOK_ADAPTER: 'Book adapter note',
  } as Record<string, string>,
}
type CompendiumCopy = typeof compendiumEn
const compendiumValues: Partial<Record<Locale, CompendiumCopy>> = {
  en: compendiumEn,
  'zh-CN': {
    open: '导入 FVTT 合集包',
    title: '导入 Foundry VTT 合集包',
    hint: '把合集包源文件（JSON / JSONL / YAML）导入你的内容库。先预览计划，为每本资料册选择处理方式，再提交。仅限房主；机制数值一律不交换，脚本一律不执行。',
    limits: '请求体上限 8 MiB。包内的场景定义只出现在报告里——请到战术场景页把它们导入具体对局。',
    chooseFile: '合集包源文件',
    packRef: '导入标识',
    packRefHint: '再次导入编辑后的同一包时，用它匹配已有资料册。只能包含字母、数字和 _ . : -。',
    packRefInvalid: '标识只能包含字母、数字和 _ . : -（1–120 字符，以字母或数字开头）。',
    preview: '预览',
    previewing: '正在预览…',
    commit: '提交导入',
    committing: '正在提交…',
    rePreview: '重新预览',
    startOver: '导入另一个文件',
    fileTooLarge: '文件超过 8 MiB 请求上限。',
    emptyFile: '请先选择合集包源文件。',
    planTitle: '导入计划',
    planHint: '下面每本资料册是一个计划项。新资料册直接创建；已有资料册必须显式选择处理方式。',
    actionCreate: '新资料册',
    actionUpdate: '有更新',
    actionUnchanged: '无变化',
    actionUnsupported: '不支持',
    decisionLabel: '处理方式',
    decisionPlaceholder: '请选择处理方式…',
    decisionUpdate: '更新现有资料册',
    decisionDuplicate: '保留两份（复制）',
    decisionSkip: '跳过',
    decisionRequired: '提交前请为每本已有资料册显式选择处理方式。',
    existingName: '现有',
    serverModified: '自上次导入后已在服务端被编辑',
    serverUnknown: '服务端编辑状态未知',
    entriesAdd: '新增',
    entriesUpdate: '更新',
    entriesRemove: '移除',
    reportTitle: '转换报告',
    reportHint: '未映射的文档只保留在本报告中；不执行任何内容。',
    noWarnings: '没有转换警告。',
    receiptTitle: '导入回执',
    receiptCreated: '导入已提交，资料册已进入你的内容库。',
    receiptPartial: '部分条目未导入，请查看下方明细。',
    statusCreated: '已创建',
    statusUpdated: '已更新',
    statusDuplicated: '已复制',
    statusSkipped: '已跳过',
    statusUnchanged: '无变化',
    statusError: '失败',
    statusNotAttempted: '未尝试',
    staleTitle: '预览已过期',
    staleHint: '预览之后内容库发生了变化。请重新预览以查看新的计划。',
    warningLabels: {
      FOUNDRY_COMPENDIUM_UNSUPPORTED: '未映射文档（仅保留在报告中）',
      FOUNDRY_COMPENDIUM_SCRIPT_IGNORED: '含可执行内容，已忽略且未运行',
      FOUNDRY_COMPENDIUM_PAGE_UNSUPPORTED: '日志页类型没有文本条目',
      FOUNDRY_COMPENDIUM_EMBEDDED_DATA_REJECTED: '内嵌 data: 资产已丢弃',
      LOREBOOK_ADAPTER: '资料册适配提示',
    },
  },
}
export function compendiumImportCopy(locale: Locale): CompendiumCopy { return compendiumValues[locale] || compendiumEn }
