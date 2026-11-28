export type CharacterJourneyLocale = 'zh-CN' | 'en' | 'de' | 'ja' | 'ru'

export interface CharacterJourneyCopy {
  libraryTab: string
  currentTab: string
  collectionsLabel: string
  chooseSave: string
  chooseSaveHint: string
  backToSaves: string
  returnToPlay: string
  gridView: string
  listView: string
  creationUnavailable: string
  importDnd5e: string
  loading: string
  description: string
}

const copy: Record<CharacterJourneyLocale, Omit<CharacterJourneyCopy, 'loading' | 'description'>> = {
  'zh-CN': { libraryTab: '角色卡库', currentTab: '本局角色', collectionsLabel: '角色集合', chooseSave: '请选择一个存档', chooseSaveHint: '本局角色只属于具体游戏。选择存档后可查看玩家与 NPC。', backToSaves: '选择存档', returnToPlay: '回到游玩', gridView: '网格视图', listView: '列表视图', creationUnavailable: '当前规则不可用，请选择有效存档或进入独立卡库。', importDnd5e: '导入 FVTT dnd5e 角色' },
  en: { libraryTab: 'Character card library', currentTab: 'This game', collectionsLabel: 'Character collections', chooseSave: 'Choose a save', chooseSaveHint: 'This game characters belong to a specific save. Choose a save to view players and NPCs.', backToSaves: 'Choose a save', returnToPlay: 'Return to play', gridView: 'Grid view', listView: 'List view', creationUnavailable: 'The current rule is unavailable. Choose a valid save or use the standalone library.', importDnd5e: 'Import FVTT dnd5e actor' },
  de: { libraryTab: 'Charakterkartenbibliothek', currentTab: 'Dieses Spiel', collectionsLabel: 'Charakter-Sammlungen', chooseSave: 'Spielstand auswählen', chooseSaveHint: 'Charaktere gehören zu einem bestimmten Spielstand. Wähle einen Spielstand für Spieler und NSCs.', backToSaves: 'Spielstand auswählen', returnToPlay: 'Zum Spiel zurück', gridView: 'Rasteransicht', listView: 'Listenansicht', creationUnavailable: 'Das aktuelle Regelwerk ist nicht verfügbar. Wähle einen Spielstand oder die eigenständige Bibliothek.', importDnd5e: 'FVTT dnd5e-Charakter importieren' },
  ja: { libraryTab: 'キャラクターカード庫', currentTab: 'このゲーム', collectionsLabel: 'キャラクターコレクション', chooseSave: 'セーブを選択', chooseSaveHint: 'このゲームのキャラクターは特定のセーブに属します。プレイヤーとNPCを表示するにはセーブを選択してください。', backToSaves: 'セーブを選択', returnToPlay: 'プレイに戻る', gridView: 'グリッド表示', listView: 'リスト表示', creationUnavailable: '現在のルールを利用できません。有効なセーブまたは独立したカード庫を選択してください。', importDnd5e: 'FVTT dnd5e キャラを取込' },
  ru: { libraryTab: 'Библиотека карточек', currentTab: 'Эта игра', collectionsLabel: 'Коллекции персонажей', chooseSave: 'Выберите сохранение', chooseSaveHint: 'Персонажи относятся к конкретному сохранению. Выберите его, чтобы увидеть игроков и NPC.', backToSaves: 'Выбрать сохранение', returnToPlay: 'Вернуться к игре', gridView: 'Вид сеткой', listView: 'Вид списком', creationUnavailable: 'Текущие правила недоступны. Выберите сохранение или отдельную библиотеку.', importDnd5e: 'Импорт персонажа FVTT dnd5e' },
}

export function characterJourneyCopy(locale: string): CharacterJourneyCopy {
  const language = (locale in copy ? locale : 'en') as CharacterJourneyLocale
  const loading = { 'zh-CN': '正在读取角色…', en: 'Loading characters…', de: 'Charaktere werden geladen…', ja: 'キャラクターを読み込み中…', ru: 'Загрузка персонажей…' }
  const descriptions = {
    'zh-CN': '角色卡可用于新冒险；本局角色记录当前游戏中的状态。',
    en: 'Reuse cards for new adventures; manage live characters in the current game.',
    de: 'Karten für neue Abenteuer; aktive Charaktere im aktuellen Spiel.',
    ja: 'カードは新しい冒険に再利用できます。このゲームの状態は別に管理します。',
    ru: 'Карточки пригодны для новых приключений; текущие персонажи сохраняются в игре.',
  }
  return { ...copy[language], loading: loading[language], description: descriptions[language] }
}
