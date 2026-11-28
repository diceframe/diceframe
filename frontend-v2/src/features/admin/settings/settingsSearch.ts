import { isSettingsSectionAvailable, type SettingsSectionId } from '@/utils/settingsSections'

export const settingsGroupIds = ['ai', 'connection', 'security', 'general'] as const
export type SettingsGroupId = typeof settingsGroupIds[number]
const groupNames: Record<SettingsGroupId, [string, string]> = {
  general: ['General', '通用'],
  ai: ['Models and AI', '模型与 AI'], connection: ['Connection and integrations', '连接与集成'],
  security: ['Security and privacy', '安全与隐私'],
}
const chinese = (locale: string) => locale === 'zh-CN'
export function settingsGroupName(group: SettingsGroupId, locale: string): string {
  return groupNames[group][chinese(locale) ? 1 : 0]
}
export function settingsGroupFor(id: SettingsSectionId): SettingsGroupId {
  if (['general', 'game', 'appearance', 'honors', 'about'].includes(id)) return 'general'
  if (id === 'api' || id === 'models') return 'ai'
  if (id === 'security' || id === 'access') return 'security'
  return 'connection'
}
export function settingsSearchCopy(locale: string) {
  return chinese(locale)
    ? { placeholder: '搜索', clear: '清除搜索', empty: '没有找到相关设置', results: '项设置' }
    : { placeholder: 'Search', clear: 'Clear search', empty: 'No settings found', results: 'settings found' }
}
export type SettingsSearchResult = {
  id: string; section: SettingsSectionId; target: string
  label: string; description: string; path: string[]; aliases: string[]
}
type SearchEntry = Omit<SettingsSearchResult, 'label' | 'description' | 'path'> & {
  labels: [string, string]; descriptions: [string, string]
}
function entry(id: string, section: SettingsSectionId, target: string, en: string, zh: string, enHint: string, zhHint: string, aliases: string[] = []): SearchEntry {
  return { id, section, target, labels: [en, zh], descriptions: [enHint, zhHint], aliases }
}
// Search metadata only: never read configuration values, prompts or credentials.
// Community-maintained locales use English until their maintainer supplies text.
const index: SearchEntry[] = [
    entry('game-defaults', 'game', 'settings-game-defaults', 'Game defaults', '游戏默认设置', 'Push, dice reveal, quick actions, Luck timeout, narration and advancement', '孤注一掷、骰子自动揭示、快捷行动建议、幸运等待、叙事风格与成长偏好', ['push', 'dice', '骰子', 'GM', '默认设置', '对局设置', '孤注一掷', '叙事', 'quick actions', '快捷行动', '行动面板']),
  entry('sampling', 'models', 'settings-generation-options', 'Sampling parameters', '采样参数', 'Temperature, top P and repetition penalties', '随机性、采样范围、重复频率与主题惩罚', ['temperature', 'top_p', 'frequency_penalty', 'presence_penalty', '温度']),
  entry('history', 'models', 'settings-history-rounds', 'Recent history rounds', '最近历史轮数', 'Limit direct conversation history within the context budget', '上下文预算内直接携带的最近对话', ['context', '上下文', '历史条数']),
  entry('check-model', 'models', 'settings-check-model', 'Check planner model', '独立检定模型', 'Dedicated provider and model for check planning', '为检定规划单独选择服务商和模型', ['骰子', '掷骰', 'judge']),
  entry('language', 'general', 'settings-language', 'Interface language', '界面语言', 'Language used by this browser', '与右上角语言选择同步', ['system language', '系统语言']),
  entry('appearance', 'appearance', 'settings-appearance', 'Theme and backgrounds', '主题与背景', 'Color mode, skin and local backgrounds', '配色、皮肤与本地背景'),
  entry('providers', 'api', 'settings-providers', 'AI providers', 'AI 服务商', 'Shared provider endpoints and API keys', '共享服务商目录与独立密钥', ['api key', 'endpoint', '密钥']),
  entry('models', 'models', 'settings-model-routing', 'Model routing', '模型分配', 'Select a model for each capability', '为各项能力选择服务商和模型'),
  entry('embedding', 'models', 'settings-embedding', 'Memory and embeddings', '记忆与向量模型', 'Long-term memory retrieval', '长期记忆检索', ['memory', 'embedding']),
  entry('fallback', 'models', 'settings-fallback', 'Fallback models', '备用模型', 'Ordered model fallback', '主模型失败后的备用顺序'),
  entry('imagegen', 'models', 'settings-imagegen', 'Image generation', '图片生成', 'Automatic scene images, storyboard, prompts and quality', '自动场景图、分镜、提示词与画质', ['生图', 'image', 'storyboard']),
  entry('imagegen-prompts', 'models', 'settings-imagegen-prompts', 'Image prompt details', '生图提示词详情', 'Style, manual and automatic image prompts', '风格前缀、手动和自动生图提示词', ['style prefix']),
  entry('tts', 'general', 'settings-tts', 'Text to speech', '语音朗读', 'Narration voices, auto-read and reading speed', '叙事音色、自动朗读与语速', ['tts', 'voice', '朗读']),
  entry('tts-model', 'models', 'settings-tts-model', 'TTS model', 'TTS 模型', 'Speech engine, audio format, cache and personal voices', '语音引擎、音频格式、缓存与我的音色', ['tts', 'cache', '缓存', '音色']),
  entry('asr', 'models', 'settings-asr', 'Speech recognition', '语音识别', 'Microphone transcription model and timeout', '麦克风转写模型与超时', ['asr', 'transcription']),
  entry('agent', 'general', 'settings-agent', 'DF Agent', 'DF Agent', 'Assistant visibility and launcher position', '助手显示与悬浮球位置', ['助手', 'assistant', '悬浮球', '找回']),
  entry('agent-budget', 'general', 'settings-agent-budget', 'DF Agent token budget', 'DF Agent Token 预算', 'Maximum output for one Agent request', '单次 Agent 请求的输出上限', ['额度', 'tokens', 'max tokens']),
  entry('generation', 'models', 'settings-generation', 'Generation budgets', '生成参数与预算', 'Output limits and request timeout', '输出上限与模型请求超时', ['tokens', 'token', '额度']),
  entry('narrative-tokens', 'models', 'settings-narrative_max_tokens', 'Narration tokens', '叙事 Token', 'GM narration output limit', 'GM 叙事输出上限'),
  entry('character-tokens', 'models', 'settings-character_gen_max_tokens', 'Character generation tokens', '角色生成 Token', 'Character draft output limit', '车卡草稿输出上限'),
  entry('summary-tokens', 'models', 'settings-summary_max_tokens', 'Summary tokens', '摘要 Token', 'Memory summary output limit', '记忆摘要输出上限'),
  entry('brief-tokens', 'models', 'settings-brief_max_tokens', 'Brief tokens', '简报 Token', 'Player brief output limit', '玩家简报输出上限'),
  entry('analysis-tokens', 'models', 'settings-analysis_max_tokens', 'Analysis tokens', '分析 Token', 'Analysis output limit', '分析输出上限'),
  entry('text-tokens', 'models', 'settings-text_gen_max_tokens', 'Text generation tokens', '文本生成 Token', 'General text output limit', '通用文本输出上限'),
  entry('connection', 'connection', 'settings-connection', 'Backend connection', '后端连接', 'Connect a standalone frontend', '独立前端的服务地址', ['backend']),
  entry('network', 'network', 'settings-network', 'Network proxy', '网络代理', 'Proxy for provider requests', '服务商请求代理', ['proxy']),
  entry('sharing', 'sharing', 'settings-sharing', 'Share address', '分享与邀请地址', 'Public invitation address and tunnel', '公开邀请地址与隧道', ['share', 'tunnel']),
  entry('bot', 'botapi', 'settings-botapi', 'Bot API', 'Bot API', 'External chat integration token', '聊天平台集成凭据', ['机器人', 'bot token']),
  entry('security', 'security', 'settings-security', 'Connection security', '连接安全', 'HTTPS and certificates', 'HTTPS 与证书'),
  entry('privacy', 'security', 'settings-hub-privacy', 'Hub privacy', 'Hub 隐私', 'Telemetry and installation identity', '遥测选择与安装标识', ['telemetry']),
  entry('access', 'access', 'settings-access', 'Access password', '访问密码', 'Password and login history', '密码与登录记录', ['login']),
  entry('logs', 'general', 'settings-runtime-logs', 'Runtime logs', '运行日志', 'Export or clear diagnostic logs', '导出、清理诊断日志', ['log', '维护']),
  entry('test-timeout', 'general', 'settings-test-timeout', 'Connection test timeout', '连接测试超时', 'Timeout for provider connection tests', '服务商连接测试的等待时间'),
  entry('honors', 'honors', 'settings-honors', 'Honors and badges', '荣誉殿堂', 'Commemorative badges showcase and community honors wall', '纪念勋章陈列柜与社区荣誉墙', ['badge', 'badges', 'honor', 'trophy', '勋章', '徽章', '奖品', '荣誉墙', '纪念章']),
  entry('about', 'about', 'settings-update', 'Updates and about', '更新与关于', 'Version, update channel and project information', '版本、更新渠道与项目信息', ['version']),
  entry('rollback', 'general', 'settings-rollback', 'Restore previous version', '回退到上一版本', 'Restore a retained application version', '切换到已保留的上一程序版本', ['rollback', '回滚']),
]
const normalize = (value: string) => value.trim().toLocaleLowerCase()
function localize(item: SearchEntry, locale: string): SettingsSearchResult {
  const language = chinese(locale) ? 1 : 0
  return { ...item, label: item.labels[language], description: item.descriptions[language], path: [settingsGroupName(settingsGroupFor(item.section), locale), item.labels[language]] }
}
export function settingsSearchTarget(id: string, locale = 'en', standaloneFrontend = false): SettingsSearchResult | undefined {
  const item = index.find(item => item.id === id && isSettingsSectionAvailable(item.section, standaloneFrontend))
  return item ? localize(item, locale) : undefined
}
export function searchSettings(query: string, locale = 'en', standaloneFrontend = false): SettingsSearchResult[] {
  const needles = normalize(query).split(/\s+/).filter(Boolean)
  if (!needles.length) return []
  return index.filter(item => {
    if (!isSettingsSectionAvailable(item.section, standaloneFrontend)) return false
    const haystack = normalize([...item.labels, ...item.descriptions, ...item.aliases, ...groupNames[settingsGroupFor(item.section)]].join(' '))
    return needles.every(needle => haystack.includes(needle))
  }).map(item => localize(item, locale))
}

export function revealSettingTarget(target: HTMLElement | null): void {
  if (!target) return
  for (let parent = target.parentElement; parent; parent = parent.parentElement) {
    if (parent instanceof HTMLDetailsElement) parent.open = true
  }
  target.classList.add('settings-search-highlight')
  target.scrollIntoView({ behavior: 'smooth', block: 'start' })
  const focusTarget = target.matches('input, select, button, textarea') ? target : target.querySelector<HTMLElement>('input, select, button, textarea')
  focusTarget?.focus({ preventScroll: true })
  window.setTimeout(() => target.classList.remove('settings-search-highlight'), 2000)
}
