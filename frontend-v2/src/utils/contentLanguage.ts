import { normalizeLocale, type Locale } from '@/i18n'

export interface LanguageTaggedContent {
  language?: unknown
}

/** Built-in legacy content without a language marker is Chinese. */
export function contentLanguageOf(item: LanguageTaggedContent | null | undefined): Locale {
  return normalizeLocale(item?.language)
}

export function filterByContentLanguage<T extends LanguageTaggedContent>(
  items: T[],
  language: Locale,
): T[] {
  return items.filter(item => contentLanguageOf(item) === language)
}
