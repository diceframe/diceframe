import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { LOCALE_STORAGE_KEY, normalizeLocale, type Locale, type MessageKey } from '@/i18n'

export type { Locale }

export function useLocale() {
  const composer = useI18n<{ message: Record<MessageKey, string> }, Locale>({ useScope: 'global' })

  function setLocale(next: Locale) {
    composer.locale.value = normalizeLocale(next)
    localStorage.setItem(LOCALE_STORAGE_KEY, composer.locale.value)
  }

  function t(key: MessageKey, params?: Record<string, string | number>): string {
    return composer.t(key, params || {})
  }

  const isEnglish = computed(() => composer.locale.value === 'en')

  return { locale: composer.locale, setLocale, t, isEnglish }
}
