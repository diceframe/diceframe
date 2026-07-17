<script setup lang="ts">
import { computed, h, onMounted } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import {
  NConfigProvider, NMessageProvider, NDialogProvider, NLoadingBarProvider,
  NLayout, NLayoutHeader, NLayoutContent, NMenu, NIcon,
  zhCN, enUS, dateZhCN, dateEnUS,
  type MenuOption,
} from 'naive-ui'
import {
  HomeOutline, GameControllerOutline, PersonOutline, BookOutline,
  CloudOutline, DocumentTextOutline, OptionsOutline, SettingsOutline,
} from '@vicons/ionicons5'
import { useTheme } from '@/composables/useTheme'
import { useLocale, type Locale } from '@/composables/useLocale'
import { useUpdateCheck } from '@/composables/useUpdateCheck'
import ThemeToggle from '@/components/ThemeToggle.vue'
import BrandLogo from '@/components/BrandLogo.vue'
import NaiveBridge from '@/components/common/NaiveBridge.vue'
import StartupUpdateCheck from '@/components/common/StartupUpdateCheck.vue'
import { readCurrentGame } from '@/stores/gameContext'

const route = useRoute()
const { naiveTheme, overrides, loadPluginThemes } = useTheme()
const { locale, setLocale, t } = useLocale()
const { updateAvailable } = useUpdateCheck()
const naiveLocale = computed(() => locale.value === 'en' ? enUS : zhCN)
const naiveDateLocale = computed(() => locale.value === 'en' ? dateEnUS : dateZhCN)

const items = [
  { id: 'overview', labelKey: 'navOverview', icon: HomeOutline },
  { id: 'play', labelKey: 'navPlay', icon: GameControllerOutline },
  { id: 'characters', labelKey: 'navCharacters', icon: PersonOutline },
  { id: 'lorebook', labelKey: 'navLorebook', icon: BookOutline },
  { id: 'memory', labelKey: 'navMemory', icon: CloudOutline },
  { id: 'logs', labelKey: 'navLogs', icon: DocumentTextOutline },
  { id: 'rules', labelKey: 'navRules', icon: OptionsOutline },
  { id: 'settings', labelKey: 'navSettings', icon: SettingsOutline },
] as const

function menuTo(id: string) {
  if (id !== 'play') return { name: id }
  const game = String(route.query.game || readCurrentGame() || '')
  return game ? { name: 'play', query: { game } } : { name: 'overview' }
}

const menuOptions = computed<MenuOption[]>(() => items.map((n) => ({
  key: n.id,
  icon: () => h(NIcon, null, { default: () => h(n.icon) }),
  label: () => h(RouterLink, { to: menuTo(n.id) }, {
    default: () => h('span', { class: 'nav-label' }, [
      t(n.labelKey),
      n.id === 'settings' && updateAvailable.value ? h('span', { class: 'nav-update-dot', 'aria-label': t('updateAvailable') }) : null,
    ]),
  }),
})))

const activeKey = computed(() => (route.name as string) ?? '')
const currentGameBadge = computed(() => String(route.query.game || readCurrentGame() || '').slice(0, 8))
const currentGameText = computed(() => currentGameBadge.value ? `${t('currentTable')} ${currentGameBadge.value}` : t('lobby'))
const fullscreen = computed(
  () => route.name === 'login' || route.name === 'join' || (route.name === 'play' && !!(route.query.user || route.query.share)),
)

function onLocaleChange(event: Event) {
  setLocale((event.target as HTMLSelectElement).value as Locale)
}

// Let desktop mouse wheels scroll the top nav horizontally in narrow windows.
function onTopMenuWheel(e: WheelEvent) {
  const el = e.currentTarget as HTMLElement
  if (!el) return
  // Let the page keep normal vertical scrolling when there is no horizontal overflow.
  if (el.scrollWidth <= el.clientWidth) return
  el.scrollLeft += e.deltaY || e.deltaX
  e.preventDefault()
}

onMounted(() => {
  loadPluginThemes().catch(() => undefined)
})
</script>
<template>
  <NConfigProvider :theme="naiveTheme" :theme-overrides="overrides" :locale="naiveLocale" :date-locale="naiveDateLocale">
    <NLoadingBarProvider>
      <NMessageProvider>
        <NDialogProvider>
          <NaiveBridge>
            <StartupUpdateCheck />
            <RouterView v-if="fullscreen" v-slot="{ Component }">
              <ThemeToggle class="theme-toggle-floating" />
              <KeepAlive :include="['PlayView']">
                <component :is="Component" />
              </KeepAlive>
            </RouterView>
            <NLayout v-else>
              <NLayoutHeader bordered class="top-nav">
                <div class="top-brand">
                  <BrandLogo :size="30" :subtitle="t('appSubtitle')" />
               </div>
                <div class="top-menu" @wheel="onTopMenuWheel">
                 <NMenu mode="horizontal" :options="menuOptions" :value="activeKey" />
                </div>
                <div class="top-right">
                  <ThemeToggle />
                  <label class="locale-select">
                    <span>{{ t('language') }}</span>
                    <select :value="locale" @change="onLocaleChange">
                      <option value="zh-CN">{{ t('chinese') }}</option>
                      <option value="en">{{ t('english') }}</option>
                    </select>
                  </label>
                  <span class="app-version">{{ currentGameText }}</span>
                </div>
              </NLayoutHeader>
              <NLayoutContent :native-scrollbar="false" class="workspace">
                <RouterView v-slot="{ Component }">
                  <KeepAlive :include="['PlayView']">
                    <component :is="Component" />
                  </KeepAlive>
                </RouterView>
              </NLayoutContent>
            </NLayout>
          </NaiveBridge>
        </NDialogProvider>
      </NMessageProvider>
    </NLoadingBarProvider>
  </NConfigProvider>
</template>
