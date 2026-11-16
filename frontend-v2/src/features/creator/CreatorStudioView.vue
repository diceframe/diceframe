<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NButton, NInput } from 'naive-ui'
import { useLocale } from '@/composables/useLocale'
import { creatorApi, type CreatorProject } from '@/api/creator'
import { creatorCopy } from './copy'
import { detectProjectKind, computeContentOverview } from './contentOverview'
import CreatorAssistantPanel from './CreatorAssistantPanel.vue'
import SubmissionWizard from './SubmissionWizard.vue'

const route = useRoute()
const router = useRouter()
const { locale, t } = useLocale()
const copy = computed(() => creatorCopy(locale.value))

const projectId = computed(() => String(route.params.projectId || ''))
const project = ref<CreatorProject | null>(null)
const loading = ref(true)
const error = ref('')
const projectAction = ref('')

// Stage
const projectStage = ref<'draft' | 'validated' | 'built' | 'submission'>('draft')
const stageLabels = computed(() => ({
  'zh-CN': { draft: '草稿', validated: '已完善', built: '已构建', submission: '待投稿' },
  en: { draft: 'Draft', validated: 'Polished', built: 'Built', submission: 'Ready' },
}[locale.value as 'zh-CN' | 'en'] || { draft: 'Draft', validated: 'Polished', built: 'Built', submission: 'Ready' }))

// Files panel
const filesOpen = ref(false)
const viewMode = ref<'chat' | 'edit'>('chat')

const projectKind = computed<string>(() => {
  if (!project.value?.files) return 'adventure'
  return detectProjectKind(project.value.files)
})

const contentOverview = computed(() => {
  if (!project.value?.files) return null
  return computeContentOverview(project.value.files, projectKind.value, locale.value === 'zh-CN')
})
const editorGroup = ref<'overview' | 'content' | 'rules' | 'docs' | 'all'>('overview')
const fileSearch = ref('')
const selectedFile = ref('')
const selectedContent = ref('')
const advancedEdit = ref(false)
const EDITOR_GROUPS = [
  { id: 'overview' as const, test: (p: string) => ['plugin.json', 'config.schema.json', 'README.md', 'manifest.json'].includes(p) },
  { id: 'content' as const, test: (p: string) => p.startsWith('content/') || p.startsWith('adventures/') },
  { id: 'rules' as const, test: (p: string) => p.startsWith('rules/') || p.startsWith('content/rules/') },
  { id: 'docs' as const, test: (p: string) => p.startsWith('docs/') },
]
const EDITOR_GROUP_KEYS = {
  overview: 'creatorEditorGroupOverview', content: 'creatorEditorGroupContent',
  rules: 'creatorEditorGroupRules', docs: 'creatorEditorGroupDocs', all: 'creatorEditorGroupAll',
} as const
const projectFileList = computed(() => Object.keys(project.value?.files || {}).sort())
const groupFiles = computed(() => {
  if (editorGroup.value === 'all') return projectFileList.value
  const group = EDITOR_GROUPS.find(g => g.id === editorGroup.value)
  return projectFileList.value.filter(p => group?.test(p))
})
const filteredFiles = computed(() => {
  const needle = fileSearch.value.trim().toLowerCase()
  return needle ? groupFiles.value.filter(p => p.toLowerCase().includes(needle)) : groupFiles.value
})
watch(editorGroup, () => { fileSearch.value = '' })

function selectFile(path: string) {
  if (!project.value) return
  selectedFile.value = path
  selectedContent.value = project.value.files[path] || ''
}

// Publish popover
const publishOpen = ref(false)
type PublishStep = 'idle' | 'running' | 'ok' | 'fail'
const publishValidate = ref<PublishStep>('idle')
const publishBuild = ref<PublishStep>('idle')
const publishSubmit = ref<PublishStep>('idle')
const publishErrors = ref<string[]>([])
const builtFileName = ref('')
const submissionPluginId = ref('')
const submissionVersion = ref('')

async function runPublish() {
  if (!project.value) return
  publishOpen.value = true
  publishValidate.value = 'running'; publishBuild.value = 'idle'; publishSubmit.value = 'idle'; publishErrors.value = []
  try {
    const r = await creatorApi.validate(project.value.id)
    const packageErrors = r.package_check && !r.package_check.ok
      ? r.package_check.errors.map(item => `${locale.value === 'zh-CN' ? '安装前检查' : 'Pre-install check'}：${item}`)
      : []
    if (r.ok && !packageErrors.length) { publishValidate.value = 'ok'; projectStage.value = 'validated' }
    else { publishValidate.value = 'fail'; publishErrors.value = packageErrors.length ? packageErrors : (r.errors || []); return }
  } catch (e) { publishValidate.value = 'fail'; publishErrors.value = [String(e)]; return }
  publishBuild.value = 'running'
  try {
    const response = await creatorApi.build(project.value.id, project.value.revision)
    const blob = await response.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    const filename = response.headers.get('Content-Disposition')?.match(/filename="([^"]+)"/)?.[1] || project.value.id + '.dfplugin'
    builtFileName.value = filename
    a.href = url; a.download = filename
    a.click(); URL.revokeObjectURL(url)
    publishBuild.value = 'ok'; projectStage.value = 'built'
  } catch (e) { publishBuild.value = 'fail'; publishErrors.value = [String(e)]; return }
  publishSubmit.value = 'running'
  try {
    const r = await creatorApi.submission(project.value.id, project.value.revision)
    if (r.ok) {
      const sub = r.submission as Record<string, unknown> | null
      submissionPluginId.value = String(sub?.plugin_id || project.value.id)
      submissionVersion.value = String(sub?.release_tag || '1.0').replace(/^v/, '')
      publishSubmit.value = 'ok'; projectStage.value = 'submission'
    }
    else { publishSubmit.value = 'fail'; publishErrors.value = [r.error || ''] }
  } catch (e) { publishSubmit.value = 'fail'; publishErrors.value = [String(e)] }
}

// Project actions
async function deleteFile(path: string) {
  if (!project.value || projectAction.value) return
  const msg = locale.value === 'zh-CN' ? `确定删除文件 ${path}？` : `Delete ${path}?`
  if (!window.confirm(msg)) return
  projectAction.value = 'save'
  try {
    const r = await creatorApi.update(project.value.id, {
      expected_revision: project.value.revision,
      remove: [path],
    })
    project.value = r.project
    if (selectedFile.value === path) { selectedFile.value = ''; selectedContent.value = '' }
  } catch (e) { error.value = String(e) }
  finally { projectAction.value = '' }
}

async function saveFile() {
  if (!project.value || !selectedFile.value || projectAction.value) return
  projectAction.value = 'save'
  try {
    const r = await creatorApi.update(project.value.id, { expected_revision: project.value.revision, files: { [selectedFile.value]: selectedContent.value } })
    project.value = r.project
  } catch (e) { error.value = String(e) }
  finally { projectAction.value = '' }
}

async function deleteProject() {
  if (!project.value || !window.confirm(copy.value.projectDeleteConfirm)) return
  projectAction.value = 'delete'
  try {
    await creatorApi.delete(project.value.id)
    router.push({ name: 'creator' })
  } catch (e) { error.value = String(e) }
  finally { projectAction.value = '' }
}

async function reloadProject(changedPaths: string[] = []) {
  if (!project.value) return
  try {
    const r = await creatorApi.read(project.value.id)
    project.value = r.project
    const nextFile = changedPaths.find(p => r.project.files[p]) || (selectedFile.value && r.project.files[selectedFile.value] ? selectedFile.value : Object.keys(r.project.files).sort()[0] || '')
    if (nextFile) { selectedFile.value = nextFile; selectedContent.value = r.project.files[nextFile] || '' }
  } catch { /* keep state */ }
}

async function load() {
  if (!projectId.value) return
  loading.value = true; error.value = ''
  try {
    const r = await creatorApi.read(projectId.value)
    project.value = r.project
    const first = Object.keys(r.project.files).sort()[0] || ''
    selectedFile.value = first; selectedContent.value = first ? r.project.files[first] : ''
  } catch (e) { error.value = String(e) }
  finally { loading.value = false }
}

onMounted(load)
watch(projectId, load)
</script>

<template>
  <div class="studio-view">
    <!-- Top bar -->
    <header class="studio-header">
      <button type="button" class="studio-back" @click="router.push({ name: 'creator' })">
        ← {{ locale === 'zh-CN' ? '返回' : 'Back' }}
      </button>
      <span class="studio-project-name">{{ project?.name || '…' }}</span>
      <div class="studio-stages">
        <span v-for="(stage, i) in ['draft', 'validated', 'built', 'submission']" :key="stage"
          :class="['studio-stage', { active: stage === projectStage, done: i < ['draft','validated','built','submission'].indexOf(projectStage) }]">
          {{ stageLabels[stage as keyof typeof stageLabels] }}
        </span>
      </div>
      <div class="studio-header-actions">
        <div class="studio-mode-toggle" role="group">
          <button type="button" :class="['studio-mode-btn', { active: viewMode === 'chat' }]" @click="viewMode = 'chat'">
            {{ locale === 'zh-CN' ? '对话' : 'Chat' }}
          </button>
          <button type="button" :class="['studio-mode-btn', { active: viewMode === 'edit' }]" @click="viewMode = 'edit'">
            {{ locale === 'zh-CN' ? '编辑' : 'Edit' }}
          </button>
        </div>
        <button v-if="viewMode === 'edit'" type="button" :class="['studio-files-btn', { active: filesOpen }]" @click="filesOpen = !filesOpen">
          {{ t('creatorStudioFilesToggle') }}
        </button>
        <div class="studio-publish-wrap">
          <NButton type="primary" size="small" @click="publishOpen ? publishOpen = false : runPublish()">
            {{ t('creatorPublishAction') }}
          </NButton>
          <div v-if="publishOpen" class="studio-publish-popover">
            <strong>{{ t('creatorPublishAction') }}</strong>
            <div class="studio-publish-steps">
              <div class="studio-publish-step" :class="`is-${publishValidate}`">
                <span class="step-icon">{{ publishValidate === 'ok' ? '✓' : publishValidate === 'fail' ? '✗' : publishValidate === 'running' ? '…' : '①' }}</span>
                <span>{{ t('creatorPublishValidate') }}</span>
                <span v-if="publishValidate === 'ok'" class="step-ok">{{ t('creatorPublishValidateOk') }}</span>
                <span v-if="publishValidate === 'fail'" class="step-fail">{{ t('creatorPublishValidateFail') }}</span>
              </div>
              <div class="studio-publish-step" :class="`is-${publishBuild}`">
                <span class="step-icon">{{ publishBuild === 'ok' ? '✓' : publishBuild === 'fail' ? '✗' : publishBuild === 'running' ? '…' : '②' }}</span>
                <span>{{ t('creatorPublishBuild') }}</span>
                <span v-if="publishBuild === 'ok'" class="step-ok">{{ t('creatorPublishBuildOk') }}</span>
              </div>
              <div class="studio-publish-step" :class="`is-${publishSubmit}`">
                <span class="step-icon">{{ publishSubmit === 'ok' ? '✓' : publishSubmit === 'fail' ? '✗' : publishSubmit === 'running' ? '…' : '③' }}</span>
                <span>{{ t('creatorPublishSubmission') }}</span>
                <span v-if="publishSubmit === 'ok'" class="step-ok">{{ t('creatorPublishSubmissionOk') }}</span>
              </div>
            </div>
            <!-- Submission wizard shown after build+receipt succeed -->
            <div v-if="publishSubmit === 'ok'" class="studio-submission-wizard">
              <SubmissionWizard
                :plugin-id="submissionPluginId || project?.id || ''"
                :version="submissionVersion || '1.0'"
                :file-name="builtFileName || ''"
              />
            </div>
            <div v-if="publishErrors.length" class="studio-publish-errors">
              <p v-for="(e, i) in publishErrors" :key="i" class="error-text">{{ e }}</p>
            </div>
            <div class="studio-publish-footer">
              <button v-if="publishValidate === 'fail' || publishBuild === 'fail' || publishSubmit === 'fail'" type="button" class="studio-retry-btn" @click="runPublish">
                {{ locale === 'zh-CN' ? '重试' : 'Retry' }}
              </button>
              <NButton v-if="publishSubmit === 'ok'" size="small" secondary @click="publishOpen = false">{{ t('creatorPublishDone') }}</NButton>
            </div>
          </div>
        </div>
        <button type="button" class="studio-delete-btn" :disabled="projectAction === 'delete'" @click="deleteProject">
          {{ copy.projectDelete }}
        </button>
      </div>
    </header>

    <p v-if="error" class="error-text studio-error">{{ error }}</p>

    <div v-if="loading" class="studio-loading muted">{{ t('modulesLoading') }}</div>

    <!-- Main area -->
    <div v-else-if="project" :class="['studio-body', `mode-${viewMode}`]">
      <!-- AI chat -->
      <div class="studio-chat">
        <CreatorAssistantPanel
          :project-id="project.id"
          :project-name="project.name"
          :project-revision="project.revision"
          :project-kind="projectKind"
          :expanded="false"
          :wide="viewMode === 'edit' && filesOpen"
          @close="router.push({ name: 'creator' })"
          @toggle-expanded="() => {}"
          @files-changed="reloadProject"
        />
      </div>

      <!-- Content overview (chat mode) -->
      <aside v-if="viewMode === 'chat' && contentOverview" class="studio-overview">
        <h3 class="studio-overview-title">{{ locale === 'zh-CN' ? '内容总览' : 'Overview' }}</h3>
        <div class="studio-overview-stats">
          <div v-for="stat in contentOverview.stats" :key="stat.label" class="studio-overview-stat">
            <span class="studio-stat-label">{{ stat.label }}</span>
            <span class="studio-stat-value">{{ stat.value }}</span>
          </div>
        </div>
        <ul v-if="contentOverview.hints.length" class="studio-overview-hints">
          <li v-for="hint in contentOverview.hints" :key="hint" class="studio-overview-hint">
            <span class="studio-hint-icon">💡</span>{{ hint }}
          </li>
        </ul>
      </aside>

      <!-- Files panel (edit mode only) -->
      <div v-if="viewMode === 'edit' && filesOpen" class="studio-files">
        <div class="studio-files-header">
          <strong>{{ t('creatorFilesPanelTitle') }}</strong>
          <span class="muted">r{{ project.revision }}</span>
          <button type="button" class="studio-adv-toggle" @click="advancedEdit = !advancedEdit">
            {{ advancedEdit ? t('creatorInspectorReadonly') : t('creatorInspectorAdvanced') }}
          </button>
        </div>
        <div class="studio-file-tabs" role="tablist">
          <button v-for="g in ['overview', 'content', 'rules', 'docs', 'all'] as const" :key="g"
            type="button" role="tab" :aria-selected="editorGroup === g" :class="{ active: editorGroup === g }"
            @click="editorGroup = g">{{ t(EDITOR_GROUP_KEYS[g]) }}</button>
        </div>
        <NInput v-model:value="fileSearch" size="small" clearable :placeholder="t('creatorFileSearchPlaceholder')" />
        <div class="studio-file-list">
          <div v-for="path in filteredFiles" :key="path" class="studio-file-row" :class="{ active: path === selectedFile }" @click="selectFile(path)">
            <span class="studio-file-row-name">{{ path }}</span>
            <button type="button" class="studio-file-delete" :title="locale === 'zh-CN' ? '删除文件' : 'Delete file'" @click.stop="deleteFile(path)">×</button>
          </div>
          <p v-if="!filteredFiles.length" class="muted">{{ fileSearch ? t('creatorFileSearchEmpty') : t('creatorEditorGroupEmpty') }}</p>
        </div>
        <div class="studio-file-content">
          <div class="studio-file-name"><code>{{ selectedFile || '—' }}</code></div>
          <textarea v-model="selectedContent" class="studio-textarea" spellcheck="false" :readonly="!advancedEdit" :placeholder="copy.projectFilePlaceholder" />
          <div v-if="advancedEdit" class="studio-file-save">
            <NButton size="small" :loading="projectAction === 'save'" :disabled="!selectedFile" @click="saveFile">{{ copy.projectSave }}</NButton>
            <span class="muted">{{ t('creatorInspectorAdvancedHint') }}</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* Full-page studio */
.studio-view { display: flex; flex-direction: column; height: 100vh; overflow: hidden; background: var(--df-canvas); }

/* Header */
.studio-header { display: flex; align-items: center; gap: .6rem; padding: .6rem 1rem; border-bottom: 1px solid var(--df-border-soft); background: var(--df-surface-1); flex: 0 0 auto; flex-wrap: wrap; }
.studio-back { padding: .25rem .6rem; border: 0; background: transparent; color: var(--df-text-muted); font: .84rem/1 inherit; cursor: pointer; white-space: nowrap; }
.studio-back:hover { color: var(--df-text); }
.studio-project-name { font-size: .95rem; font-weight: 700; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 200px; }
.studio-stages { display: flex; align-items: center; gap: .3rem; flex: 1; }
.studio-stage { padding: .18rem .5rem; border-radius: 999px; font-size: .72rem; color: var(--df-text-muted); background: var(--df-surface-2); }
.studio-stage.active { color: var(--df-text); background: color-mix(in srgb, var(--df-interactive) 20%, var(--df-surface-2)); }
.studio-stage.done { color: var(--df-success-strong, var(--df-interactive-strong)); }
.studio-header-actions { display: flex; align-items: center; gap: .5rem; }
/* Mode toggle */
.studio-mode-toggle { display: flex; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-md); overflow: hidden; }
.studio-mode-btn { padding: .25rem .65rem; border: 0; background: transparent; color: var(--df-text-muted); font: .82rem/1 inherit; cursor: pointer; transition: background .12s, color .12s; }
.studio-mode-btn:hover { color: var(--df-text); background: var(--df-surface-2); }
.studio-mode-btn.active { background: color-mix(in srgb, var(--df-interactive) 16%, var(--df-surface-2)); color: var(--df-text); font-weight: 700; }
.studio-files-btn { padding: .25rem .6rem; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-md); background: transparent; color: var(--df-text-muted); font: .82rem/1 inherit; cursor: pointer; }
.studio-files-btn:hover, .studio-files-btn.active { border-color: var(--df-interactive); color: var(--df-text); background: color-mix(in srgb, var(--df-interactive) 10%, var(--df-surface-2)); }
.studio-delete-btn { padding: .25rem .6rem; border: 1px solid color-mix(in srgb, var(--df-danger, #e57373) 40%, var(--df-border-soft)); border-radius: var(--df-radius-md); background: transparent; color: var(--df-danger-strong, #c94b55); font: .82rem/1 inherit; cursor: pointer; }
.studio-delete-btn:hover { background: color-mix(in srgb, var(--df-danger, #e57373) 10%, transparent); }
.studio-delete-btn:disabled { opacity: .5; cursor: not-allowed; }

/* Publish popover */
.studio-publish-wrap { position: relative; }
.studio-publish-popover { position: absolute; top: calc(100% + 8px); right: 0; z-index: 200; width: min(480px, calc(100vw - 2rem)); max-height: 80vh; overflow-y: auto; padding: .9rem; border: 1px solid color-mix(in srgb, var(--df-interactive) 40%, var(--df-border-soft)); border-radius: var(--df-radius-lg); background: var(--df-surface-1); box-shadow: 0 8px 32px rgba(0,0,0,.2); }
.studio-submission-wizard { margin-top: .75rem; padding-top: .75rem; border-top: 1px solid var(--df-border-soft); }
.studio-publish-popover strong { display: block; font-size: .9rem; margin-bottom: .65rem; }
.studio-publish-steps { display: grid; gap: .5rem; }
.studio-publish-step { display: flex; align-items: center; gap: .5rem; font-size: .84rem; }
.step-icon { width: 20px; height: 20px; display: inline-flex; align-items: center; justify-content: center; border-radius: 50%; background: var(--df-surface-2); font-size: .78rem; font-weight: 700; flex: 0 0 auto; }
.is-ok .step-icon { background: color-mix(in srgb, var(--df-success, #4caf50) 20%, var(--df-surface-2)); color: var(--df-success, #4caf50); }
.is-fail .step-icon { background: color-mix(in srgb, var(--df-danger) 20%, var(--df-surface-2)); color: var(--df-danger-strong); }
.is-running .step-icon { background: color-mix(in srgb, var(--df-interactive) 20%, var(--df-surface-2)); color: var(--df-interactive-strong); animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.step-ok { margin-left: auto; color: var(--df-success, #4caf50); font-size: .76rem; font-weight: 700; }
.step-fail { margin-left: auto; color: var(--df-danger-strong); font-size: .76rem; font-weight: 700; }
.studio-publish-errors { margin-top: .5rem; padding: .5rem; border-radius: var(--df-radius-sm); background: color-mix(in srgb, var(--df-danger) 8%, var(--df-surface-2)); }
.studio-publish-errors p { margin: 0; font-size: .78rem; }
.studio-publish-footer { display: flex; justify-content: flex-end; gap: .5rem; margin-top: .75rem; }
.studio-retry-btn { padding: .25rem .6rem; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-md); background: transparent; color: var(--df-text-muted); font: .82rem/1 inherit; cursor: pointer; }

/* Body */
.studio-loading { padding: 2rem; }
.studio-error { padding: .5rem 1rem; }
.studio-body { flex: 1; min-height: 0; display: grid; overflow: hidden; }
.studio-body.mode-chat { grid-template-columns: 1fr 260px; }
.studio-body.mode-edit { grid-template-columns: 1fr; }
.studio-body.mode-edit:has(.studio-files) { grid-template-columns: 1fr 320px; }

/* Chat — full height; the assistant panel manages its own scrolling */
.studio-chat { min-height: 0; overflow: hidden; display: flex; flex-direction: column; }

/* Content overview */
.studio-overview { display: flex; flex-direction: column; gap: .75rem; padding: 1rem; border-left: 1px solid var(--df-border-soft); background: var(--df-surface-1); overflow-y: auto; min-height: 0; }
.studio-overview-title { margin: 0; font-size: .82rem; font-weight: 700; color: var(--df-text-muted); letter-spacing: .04em; text-transform: uppercase; }
.studio-overview-stats { display: grid; gap: .5rem; }
.studio-overview-stat { display: flex; align-items: baseline; justify-content: space-between; gap: .5rem; padding: .4rem .6rem; border-radius: var(--df-radius-md); background: var(--df-surface-2); }
.studio-stat-label { font-size: .82rem; color: var(--df-text-muted); }
.studio-stat-value { font-size: 1.1rem; font-weight: 700; color: var(--df-text); font-variant-numeric: tabular-nums; }
.studio-stat-value:is(:only-child) { margin-left: auto; }
.studio-overview-hints { margin: 0; padding: 0; list-style: none; display: grid; gap: .4rem; }
.studio-overview-hint { display: flex; align-items: flex-start; gap: .4rem; font-size: .8rem; color: var(--df-text-muted); line-height: 1.45; padding: .35rem .5rem; border-radius: var(--df-radius-sm); background: color-mix(in srgb, var(--df-interactive) 6%, var(--df-surface-2)); }
.studio-hint-icon { flex: 0 0 auto; }

/* Files panel */
.studio-files { display: flex; flex-direction: column; gap: .5rem; padding: .75rem; border-left: 1px solid var(--df-border-soft); background: var(--df-surface-1); overflow-y: auto; min-height: 0; }
.studio-files-header { display: flex; flex-wrap: wrap; align-items: center; gap: .4rem; padding-bottom: .4rem; border-bottom: 1px solid var(--df-border-soft); }
.studio-files-header strong { font-size: .85rem; }
.studio-files-header .muted { font-size: .74rem; flex: 1; }
.studio-adv-toggle { padding: .15rem .5rem; border: 1px solid var(--df-border-soft); border-radius: 999px; background: transparent; color: var(--df-interactive-strong); font-size: .72rem; font-weight: 700; cursor: pointer; }
.studio-file-tabs { display: flex; flex-wrap: wrap; gap: .3rem; }
.studio-file-tabs button { padding: .25rem .6rem; border: 1px solid var(--df-border-soft); border-radius: 999px; background: transparent; color: var(--df-text-muted); font-size: .76rem; font-weight: 600; cursor: pointer; }
.studio-file-tabs button.active { color: var(--df-text); border-color: var(--df-interactive); background: color-mix(in srgb, var(--df-interactive) 12%, var(--df-surface-2)); }
.studio-file-list { display: grid; gap: .15rem; max-height: 180px; overflow-y: auto; }
.studio-file-row { display: flex; align-items: center; gap: .2rem; border-radius: var(--df-radius-sm); cursor: pointer; }
.studio-file-row:hover, .studio-file-row.active { background: var(--df-surface-2); }
.studio-file-row-name { flex: 1; min-width: 0; overflow: hidden; padding: .3rem .45rem; font: .78rem/1.4 inherit; color: var(--df-text-muted); text-overflow: ellipsis; white-space: nowrap; }
.studio-file-row.active .studio-file-row-name, .studio-file-row:hover .studio-file-row-name { color: var(--df-text); }
.studio-file-delete { display: none; align-items: center; justify-content: center; width: 20px; height: 20px; flex: 0 0 auto; border: 0; background: transparent; color: var(--df-text-muted); font-size: .88rem; cursor: pointer; border-radius: var(--df-radius-sm); margin-right: .2rem; }
.studio-file-row:hover .studio-file-delete { display: inline-flex; }
.studio-file-delete:hover { color: var(--df-danger-strong); background: color-mix(in srgb, var(--df-danger) 12%, transparent); }
.studio-file-content { display: flex; flex-direction: column; gap: .35rem; flex: 1; }
.studio-file-name { padding: .25rem .45rem; border-radius: var(--df-radius-sm); background: var(--df-surface-2); }
.studio-file-name code { font-size: .73rem; color: var(--df-text-muted); }
.studio-textarea { flex: 1; min-height: 140px; width: 100%; box-sizing: border-box; resize: vertical; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-md); padding: .55rem; color: var(--df-text); background: var(--df-surface-2); font: .78rem/1.5 ui-monospace, Consolas, monospace; }
.studio-textarea[readonly] { opacity: .7; cursor: default; }
.studio-file-save { display: flex; align-items: center; gap: .5rem; }

/* Responsive */
@media (max-width: 800px) {
  .studio-body.mode-chat { grid-template-columns: 1fr; grid-template-rows: 1fr auto; }
  .studio-overview { border-left: 0; border-top: 1px solid var(--df-border-soft); max-height: 40vh; }
  .studio-body.mode-edit:has(.studio-files) { grid-template-columns: 1fr; }
  .studio-files { border-left: 0; border-top: 1px solid var(--df-border-soft); max-height: 50vh; }
}
</style>
