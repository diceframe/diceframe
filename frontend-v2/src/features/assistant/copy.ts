import type { Locale } from '@/i18n'

export interface AssistantCopy {
  create: string
  start: string
  review: string
  save: string
  cancel: string
  approve: string
  pending: string
  status: string
  target: string
  name: string
  text: string
  blockers: string
  tool: string
  details: string
  statusIdle: string
  statusApproval: string
  approvalInvalid: string
  success: string
  failure: string
  openGame: string
  partial: string
  edited: string
  targetWorld: string
  targetLorebook: string
  targetCharacter: string
  targetAdventure: string
  kindWorld: string
  kindLorebook: string
  kindCharacter: string
  kindAdventure: string
  saveDraftPrompt: string
  expandWindow: string
  shrinkWindow: string
  workspaceTitle: string
  workspaceHint: string
}

const zh: AssistantCopy = { create: '创作内容', start: '开始一局', review: '重新审查', save: '保存草稿', cancel: '取消', approve: '确认执行', pending: '等待确认', status: '状态', target: '目标', name: '名称', text: '正文', blockers: '需要处理', tool: '操作', details: '查看详情', statusIdle: '就绪', statusApproval: '等待确认', approvalInvalid: '确认已过期，需要重新生成', success: '已完成', failure: '失败', openGame: '打开游戏', partial: '部分完成', edited: '已修改', targetWorld: '世界', targetLorebook: '世界书', targetCharacter: '角色模板', targetAdventure: '冒险包', kindWorld: '世界', kindLorebook: '世界书', kindCharacter: '角色模板', kindAdventure: '冒险包', saveDraftPrompt: '保存草稿 {id}', expandWindow: '最大化窗口', shrinkWindow: '还原窗口', workspaceTitle: '任务工作区', workspaceHint: '草稿、待确认操作和执行结果会显示在这里。' }
const copy: Record<Locale, AssistantCopy> = {
  'zh-CN': zh,
  en: { create: 'Create content', start: 'Start a game', review: 'Review again', save: 'Save draft', cancel: 'Cancel', approve: 'Confirm', pending: 'Waiting for confirmation', status: 'Status', target: 'Target', name: 'Name', text: 'Text', blockers: 'Needs attention', tool: 'Action', details: 'Details', statusIdle: 'Ready', statusApproval: 'Waiting for confirmation', approvalInvalid: 'Confirmation expired; generate a new plan', success: 'Completed', failure: 'Failed', openGame: 'Open game', partial: 'Partially completed', edited: 'Edited', targetWorld: 'World', targetLorebook: 'Lorebook', targetCharacter: 'Character template', targetAdventure: 'Adventure', kindWorld: 'World', kindLorebook: 'Lorebook', kindCharacter: 'Character template', kindAdventure: 'Adventure', saveDraftPrompt: 'Save draft {id}', expandWindow: 'Maximize window', shrinkWindow: 'Restore window', workspaceTitle: 'Task workspace', workspaceHint: 'Drafts, pending actions and execution results appear here.' },
  de: { create: 'Inhalt erstellen', start: 'Spiel starten', review: 'Erneut prüfen', save: 'Entwurf speichern', cancel: 'Abbrechen', approve: 'Bestätigen', pending: 'Bestätigung ausstehend', status: 'Status', target: 'Ziel', name: 'Name', text: 'Text', blockers: 'Zu bearbeiten', tool: 'Aktion', details: 'Details', statusIdle: 'Bereit', statusApproval: 'Bestätigung ausstehend', approvalInvalid: 'Bestätigung abgelaufen; neuen Plan erstellen', success: 'Abgeschlossen', failure: 'Fehlgeschlagen', openGame: 'Spiel öffnen', partial: 'Teilweise abgeschlossen', edited: 'Bearbeitet', targetWorld: 'Welt', targetLorebook: 'Weltenbuch', targetCharacter: 'Charaktervorlage', targetAdventure: 'Abenteuerpaket', kindWorld: 'Welt', kindLorebook: 'Weltenbuch', kindCharacter: 'Charaktervorlage', kindAdventure: 'Abenteuerpaket', saveDraftPrompt: 'Entwurf {id} speichern', expandWindow: 'Fenster maximieren', shrinkWindow: 'Fenster wiederherstellen', workspaceTitle: 'Aufgabenbereich', workspaceHint: 'Entwürfe, ausstehende Aktionen und Ergebnisse erscheinen hier.' },
  ja: { create: 'コンテンツを作成', start: 'ゲームを開始', review: '再審査', save: '下書きを保存', cancel: 'キャンセル', approve: '実行を確認', pending: '確認待ち', status: '状態', target: '対象', name: '名前', text: '本文', blockers: '要対応', tool: '操作', details: '詳細', statusIdle: '準備完了', statusApproval: '確認待ち', approvalInvalid: '確認の期限切れです。新しい計画を生成してください', success: '完了', failure: '失敗', openGame: 'ゲームを開く', partial: '一部完了', edited: '編集済み', targetWorld: '世界', targetLorebook: '世界書', targetCharacter: 'キャラクターテンプレート', targetAdventure: '冒険パック', kindWorld: '世界', kindLorebook: '世界書', kindCharacter: 'キャラクターテンプレート', kindAdventure: '冒険パック', saveDraftPrompt: '下書き {id} を保存', expandWindow: 'ウィンドウを最大化', shrinkWindow: 'ウィンドウを元に戻す', workspaceTitle: 'タスクスペース', workspaceHint: '下書き、確認待ちの操作、実行結果がここに表示されます。' },
  ru: { create: 'Создать контент', start: 'Начать игру', review: 'Проверить снова', save: 'Сохранить черновик', cancel: 'Отмена', approve: 'Подтвердить', pending: 'Ожидается подтверждение', status: 'Статус', target: 'Цель', name: 'Название', text: 'Текст', blockers: 'Требует внимания', tool: 'Действие', details: 'Подробности', statusIdle: 'Готово', statusApproval: 'Ожидается подтверждение', approvalInvalid: 'Подтверждение истекло; создайте новый план', success: 'Выполнено', failure: 'Ошибка', openGame: 'Открыть игру', partial: 'Выполнено частично', edited: 'Изменено', targetWorld: 'Мир', targetLorebook: 'Книга мира', targetCharacter: 'Шаблон персонажа', targetAdventure: 'Приключенческий пакет', kindWorld: 'Мир', kindLorebook: 'Книга мира', kindCharacter: 'Шаблон персонажа', kindAdventure: 'Приключенческий пакет', saveDraftPrompt: 'Сохранить черновик {id}', expandWindow: 'Развернуть окно', shrinkWindow: 'Восстановить окно', workspaceTitle: 'Рабочая область задач', workspaceHint: 'Здесь отображаются черновики, ожидающие действия и результаты.' },
}

export function assistantCopy(locale: string): AssistantCopy {
  return copy[(locale in copy ? locale : 'en') as Locale]
}

const TOOL_LABELS: Record<Locale, Record<string, string>> = {
  'zh-CN': { read_catalog: '查看目录', read_game: '查看游戏', navigate: '打开页面', draft_content: '生成内容草稿', save_content: '保存内容', create_game: '创建游戏', set_player_control: '切换角色控制', submit_action: '提交行动', create_rule: '生成规则', generate_image: '生成图片', invoke_plugin_tool: '运行插件工具', set_plugin_enabled: '设置插件状态', diagnose_runtime: '诊断运行日志', read_content: '查看内容', edit_world: '编辑世界', write_lorebook_entries: '写入世界书条目', edit_character_card: '编辑角色卡', creator_project_read: '查看工程目录', creator_project_inspect: '查看工程目录', creator_project_read_file: '读取工程文件', creator_project_update: '修改工程文件', creator_project_validate: '预检工程', creator_project_preview: '预览工程', creator_project_build: '构建 .dfplugin', creator_project_submission: '准备投稿回执' },
  en: { read_catalog: 'Read catalog', read_game: 'Read game', navigate: 'Open page', draft_content: 'Draft content', save_content: 'Save content', create_game: 'Create game', set_player_control: 'Change character control', submit_action: 'Submit action', create_rule: 'Create rules', generate_image: 'Generate image', invoke_plugin_tool: 'Run plugin tool', set_plugin_enabled: 'Set plugin state', diagnose_runtime: 'Diagnose runtime', read_content: 'Read content', edit_world: 'Edit world', write_lorebook_entries: 'Write lorebook entries', edit_character_card: 'Edit character card', creator_project_read: 'Inspect project files', creator_project_inspect: 'Inspect project files', creator_project_read_file: 'Read project file', creator_project_update: 'Edit project files', creator_project_validate: 'Validate project', creator_project_preview: 'Preview project', creator_project_build: 'Build .dfplugin', creator_project_submission: 'Prepare submission receipt' },
  de: { read_catalog: 'Katalog öffnen', read_game: 'Spiel öffnen', navigate: 'Seite öffnen', draft_content: 'Inhalt entwerfen', save_content: 'Inhalt speichern', create_game: 'Spiel erstellen', set_player_control: 'Charaktersteuerung ändern', submit_action: 'Aktion senden', create_rule: 'Regeln erstellen', generate_image: 'Bild erzeugen', invoke_plugin_tool: 'Plugin-Werkzeug ausführen', set_plugin_enabled: 'Plugin-Status ändern', diagnose_runtime: 'Laufzeit diagnostizieren', read_content: 'Inhalt öffnen', edit_world: 'Welt bearbeiten', write_lorebook_entries: 'Weltenbuch-Einträge schreiben', edit_character_card: 'Charakterkarte bearbeiten' },
  ja: { read_catalog: 'カタログを確認', read_game: 'ゲームを確認', navigate: 'ページを開く', draft_content: 'コンテンツを下書き', save_content: 'コンテンツを保存', create_game: 'ゲームを作成', set_player_control: 'キャラクター操作を変更', submit_action: '行動を送信', create_rule: 'ルールを作成', generate_image: '画像を生成', invoke_plugin_tool: 'プラグインツールを実行', set_plugin_enabled: 'プラグイン状態を変更', diagnose_runtime: '実行ログを診断', read_content: 'コンテンツを確認', edit_world: '世界を編集', write_lorebook_entries: '世界書エントリを作成', edit_character_card: 'キャラクターカードを編集' },
  ru: { read_catalog: 'Открыть каталог', read_game: 'Открыть игру', navigate: 'Открыть страницу', draft_content: 'Создать черновик', save_content: 'Сохранить контент', create_game: 'Создать игру', set_player_control: 'Изменить управление персонажем', submit_action: 'Отправить действие', create_rule: 'Создать правила', generate_image: 'Создать изображение', invoke_plugin_tool: 'Запустить инструмент плагина', set_plugin_enabled: 'Изменить состояние плагина', diagnose_runtime: 'Диагностировать журнал', read_content: 'Открыть контент', edit_world: 'Изменить мир', write_lorebook_entries: 'Записать записи в книгу мира', edit_character_card: 'Изменить карту персонажа' },
}

export function assistantToolLabel(locale: string, tool: unknown): string {
  const selected = locale in TOOL_LABELS ? locale as Locale : 'en'
  const key = String(tool || '')
  return TOOL_LABELS[selected][key] || key
}

export function assistantTargetLabel(locale: string, target: unknown): string {
  const selected = assistantCopy(locale)
  const labels: Record<string, string> = { world: selected.targetWorld, lorebook: selected.targetLorebook, character_template: selected.targetCharacter, adventure: selected.targetAdventure }
  return labels[String(target || '').trim().toLowerCase()] || ''
}

export function assistantKindLabel(locale: string, kind: unknown): string {
  const selected = assistantCopy(locale)
  const labels: Record<string, string> = { world: selected.kindWorld, lorebook: selected.kindLorebook, character: selected.kindCharacter, character_template: selected.kindCharacter, adventure: selected.kindAdventure }
  return labels[String(kind || '').trim().toLowerCase()] || selected.create
}

export function assistantSaveDraftPrompt(locale: string, draftId: string): string {
  return assistantCopy(locale).saveDraftPrompt.replace('{id}', draftId)
}
