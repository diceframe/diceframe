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
    adventureLabel: '冒险模组', resourceLabel: '资源包', playableAdventures: '可玩冒险', resourcePack: '资源包',
    noFixedStory: '不含固定剧情；查看资料并创建自己的内容副本。', resourceContributions: '包含资料',
    resourceBrowse: '包内资料 · 不含固定剧情 · 查看资料',
    packLabel: '内容包', inventoryUnknown: '内容清单暂不可读；请检查包状态后重试。', installedAdventures: '已安装冒险', disabledAdventures: '已禁用，启用后在详情中选择冒险。',
    adventureSummary: count => `包含 ${count} 个冒险 · 在详情中选择具体冒险`,
    resourceSummary: count => `包含 ${count} 项资料 · 不含固定剧情 · 查看资料`,
  },
  en: {
    contentProfile: 'Content profile',
    adventureLabel: 'Adventure module', resourceLabel: 'Resource pack', playableAdventures: 'Playable adventures', resourcePack: 'Resource pack',
    noFixedStory: 'No fixed story included. Browse resources and create your own copies.', resourceContributions: 'Included content',
    resourceBrowse: 'Pack content · No fixed story · Browse resources',
    packLabel: 'Content pack', inventoryUnknown: 'Content inventory is unreadable. Check the package status and retry.', installedAdventures: 'Installed adventures', disabledAdventures: 'Disabled. Enable the pack to choose an adventure in its details.',
    adventureSummary: count => `${count} adventure${count === 1 ? '' : 's'} included · Choose one in the details`,
    resourceSummary: count => `${count} resource${count === 1 ? '' : 's'} included · No fixed story · Browse resources`,
  },
  ja: {
    contentProfile: 'コンテンツプロファイル',
    adventureLabel: '冒険コンテンツパック', resourceLabel: 'コンテンツパック', playableAdventures: 'プレイ可能な冒険', resourcePack: 'コンテンツパック',
    noFixedStory: '固定シナリオはありません。資料を確認して自分用のコピーを作成できます。', resourceContributions: '収録コンテンツ',
    resourceBrowse: 'パック内資料 · 固定シナリオなし · 資料を見る',
    packLabel: 'コンテンツパック', inventoryUnknown: '内容一覧を読み取れません。パックの状態を確認して再試行してください。', installedAdventures: 'インストール済みの冒険', disabledAdventures: '無効です。有効にして詳細から冒険を選択してください。',
    adventureSummary: count => `冒険 ${count} 件を収録 · 詳細で冒険を選択`,
    resourceSummary: count => `資料 ${count} 件を収録 · 固定シナリオなし · 資料を見る`,
  },
  de: {
    contentProfile: 'Inhaltsprofil',
    adventureLabel: 'Abenteuer-Inhaltspaket', resourceLabel: 'Inhaltspaket', playableAdventures: 'Spielbare Abenteuer', resourcePack: 'Inhaltspaket',
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

export function moduleCategory(value: { adventure_count?: number | null; adventures?: unknown[]; inventory_status?: string }): ModuleCategory {
  if (value.inventory_status === 'unknown') return 'unknown'
  const adventures = Array.isArray(value.adventures) ? value.adventures.length : 0
  if (Number(value.adventure_count) > 0 || adventures > 0) return 'adventure'
  return value.adventure_count == null ? 'unknown' : 'resource'
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
