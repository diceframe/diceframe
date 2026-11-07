<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useLocale } from '@/composables/useLocale'
import { contentPackSubmissionUrl } from './submission'

const props = defineProps<{ pluginId: string; version: string; fileName: string }>()
const { t } = useLocale()

const STORAGE_KEY = 'diceframe:submission-repository-url'
const repoUrl = ref(readStored())
const copied = ref('')

function readStored(): string {
  try {
    return localStorage.getItem(STORAGE_KEY) || ''
  } catch {
    return ''
  }
}

watch(repoUrl, value => {
  try {
    if (value.trim()) localStorage.setItem(STORAGE_KEY, value.trim())
    else localStorage.removeItem(STORAGE_KEY)
  } catch {
    /* 隐私模式下 localStorage 不可用：只在本次会话里记住 */
  }
})

/** 从粘贴的地址里取出 owner/repo；不合法的地址不解锁后面的步骤。 */
const repository = computed(() => {
  const match = repoUrl.value.trim().match(/^(?:https?:\/\/)?(?:www\.)?github\.com\/([\w.-]+)\/([\w.-]+?)(?:\.git)?\/?$/i)
  if (!match) return null
  return { owner: match[1], repo: match[2] }
})

const repoName = computed(() => props.pluginId)
const newRepositoryUrl = computed(() => `https://github.com/new?name=${encodeURIComponent(repoName.value)}`)
const uploadUrl = computed(() => (repository.value ? `https://github.com/${repository.value.owner}/${repository.value.repo}/upload/main` : ''))
const releaseUrl = computed(() => (repository.value
  ? `https://github.com/${repository.value.owner}/${repository.value.repo}/releases/new?tag=${encodeURIComponent(`v${props.version}`)}`
  : ''))
const submitUrl = computed(() => contentPackSubmissionUrl(props.pluginId, repoUrl.value))

async function copy(text: string) {
  try {
    await navigator.clipboard.writeText(text)
    copied.value = text
    window.setTimeout(() => { if (copied.value === text) copied.value = '' }, 1600)
  } catch {
    copied.value = ''
  }
}

// .dfplugin 其实是 zip 改了后缀，直接右键解压会被系统拒绝，得先改名或用 7-Zip。
const isDfplugin = computed(() => props.fileName.toLowerCase().endsWith('.dfplugin'))

const steps = computed(() => [
  {
    id: 'prepare',
    title: t('submitWizardStepFile'),
    hint: isDfplugin.value
      ? t('submitWizardStepFileHintDfplugin', { file: props.fileName })
      : t('submitWizardStepFileHint', { file: props.fileName }),
    action: null as { label: string; href: string } | null,
    ready: true,
  },
  {
    id: 'repository',
    title: t('submitWizardStepRepo'),
    hint: t('submitWizardStepRepoHint', { id: props.pluginId }),
    action: { label: t('submitWizardOpen'), href: newRepositoryUrl.value },
    ready: true,
  },
  {
    id: 'upload',
    title: t('submitWizardStepUpload'),
    hint: t('submitWizardStepUploadHint'),
    action: uploadUrl.value ? { label: t('submitWizardOpen'), href: uploadUrl.value } : null,
    ready: Boolean(repository.value),
  },
  {
    id: 'release',
    title: t('submitWizardStepRelease'),
    hint: t('submitWizardStepReleaseHint', { version: `v${props.version}` }),
    action: releaseUrl.value ? { label: t('submitWizardOpen'), href: releaseUrl.value } : null,
    ready: Boolean(repository.value),
  },
  {
    id: 'submit',
    title: t('submitWizardStepSubmit'),
    hint: t('submitWizardStepSubmitHint'),
    action: { label: t('submitWizardSubmit'), href: submitUrl.value },
    ready: Boolean(repository.value),
  },
])
</script>

<template>
  <div class="submission-wizard" data-testid="submission-wizard">
    <p class="submission-wizard-intro">{{ t('submitWizardIntro') }}</p>
    <label class="submission-wizard-repo">
      <span>{{ t('submitWizardRepoLabel') }}</span>
      <input v-model="repoUrl" type="url" inputmode="url" spellcheck="false" data-testid="submission-wizard-repo-input" placeholder="https://github.com/you/your-pack">
      <small>{{ repository ? t('submitWizardRepoDetected', { owner: repository.owner, repo: repository.repo }) : t('submitWizardRepoHint') }}</small>
    </label>
    <ol class="submission-wizard-steps">
      <li v-for="(step, index) in steps" :key="step.id" :class="{ 'is-locked': !step.ready }" :data-testid="`submission-wizard-step-${step.id}`">
        <span class="submission-wizard-index">{{ index + 1 }}</span>
        <div class="submission-wizard-body">
          <strong>{{ step.title }}</strong>
          <p>{{ step.hint }}</p>
          <p v-if="!step.ready" class="submission-wizard-locked">{{ t('submitWizardNeedsRepo') }}</p>
          <a v-else-if="step.action" :href="step.action.href" target="_blank" rel="noopener" class="submission-wizard-action">{{ step.action.label }}</a>
        </div>
      </li>
    </ol>
    <p class="submission-wizard-copy">
      <button type="button" class="submission-wizard-copy-button" data-testid="submission-wizard-copy-repo" @click="copy(newRepositoryUrl)">
        {{ copied === newRepositoryUrl ? t('submitWizardCopied') : t('submitWizardCopyRepo') }}
      </button>
      <small>{{ t('submitWizardCopyHint') }}</small>
    </p>
  </div>
</template>

<style scoped>
.submission-wizard { display: grid; gap: .8rem; }
.submission-wizard-intro { margin: 0; color: var(--df-text-muted); font-size: .85rem; line-height: 1.5; }
.submission-wizard-repo { display: grid; gap: .3rem; font-size: .85rem; }
.submission-wizard-repo input { box-sizing: border-box; width: 100%; min-width: 0; padding: .5rem .6rem; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-sm); background: var(--df-control-bg); color: var(--df-text); }
.submission-wizard-repo small { color: var(--df-text-muted); }
.submission-wizard-steps { display: grid; gap: .5rem; margin: 0; padding: 0; list-style: none; counter-reset: none; }
.submission-wizard-steps li { display: grid; grid-template-columns: 1.6rem minmax(0, 1fr); gap: .6rem; padding: .7rem .8rem; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-sm); background: var(--df-surface-1); }
.submission-wizard-steps li.is-locked { opacity: .58; }
.submission-wizard-index { display: grid; place-items: center; width: 1.6rem; height: 1.6rem; border-radius: 999px; background: color-mix(in srgb, var(--df-accent) 18%, var(--df-surface-3)); color: var(--df-accent-strong); font-size: .8rem; font-weight: 600; }
.submission-wizard-body { display: grid; gap: .25rem; min-width: 0; }
.submission-wizard-body p { margin: 0; color: var(--df-text-secondary); font-size: .82rem; line-height: 1.5; overflow-wrap: anywhere; }
.submission-wizard-body strong { font-size: .88rem; }
.submission-wizard-action { justify-self: start; margin-top: .15rem; font-size: .82rem; }
.submission-wizard-locked { color: var(--df-text-muted); font-size: .78rem; }
.submission-wizard-copy { display: flex; flex-wrap: wrap; align-items: center; gap: .5rem; margin: 0; }
.submission-wizard-copy-button { font-size: .8rem; }
.submission-wizard-copy small { color: var(--df-text-muted); font-size: .78rem; }
</style>
