import { computed, ref } from 'vue'
import { darkTheme, type GlobalTheme } from 'naive-ui'
import { darkThemeOverrides, lightThemeOverrides } from '@/styles/theme'

export type ThemeName = 'dark' | 'light'

const STORAGE_KEY = 'trpg_theme'

function readInitial(): ThemeName {
  if (typeof localStorage === 'undefined') return 'dark'
  return localStorage.getItem(STORAGE_KEY) === 'light' ? 'light' : 'dark'
}

const current = ref<ThemeName>(readInitial())

function applyBodyClass(name: ThemeName) {
  document.body.classList.toggle('light', name === 'light')
}
if (typeof document !== 'undefined') applyBodyClass(current.value)

export function useTheme() {
  const naiveTheme = computed<GlobalTheme | null>(() => (current.value === 'dark' ? darkTheme : null))
  const overrides = computed(() => (current.value === 'dark' ? darkThemeOverrides : lightThemeOverrides))
  function apply(name: ThemeName) {
    current.value = name
    applyBodyClass(name)
    localStorage.setItem(STORAGE_KEY, name)
  }
  function toggle() {
    apply(current.value === 'dark' ? 'light' : 'dark')
  }
  return { current, naiveTheme, overrides, apply, toggle }
}
