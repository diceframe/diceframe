import type { Locale } from '@/i18n'

export interface GettingStartedCopy {
  kicker: string
  close: string
  reopen: string
  title: string
  description: string
  start: string
  friends: string
  collapse: string
  expand: string
  soloTitle: string
  soloIntro: string
  settings: string
  settingsModels: string
  create: string
  loading: string
  configured: string
  notConfigured: string
  configError: string
  retry: string
  testConnection: string
  mediaHint: string
  friendsTitle: string
  friendsIntro: string
  hostInvite: string
  playerJoin: string
  joinFriends: string
  stepOne: string
  stepOneTitle: string
  stepTwo: string
  stepTwoTitle: string
  stepThree: string
  stepThreeTitle: string
  actionInstruction: string
  actionExample: string
  resultHint: string
  resultExplanation: string
  readyLabel: string
}

const zhCN: GettingStartedCopy = {
  kicker: '新手指引',
  close: '关闭新手指引',
  reopen: '新手引导',
  title: '从第一回合开始',
  description: '用几步熟悉 DiceFrame：先准备模型，再创建一局，最后提交一次行动。',
  start: '带我开始',
  friends: '加入好友',
  collapse: '收起新手指引',
  expand: '展开新手指引',
  soloTitle: '单人起步',
  soloIntro: '添加服务商，在主模型中选择模型，测试连接，然后创建一局游戏。',
  settings: 'API 服务商设置',
  settingsModels: '模型分配设置',
  create: '创建一局游戏',
  loading: '正在读取配置…',
  configured: '已配置',
  notConfigured: '待配置',
  configError: '暂时无法读取配置，请稍后重试。',
  retry: '重试',
  testConnection: '配置后请在设置中测试连接。',
  mediaHint: '图片和语音可以稍后再开启。',
  friendsTitle: '加入好友',
  friendsIntro: '房主进入游玩页后点击“邀请好友”生成邀请链接；玩家可直接打开好友发来的邀请链接，或回到首页点击“加入好友”粘贴链接。玩家无需自己配置模型。',
  hostInvite: '房主进入游玩页，点击“邀请好友”并发送生成的邀请链接',
  playerJoin: '玩家直接打开邀请链接，或在首页“加入好友”中粘贴链接',
  joinFriends: '加入好友',
  stepOne: '第一步',
  stepOneTitle: '准备模型',
  stepTwo: '第二步',
  stepTwoTitle: '创建游戏',
  stepThree: '第三步',
  stepThreeTitle: '提交行动',
  actionInstruction: '进入游戏后，在底部行动框描述你想做的事。例如：',
  actionExample: '我观察门边的灰尘，寻找最近有人经过的痕迹。',
  resultHint: '写清楚想做什么、对谁做、打算怎么做。',
  resultExplanation: '系统会根据你的行动继续叙事；需要检定时，结果会在回合中说明。',
  readyLabel: '模型状态',
}

const values: Record<Locale, GettingStartedCopy> = {
  'zh-CN': zhCN,
  en: {
    kicker: 'Getting started', close: 'Close getting started', reopen: 'Getting started', title: 'Start with your first round',
    description: 'Get familiar with DiceFrame in a few steps: prepare a model, create a game, then submit an action.',
    start: 'Show me how', friends: 'Join friends', collapse: 'Collapse getting started', expand: 'Expand getting started',
    soloTitle: 'Start solo', soloIntro: 'Add a provider, choose a model in Main model, test the connection, then create a game.', settings: 'API provider settings', settingsModels: 'Model assignment settings', create: 'Create a game', loading: 'Reading configuration…', configured: 'Configured', notConfigured: 'Needs setup',
    configError: 'The configuration could not be read. Please try again.', retry: 'Retry', testConnection: 'After configuring it, test the connection in Settings.', mediaHint: 'Images and voice can be enabled later.',
    friendsTitle: 'Join friends', friendsIntro: 'The host enters the play page and clicks “Invite friends” to create an invitation link. Guests can open the link directly, or paste it into “Join friends” on the homepage. Guests do not need to configure a model.',
    hostInvite: 'The host enters the play page, clicks “Invite friends”, and sends the generated invitation link', playerJoin: 'Guests open the invitation link directly, or paste it into “Join friends” on the homepage', joinFriends: 'Join friends',
    stepOne: 'Step one', stepOneTitle: 'Prepare a model', stepTwo: 'Step two', stepTwoTitle: 'Create a game', stepThree: 'Step three', stepThreeTitle: 'Submit an action',
    actionInstruction: 'Once in the game, describe what you want to do in the action box at the bottom. For example:', actionExample: 'I inspect the dust by the door for signs that someone passed recently.', resultHint: 'Say what you want to do, who or what you act on, and how.', resultExplanation: 'DiceFrame continues the story from your action. If a check is needed, its result is explained in the round.', readyLabel: 'Model status',
  },
  ja: {
    kicker: 'はじめに', close: 'ガイドを閉じる', reopen: 'はじめに', title: '最初のラウンドを始める', description: 'モデルを準備し、ゲームを作成して、行動を送るまでを数ステップで案内します。', start: '始め方を見る', friends: 'Join friends', collapse: 'ガイドを閉じる', expand: 'ガイドを開く',
    soloTitle: '一人で始める', soloIntro: 'サービスを追加し、メインモデルでモデルを選び、接続をテストしてからゲームを作成します。', settings: 'API サービス設定', settingsModels: 'モデル割り当て設定', create: 'ゲームを作成', loading: '設定を読み込んでいます…', configured: '設定済み', notConfigured: '未設定', configError: '設定を読み込めませんでした。もう一度お試しください。', retry: '再試行', testConnection: '設定後、設定画面で接続をテストしてください。', mediaHint: '画像と音声は後から有効にできます。',
    friendsTitle: 'Join friends', friendsIntro: 'The host enters the play page and clicks “Invite friends” to create an invitation link. Guests can open the link directly, or paste it into “Join friends” on the homepage. Guests do not need to configure a model.', hostInvite: 'The host enters the play page, clicks “Invite friends”, and sends the generated invitation link', playerJoin: 'Guests open the invitation link directly, or paste it into “Join friends” on the homepage', joinFriends: 'Join friends',
    stepOne: 'ステップ 1', stepOneTitle: 'モデルを準備', stepTwo: 'ステップ 2', stepTwoTitle: 'ゲームを作成', stepThree: 'ステップ 3', stepThreeTitle: '行動を送る', actionInstruction: 'ゲームに入ったら、画面下部の行動欄にしたいことを書きます。例：', actionExample: 'ドアのそばのほこりを調べ、最近誰かが通った跡を探す。', resultHint: '何をしたいか、誰や何を対象に、どう行うかを書きましょう。', resultExplanation: '行動をもとに物語が続きます。判定が必要な場合はラウンド内で結果が説明されます。', readyLabel: 'モデルの状態',
  },
  de: {
    kicker: 'Erste Schritte', close: 'Erste Schritte schließen', reopen: 'Erste Schritte', title: 'Mit deiner ersten Runde beginnen', description: 'Lerne DiceFrame in wenigen Schritten kennen: Modell vorbereiten, Spiel erstellen und eine Aktion senden.', start: 'Anleitung starten', friends: 'Join friends', collapse: 'Anleitung einklappen', expand: 'Anleitung ausklappen',
    soloTitle: 'Allein starten', soloIntro: 'Füge einen Anbieter hinzu, wähle ein Modell unter Hauptmodell, teste die Verbindung und erstelle danach ein Spiel.', settings: 'API-Anbieter', settingsModels: 'Modellzuordnung', create: 'Spiel erstellen', loading: 'Konfiguration wird gelesen…', configured: 'Konfiguriert', notConfigured: 'Einrichtung nötig', configError: 'Die Konfiguration konnte nicht gelesen werden. Bitte versuche es erneut.', retry: 'Erneut versuchen', testConnection: 'Teste die Verbindung nach der Einrichtung in den Einstellungen.', mediaHint: 'Bilder und Sprache können später aktiviert werden.',
    friendsTitle: 'Join friends', friendsIntro: 'The host enters the play page and clicks “Invite friends” to create an invitation link. Guests can open the link directly, or paste it into “Join friends” on the homepage. Guests do not need to configure a model.', hostInvite: 'The host enters the play page, clicks “Invite friends”, and sends the generated invitation link', playerJoin: 'Guests open the invitation link directly, or paste it into “Join friends” on the homepage', joinFriends: 'Join friends',
    stepOne: 'Schritt 1', stepOneTitle: 'Modell vorbereiten', stepTwo: 'Schritt 2', stepTwoTitle: 'Spiel erstellen', stepThree: 'Schritt 3', stepThreeTitle: 'Aktion senden', actionInstruction: 'Sobald du im Spiel bist, beschreibe im Aktionsfeld unten, was du tun möchtest. Zum Beispiel:', actionExample: 'Ich untersuche den Staub an der Tür nach frischen Spuren.', resultHint: 'Beschreibe, was du tun möchtest, auf wen oder was du einwirkst und wie.', resultExplanation: 'DiceFrame führt die Geschichte aus deiner Aktion weiter. Falls eine Probe nötig ist, wird ihr Ergebnis in der Runde erklärt.', readyLabel: 'Modellstatus',
  },
  ru: {
    kicker: 'Начало работы', close: 'Закрыть руководство', reopen: 'Начало работы', title: 'Начните с первого раунда', description: 'Познакомьтесь с DiceFrame в несколько шагов: настройте модель, создайте игру и отправьте действие.', start: 'Показать, как начать', friends: 'Join friends', collapse: 'Свернуть руководство', expand: 'Развернуть руководство',
    soloTitle: 'Начать одному', soloIntro: 'Добавьте провайдера, выберите модель в разделе основной модели, проверьте соединение, затем создайте игру.', settings: 'Настройки API-провайдера', settingsModels: 'Распределение моделей', create: 'Создать игру', loading: 'Читаем конфигурацию…', configured: 'Настроено', notConfigured: 'Требуется настройка', configError: 'Не удалось прочитать конфигурацию. Попробуйте ещё раз.', retry: 'Повторить', testConnection: 'После настройки проверьте соединение в настройках.', mediaHint: 'Изображения и голос можно включить позже.',
    friendsTitle: 'Join friends', friendsIntro: 'The host enters the play page and clicks “Invite friends” to create an invitation link. Guests can open the link directly, or paste it into “Join friends” on the homepage. Guests do not need to configure a model.', hostInvite: 'The host enters the play page, clicks “Invite friends”, and sends the generated invitation link', playerJoin: 'Guests open the invitation link directly, or paste it into “Join friends” on the homepage', joinFriends: 'Join friends',
    stepOne: 'Шаг 1', stepOneTitle: 'Подготовить модель', stepTwo: 'Шаг 2', stepTwoTitle: 'Создать игру', stepThree: 'Шаг 3', stepThreeTitle: 'Отправить действие', actionInstruction: 'Войдя в игру, опишите в поле действия внизу, что хотите сделать. Например:', actionExample: 'Я осматриваю пыль у двери в поисках свежих следов.', resultHint: 'Укажите, что хотите сделать, с кем или чем взаимодействуете и каким способом.', resultExplanation: 'DiceFrame продолжит историю с учётом действия. Если нужна проверка, её результат будет объяснён в раунде.', readyLabel: 'Состояние модели',
  },
}

export function gettingStartedCopy(locale: string): GettingStartedCopy {
  return values[(locale in values ? locale : 'en') as Locale]
}
