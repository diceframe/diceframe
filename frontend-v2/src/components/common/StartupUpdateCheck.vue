<script setup lang="ts">
import { h, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import type { DialogOptions } from 'naive-ui'
import { errorMessage } from '@/api/client'
import { useUpdateCheck } from '@/composables/useUpdateCheck'
import { getDialog } from '@/composables/useNaiveBridge'

const route = useRoute()
const { checkForUpdates } = useUpdateCheck()
// 本次进程内只弹一次（跨版本仍会弹：见下方 version 去重）
let notified = false

function shouldSkipCurrentRoute(): boolean {
  if (route.name === 'login' || route.name === 'join') return true
  if (route.name === 'play' && (route.query.user || route.query.share)) return true
  return false
}

function openReleaseUrl(url: string) {
  if (url) window.open(url, '_blank', 'noopener')
}

// 同一版本只弹一次：避免每次启动/刷新都打扰用户；出现更新的版本时再弹。
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
    /* localStorage 不可用时退化为本次会话内不重复弹 */
  }
}

function showUpdateDialog(version: string, body: string, url: string) {
  const dialog = getDialog()
  const cfg: DialogOptions = {
    title: `发现新版本 ${version}`,
    content: () => h('div', { class: 'update-dialog-body' }, [
      h('p', { class: 'muted' }, `DiceFrame ${version} 已发布，建议升级后再继续跑团。`),
      body
        ? h('pre', { class: 'update-dialog-notes' }, body)
        : null,
    ]),
    positiveText: '打开发布页',
    negativeText: '稍后说',
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
      const version = result.latest.tag_name || result.latest.version || '新版本'
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
    // 自动检查失败不打扰用户，但保留日志便于排查
    console.warn('DiceFrame update check failed:', errorMessage(e))
  }
}

onMounted(checkOnce)
watch(() => [route.name, route.query.user, route.query.share], checkOnce)
</script>

<template></template>
