<script setup lang="ts">
import { h, onBeforeUnmount, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import type { DialogOptions, DialogReactive } from 'naive-ui'
import { errorMessage } from '@/api/client'
import { useUpdateCheck } from '@/composables/useUpdateCheck'
import { getDialog } from '@/composables/useNaiveBridge'
import { useLocale } from '@/composables/useLocale'
import { selectReleaseNotes } from '@/utils/releaseNotes'

const route = useRoute()
const router = useRouter()
const emit = defineEmits<{ settled: [] }>()
const { checkForUpdates } = useUpdateCheck()
const { t, locale } = useLocale()
// Show at most once per process; cross-version dedupe is handled below.
let notified = false
let activeDialog: DialogReactive | null = null
let settled = false
const STARTUP_CHECK_TIMEOUT_MS = 5000

function settle() {
  if (settled) return
  settled = true
  emit('settled')
}

function shouldSkipCurrentRoute(): boolean {
  // Never interrupt an active workflow: login, joining, character creation,
  // and the table itself. Updates remain available from Settings -> About.
  if (route.name === 'login' || route.name === 'join' || route.name === 'create' || route.name === 'play') return true
  return false
}

// Show once per version so refreshes do not keep interrupting the user.
function alreadyNotified(version: string): boolean {
  try {
    return Boolean(version) && localStorage.getItem('trpg_update_notified_version') === version
  } catch {
    return false
  }
}

function markNotified(version: string) {
  try {
    if (version) localStorage.setItem('trpg_update_notified_version', version)
  } catch {
    /* Fall back to process-level dedupe when localStorage is unavailable. */
  }
}

function showUpdateDialog(version: string, body: string) {
  const dialog = getDialog()
  const cfg: DialogOptions = {
    title: t('updateDialogTitle', { version }),
    content: () => h('div', { class: 'update-dialog-body' }, [
      h('p', { class: 'muted' }, t('updateDialogBody', { version })),
      body
        ? h('pre', { class: 'update-dialog-notes' }, body)
        : null,
    ]),
    positiveText: t('goToUpdateSettings'),
    negativeText: t('laterSay'),
    maskClosable: true,
    closeOnEsc: true,
    positiveButtonProps: { type: 'primary' },
    negativeButtonProps: { secondary: true },
    onPositiveClick: () => {
      settle()
      return router.push({
        name: 'settings',
        query: { section: 'about', focus: 'update' },
      })
    },
    onNegativeClick: settle,
    onClose: settle,
  }
  activeDialog = dialog.info(cfg)
}

async function checkOnce() {
  if (shouldSkipCurrentRoute()) {
    activeDialog?.destroy()
    activeDialog = null
    settle()
    return
  }
  try {
    const result = await Promise.race([
      checkForUpdates(),
      new Promise<null>(resolve => setTimeout(() => resolve(null), STARTUP_CHECK_TIMEOUT_MS)),
    ])
    if (!notified && result?.ok && result.update_available && result.latest) {
      const version = result.latest.tag_name || result.latest.version || t('newVersion')
      if (alreadyNotified(result.latest.version || '')) {
        settle()
        return
      }
      notified = true
      markNotified(result.latest.version || '')
      showUpdateDialog(
        version,
        selectReleaseNotes(String(result.latest.body || ''), locale.value).text,
      )
      return
    }
    settle()
  } catch (e: unknown) {
    // Keep automatic check failures quiet for users, but leave a debug trail.
    console.warn('DiceFrame update check failed:', errorMessage(e))
    settle()
  }
}

onMounted(checkOnce)
watch(() => [route.name, route.query.user, route.query.share], checkOnce)
onBeforeUnmount(() => activeDialog?.destroy())
</script>

<template>
  <span v-if="false" />
</template>
