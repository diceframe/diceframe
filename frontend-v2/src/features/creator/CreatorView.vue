<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { NButton, NInput } from 'naive-ui'
import ContentPageHeader from '@/components/content/ContentPageHeader.vue'
import { useLocale } from '@/composables/useLocale'
import { creatorCopy, type CreatorCardId, type CreatorPackageExportMode } from './copy'
import ContentTextReviewDialog from '@/features/content-review/ContentTextReviewDialog.vue'
import CreatorPackageExportDialog from './CreatorPackageExportDialog.vue'
import CreatorAiPanel from './CreatorAiPanel.vue'
import { templateSource, type CreatorTemplateId } from './templates'
import { creatorApi, type CreatorProject, type CreatorProjectSummary } from '@/api/creator'

const { t, locale } = useLocale()
const copy = computed(() => creatorCopy(locale.value))
const taxonomy = computed(() => locale.value === 'zh-CN'
  ? {
      title: '先分清三层，再开始制作',
      asset: '内容资产', assetText: '冒险、世界、资料册、规则、角色和媒体；这些是你真正编辑的内容。',
      workspace: '工程工作区', workspaceText: '保存源文件、revision、预检和构建记录；它不是安装包，也不是在线商店。',
      package: '统一内容扩展包', packageText: '.dfplugin：把选中的内容资产组合成可分享、可安装的包；安装后到“内容包”管理。',
      backup: '单个冒险备份', backupText: '.dfadventure.zip：只用于备份/迁移一个冒险，不作为在线安装包。',
    }
  : {
      title: 'Four layers keep creation clear',
      asset: 'Content assets', assetText: 'Adventures, worlds, lorebooks, rules, characters, and media are what you edit.',
      workspace: 'Project workspace', workspaceText: 'Keeps source files, revisions, validation, and build receipts; it is not an install package.',
      package: 'Unified content extension', packageText: '.dfplugin combines selected assets for sharing and installation; manage installed packages in Content.',
      backup: 'Single-adventure backup', backupText: '.dfadventure.zip is for backup or migration of one adventure, not store installation.',
    })

type CreatorGroup = 'story' | 'foundation' | 'source' | 'tools' | 'packaging'
const groups: Array<{ id: CreatorGroup; cards: CreatorCardId[] }> = [
  { id: 'story', cards: ['adventures'] },
  { id: 'foundation', cards: ['worlds', 'lorebook', 'rules'] },
  { id: 'source', cards: ['modules'] },
  { id: 'tools', cards: ['textReview'] },
  { id: 'packaging', cards: ['exportAdventureModule', 'exportAdventureBundle'] },
]
const textReviewOpen = ref(false)
const packageExportMode = ref<CreatorPackageExportMode | null>(null)
const projects = ref<CreatorProjectSummary[]>([])
const projectName = ref('')
// W3 拍板 B：先问「要做什么」，按类型给模板工程。
const wizardType = ref<CreatorTemplateId>('adventure')
const projectLoading = ref(false)
const projectError = ref('')
const selectedProject = ref<CreatorProject | null>(null)
const selectedFile = ref('')
const selectedContent = ref('')
const projectAction = ref('')
const projectPreview = ref<Record<string, unknown> | null>(null)
const submissionReceipt = ref<Record<string, unknown> | null>(null)
const projectStage = ref<'draft' | 'validated' | 'built' | 'submission'>('draft')
// W3：分区编辑（按文件前缀分组）+ 校验问题数 + AI 面板开关。
const editorGroup = ref<'overview' | 'content' | 'rules' | 'docs' | 'all'>('overview')
const validationIssues = ref(0)
const aiOpen = ref(false)
const stageLabels = computed(() => ({
  'zh-CN': { draft: '草稿', validated: '已校验', built: '已生成', submission: '待提交' },
  en: { draft: 'Draft', validated: 'Validated', built: 'Built', submission: 'Ready to submit' },
  ja: { draft: '下書き', validated: '検証済み', built: '生成済み', submission: '投稿準備完了' },
  de: { draft: 'Entwurf', validated: 'Geprüft', built: 'Erstellt', submission: 'Bereit zur Einreichung' },
  ru: { draft: 'Черновик', validated: 'Проверено', built: 'Собрано', submission: 'Готово к отправке' },
}[locale.value] || { draft: 'Draft', validated: 'Validated', built: 'Built', submission: 'Ready to submit' }))
function stageLabel(stage: string) {
  return stageLabels.value[stage as keyof typeof stageLabels.value]
}

async function loadProjects() {
  try {
    const result = await creatorApi.list()
    projects.value = result.projects || []
  } catch {
    // The workshop remains useful when the local project API is unavailable.
  }
}

async function createProject() {
  const name = projectName.value.trim()
  if (!name || projectLoading.value) return
  projectLoading.value = true
  projectError.value = ''
  try {
    // W6 复核：create 的 source 是「来源元数据」（kind/id），不是初始文件；
    // 模板文件走 update 写入（revision 0 → 1），失败不阻断空工程创建。
    const result = await creatorApi.create(name)
    let project = result.project
    try {
      const updated = await creatorApi.update(project.id, {
        expected_revision: project.revision,
        files: templateSource(wizardType.value, name),
      })
      project = updated.project
    } catch {
      projectError.value = locale.value === 'zh-CN'
        ? '模板初始化失败；工程已创建（空），可手动添加文件。'
        : 'Template initialization failed; the empty project was created.'
    }
    projects.value = [project, ...projects.value]
    projectName.value = ''
    await openProject(project)
  } catch (error: unknown) {
    projectError.value = error instanceof Error ? error.message : String(error)
  } finally {
    projectLoading.value = false
  }
}

async function openProject(project: CreatorProjectSummary) {
  projectError.value = ''
  try {
    const result = await creatorApi.read(project.id)
    selectedProject.value = result.project
    projectPreview.value = null
    submissionReceipt.value = null
    const first = Object.keys(result.project.files).sort()[0] || ''
    selectedFile.value = first
    selectedContent.value = first ? result.project.files[first] : ''
    projectPreview.value = null
    submissionReceipt.value = null
    projectStage.value = 'draft'
  } catch (error: unknown) {
    projectError.value = error instanceof Error ? error.message : String(error)
  }
}

function selectFile(path: string) {
  if (!selectedProject.value) return
  selectedFile.value = path
  selectedContent.value = selectedProject.value.files[path] || ''
}

// W3 分区编辑：按文件前缀分组；「源码」页签看全部文件。
const EDITOR_GROUPS: Array<{ id: 'overview' | 'content' | 'rules' | 'docs' | 'all'; test: (path: string) => boolean }> = [
  { id: 'overview', test: path => ['plugin.json', 'config.schema.json', 'README.md', 'manifest.json'].includes(path) },
  { id: 'content', test: path => path.startsWith('content/') },
  { id: 'rules', test: path => path.startsWith('rules/') },
  { id: 'docs', test: path => path.startsWith('docs/') },
]
const EDITOR_GROUP_KEYS = {
  overview: 'creatorEditorGroupOverview',
  content: 'creatorEditorGroupContent',
  rules: 'creatorEditorGroupRules',
  docs: 'creatorEditorGroupDocs',
  all: 'creatorEditorGroupAll',
} as const
const projectFileList = computed(() => Object.keys(selectedProject.value?.files || {}).sort())
const groupFiles = computed(() => {
  const files = projectFileList.value
  if (editorGroup.value === 'all') return files
  const group = EDITOR_GROUPS.find(item => item.id === editorGroup.value)
  return files.filter(path => group?.test(path))
})

const stageHint = computed(() => {
  const zh = locale.value === 'zh-CN'
  if (validationIssues.value > 0) {
    return zh ? `预检发现 ${validationIssues.value} 个问题；修复后重新预检。` : `Validation found ${validationIssues.value} issue(s); fix and re-validate.`
  }
  switch (projectStage.value) {
    case 'draft':
      return zh ? '下一步：编辑内容后点「预检」检查工程；拿不准就点「AI 助手」描述你的想法。' : 'Next: edit content and run validation, or describe your idea to the AI assistant.'
    case 'validated':
      return zh ? '工程通过预检。可以「构建 .dfplugin」，或让 AI 继续完善内容。' : 'Validation passed. Build the .dfplugin, or keep refining with the AI assistant.'
    case 'built':
      return zh ? '.dfplugin 已下载。到「内容包」本地导入试装。' : 'The .dfplugin is downloaded; import it locally under Content packages.'
    case 'submission':
      return zh ? '投稿回执已生成；按回执里的步骤完成发布。' : 'Submission receipt ready; follow its steps to publish.'
    default:
      return ''
  }
})

const wizardTypes = computed(() => {
  const zh = locale.value === 'zh-CN'
  return [
    { id: 'adventure' as CreatorTemplateId, title: zh ? '做一个冒险' : 'An adventure', desc: zh ? '剧情、NPC、遭遇、章节——装进内容包就能开团' : 'Story, NPCs, encounters and chapters for your table' },
    { id: 'rule' as CreatorTemplateId, title: zh ? '做一个规则' : 'A ruleset', desc: zh ? '属性、骰制、结算；复杂规则可接外部运行时' : 'Attributes, dice and resolution mechanics' },
    { id: 'gameplay' as CreatorTemplateId, title: zh ? '做一种玩法' : 'A gameplay mode', desc: zh ? '回合怎么走、阶段怎么推进的玩法设计' : 'How a session is organized and advanced' },
    { id: 'toolkit' as CreatorTemplateId, title: zh ? '做一个工具插件' : 'A tool plugin', desc: zh ? '给桌子加小工具：骰子助手、查询器、导入器' : 'Table utilities: dice helpers, lookups, importers' },
  ]
})

async function saveProject() {
  if (!selectedProject.value || !selectedFile.value || projectAction.value) return
  projectAction.value = 'save'
  projectError.value = ''
  try {
    const result = await creatorApi.update(selectedProject.value.id, {
      expected_revision: selectedProject.value.revision,
      files: { [selectedFile.value]: selectedContent.value },
    })
    selectedProject.value = result.project
    projectStage.value = 'draft'
    projects.value = projects.value.map(item => item.id === result.project.id
      ? { ...item, revision: result.project.revision, file_count: Object.keys(result.project.files).length }
      : item)
  } catch (error: unknown) {
    projectError.value = error instanceof Error ? error.message : String(error)
  } finally {
    projectAction.value = ''
  }
}

async function validateProject() {
  if (!selectedProject.value || projectAction.value) return
  projectAction.value = 'validate'
  projectError.value = ''
  try {
    const result = await creatorApi.validate(selectedProject.value.id)
    validationIssues.value = (result.errors || []).length
    if (result.ok) projectStage.value = 'validated'
    projectError.value = result.ok ? copy.value.projectValidated : (result.errors || []).join('；') || copy.value.projectInvalid
  } catch (error: unknown) {
    projectError.value = error instanceof Error ? error.message : String(error)
  } finally {
    projectAction.value = ''
  }
}

// W3：AI 改完文件后重读工程，编辑器与名牌保持最新（diff 摘要在 AI 面板内）。
async function reloadProject(): Promise<void> {
  if (!selectedProject.value) return
  try {
    const result = await creatorApi.read(selectedProject.value.id)
    selectedProject.value = result.project
    if (selectedFile.value && result.project.files[selectedFile.value] !== undefined) {
      selectedContent.value = result.project.files[selectedFile.value]
    }
  } catch {
    // 保留当前编辑态；下次保存/预检仍按 revision 冲突保护。
  }
}

async function previewProject() {
  if (!selectedProject.value || projectAction.value) return
  projectAction.value = 'preview'
  projectError.value = ''
  try {
    const result = await creatorApi.preview(selectedProject.value.id)
    if (result.ok) projectPreview.value = result.preview || null
    else projectError.value = (result.errors || []).join('；') || copy.value.projectInvalid
  } catch (error: unknown) {
    projectError.value = error instanceof Error ? error.message : String(error)
  } finally {
    projectAction.value = ''
  }
}

async function buildProject() {
  if (!selectedProject.value || projectAction.value) return
  projectAction.value = 'build'
  projectError.value = ''
  try {
    const response = await creatorApi.build(selectedProject.value.id, selectedProject.value.revision)
    const blob = await response.blob()
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = response.headers.get('Content-Disposition')?.match(/filename="([^"]+)"/)?.[1] || selectedProject.value.id + '.dfplugin'
    anchor.click()
    URL.revokeObjectURL(url)
    projectStage.value = 'built'
    projectError.value = copy.value.projectBuilt
  } catch (error: unknown) {
    projectError.value = error instanceof Error ? error.message : String(error)
  } finally {
    projectAction.value = ''
  }
}

async function prepareSubmission() {
  if (!selectedProject.value || projectAction.value) return
  projectAction.value = 'submission'
  projectError.value = ''
  try {
    const result = await creatorApi.submission(selectedProject.value.id, selectedProject.value.revision)
    if (result.ok) {
      submissionReceipt.value = result.submission || null
      projectStage.value = 'submission'
    }
    else projectError.value = result.error || copy.value.projectInvalid
  } catch (error: unknown) {
    projectError.value = error instanceof Error ? error.message : String(error)
  } finally {
    projectAction.value = ''
  }
}

async function deleteProject() {
  if (!selectedProject.value || projectAction.value || !window.confirm(copy.value.projectDeleteConfirm)) return
  projectAction.value = 'delete'
  projectError.value = ''
  try {
    await creatorApi.delete(selectedProject.value.id)
    projects.value = projects.value.filter(item => item.id !== selectedProject.value?.id)
    selectedProject.value = null
    projectError.value = copy.value.projectDeleted
  } catch (error: unknown) {
    projectError.value = error instanceof Error ? error.message : String(error)
  } finally {
    projectAction.value = ''
  }
}

// Cards with a route are links; the rest open a dialog in place.
// A single adventure is exported from its row in the adventure list, so
// that card links there instead of duplicating the export action.
const routeFor: Partial<Record<CreatorCardId, string>> = {
  adventures: 'adventures',
  worlds: 'worlds',
  lorebook: 'lorebook',
  rules: 'rules',
  modules: 'modules',
  exportAdventureBundle: 'adventures',
}
const exportModeFor: Partial<Record<CreatorCardId, CreatorPackageExportMode>> = {
  exportAdventureModule: 'unified',
  exportResourcePack: 'resource',
}

const cardIndex: Record<CreatorCardId, string> = {
  adventures: '01', worlds: '02', lorebook: '03', rules: '04', modules: '05', textReview: '06',
  exportAdventureModule: '07', exportResourcePack: '08', exportAdventureBundle: '09',
}

function groupTitle(group: CreatorGroup): string {
  const value = copy.value
  return { story: value.storyGroup, foundation: value.foundationGroup, source: value.sourceGroup, tools: value.toolGroup, packaging: value.packagingGroup }[group]
}

function openCard(id: CreatorCardId) {
  if (id === 'textReview') textReviewOpen.value = true
  const mode = exportModeFor[id]
  if (mode) packageExportMode.value = mode
}

onMounted(loadProjects)
</script>

<template>
  <section class="view creator-workshop" data-testid="creator-workshop">
    <ContentPageHeader kicker="CREATOR STUDIO" :title="copy.title" :description="copy.intro" />
    <!-- W6 复核（用户拍板）：三层说明折叠为「创作帮助」，默认收起不占首屏。 -->
    <details class="creator-taxonomy" data-testid="creator-taxonomy">
      <summary>{{ t('creatorHelpTitle') }}</summary>
      <p class="muted creator-taxonomy-title">{{ taxonomy.title }}</p>
      <div class="creator-taxonomy-grid">
        <article><strong>{{ taxonomy.asset }}</strong><span>{{ taxonomy.assetText }}</span></article>
        <article><strong>{{ taxonomy.workspace }}</strong><span>{{ taxonomy.workspaceText }}</span></article>
        <article><strong>{{ taxonomy.package }}</strong><span>{{ taxonomy.packageText }}</span></article>
        <article><strong>{{ taxonomy.backup }}</strong><span>{{ taxonomy.backupText }}</span></article>
      </div>
    </details>

    <section class="creator-projects" data-testid="creator-projects">
      <div class="creator-group-heading">
        <h2>{{ copy.projectGroup }}</h2>
      </div>
      <div class="creator-wizard" data-testid="creator-wizard-types">
        <div class="creator-wizard-types" role="radiogroup">
          <button
            v-for="option in wizardTypes"
            :key="option.id"
            type="button"
            role="radio"
            :aria-checked="wizardType === option.id"
            :class="{ active: wizardType === option.id }"
            :data-testid="`creator-wizard-${option.id}`"
            @click="wizardType = option.id"
          >
            <strong>{{ option.title }}</strong>
            <span>{{ option.desc }}</span>
          </button>
        </div>
        <form class="creator-project-form" @submit.prevent="createProject">
          <NInput v-model:value="projectName" :placeholder="copy.projectPlaceholder" :disabled="projectLoading" />
          <NButton type="primary" attr-type="submit" :loading="projectLoading" :disabled="!projectName.trim()">
            {{ copy.projectCreate }}
          </NButton>
        </form>
      </div>
      <p v-if="projectError" class="creator-project-error" role="alert">{{ projectError }}</p>
      <div v-if="projects.length" class="creator-project-list">
        <button v-for="project in projects" :key="project.id" type="button" class="creator-project-row" @click="openProject(project)">
          <strong>{{ project.name }}</strong>
          <span>{{ project.id }} · r{{ project.revision }} · {{ project.file_count }} files</span>
        </button>
      </div>
      <p v-else class="creator-project-empty">{{ copy.projectEmpty }}</p>
      <div v-if="selectedProject" class="creator-project-editor">
        <div class="creator-project-editor-heading">
          <strong>{{ selectedProject.name }}</strong>
          <span>r{{ selectedProject.revision }} · {{ selectedProject.digest }}</span>
        </div>
        <div class="creator-project-stage" data-testid="creator-project-stage" aria-live="polite">
          <span v-for="stage in ['draft', 'validated', 'built', 'submission']" :key="stage" :class="['creator-stage', { active: stage === projectStage, done: ['validated', 'built', 'submission'].includes(projectStage) && ['draft', 'validated', 'built', 'submission'].indexOf(stage) < ['draft', 'validated', 'built', 'submission'].indexOf(projectStage) }]">
            {{ stageLabel(stage) }}
          </span>
          <span class="creator-stage-hint" data-testid="creator-stage-hint">{{ stageHint }}</span>
        </div>
        <div class="creator-editor-groups" role="tablist" data-testid="creator-editor-groups">
          <button
            v-for="group in ['overview', 'content', 'rules', 'docs', 'all'] as const"
            :key="group"
            type="button"
            role="tab"
            :aria-selected="editorGroup === group"
            :class="{ active: editorGroup === group }"
            :data-testid="`creator-editor-group-${group}`"
            @click="editorGroup = group"
          >
            {{ t(EDITOR_GROUP_KEYS[group]) }}
          </button>
        </div>
        <div class="creator-project-editor-grid">
          <div class="creator-project-files">
            <button v-for="path in groupFiles" :key="path" type="button" :class="{ active: path === selectedFile }" @click="selectFile(path)">
              {{ path }}
            </button>
            <p v-if="!groupFiles.length" class="muted creator-project-files-empty">{{ t('creatorEditorGroupEmpty') }}</p>
          </div>
          <textarea v-model="selectedContent" class="creator-project-textarea" spellcheck="false" :placeholder="copy.projectFilePlaceholder" />
        </div>
        <div class="creator-project-actions">
          <NButton :loading="projectAction === 'save'" :disabled="!selectedFile" @click="saveProject">{{ copy.projectSave }}</NButton>
          <NButton :loading="projectAction === 'validate'" @click="validateProject">{{ copy.projectValidate }}</NButton>
          <NButton :loading="projectAction === 'preview'" @click="previewProject">{{ copy.projectPreview }}</NButton>
          <NButton :loading="projectAction === 'submission'" @click="prepareSubmission">{{ copy.projectSubmission }}</NButton>
          <NButton type="primary" :loading="projectAction === 'build'" @click="buildProject">{{ copy.projectBuild }}</NButton>
          <NButton type="primary" :data-testid="`creator-ai-toggle`" @click="aiOpen = !aiOpen">{{ t('creatorAiToggle') }}</NButton>
          <NButton type="error" :loading="projectAction === 'delete'" @click="deleteProject">{{ copy.projectDelete }}</NButton>
        </div>
        <CreatorAiPanel v-if="aiOpen" :project="selectedProject" data-testid="creator-ai-panel-embed" @files-changed="reloadProject" />
        <pre v-if="projectPreview" class="creator-project-preview">{{ JSON.stringify(projectPreview, null, 2) }}</pre>
        <pre v-if="submissionReceipt" class="creator-project-preview">{{ JSON.stringify(submissionReceipt, null, 2) }}</pre>
      </div>
    </section>

    <section v-for="group in groups" :key="group.id" class="creator-group">
      <header class="creator-group-heading">
        <h2>{{ groupTitle(group.id) }}</h2>
      </header>
      <div class="creator-card-grid">
        <component
          v-for="id in group.cards"
          :key="id"
          :is="routeFor[id] ? RouterLink : 'button'"
          class="creator-card"
          :data-testid="`creator-card-${id}`"
          v-bind="routeFor[id] ? { to: { name: routeFor[id] } } : { type: 'button' }"
          @click="routeFor[id] ? undefined : openCard(id)"
        >
          <span class="creator-card-index" aria-hidden="true">{{ cardIndex[id] }}</span>
          <span class="creator-card-copy">
            <strong>{{ copy.cards[id].title }}</strong>
            <span>{{ copy.cards[id].description }}</span>
          </span>
          <span class="creator-card-action">{{ copy.cards[id].action }} <span aria-hidden="true">→</span></span>
        </component>
      </div>
    </section>
    <ContentTextReviewDialog v-if="textReviewOpen" @close="textReviewOpen = false" />
    <CreatorPackageExportDialog v-if="packageExportMode" :mode="packageExportMode" @close="packageExportMode = null" />
  </section>
</template>

<style scoped>
.creator-workshop { display: grid; gap: 1.7rem; max-width: 100%; overflow-x: hidden; }
/* W6 复核：三层说明折叠为「创作帮助」，默认收起。 */
.creator-taxonomy { padding: 1rem; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-lg); background: var(--df-surface-1); }
.creator-taxonomy summary { cursor: pointer; font-weight: 700; color: var(--df-text-muted); }
.creator-taxonomy[open] summary { color: var(--df-text); margin-bottom: .4rem; }
.creator-taxonomy-title { margin: 0 0 .5rem; font-weight: 600; }
.creator-taxonomy h2 { margin: 0; font-size: 1rem; }
.creator-taxonomy-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: .6rem; }
.creator-taxonomy-grid article { display: grid; gap: .25rem; padding: .65rem; border-radius: var(--df-radius-md); background: var(--df-surface-2); }
.creator-taxonomy-grid span { color: var(--df-text-muted); font-size: .82rem; line-height: 1.45; }
@media (max-width: 900px) { .creator-taxonomy-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 520px) { .creator-taxonomy-grid { grid-template-columns: 1fr; } }
.creator-projects { display: grid; gap: .75rem; padding: 1rem; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-lg); background: var(--df-surface-1); }
/* W6 复核：向导是独立块（不再挤进 600px 表单行），类型卡自适应换行。 */
.creator-wizard { display: grid; gap: .8rem; min-width: 0; }
.creator-wizard-types { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: .6rem; }
.creator-wizard-types button { display: grid; gap: .2rem; padding: .7rem .8rem; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-md); background: var(--df-surface-2); color: var(--df-text-muted); text-align: left; cursor: pointer; font: inherit; min-width: 0; }
.creator-wizard-types button strong { color: var(--df-text); font-size: .92rem; }
.creator-wizard-types button span { font-size: .78rem; line-height: 1.4; }
.creator-wizard-types button.active { border-color: var(--df-interactive); background: color-mix(in srgb, var(--df-interactive) 10%, var(--df-surface-2)); }
.creator-project-form { display: flex; gap: .65rem; align-items: center; max-width: 600px; }
.creator-project-form .n-input { flex: 1; min-width: 0; }
.creator-project-list { display: grid; gap: .45rem; }
.creator-project-row { display: grid; gap: .2rem; padding: .65rem .75rem; border-radius: var(--df-radius-md); background: var(--df-surface-2); }
.creator-project-row { border: 0; color: inherit; text-align: left; cursor: pointer; font: inherit; }
.creator-project-row:hover, .creator-project-row:focus-visible { outline: 1px solid var(--df-interactive); }
.creator-project-editor { display: grid; gap: .75rem; padding-top: .5rem; }
.creator-project-stage { display: flex; flex-wrap: wrap; align-items: center; gap: .45rem; }
.creator-stage-hint { color: var(--df-text-muted); font-size: .8rem; }
/* W3 分区编辑页签。 */
.creator-editor-groups { display: flex; flex-wrap: wrap; gap: .4rem; }
.creator-editor-groups button { padding: .3rem .8rem; border: 1px solid var(--df-border-soft); border-radius: 999px; background: transparent; color: var(--df-text-muted); font-size: .8rem; font-weight: 600; cursor: pointer; }
.creator-editor-groups button.active { color: var(--df-text); border-color: var(--df-interactive); background: color-mix(in srgb, var(--df-interactive) 12%, var(--df-surface-2)); }
.creator-project-files-empty { margin: 0; font-size: .78rem; }
.creator-stage { padding: .25rem .55rem; border-radius: 999px; color: var(--df-text-muted); background: var(--df-surface-2); font-size: .78rem; }
.creator-stage.active { color: var(--df-text); background: color-mix(in srgb, var(--df-interactive) 22%, var(--df-surface-2)); }
.creator-stage.done { color: var(--df-success-strong, var(--df-interactive-strong)); }
.creator-project-editor-heading { display: flex; flex-wrap: wrap; justify-content: space-between; gap: .5rem; }
.creator-project-editor-heading span { color: var(--df-text-muted); font-size: .78rem; }
.creator-project-editor-grid { display: grid; grid-template-columns: minmax(150px, .35fr) minmax(0, 1fr); gap: .75rem; min-height: 220px; }
.creator-project-files { display: grid; align-content: start; gap: .35rem; }
.creator-project-files button { padding: .4rem .5rem; border: 0; border-radius: var(--df-radius-sm); color: var(--df-text-muted); background: transparent; text-align: left; cursor: pointer; }
.creator-project-files button.active, .creator-project-files button:hover { color: var(--df-text); background: var(--df-surface-2); }
.creator-project-textarea { min-height: 220px; width: 100%; box-sizing: border-box; resize: vertical; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-md); padding: .65rem; color: var(--df-text); background: var(--df-surface-2); font: .85rem/1.5 ui-monospace, SFMono-Regular, Consolas, monospace; }
.creator-project-actions { display: flex; flex-wrap: wrap; gap: .5rem; }
.creator-project-preview { max-height: 16rem; overflow: auto; margin: 0; padding: .7rem; border-radius: var(--df-radius-md); background: var(--df-surface-2); color: var(--df-text-secondary); font: .78rem/1.45 ui-monospace, SFMono-Regular, Consolas, monospace; }
.creator-project-row span, .creator-project-empty { color: var(--df-text-muted); font-size: .84rem; }
.creator-project-error { color: var(--df-danger-strong); }
.creator-group { display: grid; gap: .75rem; }
.creator-group-heading { display: flex; align-items: baseline; justify-content: space-between; gap: 1rem; }
.creator-group-heading h2 { margin: 0; font-size: 1.05rem; }
.creator-card-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .85rem; }
.creator-card { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: .85rem; align-content: start; padding: 1.05rem; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-lg); color: var(--df-text); background: var(--df-surface-1); text-decoration: none; transition: border-color 120ms ease, background-color 120ms ease, transform 120ms ease; text-align: left; font: inherit; cursor: pointer; }
.creator-card:hover, .creator-card:focus-visible { border-color: var(--df-interactive); background: var(--df-surface-2); transform: translateY(-1px); }
.creator-card-index { color: var(--df-accent-strong); font: 700 .76rem/1 ui-monospace, SFMono-Regular, Consolas, monospace; }
.creator-card-copy { display: grid; gap: .4rem; min-width: 0; }
.creator-card-copy strong { font-size: 1rem; }
.creator-card-copy span { color: var(--df-text-muted); line-height: 1.5; }
.creator-card-action { grid-column: 2; color: var(--df-interactive-strong); font-size: .84rem; font-weight: 700; }
@media (max-width: 640px) {
  .creator-project-form { align-items: stretch; flex-direction: column; }
  .creator-project-editor-grid { grid-template-columns: 1fr; }
  .creator-card-grid { grid-template-columns: 1fr; }
  .creator-card { padding: .95rem; }
}
</style>
