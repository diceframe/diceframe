import { createI18n } from 'vue-i18n'
import { en } from './messages/en'
import { ja } from './messages/ja'
import { de } from './messages/de'
import { ru } from './messages/ru'
import { zhCN } from './messages/zh-CN'

export type Locale = 'zh-CN' | 'en' | 'ja' | 'de' | 'ru'
export type MessageKey = keyof typeof zhCN

export const LOCALE_STORAGE_KEY = 'diceframe_locale'

export const messages = {
  'zh-CN': zhCN,
  en,
  ja,
  de,
  ru,
} as const

export function normalizeLocale(value: unknown): Locale {
  const text = String(value || '').toLowerCase()
  if (text === 'ja' || text.startsWith('ja-') || text === '日本語') return 'ja'
  if (text === 'de' || text.startsWith('de-') || text === 'german' || text === 'deutsch') return 'de'
  if (text === 'ru' || text.startsWith('ru-') || text === 'russian' || text === 'русский') return 'ru'
  return text === 'en' || text.startsWith('en-') ? 'en' : 'zh-CN'
}

/** Every locale the app ships, in picker order. Render language menus from this
 * list rather than hardcoding options: de and ja were previously missing from
 * most pickers because each one carried its own copy. */
export const SUPPORTED_LOCALES = Object.keys(messages) as Locale[]

/** A locale's own name. Used by UI-language switchers, where a reader looks for
 * their language written the way they write it. */
const LOCALE_ENDONYMS: Record<Locale, string> = {
  'zh-CN': '简体中文',
  en: 'English',
  ja: '日本語',
  de: 'Deutsch',
  ru: 'Русский',
}

export function localeEndonym(locale: Locale): string {
  return LOCALE_ENDONYMS[locale] || locale
}

/**
 * Fallback order mirroring the backend `localized_text` chain in
 * src/engine/language.py: the requested locale, then English, then Chinese.
 * Without this, any locale beyond zh-CN/en silently lands on Chinese content.
 */
export function localeChain(locale: Locale): Locale[] {
  const chain: Locale[] = [locale]
  for (const fallback of ['en', 'zh-CN'] as const) {
    if (!chain.includes(fallback)) chain.push(fallback)
  }
  return chain
}

/** BCP-47 tag handed to the speech backend for a UI locale. */
const SPEECH_TAGS: Record<Locale, string> = {
  'zh-CN': 'zh-CN',
  en: 'en-US',
  ja: 'ja-JP',
  de: 'de-DE',
  ru: 'ru-RU',
}

export function speechLocaleTag(locale: Locale): string {
  return SPEECH_TAGS[locale] || 'en-US'
}

function initialLocale(): Locale {
  if (typeof localStorage !== 'undefined') {
    const stored = localStorage.getItem(LOCALE_STORAGE_KEY)
    if (stored) return normalizeLocale(stored)
  }
  if (typeof navigator !== 'undefined') {
    const preferred = navigator.languages?.find(Boolean) || navigator.language
    if (preferred) return normalizeLocale(preferred)
  }
  return 'zh-CN'
}

export const i18n = createI18n({
  legacy: false,
  locale: initialLocale(),
  // 缺失语言（如 ja/de 尚未翻译的文案）回退英文，而非中文。
  fallbackLocale: 'en',
  messages,
  missingWarn: false,
  fallbackWarn: false,
})
