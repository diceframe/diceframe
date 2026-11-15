<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { NButton, NInput, NIcon } from 'naive-ui'
import { MapOutline, OptionsOutline, GameControllerOutline, BuildOutline } from '@vicons/ionicons5'
import { useLocale } from '@/composables/useLocale'
import { creatorCopy } from './copy'
import type { CreatorCardId, CreatorPackageExportMode } from './copy'
import ContentTextReviewDialog from '@/features/content-review/ContentTextReviewDialog.vue'
import CreatorPackageExportDialog from './CreatorPackageExportDialog.vue'
import CreatorAssistantWindow from './CreatorAssistantWindow.vue'
import { templateSource } from './templates'
import type { CreatorTemplateId } from './templates'
import { creatorApi } from '@/api/creator'
import type { CreatorProjectSummary } from '@/api/creator'

const { t, locale } = useLocale()
const copy = computed(() => creatorCopy(locale.value))

// ── Active project for the floating window ──────────────────
const activeProject = ref<{ id: string; name: string; revision: number; kind: string; initialPrompt?: string } | null>(null)

function detectKindFromFiles(files: Record<string, string>): string {
  try {
    const manifest = JSON.parse(files['plugin.json'] || '{}')
    if (manifest.kind) return manifest.kind
    if (manifest.plugin_type === 'tool') return 'toolkit'
    const caps: string[] = manifest.capabilities || []
    if (caps.includes('content.rule')) return 'rule'
    if (manifest.plugin_type === 'gameplay') return 'gameplay'
  } catch { /* keep adventure */ }
  return 'adventure'
}

function openProjectWindow(project: CreatorProjectSummary, kind?: string, initialPrompt?: string) {
  let resolvedKind = kind
  if (!resolvedKind) {
    const src = project.source || {}
    if (src.kind) resolvedKind = src.kind
    else if (src.plugin_type === 'tool') resolvedKind = 'toolkit'
    else if (src.capabilities?.includes?.('content.rule')) resolvedKind = 'rule'
    else {
      const files = (project as unknown as { files?: Record<string, string> }).files
      if (files) resolvedKind = detectKindFromFiles(files)
    }
  }
  activeProject.value = { id: project.id, name: project.name, revision: project.revision, kind: resolvedKind || 'adventure', initialPrompt }
}

// ── Template tiles ──────────────────────────────────────────
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

// ── Hero input ──────────────────────────────────────────────
const heroInput = ref('')
const heroLoading = ref(false)
const heroKind = ref<CreatorTemplateId>('adventure')

// ── Template inline expansion ───────────────────────────────
const expandedTemplate = ref<CreatorTemplateId | null>(null)
const templateName = ref('')
const templateLoading = ref(false)

const wizardTypes = computed(() => {
  const zh = locale.value === 'zh-CN'
  return [
    { id: 'adventure' as CreatorTemplateId, icon: MapOutline, title: zh ? '冒险故事' : 'Adventure', desc: zh ? '剧情、NPC、遭遇、章节' : 'Story, NPCs, encounters, chapters', warning: '' },
    { id: 'rule' as CreatorTemplateId, icon: OptionsOutline, title: zh ? '规则系统' : 'Ruleset', desc: zh ? '属性、骰制、战斗模型' : 'Attributes, dice, combat model', warning: '' },
    { id: 'gameplay' as CreatorTemplateId, icon: GameControllerOutline, title: zh ? '玩法机制' : 'Gameplay', desc: zh ? '回合、阶段、推进方式' : 'Turns, phases, progression', warning: zh ? '高级·需编码，产出设计草稿' : 'Advanced · produces a design draft only' },
    { id: 'toolkit' as CreatorTemplateId, icon: BuildOutline, title: zh ? '工具扩展' : 'Tools', desc: zh ? '骰子助手、查询器、导入器' : 'Dice helpers, lookups, importers', warning: '' },
  ]
})

// ── Projects ────────────────────────────────────────────────
const projects = ref<CreatorProjectSummary[]>([])
const projectLoading = ref(false)
const projectError = ref('')

async function loadProjects() {
  try {
    const result = await creatorApi.list()
    // Sort by updated_at descending (most recently edited first)
    const sorted = (result.projects || []).sort((a, b) => {
      const ta = a.updated_at ? new Date(a.updated_at).getTime() : 0
      const tb = b.updated_at ? new Date(b.updated_at).getTime() : 0
      return tb - ta
    })
    projects.value = sorted
  } catch { /* silent */ }
}

async function createAndOpen(name: string, type: CreatorTemplateId, initialPrompt?: string) {
  projectLoading.value = true
  projectError.value = ''
  try {
    const result = await creatorApi.create(name, { kind: type })
    let project = result.project
    try {
      const updated = await creatorApi.update(project.id, {
        expected_revision: project.revision,
        files: templateSource(type, name),
      })
      project = updated.project
    } catch { /* template init failed, empty project still usable */ }
    projects.value = [project, ...projects.value]
    openProjectWindow(project, type, initialPrompt)
  } catch (error: unknown) {
    projectError.value = error instanceof Error ? error.message : String(error)
  } finally {
    projectLoading.value = false
  }
}

async function startFromHero() {
  const text = heroInput.value.trim()
  if (!text || heroLoading.value) return
  heroLoading.value = true
  const name = text.slice(0, 24).replace(/[^\w\u4e00-\u9fff\s-]/g, '').trim() || `project-${Date.now()}`
  await createAndOpen(name, heroKind.value, text)
  heroInput.value = ''
  heroLoading.value = false
}

async function startFromTemplate(type: CreatorTemplateId) {
  const name = templateName.value.trim()
  if (!name || templateLoading.value) return
  templateLoading.value = true
  await createAndOpen(name, type)
  templateName.value = ''
  expandedTemplate.value = null
  templateLoading.value = false
}

function projectKindLabel(project: CreatorProjectSummary): string {
  const kind = project.source?.kind || project.source?.plugin_type || ''
  const zh = locale.value === 'zh-CN'
  if (kind === 'tool' || kind === 'toolkit') return zh ? '工具' : 'Tool'
  if (kind === 'rule') return zh ? '规则' : 'Rule'
  if (kind === 'gameplay') return zh ? '玩法' : 'Gameplay'
  return zh ? '冒险' : 'Adventure'
}

function inferKindId(project: CreatorProjectSummary): CreatorTemplateId {
  const kind = project.source?.kind || project.source?.plugin_type || ''
  if (kind === 'tool' || kind === 'toolkit') return 'toolkit'
  if (kind === 'rule') return 'rule'
  if (kind === 'gameplay') return 'gameplay'
  return 'adventure'
}

async function deleteProjectFromList(id: string) {
  if (!window.confirm(copy.value.projectDeleteConfirm)) return
  try {
    await creatorApi.delete(id)
    projects.value = projects.value.filter(p => p.id !== id)
    if (activeProject.value?.id === id) activeProject.value = null
  } catch { /* silent */ }
}

// Cards (for the collapsed tools section)
const routeFor: Partial<Record<CreatorCardId, string>> = {
  adventures: 'adventures', worlds: 'worlds', lorebook: 'lorebook',
  rules: 'rules', modules: 'modules', exportAdventureBundle: 'adventures',
}
const exportModeFor: Partial<Record<CreatorCardId, CreatorPackageExportMode>> = {
  exportAdventureModule: 'unified', exportResourcePack: 'resource',
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

    <!-- ── 英雄区 ── -->
    <section class="creator-hero">
      <div class="creator-hero-copy">
        <p class="creator-hero-kicker">CREATOR STUDIO</p>
        <h1 class="creator-hero-title">{{ t('creatorHeroTitle') }}</h1>
        <p class="creator-hero-sub">{{ t('creatorHeroSubtitle') }}</p>
      </div>
      <form class="creator-hero-form" @submit.prevent="startFromHero">
        <textarea
          v-model="heroInput"
          class="creator-hero-input"
          rows="3"
          :placeholder="t('creatorHeroPlaceholder')"
          @keydown.enter.ctrl.prevent="startFromHero"
        />
        <div class="creator-hero-kinds">
          <button
            v-for="k in wizardTypes"
            :key="k.id"
            type="button"
            :class="['creator-hero-kind-chip', { active: heroKind === k.id }]"
            @click="heroKind = k.id"
          >
            <NIcon :component="k.icon" class="creator-kind-icon" />
            <span>{{ k.title }}</span>
            <span v-if="k.warning" class="creator-kind-warn">⚠</span>
          </button>
        </div>
        <p v-if="heroKind === 'gameplay'" class="creator-hero-warn">
          {{ locale === 'zh-CN' ? '⚠ 玩法机制产出的是设计草稿，需进一步实现 Provider 才能运行。' : '⚠ Gameplay produces a design draft only; implementing a Provider is needed to run it.' }}
        </p>
        <NButton type="primary" attr-type="submit" :loading="heroLoading" :disabled="!heroInput.trim()" class="creator-hero-btn">
          {{ t('creatorHeroAction') }} →
        </NButton>
      </form>
    </section>

    <!-- ── 模板卡 ── -->
    <section class="creator-templates" data-testid="creator-templates">
      <h2 class="creator-section-title">{{ t('creatorTemplatesTitle') }}</h2>
      <div class="creator-template-grid">
        <div
          v-for="tpl in wizardTypes"
          :key="tpl.id"
          class="creator-template-card"
          :class="{ expanded: expandedTemplate === tpl.id }"
          :data-testid="`creator-template-${tpl.id}`"
        >
          <button type="button" class="creator-template-btn" @click="expandedTemplate = expandedTemplate === tpl.id ? null : tpl.id">
            <NIcon :component="tpl.icon" class="creator-template-icon" />
            <strong>{{ tpl.title }}</strong>
            <span class="creator-template-desc">{{ tpl.desc }}</span>
            <span v-if="tpl.warning" class="creator-template-warn">⚠ {{ tpl.warning }}</span>
          </button>
          <form v-if="expandedTemplate === tpl.id" class="creator-template-form" @submit.prevent="startFromTemplate(tpl.id)">
            <NInput v-model:value="templateName" :placeholder="copy.projectPlaceholder" autofocus />
            <NButton type="primary" attr-type="submit" :loading="templateLoading" :disabled="!templateName.trim()">
              {{ t('creatorHeroAction') }}
            </NButton>
            <button type="button" class="creator-template-cancel" @click="expandedTemplate = null; templateName = ''">✕</button>
          </form>
        </div>
      </div>
    </section>

    <!-- ── 我的作品 ── -->
    <section v-if="projects.length" class="creator-myworks" data-testid="creator-myworks">
      <h2 class="creator-section-title">{{ t('creatorMyWorksTitle') }}</h2>
      <div class="creator-works-grid">
        <div v-for="project in projects" :key="project.id" class="creator-work-card">
          <div class="creator-work-info">
            <strong>{{ project.name }}</strong>
            <span class="muted">
              <span class="creator-work-kind">
                <NIcon :component="wizardTypes.find(w => w.id === inferKindId(project))?.icon || MapOutline" class="creator-work-kind-icon" />
                {{ projectKindLabel(project) }}
              </span>
              · {{ project.file_count }} {{ locale === 'zh-CN' ? '个文件' : 'files' }}
            </span>
          </div>
          <div class="creator-work-actions">
            <NButton size="small" type="primary" @click="openProjectWindow(project)">{{ t('creatorStudioContinue') }}</NButton>
            <RouterLink :to="{ name: 'creator-studio', params: { projectId: project.id } }" class="creator-work-studio-btn" :title="locale === 'zh-CN' ? '全屏编辑' : 'Full-screen editor'">⛶</RouterLink>
            <button type="button" class="creator-work-delete" :title="copy.projectDeleteConfirm" @click.stop="deleteProjectFromList(project.id)">×</button>
          </div>
        </div>
      </div>
    </section>
    <p v-else class="creator-works-empty muted">{{ t('creatorMyWorksEmpty') }}</p>

    <p v-if="projectError" class="creator-error" role="alert">{{ projectError }}</p>

    <!-- ── 内容资产与工具（折叠） ── -->
    <details class="creator-tools-details" data-testid="creator-tools">
      <summary class="creator-tools-summary">{{ t('creatorToolsTitle') }}</summary>
      <div class="creator-tools-body">
        <section v-for="group in groups" :key="group.id" class="creator-group">
          <header class="creator-group-heading"><h2>{{ groupTitle(group.id) }}</h2></header>
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
      </div>
    </details>

    <ContentTextReviewDialog v-if="textReviewOpen" @close="textReviewOpen = false" />
    <CreatorPackageExportDialog v-if="packageExportMode" :mode="packageExportMode" @close="packageExportMode = null" />

    <!-- Floating creator assistant window -->
    <Teleport to="body">
      <CreatorAssistantWindow
        v-if="activeProject"
        :project-id="activeProject.id"
        :project-name="activeProject.name"
        :project-revision="activeProject.revision"
        :project-kind="activeProject.kind"
        :initial-prompt="activeProject.initialPrompt"
        @close="activeProject = null"
      />
    </Teleport>
  </section>
</template>

<style scoped>
.creator-workshop { display: grid; gap: 2rem; max-width: 100%; }
.creator-section-title { margin: 0 0 .9rem; font-size: 1rem; font-weight: 700; color: var(--df-text-muted); letter-spacing: .02em; }
.creator-hero { display: grid; gap: 1.5rem; padding: 2rem 2rem 2.25rem; border: 1px solid color-mix(in srgb, var(--df-accent) 22%, var(--df-border-soft)); border-radius: var(--df-radius-lg); background: radial-gradient(circle at 70% 0, color-mix(in srgb, var(--df-interactive) 14%, transparent), transparent 55%), var(--df-surface-1); position: relative; overflow: hidden; isolation: isolate; }
.creator-hero::before { content: ''; position: absolute; inset: 0; background: var(--df-bg-atmosphere-image, none) center/cover; opacity: .07; z-index: 0; pointer-events: none; }
.creator-hero > * { position: relative; z-index: 1; }
.creator-hero-kicker { margin: 0 0 .4rem; color: var(--df-interactive-strong); font-size: .7rem; font-weight: 800; letter-spacing: .16em; text-transform: uppercase; }
.creator-hero-title { margin: 0 0 .4rem; font-family: var(--df-font-title, inherit); font-size: clamp(1.6rem, 3vw, 2.4rem); line-height: 1.18; color: var(--df-accent-strong); }
.creator-hero-sub { margin: 0; color: var(--df-text-muted); font-size: .92rem; line-height: 1.55; max-width: 54ch; }
.creator-hero-form { display: grid; gap: .65rem; max-width: 680px; }
.creator-hero-input { width: 100%; box-sizing: border-box; resize: none; padding: .85rem 1rem; border: 1px solid color-mix(in srgb, var(--df-interactive) 35%, var(--df-border-soft)); border-radius: var(--df-radius-lg); background: color-mix(in srgb, var(--df-surface-2) 80%, transparent); color: var(--df-text); font: .95rem/1.55 inherit; transition: border-color .15s; }
.creator-hero-input:focus { outline: none; border-color: var(--df-interactive); }
.creator-hero-btn { justify-self: start; }
.creator-hero-kinds { display: flex; flex-wrap: wrap; gap: .4rem; }
.creator-hero-kind-chip { display: inline-flex; align-items: center; gap: .35rem; padding: .28rem .7rem; border: 1px solid var(--df-border-soft); border-radius: 999px; background: transparent; color: var(--df-text-muted); font: .82rem/1 inherit; cursor: pointer; transition: border-color .12s, color .12s, background .12s; }
.creator-hero-kind-chip:hover { border-color: var(--df-interactive); color: var(--df-text); }
.creator-hero-kind-chip.active { border-color: var(--df-interactive); background: color-mix(in srgb, var(--df-interactive) 14%, var(--df-surface-2)); color: var(--df-text); font-weight: 700; }
.creator-kind-icon { font-size: .9rem; }
.creator-kind-warn { font-size: .75rem; color: var(--df-warn-strong, #c97b22); }
.creator-hero-warn { margin: 0; padding: .45rem .7rem; border-radius: var(--df-radius-md); background: color-mix(in srgb, #c97b22 10%, var(--df-surface-2)); color: var(--df-warn-strong, #c97b22); font-size: .82rem; line-height: 1.5; }
.creator-templates { display: grid; gap: .75rem; }
.creator-template-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: .75rem; }
.creator-template-card { display: flex; flex-direction: column; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-lg); overflow: hidden; transition: border-color .15s; }
.creator-template-card:hover, .creator-template-card.expanded { border-color: var(--df-interactive); }
.creator-template-btn { display: grid; gap: .3rem; padding: 1.1rem 1rem; background: var(--df-surface-1); color: var(--df-text); text-align: left; border: 0; cursor: pointer; font: inherit; transition: background .12s; flex: 1; align-content: start; }
.creator-template-btn:hover { background: var(--df-surface-2); }
.creator-template-icon { font-size: 1.6rem; color: var(--df-interactive-strong); margin-bottom: .15rem; }
.creator-template-btn strong { font-size: .95rem; }
.creator-template-desc { color: var(--df-text-muted); font-size: .78rem; line-height: 1.4; }
.creator-template-warn { color: var(--df-warn-strong, #c97b22); font-size: .74rem; line-height: 1.4; font-weight: 600; }
.creator-template-form { display: flex; gap: .5rem; align-items: center; padding: .65rem .9rem; background: var(--df-surface-2); border-top: 1px solid var(--df-border-soft); justify-content: flex-start; }
.creator-template-form .n-input { flex: 1; min-width: 0; }
.creator-template-cancel { display: inline-flex; align-items: center; justify-content: center; width: 28px; height: 28px; border: 0; border-radius: var(--df-radius-sm); background: transparent; color: var(--df-text-muted); cursor: pointer; font-size: .9rem; }
.creator-template-cancel:hover { background: var(--df-surface-1); color: var(--df-text); }
.creator-myworks { display: grid; gap: .75rem; }
.creator-works-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: .6rem; }
.creator-work-card { display: flex; align-items: center; justify-content: space-between; gap: .75rem; padding: .75rem 1rem; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-md); background: var(--df-surface-1); }
.creator-work-info { display: grid; gap: .1rem; min-width: 0; flex: 1; }
.creator-work-info strong { font-size: .9rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.creator-work-actions { display: flex; align-items: center; gap: .4rem; flex: 0 0 auto; }
.creator-work-studio-btn { display: inline-flex; align-items: center; justify-content: center; width: 28px; height: 28px; border-radius: var(--df-radius-sm); color: var(--df-text-muted); font-size: 1.1rem; text-decoration: none; transition: color .12s, background .12s; }
.creator-work-studio-btn:hover { color: var(--df-text); background: var(--df-surface-2); }
.creator-work-kind { display: inline-flex; align-items: center; gap: .25rem; padding: .05rem .4rem; border-radius: 999px; background: color-mix(in srgb, var(--df-interactive) 12%, var(--df-surface-2)); color: var(--df-interactive-strong); font-size: .72rem; font-weight: 700; }
.creator-work-kind-icon { font-size: .78rem; }
.creator-work-delete { display: inline-flex; align-items: center; justify-content: center; width: 26px; height: 26px; border: 0; border-radius: var(--df-radius-sm); background: transparent; color: var(--df-text-muted); font-size: 1rem; line-height: 1; cursor: pointer; }
.creator-work-delete:hover { background: color-mix(in srgb, var(--df-danger) 12%, transparent); color: var(--df-danger-strong); }
.creator-works-empty { margin: 0; }
.creator-error { color: var(--df-danger-strong); margin: 0; font-size: .88rem; }
.creator-tools-details { border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-lg); overflow: hidden; }
.creator-tools-summary { display: flex; align-items: center; gap: .5rem; padding: .75rem 1rem; background: var(--df-surface-1); color: var(--df-text-muted); font-size: .88rem; font-weight: 700; cursor: pointer; list-style: none; user-select: none; }
.creator-tools-summary::-webkit-details-marker { display: none; }
.creator-tools-summary::before { content: '▸'; font-size: .75rem; transition: transform .15s; }
.creator-tools-details[open] .creator-tools-summary::before { transform: rotate(90deg); color: var(--df-text); }
.creator-tools-details[open] .creator-tools-summary { color: var(--df-text); border-bottom: 1px solid var(--df-border-soft); }
.creator-tools-body { display: grid; gap: 1.25rem; padding: 1rem; background: var(--df-surface-1); }
.creator-group { display: grid; gap: .6rem; }
.creator-group-heading h2 { margin: 0; font-size: .9rem; color: var(--df-text-muted); font-weight: 700; }
.creator-card-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .65rem; }
.creator-card { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: .7rem; align-content: start; padding: .85rem; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-md); color: var(--df-text); background: var(--df-surface-2); text-decoration: none; text-align: left; font: inherit; cursor: pointer; transition: border-color 120ms, background 120ms, transform 120ms; }
.creator-card:hover, .creator-card:focus-visible { border-color: var(--df-interactive); background: var(--df-surface-1); transform: translateY(-1px); }
.creator-card-index { color: var(--df-accent-strong); font: 700 .72rem/1 ui-monospace, Consolas, monospace; }
.creator-card-copy { display: grid; gap: .3rem; min-width: 0; }
.creator-card-copy strong { font-size: .88rem; }
.creator-card-copy span { color: var(--df-text-muted); font-size: .78rem; line-height: 1.4; }
.creator-card-action { grid-column: 2; color: var(--df-interactive-strong); font-size: .78rem; font-weight: 700; }
@media (max-width: 800px) { .creator-template-grid { grid-template-columns: repeat(2, 1fr); } }
@media (max-width: 520px) { .creator-hero { padding: 1.25rem; } .creator-template-grid { grid-template-columns: 1fr; } .creator-card-grid { grid-template-columns: 1fr; } }
</style>