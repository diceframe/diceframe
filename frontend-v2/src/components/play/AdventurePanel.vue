<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '@/api/client'
import { useLocale } from '@/composables/useLocale'

type AdventureNode = {
  id: string
  type: string
  chapter_id?: string | null
  name?: string
  description?: string
  optional?: boolean
  scene_ref?: string | null
  encounter_ref?: string | null
  npc_refs?: string[]
  transitions?: Array<{ to: string }>
}
type AdventureGraph = {
  chapters: Array<{ id: string; name?: string }>
  nodes: AdventureNode[]
  objectives: Array<{ id: string; name?: string; node_ids?: string[] }>
  milestones: Array<{ id: string; name?: string; node_ids?: string[] }>
}
type AdventureResponse = {
  adventure: null | {
    binding?: {
      adventure_id?: string
      version?: string
      format?: string
      content_digest?: string
      source_kind?: string
      source_id?: string
    }
    available: boolean
    reason?: string
    format?: string
    projection?: AdventureGraph | null
  }
}

const props = defineProps<{ gameKey: string; isGm: boolean }>()
const { t } = useLocale()
const router = useRouter()
const loading = ref(false)
const error = ref('')
const adventure = ref<AdventureResponse['adventure']>(null)

const graph = computed(() => adventure.value?.projection || null)
const chapters = computed(() => graph.value?.chapters || [])
const ungroupedNodes = computed(() => (
  graph.value?.nodes.filter(node => !node.chapter_id) || []
))
function nodesFor(chapterId: string): AdventureNode[] {
  return graph.value?.nodes.filter(node => node.chapter_id === chapterId) || []
}
function nodeLabel(node: AdventureNode): string {
  return node.name || node.id
}
function nodeKindLabel(type: string): string {
  const labels: Record<string, string> = {
    scene: t('adventureNode_scene'), objective: t('adventureNode_objective'),
    encounter: t('adventureNode_encounter'), decision: t('adventureNode_decision'),
    milestone: t('adventureNode_milestone'), reference: t('adventureNode_reference'),
  }
  return labels[type] || type
}
const unavailableReason = computed(() => (
  adventure.value?.reason === 'binding_changed'
    ? t('adventurePanelReason_binding_changed')
    : adventure.value?.reason === 'package_invalid'
      ? t('adventurePanelReason_package_invalid')
      : t('adventurePanelReason_package_unavailable')
))
function linkedNames(ids: string[] | undefined): string {
  if (!ids?.length) return t('adventureNoLinkedNodes')
  const labels = new Map((graph.value?.nodes || []).map(node => [node.id, nodeLabel(node)]))
  return ids.map(id => labels.get(id) || id).join(' · ')
}
function openModuleRecovery(): void {
  const binding = adventure.value?.binding || {}
  // §8 Recovery：把**存档里的绑定身份**（版本 / 内容指纹 / 来源）与对局一起交给
  // 模组库，恢复页才能校验"同 id 但不同 package"并回到本局。
  void router.push({
    name: 'modules',
    query: {
      adventure: binding.adventure_id || '',
      version: binding.version || '',
      digest: binding.content_digest || '',
      source_kind: binding.source_kind || '',
      source_id: binding.source_id || '',
      game: props.gameKey || '',
    },
  })
}

async function load(): Promise<void> {
  if (!props.gameKey) return
  loading.value = true
  error.value = ''
  try {
    const response = await api<AdventureResponse>(`/games/${encodeURIComponent(props.gameKey)}/adventure`)
    adventure.value = response.adventure || null
  } catch (caught: unknown) {
    adventure.value = null
    error.value = caught instanceof Error ? caught.message : String(caught || t('operationFailed'))
  } finally {
    loading.value = false
  }
}

watch(() => props.gameKey, () => { void load() })
onMounted(() => { void load() })
</script>

<template>
  <section class="adventure-panel panel" data-testid="game-adventure-panel">
    <header>
      <div>
        <span class="eyebrow">{{ isGm ? t('adventureGmEyebrow') : t('adventurePlayerEyebrow') }}</span>
        <h3>{{ t('adventurePanelTitle') }}</h3>
      </div>
      <button class="text-button" type="button" :disabled="loading" @click="load">{{ t('refresh') }}</button>
    </header>
    <p v-if="loading" class="muted">{{ t('adventurePanelLoading') }}</p>
    <p v-else-if="error" class="error-copy">{{ error }}</p>
    <p v-else-if="!adventure" class="muted">{{ t('adventurePanelNone') }}</p>
    <template v-else-if="!adventure.available">
      <p class="error-copy">{{ t('adventurePanelUnavailable') }}</p>
      <p class="muted">{{ unavailableReason }}</p>
      <p class="muted adventure-binding">{{ t('adventureRecoveryRequired', { adventure: adventure.binding?.adventure_id || '', version: adventure.binding?.version || '' }) }}</p>
      <button class="primary recovery-button" type="button" @click="openModuleRecovery">{{ t('adventureRecoveryOpenModules') }}</button>
    </template>
    <template v-else-if="!graph">
      <p class="muted">{{ t('adventurePanelLegacy') }}</p>
    </template>
    <template v-else>
      <p class="muted adventure-binding">
        {{ adventure.binding?.adventure_id }} · {{ adventure.binding?.version }}
      </p>
      <section v-for="chapter in chapters" :key="chapter.id" class="adventure-chapter">
        <h4>{{ chapter.name || chapter.id }}</h4>
        <article v-for="node in nodesFor(chapter.id)" :key="node.id" class="adventure-node">
          <div class="adventure-node-heading">
            <strong>{{ nodeLabel(node) }}</strong>
            <span>{{ nodeKindLabel(node.type) }}</span>
          </div>
          <p v-if="node.description">{{ node.description }}</p>
          <p v-if="node.scene_ref" class="muted">{{ t('adventureSceneRef', { ref: node.scene_ref }) }}</p>
          <p v-if="node.encounter_ref" class="muted">{{ t('adventureEncounterRef', { ref: node.encounter_ref }) }}</p>
          <p v-if="node.transitions?.length" class="muted">{{ t('adventureNextNodes', { nodes: linkedNames(node.transitions.map(item => item.to)) }) }}</p>
        </article>
      </section>
      <section v-if="ungroupedNodes.length" class="adventure-chapter">
        <h4>{{ t('adventureUnsortedNodes') }}</h4>
        <article v-for="node in ungroupedNodes" :key="node.id" class="adventure-node">
          <div class="adventure-node-heading"><strong>{{ nodeLabel(node) }}</strong><span>{{ nodeKindLabel(node.type) }}</span></div>
          <p v-if="node.description">{{ node.description }}</p>
        </article>
      </section>
      <section v-if="graph.objectives.length || graph.milestones.length" class="adventure-goals">
        <h4>{{ t('adventureGoals') }}</h4>
        <p v-for="objective in graph.objectives" :key="objective.id"><strong>{{ objective.name || objective.id }}</strong> — {{ linkedNames(objective.node_ids) }}</p>
        <p v-for="milestone in graph.milestones" :key="milestone.id"><strong>{{ milestone.name || milestone.id }}</strong> — {{ linkedNames(milestone.node_ids) }}</p>
      </section>
    </template>
  </section>
</template>

<style scoped>
.adventure-panel { display: grid; gap: 12px; margin-bottom: 16px; }
.adventure-panel header { display: flex; justify-content: space-between; gap: 12px; align-items: start; }
.adventure-panel h3, .adventure-panel h4, .adventure-panel p { margin: 0; }
.adventure-panel h3 { font-size: 1rem; }
.adventure-panel h4 { font-size: .86rem; color: var(--text-secondary); }
.eyebrow { color: var(--primary); font-size: .72rem; font-weight: 700; letter-spacing: .06em; text-transform: uppercase; }
.text-button { border: 0; background: transparent; color: var(--primary); cursor: pointer; padding: 0; }
.adventure-binding { font-size: .78rem; overflow-wrap: anywhere; }
.adventure-chapter, .adventure-goals { display: grid; gap: 8px; }
.adventure-node { border-left: 2px solid var(--primary); padding-left: 10px; display: grid; gap: 5px; }
.adventure-node-heading { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; }
.adventure-node-heading span { color: var(--text-tertiary); font-size: .75rem; }
.adventure-node p, .adventure-goals p { font-size: .82rem; line-height: 1.45; }
.error-copy { color: var(--error); }
@media (max-width: 480px) {
  .adventure-panel { gap: 10px; margin-bottom: 10px; }
  .adventure-panel header { align-items: center; }
  .adventure-node { padding-left: 8px; }
  .adventure-node-heading { align-items: flex-start; flex-direction: column; gap: 2px; }
  .recovery-button { width: 100%; min-height: 40px; }
}
</style>
