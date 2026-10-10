import { i18n, localeChain, localeEndonym, normalizeLocale, type Locale } from '@/i18n'

export interface LanguageTaggedContent {
  language?: unknown
}

/** Built-in legacy content without a language marker is Chinese. */
export function contentLanguageOf(item: LanguageTaggedContent | null | undefined): Locale {
  return normalizeLocale(item?.language)
}

/**
 * Pick the best-matching content for a locale, falling back the way the backend
 * does: requested locale, then English, then Chinese. An exact-match filter left
 * every locale beyond zh-CN/en staring at an empty list, because no built-in
 * world, rule or template is tagged ja, de or ru.
 */
export function filterByContentLanguage<T extends LanguageTaggedContent>(
  items: T[],
  language: Locale,
): T[] {
  for (const candidate of localeChain(language)) {
    const matched = items.filter(item => contentLanguageOf(item) === candidate)
    if (matched.length) return matched
  }
  return []
}

/**
 * Display name of a content language, for pickers and "world · language · N
 * entries" lines. Uses the translated name where the catalogue has a key and the
 * locale's own name otherwise, keeping the project's existing convention.
 */
export function localeLabel(locale: Locale): string {
  if (locale === 'zh-CN') return i18n.global.t('chinese')
  if (locale === 'en') return i18n.global.t('english')
  if (locale === 'de') return i18n.global.t('german')
  return localeEndonym(locale)
}
