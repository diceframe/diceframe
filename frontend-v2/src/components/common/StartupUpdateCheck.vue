<script setup lang="ts">
import { h, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import type { DialogOptions } from 'naive-ui'
import { errorMessage } from '@/api/client'
import { useUpdateCheck } from '@/composables/useUpdateCheck'
import { getDialog } from '@/composables/useNaiveBridge'
import { useLocale } from '@/composables/useLocale'

const route = useRoute()
const { checkForUpdates } = useUpdateCheck()
const { t } = useLocale()
// Show at most once per process; cross-version dedupe is handled below.
let notified = false

function shouldSkipCurrentRoute(): boolean {
  if (route.name === 'login' || route.name === 'join') return true
  if (route.name === 'play' && (route.query.user || route.query.share)) return true
  return false
}

function openReleaseUrl(url: string) {
  if (url) window.open(url, '_blank', 'noopener')
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

function showUpdateDialog(version: string, body: string, url: string) {
  const dialog = getDialog()
  const cfg: DialogOptions = {
    title: t('updateDialogTitle', { version }),
    content: () => h('div', { class: 'update-dialog-body' }, [
      h('p', { class: 'muted' }, t('updateDialogBody', { version })),
      body
        ? h('pre', { class: 'update-dialog-notes' }, body)
        : null,
    ]),
    positiveText: t('openReleasePage'),
    negativeText: t('laterSay'),
    maskClosable: true,
    closeOnEsc: true,
    positiveButtonProps: { type: 'primary' },
    negativeButtonProps: { secondary: true },
    onPositiveClick: () => openReleaseUrl(url),
  }
  dialog.info(cfg)
}

async function checkOnce() {
  if (shouldSkipCurrentRoute()) return
  try {
    const result = await checkForUpdates()
    if (!notified && result?.ok && result.update_available && result.latest) {
      const version = result.latest.tag_name || result.latest.version || t('newVersion')
      if (alreadyNotified(result.latest.version || '')) return
      notified = true
      markNotified(result.latest.version || '')
      showUpdateDialog(
        version,
        String(result.latest.body || ''),
        result.latest.html_url || result.release_url || result.releases_url || result.source_url || '',
      )
    }
  } catch (e: unknown) {
    // Keep automatic check failures quiet for users, but leave a debug trail.
    console.warn('DiceFrame update check failed:', errorMessage(e))
  }
}

onMounted(checkOnce)
watch(() => [route.name, route.query.user, route.query.share], checkOnce)
</script>

<template></template>
