import type { Locale } from '@/i18n'

export interface AppTourCopy {
  /** Label of the replay button on the overview page. */
  replay: string
  next: string
  prev: string
  done: string
  /** driver.js progress template; keeps its own `{{current}}` / `{{total}}` tokens. */
  progress: string
  welcomeTitle: string
  welcomeBody: string
  overviewTitle: string
  overviewBody: string
  playTitle: string
  playBody: string
  charactersTitle: string
  charactersBody: string
  contentTitle: string
  contentBody: string
  managementTitle: string
  managementBody: string
  assistantTitle: string
  assistantBody: string
  createTitle: string
  createBody: string
}

const zhCN: AppTourCopy = {
  replay: '界面导览',
  next: '下一步',
  prev: '上一步',
  done: '知道了',
  progress: '{{current}} / {{total}}',
  welcomeTitle: '欢迎来到 DiceFrame',
  welcomeBody: '用半分钟认识界面：导航在哪、去哪配置模型、怎么开始第一局。随时按 Esc 可以跳过，之后也能在总览页重新打开。',
  overviewTitle: '总览',
  overviewBody: '你的存档中心。继续上次的冒险，或从这里新建一局。',
  playTitle: '游玩',
  playBody: '进入当前选中的游戏，在这里提交行动、掷骰、阅读叙事。',
  charactersTitle: '角色',
  charactersBody: '创建和管理角色卡，也可以导入已有的角色。',
  contentTitle: '内容',
  contentBody: '冒险、世界、模块、传说书与规则都收在这里，既能使用现成内容，也能自己创作。',
  managementTitle: '管理',
  managementBody: '第一次使用请先到「设置」添加 API 服务商并选择主模型；日志、记忆与插件也在这里。',
  assistantTitle: '助手',
  assistantBody: '遇到不懂的设置或规则，可以随时问助手。点击展开，再点一次收起。',
  createTitle: '开始第一局',
  createBody: '模型准备好后，从这里创建一局游戏。想再看一遍导览，点标题旁的「界面导览」。',
}

const values: Record<Locale, AppTourCopy> = {
  'zh-CN': zhCN,
  en: {
    replay: 'Interface tour', next: 'Next', prev: 'Back', done: 'Got it', progress: '{{current}} / {{total}}',
    welcomeTitle: 'Welcome to DiceFrame',
    welcomeBody: 'Take half a minute to see where things are, where to set up a model, and how to start your first game. Press Esc to skip at any time; you can reopen this tour from the overview page.',
    overviewTitle: 'Overview', overviewBody: 'Your save archive. Continue a previous adventure or start a new one from here.',
    playTitle: 'Play', playBody: 'Enter the selected game to submit actions, roll dice and read the story.',
    charactersTitle: 'Characters', charactersBody: 'Create and manage character sheets, or import existing ones.',
    contentTitle: 'Content', contentBody: 'Adventures, worlds, modules, lorebooks and rules live here, whether you use ready-made content or create your own.',
    managementTitle: 'Management', managementBody: 'On first use, open Settings to add an API provider and choose a main model. Logs, memory and plugins are here too.',
    assistantTitle: 'Assistant', assistantBody: 'Ask the assistant whenever a setting or rule is unclear. Click to open it, click again to collapse.',
    createTitle: 'Start your first game', createBody: 'Once a model is ready, create a game from here. To see this tour again, use “Interface tour” next to the title.',
  },
  ja: {
    replay: '画面ツアー', next: '次へ', prev: '戻る', done: '了解', progress: '{{current}} / {{total}}',
    welcomeTitle: 'DiceFrame へようこそ',
    welcomeBody: '30 秒で画面の見方、モデルの設定場所、最初のゲームの始め方を確認します。Esc でいつでもスキップでき、あとで総覧ページから再表示できます。',
    overviewTitle: '総覧', overviewBody: 'セーブの一覧です。前回の冒険を続けるか、ここから新しく始めます。',
    playTitle: 'プレイ', playBody: '選択中のゲームに入り、行動の送信、ダイス、物語の閲覧を行います。',
    charactersTitle: 'キャラクター', charactersBody: 'キャラクターシートの作成・管理や、既存のキャラクターのインポートができます。',
    contentTitle: 'コンテンツ', contentBody: '冒険、世界、モジュール、ロアブック、ルールはここにまとまっています。既存のコンテンツも自作も使えます。',
    managementTitle: '管理', managementBody: '初回は「設定」で API サービスを追加し、メインモデルを選んでください。ログ、記憶、プラグインもここにあります。',
    assistantTitle: 'アシスタント', assistantBody: '設定やルールで迷ったらいつでも聞けます。クリックで開き、もう一度クリックで閉じます。',
    createTitle: '最初のゲームを始める', createBody: 'モデルの準備ができたら、ここからゲームを作成します。このツアーをもう一度見るには、タイトル横の「画面ツアー」を押してください。',
  },
  de: {
    replay: 'Oberflächen-Tour', next: 'Weiter', prev: 'Zurück', done: 'Verstanden', progress: '{{current}} / {{total}}',
    welcomeTitle: 'Willkommen bei DiceFrame',
    welcomeBody: 'In einer halben Minute siehst du, wo alles liegt, wo du ein Modell einrichtest und wie du dein erstes Spiel startest. Mit Esc kannst du jederzeit überspringen; auf der Übersichtsseite lässt sich die Tour erneut öffnen.',
    overviewTitle: 'Übersicht', overviewBody: 'Dein Spielstandarchiv. Setze ein früheres Abenteuer fort oder starte hier ein neues.',
    playTitle: 'Spielen', playBody: 'Betritt das ausgewählte Spiel, sende Aktionen, würfle und lies die Geschichte.',
    charactersTitle: 'Figuren', charactersBody: 'Erstelle und verwalte Charakterbögen oder importiere vorhandene.',
    contentTitle: 'Inhalte', contentBody: 'Abenteuer, Welten, Module, Lorebooks und Regeln findest du hier – fertige Inhalte nutzen oder selbst erschaffen.',
    managementTitle: 'Verwaltung', managementBody: 'Öffne beim ersten Mal die Einstellungen, füge einen API-Anbieter hinzu und wähle ein Hauptmodell. Protokolle, Gedächtnis und Plugins sind ebenfalls hier.',
    assistantTitle: 'Assistent', assistantBody: 'Frag den Assistenten, wenn eine Einstellung oder Regel unklar ist. Klick öffnet ihn, ein weiterer Klick klappt ihn ein.',
    createTitle: 'Erstes Spiel starten', createBody: 'Sobald ein Modell bereit ist, erstellst du hier ein Spiel. Die Tour erneut ansehen: „Oberflächen-Tour“ neben dem Titel.',
  },
  ru: {
    replay: 'Обзор интерфейса', next: 'Далее', prev: 'Назад', done: 'Понятно', progress: '{{current}} / {{total}}',
    welcomeTitle: 'Добро пожаловать в DiceFrame',
    welcomeBody: 'За полминуты вы узнаете, где что находится, где настроить модель и как начать первую игру. Esc пропускает обзор в любой момент; открыть его снова можно на странице обзора.',
    overviewTitle: 'Обзор', overviewBody: 'Ваш архив сохранений. Продолжите прежнее приключение или начните новое отсюда.',
    playTitle: 'Игра', playBody: 'Войдите в выбранную игру, отправляйте действия, бросайте кубики и читайте повествование.',
    charactersTitle: 'Персонажи', charactersBody: 'Создавайте и ведите листы персонажей или импортируйте готовые.',
    contentTitle: 'Контент', contentBody: 'Приключения, миры, модули, лорбуки и правила собраны здесь: можно взять готовое или создать своё.',
    managementTitle: 'Управление', managementBody: 'При первом запуске откройте «Настройки», добавьте API-провайдера и выберите основную модель. Здесь же журналы, память и плагины.',
    assistantTitle: 'Помощник', assistantBody: 'Спросите помощника, если настройка или правило непонятны. Нажмите, чтобы открыть, и ещё раз, чтобы свернуть.',
    createTitle: 'Начните первую игру', createBody: 'Когда модель готова, создайте игру отсюда. Чтобы пройти обзор снова, нажмите «Обзор интерфейса» рядом с заголовком.',
  },
}

export function appTourCopy(locale: string): AppTourCopy {
  return values[(locale in values ? locale : 'en') as Locale]
}
