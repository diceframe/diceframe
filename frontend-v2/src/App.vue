<script setup lang="ts">
import { computed, defineAsyncComponent, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import {
  NConfigProvider, NMessageProvider, NDialogProvider, NLoadingBarProvider, NIcon,
  zhCN, enUS, deDE, ruRU, dateZhCN, dateEnUS, dateDeDE, dateRuRU,
} from 'naive-ui'
import { useTheme } from '@/composables/useTheme'
import { initializeBackgroundImages } from '@/composables/useBackgroundImages'
import { useLocale, type Locale } from '@/composables/useLocale'
import { SUPPORTED_LOCALES, localeEndonym } from '@/i18n'
import { useUpdateCheck } from '@/composables/useUpdateCheck'
import { useAnnouncements } from '@/composables/useAnnouncements'
import ThemeToggle from '@/components/ThemeToggle.vue'
import AnnouncementButton from '@/components/AnnouncementButton.vue'
import AnnouncementPanel from '@/components/AnnouncementPanel.vue'
import DevicePairingButton from '@/components/DevicePairingButton.vue'
import BrandLogo from '@/components/BrandLogo.vue'
import NaiveBridge from '@/components/common/NaiveBridge.vue'
import StartupPrivacyChoice from '@/components/common/StartupPrivacyChoice.vue'
import StartupUpdateCheck from '@/components/common/StartupUpdateCheck.vue'
import SectionWorkspaceShell from '@/components/navigation/SectionWorkspaceShell.vue'
import { readCurrentGame } from '@/stores/gameContext'
import { isPublicRoute } from '@/router'
import {
  appNavGroups,
  navGroupForRoute,
  navItem,
  primaryNavItemIds,
  type AppNavGroupId,
} from '@/navigation/appNavigation'

const route = useRoute()
const { naiveTheme, overrides, loadPluginThemes, suspendPluginTheme, restorePluginTheme } = useTheme()
const { locale, setLocale, t } = useLocale()
const { updateAvailable } = useUpdateCheck()
// naive-ui 无 ja locale；ja 界面回退英文组件语言，而非中文。de/ru 有内置 locale，直接使用。
const naiveLocale = computed(() => {
  if (locale.value === 'zh-CN') return zhCN
  if (locale.value === 'de') return deDE
  if (locale.value === 'ru') return ruRU
  return enUS
})
const naiveDateLocale = computed(() => {
  if (locale.value === 'zh-CN') return dateZhCN
  if (locale.value === 'de') return dateDeDE
  if (locale.value === 'ru') return dateRuRU
  return dateEnUS
})

const primaryItems = primaryNavItemIds.map(navItem)
function menuTo(id: string) {
  if (id !== 'play') return { name: id }
  const game = String(route.query.game || readCurrentGame() || '')
  return game ? { name: 'play', query: { game } } : { name: 'overview' }
}

function groupTo(groupId: AppNavGroupId) {
  const group = appNavGroups.find(candidate => candidate.id === groupId)!
  return menuTo(group.defaultItemId)
}

const activeKey = computed(() => (route.name as string) ?? '')
const currentGameBadge = computed(() => String(route.query.game || readCurrentGame() || '').slice(0, 8))
const currentGameText = computed(() => currentGameBadge.value ? `${t('currentTable')} ${currentGameBadge.value}` : t('lobby'))
const publicRoute = computed(() => isPublicRoute(route))
const fullscreen = publicRoute
const workspaceGroup = computed(() => navGroupForRoute(activeKey.value))
function groupIsActive(groupId: AppNavGroupId) {
  return navGroupForRoute(activeKey.value) === groupId
}

function onLocaleChange(event: Event) {
  setLocale((event.target as HTMLSelectElement).value as Locale)
}

let pluginThemesLoaded = false
async function loadOwnerPluginThemes() {
  if (publicRoute.value || pluginThemesLoaded) return
  try {
    await loadPluginThemes()
    pluginThemesLoaded = true
  } catch {
    pluginThemesLoaded = false
  }
}

const { hasUnread, load, markRead } = useAnnouncements()
const announcementOpen = ref(false)
// 配对弹窗按需加载：不打开就不会把 QrCode/配对面板带进首屏包。
const DevicePairingModal = defineAsyncComponent(
  () => import('@/features/admin/settings/DevicePairingModal.vue'),
)
const pairingOpen = ref(false)
const startupPrivacySettled = ref(false)
const startupUpdateSettled = ref(false)

function tryOpenStartupAnnouncement() {
  if (publicRoute.value || !startupUpdateSettled.value || !hasUnread.value) return
  queueMicrotask(() => {
    if (!publicRoute.value && startupUpdateSettled.value && hasUnread.value) {
      announcementOpen.value = true
    }
  })
}

function onStartupUpdateSettled() {
  startupUpdateSettled.value = true
  tryOpenStartupAnnouncement()
}

function onStartupPrivacySettled() {
  startupPrivacySettled.value = true
}

onMounted(() => {
  void initializeBackgroundImages()
  if (publicRoute.value) suspendPluginTheme()
  void loadOwnerPluginThemes()
  void load(locale.value).then(() => {
    tryOpenStartupAnnouncement()
  })
})
watch(announcementOpen, (open) => { if (!open) markRead() })
watch(locale, (next) => {
  void load(next).then(() => {
    tryOpenStartupAnnouncement()
  })
})
watch(publicRoute, (isPublic) => {
  if (isPublic) {
    startupPrivacySettled.value = false
    startupUpdateSettled.value = false
    announcementOpen.value = false
    pairingOpen.value = false
    suspendPluginTheme()
    return
  }
  restorePluginTheme()
  void loadOwnerPluginThemes()
})
</script>

<template>
  <NConfigProvider
    :class="{ 'content-height-provider': route.name === 'join' }"
    :theme="naiveTheme"
    :theme-overrides="overrides"
    :locale="naiveLocale"
    :date-locale="naiveDateLocale"
  >
    <NLoadingBarProvider>
      <NMessageProvider>
        <NDialogProvider>
          <NaiveBridge>
            <StartupPrivacyChoice v-if="!publicRoute" @settled="onStartupPrivacySettled" />
            <StartupUpdateCheck
              v-if="!publicRoute && startupPrivacySettled"
              @settled="onStartupUpdateSettled"
            />
            <RouterView v-if="fullscreen" v-slot="{ Component }">
              <ThemeToggle class="theme-toggle-floating" />
              <KeepAlive :include="['PlayView']">
                <component :is="Component" />
              </KeepAlive>
            </RouterView>

            <div v-else class="app-shell" :class="{ 'app-shell-play': activeKey === 'play' }">
              <header class="app-header">
                <div class="app-header-inner">
                  <RouterLink :to="{ name: 'overview' }" class="app-brand" :aria-label="t('navOverview')">
                    <BrandLogo :size="32" :subtitle="t('appSubtitle')" />
                  </RouterLink>

                  <nav class="desktop-nav" :aria-label="t('appSubtitle')">
                    <RouterLink
                      v-for="item in primaryItems"
                      :key="item.id"
                      :to="menuTo(item.id)"
                      class="desktop-nav-link"
                      :class="{ active: activeKey === item.id }"
                    >
                      <NIcon :component="item.icon" />
                      <span>{{ t(item.labelKey) }}</span>
                    </RouterLink>
                    <RouterLink
                      v-for="group in appNavGroups"
                      :key="group.id"
                      :to="groupTo(group.id)"
                      class="desktop-nav-link"
                      :class="{ active: groupIsActive(group.id) }"
                    >
                      <NIcon :component="group.icon" />
                      <span>{{ t(group.labelKey) }}</span>
                      <i
                        v-if="group.id === 'management' && updateAvailable"
                        class="nav-update-dot"
                        :aria-label="t('updateAvailable')"
                      />
                    </RouterLink>
                  </nav>

                  <div class="app-header-actions">
                    <AnnouncementButton @open="announcementOpen = true" />
                    <AnnouncementPanel v-model:show="announcementOpen" />
                    <!-- owner-only：publicRoute（join / 公开访问 / play?user=）没有顶栏，
                         这里再显式挡一次，避免任何公共入口拿到配对能力。 -->
                    <DevicePairingButton v-if="!publicRoute" @open="pairingOpen = true" />
                    <ThemeToggle />
                    <label class="locale-select header-locale">
                      <span>{{ t('language') }}</span>
                      <select :value="locale" @change="onLocaleChange">
                        <option v-for="code in SUPPORTED_LOCALES" :key="code" :value="code">{{ localeEndonym(code) }}</option>
                      </select>
                    </label>
                    <div class="operator-chip" :title="currentGameText">
                      <span class="operator-copy">
                        <strong>{{ currentGameText }}</strong>
                        <small><i />{{ t('online') }}</small>
                      </span>
                    </div>
                  </div>
                </div>
              </header>

              <DevicePairingModal v-if="pairingOpen && !publicRoute" @close="pairingOpen = false" />

              <main class="app-workspace">
                <RouterView v-slot="{ Component }">
                  <SectionWorkspaceShell v-if="workspaceGroup" :group-id="workspaceGroup">
                    <component :is="Component" />
                  </SectionWorkspaceShell>
                  <KeepAlive v-else :include="['PlayView']">
                    <component :is="Component" />
                  </KeepAlive>
                </RouterView>
              </main>

              <nav class="mobile-bottom-nav" :aria-label="t('appSubtitle')">
                <RouterLink
                  v-for="item in primaryItems"
                  :key="item.id"
                  :to="menuTo(item.id)"
                  :class="{ active: activeKey === item.id }"
                >
                  <span class="mobile-nav-icon">
                    <NIcon :component="item.icon" />
                  </span>
                  <small>{{ t(item.labelKey) }}</small>
                </RouterLink>
                <RouterLink
                  v-for="group in appNavGroups"
                  :key="group.id"
                  :to="groupTo(group.id)"
                  :class="{ active: groupIsActive(group.id) }"
                >
                  <span class="mobile-nav-icon">
                    <NIcon :component="group.icon" />
                    <i
                      v-if="group.id === 'management' && updateAvailable"
                      class="nav-update-dot"
                      :aria-label="t('updateAvailable')"
                    />
                  </span>
                  <small>{{ t(group.labelKey) }}</small>
                </RouterLink>
              </nav>
            </div>
          </NaiveBridge>
        </NDialogProvider>
      </NMessageProvider>
    </NLoadingBarProvider>
  </NConfigProvider>
</template>
