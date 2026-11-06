type LibraryCopy = {
  kicker: string
  title: string
  intro: string
  adventures: string
  worlds: string
  sources: string
  search: string
  allRules: string
  allSources: string
  builtin: string
  user: string
  package: string
  start: string
  details: string
  manageSources: string
  createAdventure: string
  freePlay: string
  loading: string
  emptyAdventures: string
  emptyWorlds: string
  noMatches: string
  retry: string
  source: string
  ruleset: string
  version: string
  worldPolicy: string
  fixedWorld: string
  portableWorld: string
  agnosticWorld: string
  compatibilityHint: string
  unknownRuntime: string
  backLibrary: string
  sourceTitle: string
  sourceIntro: string
  chooseRules: string
  previewWorld: string
  unavailable: string
  refresh: string
  clearFilters: string
  worldsHint: string
  detailsHint: string
  sourceHint: string
  importScript: string
  aiAdventure: string
  importPackage: string
  textReview: string
}

const en: LibraryCopy = {
  kicker: 'CONTENT LIBRARY',
  title: 'Adventures',
  intro: 'Choose an adventure, or start your own story from a world.',
  adventures: 'Adventures',
  worlds: 'Worlds',
  sources: 'Content packs',
  search: 'Search',
  allRules: 'All rulesets',
  allSources: 'All sources',
  builtin: 'Built-in',
  user: 'User',
  package: 'Package',
  start: 'Start with this adventure',
  details: 'Adventure details',
  manageSources: 'Manage content packages',
  createAdventure: 'Create adventure',
  freePlay: 'Free play',
  loading: 'Loading…',
  emptyAdventures: 'No adventures yet.',
  emptyWorlds: 'No worlds yet.',
  noMatches: 'No matches.',
  retry: 'Retry',
  source: 'Source',
  ruleset: 'Ruleset',
  version: 'Version',
  worldPolicy: 'World policy',
  fixedWorld: 'Uses specified world',
  portableWorld: 'Can be paired with other worlds',
  agnosticWorld: 'No world restriction',
  compatibilityHint: 'Check the ruleset and world when starting.',
  unknownRuntime: 'Ruleset requirements not declared',
  backLibrary: 'Back to library',
  sourceTitle: 'Content package management',
  sourceIntro: 'Install and update content packages, and view resources and versions.',
  chooseRules: 'Choose rules',
  previewWorld: 'Preview world',
  unavailable: 'Unavailable',
  refresh: 'Refresh',
  clearFilters: 'Clear filters',
  worldsHint: 'Start with a world and create your own story.',
  detailsHint: 'Review the adventure, rules, world policy, and source before starting.',
  sourceHint: 'Packages provide adventures or reference material; whether you can start depends on the adventure.',
  importScript: 'Import script', aiAdventure: 'AI adventure', importPackage: 'Import package', textReview: 'Review text',
}

const values: Record<string, LibraryCopy> = {
  en,
  'zh-CN': {
    kicker: '内容库', title: '冒险', intro: '挑选一段冒险，或从一个世界开始自己的故事。',
    adventures: '冒险', worlds: '世界与资料', sources: '内容包', search: '搜索', allRules: '全部规则', allSources: '全部来源',
    builtin: '内置', user: '我的创作', package: '已安装内容包', start: '开始冒险', details: '冒险简介', manageSources: '管理内容包',
    createAdventure: '创作冒险', freePlay: '自由游玩', loading: '正在加载…', emptyAdventures: '还没有冒险。', emptyWorlds: '还没有世界。',
    noMatches: '没有匹配项。', retry: '重试', source: '来源', ruleset: '规则', version: '版本', worldPolicy: '世界策略',
    fixedWorld: '使用指定世界', portableWorld: '可搭配其他世界', agnosticWorld: '不限世界', compatibilityHint: '开局时核对规则与世界',
    unknownRuntime: '规则需求未声明', backLibrary: '返回内容库', sourceTitle: '内容包管理', sourceIntro: '安装和更新内容包，查看资源与版本。',
    chooseRules: '选择规则', previewWorld: '预览世界', unavailable: '不可用', refresh: '刷新', clearFilters: '清除筛选',
    worldsHint: '从一个世界开始，创作自己的故事。', detailsHint: '开局前查看冒险、规则、世界策略和来源。',
    sourceHint: '安装包提供冒险或资料；是否可开局由具体冒险决定。', importScript: '导入剧本', aiAdventure: 'AI 创作', importPackage: '导入冒险包', textReview: '文字审核',
  },
  ja: {
    kicker: 'コンテンツライブラリ', title: '冒険', intro: '冒険を選ぶか、世界から自分の物語を始めます。',
    adventures: '冒険', worlds: '世界', sources: 'コンテンツパック', search: '検索', allRules: 'すべてのルール', allSources: 'すべてのソース',
    builtin: '内蔵', user: 'ユーザー', package: 'パッケージ', start: 'この冒険で始める', details: '冒険の概要', manageSources: 'ソースを管理',
    createAdventure: '冒険を作成', freePlay: 'フリープレイ', loading: '読み込み中…', emptyAdventures: '冒険はまだありません。', emptyWorlds: '世界はまだありません。',
    noMatches: '一致する項目がありません。', retry: '再試行', source: 'ソース', ruleset: 'ルール', version: 'バージョン', worldPolicy: '世界ポリシー',
    fixedWorld: '指定された世界を使用', portableWorld: '他の世界と組み合わせ可能', agnosticWorld: '世界の指定なし', compatibilityHint: '開始時にルールと世界を確認',
    unknownRuntime: 'ルール要件未宣言', backLibrary: 'ライブラリに戻る', sourceTitle: 'コンテンツソース管理', sourceIntro: 'コンテンツパッケージをインストール・更新し、リソースとバージョンを確認します。',
    chooseRules: 'ルールを選択', previewWorld: '世界をプレビュー', unavailable: '利用できません', refresh: '更新', clearFilters: '絞り込みを解除',
    worldsHint: '世界から始めて、自分の物語を作ります。', detailsHint: '開始前に冒険、ルール、世界ポリシー、ソースを確認します。',
    sourceHint: 'パッケージは冒険または資料を提供します。開始できるかは各冒険で決まります。', importScript: '脚本をインポート', aiAdventure: 'AI で作成', importPackage: '冒険パッケージをインポート', textReview: 'テキストを確認',
  },
  de: {
    kicker: 'INHALTSBIBLIOTHEK', title: 'Abenteuer', intro: 'Wähle ein Abenteuer oder beginne deine eigene Geschichte in einer Welt.',
    adventures: 'Abenteuer', worlds: 'Welten', sources: 'Inhaltspakete', search: 'Suchen', allRules: 'Alle Regelsysteme', allSources: 'Alle Quellen',
    builtin: 'Integriert', user: 'Benutzer', package: 'Paket', start: 'Mit diesem Abenteuer starten', details: 'Abenteuerdetails', manageSources: 'Quellen verwalten',
    createAdventure: 'Abenteuer erstellen', freePlay: 'Freies Spiel', loading: 'Wird geladen…', emptyAdventures: 'Noch keine Abenteuer.', emptyWorlds: 'Noch keine Welten.',
    noMatches: 'Keine Treffer.', retry: 'Erneut versuchen', source: 'Quelle', ruleset: 'Regelsystem', version: 'Version', worldPolicy: 'Weltvorgabe',
    fixedWorld: 'Angegebene Welt verwenden', portableWorld: 'Mit anderen Welten kombinierbar', agnosticWorld: 'Keine Weltvorgabe', compatibilityHint: 'Beim Start Regelsystem und Welt prüfen',
    unknownRuntime: 'Regelanforderungen nicht angegeben', backLibrary: 'Zur Bibliothek', sourceTitle: 'Inhaltsquellen verwalten', sourceIntro: 'Inhaltspakete installieren und aktualisieren sowie Ressourcen und Versionen ansehen.',
    chooseRules: 'Regeln wählen', previewWorld: 'Weltvorschau', unavailable: 'Nicht verfügbar', refresh: 'Aktualisieren', clearFilters: 'Filter löschen',
    worldsHint: 'Beginne mit einer Welt und erzähle deine eigene Geschichte.', detailsHint: 'Abenteuer, Regeln, Weltvorgabe und Quelle vor dem Start prüfen.',
    sourceHint: 'Pakete liefern Abenteuer oder Referenzmaterial; ob ein Start möglich ist, hängt vom jeweiligen Abenteuer ab.', importScript: 'Skript importieren', aiAdventure: 'KI-Abenteuer', importPackage: 'Paket importieren', textReview: 'Text prüfen',
  },
  ru: {
    kicker: 'БИБЛИОТЕКА КОНТЕНТА', title: 'Приключения', intro: 'Выберите приключение или начните свою историю с мира.',
    adventures: 'Приключения', worlds: 'Миры', sources: 'Пакеты контента', search: 'Поиск', allRules: 'Все правила', allSources: 'Все источники',
    builtin: 'Встроенное', user: 'Пользовательское', package: 'Пакет', start: 'Начать с этого приключения', details: 'Описание приключения', manageSources: 'Управление источниками',
    createAdventure: 'Создать приключение', freePlay: 'Свободная игра', loading: 'Загрузка…', emptyAdventures: 'Приключений пока нет.', emptyWorlds: 'Миров пока нет.',
    noMatches: 'Совпадений нет.', retry: 'Повторить', source: 'Источник', ruleset: 'Набор правил', version: 'Версия', worldPolicy: 'Политика мира',
    fixedWorld: 'Использовать указанный мир', portableWorld: 'Можно сочетать с другими мирами', agnosticWorld: 'Мир не ограничен', compatibilityHint: 'При старте проверьте правила и мир',
    unknownRuntime: 'Требования к правилам не заявлены', backLibrary: 'Назад в библиотеку', sourceTitle: 'Управление источниками контента', sourceIntro: 'Устанавливайте и обновляйте пакеты контента, просматривайте ресурсы и версии.',
    chooseRules: 'Выбрать правила', previewWorld: 'Предпросмотр мира', unavailable: 'Недоступно', refresh: 'Обновить', clearFilters: 'Сбросить фильтры',
    worldsHint: 'Начните с мира и создайте свою историю.', detailsHint: 'Перед стартом проверьте приключение, правила, политику мира и источник.',
    sourceHint: 'Пакеты предоставляют приключения или справочные материалы; возможность старта определяется конкретным приключением.', importScript: 'Импортировать сценарий', aiAdventure: 'Приключение с ИИ', importPackage: 'Импортировать пакет', textReview: 'Проверить текст',
  },
}

export function libraryCopy(locale: string): LibraryCopy {
  return values[locale] || en
}

export function duration(minutes: number): string {
  return `${minutes} min`
}

export function count(count: number): string {
  return String(count)
}
