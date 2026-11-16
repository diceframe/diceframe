<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, errorMessage } from '@/api/client'
import type { AdventuresResponse, GmStyle, SceneImageRef, WorldCloneResponse, WorldListResponse, WorldSummary, WorldTemplateSummary, WorldTemplatesResponse } from '@/api/types'
import { pickSceneImageReference, resolveSceneImageUrl, revokeSceneImageUrl, SCENE_IMAGE_ACCEPT, uploadSceneImage } from '@/api/sceneImages'
import { ruleSceneUrl } from '@/composables/useBackgroundImages'
import { useConfirm } from '@/composables/useConfirm'
import { useToast } from '@/composables/useToast'
import { useLocale } from '@/composables/useLocale'
import { filterByContentLanguage } from '@/utils/contentLanguage'
import Modal from '@/components/ui/Modal.vue'
import ContentPageHeader from '@/components/content/ContentPageHeader.vue'
import WorldDefinitionEditor, { type WorldDefinitionClonePayload } from './WorldDefinitionEditor.vue'
import WorldMaterialsDialog from './WorldMaterialsDialog.vue'
import { worldsCopy } from './copy'

type GalleryCard = {
  id: string
  name: string
  description: string
  language: string
  source: 'builtin' | 'user' | 'plugin'
  lorebookCount: number
  defaultRule: string
  sceneImage?: SceneImageRef
  sceneImages?: SceneImageRef[]
  gmStyle: GmStyle | null
  adventureName: string
}

const DEFAULT_STYLE: GmStyle = { tone: '', verbosity: 'normal', pace: 'normal', custom_instructions: '' }

const { locale, t } = useLocale()
const copy = computed(() => worldsCopy(locale.value))
const router = useRouter()
const route = useRoute()
const toast = useToast()
const { confirm } = useConfirm()

const cards = ref<GalleryCard[]>([])
const loadedWorldIds = ref(new Set<string>())
const materialsTarget = ref<GalleryCard | null>(null)
const coverUrls = ref<Record<string, string>>({})
const error = ref('')
const busy = ref(false)
const previewCard = ref<GalleryCard | null>(null)
const styleForm = ref<GmStyle>({ ...DEFAULT_STYLE })
const styleBusy = ref(false)
const metadataEdit = ref(false)
const coverInput = ref<HTMLInputElement | null>(null)
const coverTargetId = ref('')
let lastRouteEditRequest = ''
let previewContextEpoch = 0

// 用户世界的头图：走创建页同一条 /scene-images 上传 + 模板写回路径
function changeCover(card: GalleryCard) {
  coverTargetId.value = card.id
  coverInput.value?.click()
}

async function onCoverFilePicked(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  const worldId = coverTargetId.value
  if (!file || !worldId || busy.value) return
  busy.value = true
  try {
    const sceneImage = await uploadSceneImage(file)
    const result = await api<{ ok: boolean; error?: string }>('/worlds/user-scene-image', {
      method: 'POST',
      body: JSON.stringify({ world_id: worldId, scene_image: sceneImage }),
    })
    if (!result.ok) throw new Error(result.error || 'cover-save-failed')
    toast.success(t('worldsCoverUpdated'))
    await load()
  } catch (cause: unknown) {
    toast.error(errorMessage(cause))
  } finally {
    busy.value = false
  }
}

function templateCard(template: WorldTemplateSummary): GalleryCard | null {
  const id = String(template.world_id || template.id || '')
  // 对局临时模板（*_copy_* / *_blank_*）不属于画廊内容。
  if (!id || template.game_scoped) return null
  const source = template.source === 'plugin' ? 'plugin' : template.source === 'user' ? 'user' : 'builtin'
  return {
    id,
    name: String(template.world_name || template.name || id),
    description: String(template.description || ''),
    language: String(template.active_locale || template.language || ''),
    source,
    lorebookCount: Number(template.lorebook_count || 0),
    defaultRule: String(template.default_rule || ''),
    sceneImage: template.scene_image,
    sceneImages: template.scene_images,
    gmStyle: template.gm_style ?? null,
    adventureName: '',
  }
}

function worldCard(world: WorldSummary): GalleryCard | null {
  const id = String(world.id || world.world_id || '')
  if (!id) return null
  return {
    id,
    name: String(world.name || world.world_name || id),
    description: String(world.description || ''),
    language: String(world.language || ''),
    source: 'user',
    lorebookCount: Number(world.entry_count || 0),
    defaultRule: '',
    gmStyle: world.gm_style ?? null,
    sceneImages: world.scene_images,
    adventureName: '',
  }
}

async function load() {
  error.value = ''
  try {
    const [templateData, worldData, adventureData] = await Promise.all([
      api<WorldTemplatesResponse>(`/world-templates?language=${encodeURIComponent(locale.value)}`),
      api<WorldListResponse>('/worlds'),
      api<AdventuresResponse>(`/adventures?language=${encodeURIComponent(locale.value)}`).catch(() => ({ ok: true, adventures: [] } as AdventuresResponse)),
    ])
    const adventureByWorld = new Map<string, string>()
    for (const adventure of adventureData.adventures || []) {
      const worldId = String(adventure.recommended_world_id || '')
      if (worldId && !adventureByWorld.has(worldId)) adventureByWorld.set(worldId, String(adventure.name || ''))
    }
    const seen = new Set<string>()
    const merged: GalleryCard[] = []
    for (const template of templateData.templates || []) {
      const card = templateCard(template)
      if (!card) continue
      seen.add(card.id)
      card.adventureName = adventureByWorld.get(card.id) || ''
      merged.push(card)
    }
    // lore 世界按内容语言过滤：避免 zh 界面混入 *_en 等异语世界卡。
    for (const world of filterByContentLanguage(worldData.worlds || [], locale.value)) {
      const card = worldCard(world)
      if (!card || seen.has(card.id)) continue
      card.adventureName = adventureByWorld.get(card.id) || ''
      merged.push(card)
    }
    cards.value = merged
    loadedWorldIds.value = new Set((worldData.worlds || []).map(world => String(world.id || world.world_id || '')))
    const requestedWorld = typeof route.query.world === 'string' ? route.query.world : ''
    const editRequest = route.query.edit === '1' && requestedWorld ? `${requestedWorld}:edit` : ''
    if (editRequest && editRequest !== lastRouteEditRequest) {
      const requestedCard = merged.find(card => card.id === requestedWorld && card.source === 'user')
      if (requestedCard) {
        lastRouteEditRequest = editRequest
        openMetadataEditor(requestedCard)
      }
    }
    void loadCovers(merged)
  } catch (cause: unknown) {
    error.value = errorMessage(cause)
  }
}

// 排序 + 客户端翻页：世界数量在几十量级，前端分页足够
type WorldsSortMode = 'default' | 'user-first' | 'builtin-first' | 'name' | 'entries'
const WORLDS_PAGE_SIZE = 12
const worldsSort = ref<WorldsSortMode>('default')
const worldsPage = ref(1)

const SOURCE_RANK_USER_FIRST: Record<GalleryCard['source'], number> = { user: 0, plugin: 1, builtin: 2 }
const SOURCE_RANK_BUILTIN_FIRST: Record<GalleryCard['source'], number> = { builtin: 0, plugin: 1, user: 2 }

const sortedCards = computed(() => {
  const list = [...cards.value]
  if (worldsSort.value === 'name') list.sort((a, b) => a.name.localeCompare(b.name, locale.value))
  if (worldsSort.value === 'entries') list.sort((a, b) => b.lorebookCount - a.lorebookCount || a.name.localeCompare(b.name, locale.value))
  if (worldsSort.value === 'user-first') list.sort((a, b) => SOURCE_RANK_USER_FIRST[a.source] - SOURCE_RANK_USER_FIRST[b.source])
  if (worldsSort.value === 'builtin-first') list.sort((a, b) => SOURCE_RANK_BUILTIN_FIRST[a.source] - SOURCE_RANK_BUILTIN_FIRST[b.source])
  return list
})
const worldsTotalPages = computed(() => Math.max(1, Math.ceil(sortedCards.value.length / WORLDS_PAGE_SIZE)))
const pagedCards = computed(() => {
  const page = Math.min(Math.max(1, worldsPage.value), worldsTotalPages.value)
  return sortedCards.value.slice((page - 1) * WORLDS_PAGE_SIZE, page * WORLDS_PAGE_SIZE)
})

watch(worldsSort, () => { worldsPage.value = 1 })
watch(worldsTotalPages, total => {
  if (worldsPage.value > total) worldsPage.value = total
})

function onWorldsSortChange(event: Event) {
  worldsSort.value = (event.target as HTMLSelectElement).value as WorldsSortMode
}

let coverSequence = 0
async function loadCovers(list: GalleryCard[]) {
  const sequence = ++coverSequence
  const previous = coverUrls.value
  const next: Record<string, string> = {}
  await Promise.all(list.map(async card => {
    try {
      const selected = pickSceneImageReference(card.sceneImage, card.sceneImages, `worlds:${card.id}`, false)
      next[card.id] = await resolveSceneImageUrl(selected, card.defaultRule)
    } catch {
      next[card.id] = ruleSceneUrl(card.defaultRule)
    }
  }))
  if (sequence !== coverSequence) {
    Object.values(next).forEach(revokeSceneImageUrl)
    return
  }
  coverUrls.value = next
  for (const url of Object.values(previous)) {
    if (!Object.values(next).includes(url)) revokeSceneImageUrl(url)
  }
}

onMounted(load)
watch(locale, load)
onBeforeUnmount(() => Object.values(coverUrls.value).forEach(revokeSceneImageUrl))

function useForGame(card: GalleryCard) {
  void router.push({ name: 'create', query: { world: card.id } })
}

function editMaterials(card: GalleryCard) {
  if (card.source === 'plugin' || !loadedWorldIds.value.has(card.id)) {
    closePreview()
    materialsTarget.value = card
    return
  }
  void router.push({ name: 'lorebook', query: { world: card.id } })
}

function editCopiedMaterials(worldId: string) {
  materialsTarget.value = null
  void router.push({ name: 'lorebook', query: { world: worldId } })
}

async function cloneWorld(card: GalleryCard) {
  busy.value = true
  try {
    const result = await api<WorldCloneResponse>('/worlds/clone-from-template', {
      method: 'POST',
      body: JSON.stringify({ template_id: card.id }),
    })
    if (!result.ok) throw new Error(result.error || 'clone-failed')
    toast.success(t('worldsCloned'))
    await load()
    const clonedId = String(result.world_id || '')
    if (clonedId) {
      const clonedCard = cards.value.find(candidate => candidate.id === clonedId) || {
        id: clonedId,
        name: String(result.name || card.name),
        description: card.description,
        language: String(result.language || card.language || locale.value),
        source: 'user' as const,
        lorebookCount: card.lorebookCount,
        defaultRule: card.defaultRule,
        sceneImage: card.sceneImage,
        sceneImages: card.sceneImages,
        gmStyle: null,
        adventureName: '',
      }
      openMetadataEditor(clonedCard)
    }
  } catch (cause: unknown) {
    toast.error(errorMessage(cause))
  } finally {
    busy.value = false
  }
}

async function deleteWorld(card: GalleryCard) {
  const agreed = await confirm({
    type: 'error',
    title: t('worldsActionDelete'),
    content: t('worldsDeleteConfirm', { name: card.name }),
  })
  if (!agreed) return
  try {
    const result = await api<{ ok: boolean; error?: string }>(`/worlds/${encodeURIComponent(card.id)}`, { method: 'DELETE' })
    if (!result.ok) throw new Error(result.error || 'delete-failed')
    toast.success(t('worldsDeleted'))
    closePreview()
    await load()
  } catch (cause: unknown) {
    toast.error(errorMessage(cause))
  }
}

function openPreview(card: GalleryCard) {
  previewContextEpoch += 1
  previewCard.value = card
  styleForm.value = { ...(card.gmStyle || DEFAULT_STYLE) }
  metadataEdit.value = false
}

function openMetadataEditor(card: GalleryCard) {
  openPreview(card)
  metadataEdit.value = true
}

function closePreview() {
  previewContextEpoch += 1
  previewCard.value = null
  metadataEdit.value = false
}

async function onDefinitionSaved(payload: { worldId: string }) {
  const card = previewCard.value
  if (!card || card.id !== payload.worldId) return
  closePreview()
  toast.success(t('worldsUpdated'))
  await load()
}

async function onDefinitionCloned(payload: WorldDefinitionClonePayload) {
  const sourceCard = previewCard.value
  const contextEpoch = previewContextEpoch
  if (!sourceCard || sourceCard.id !== payload.sourceWorldId) return
  busy.value = true
  try {
    await load()
    if (contextEpoch !== previewContextEpoch || previewCard.value?.id !== payload.sourceWorldId) return
    const clonedCard = cards.value.find(candidate => candidate.id === payload.worldId) || {
      id: payload.worldId,
      name: String(payload.name || sourceCard.name),
      description: sourceCard.description,
      language: String(payload.language || sourceCard.language || locale.value),
      source: 'user' as const,
      lorebookCount: sourceCard.lorebookCount,
      defaultRule: sourceCard.defaultRule,
      sceneImage: sourceCard.sceneImage,
      sceneImages: sourceCard.sceneImages,
      gmStyle: null,
      adventureName: '',
    }
    openMetadataEditor(clonedCard)
  } finally {
    busy.value = false
  }
}

async function saveStyle() {
  if (!previewCard.value) return
  styleBusy.value = true
  try {
    const result = await api<{ ok: boolean; error?: string; gm_style?: GmStyle }>(
      `/worlds/${encodeURIComponent(previewCard.value.id)}/gm-style`,
      { method: 'PUT', body: JSON.stringify({ gm_style: styleForm.value }) },
    )
    if (!result.ok) throw new Error(result.error || 'save-failed')
    toast.success(t('worldsGmStyleSaved'))
    closePreview()
    await load()
  } catch (cause: unknown) {
    toast.error(errorMessage(cause))
  } finally {
    styleBusy.value = false
  }
}

function resetStyle() {
  styleForm.value = { ...DEFAULT_STYLE }
}

const canEditStyle = computed(() => Boolean(previewCard.value && previewCard.value.gmStyle))

function languageLabel(card: GalleryCard): string {
  const language = card.language.toLowerCase()
  if (language.startsWith('ja')) return '日本語'
  if (language.startsWith('de')) return t('german')
  if (language.startsWith('en')) return t('english')
  return t('chinese')
}

function sourceLabel(card: GalleryCard): string {
  if (card.source === 'plugin') return t('worldsSourcePlugin')
  if (card.source === 'user') return t('worldsSourceUser')
  return t('worldsSourceBuiltin')
}

function coverStyle(card: GalleryCard): Record<string, string> {
  const url = coverUrls.value[card.id] || ''
  return url ? { '--df-world-cover': `url("${url.replace(/"/g, '%22')}")` } : {}
}
</script>

<template>
  <section class="view archive-page worlds-page">
    <ContentPageHeader compact
      class="view-title"
      kicker="WORLD LORE"
      :title="t('navWorlds')"
      :description="copy.intro"
    />

    <p v-if="error" class="notice">{{ error }}</p>

    <div class="worlds-toolbar">
      <label class="worlds-sort">
        <span>{{ t('worldsSortLabel') }}</span>
        <select :value="worldsSort" @change="onWorldsSortChange">
          <option value="default">{{ t('worldsSortDefault') }}</option>
          <option value="user-first">{{ t('worldsSortUserFirst') }}</option>
          <option value="builtin-first">{{ t('worldsSortBuiltinFirst') }}</option>
          <option value="name">{{ t('worldsSortName') }}</option>
          <option value="entries">{{ t('worldsSortEntries') }}</option>
        </select>
      </label>
    </div>
    <div class="worlds-grid">
      <article v-for="card in pagedCards" :key="card.id" class="world-card" :data-world-id="card.id">
        <div class="world-card-cover" :style="coverStyle(card)" />
        <div class="world-card-badges">
          <span class="world-card-badge" :class="`world-card-badge-${card.source}`">{{ sourceLabel(card) }}</span>
          <span v-if="card.adventureName" class="world-card-badge world-card-badge-pack">{{ t('worldsAdventurePack', { name: card.adventureName }) }}</span>
        </div>
        <div class="world-card-body">
          <h2 :title="card.name">{{ card.name }}</h2>
          <span class="world-card-purpose">{{ copy.purpose }}</span>
          <p v-if="card.description" class="world-card-desc">{{ card.description }}</p>
          <p class="world-card-meta">
            {{ languageLabel(card) }} · {{ copy.materialCount.replace('{count}', String(card.lorebookCount)) }}
          </p>
          <p class="world-card-material-hint">{{ copy.materialHint }}</p>
          <div class="world-card-actions">
            <button class="primary" :disabled="busy" @click="useForGame(card)">{{ t('worldsActionUse') }}</button>
            <button class="world-card-lorebook" data-testid="world-edit-lore" :disabled="busy" @click="editMaterials(card)">{{ copy.editMaterials }}</button>
            <button v-if="card.source === 'user'" data-testid="world-edit" @click="openMetadataEditor(card)">{{ t('worldsActionEdit') }}</button>
            <button @click="openPreview(card)">{{ t('worldsActionPreview') }}</button>
            <button class="world-card-clone" :disabled="busy || card.source === 'user'" @click="cloneWorld(card)">
              {{ t('worldsActionClone') }}
            </button>
            <button v-if="card.source === 'user'" :disabled="busy" @click="changeCover(card)">
              {{ t('worldsActionChangeCover') }}
            </button>
          </div>
        </div>
      </article>
    </div>

    <div v-if="worldsTotalPages > 1" class="worlds-pager">
      <button type="button" :disabled="worldsPage <= 1" @click="worldsPage--">{{ t('worldsPagePrev') }}</button>
      <span>{{ t('worldsPageOf', { page: Math.min(Math.max(1, worldsPage), worldsTotalPages), total: worldsTotalPages }) }}</span>
      <button type="button" :disabled="worldsPage >= worldsTotalPages" @click="worldsPage++">{{ t('worldsPageNext') }}</button>
    </div>

    <input
      ref="coverInput"
      type="file"
      :accept="SCENE_IMAGE_ACCEPT"
      style="display: none"
      @change="onCoverFilePicked"
    >

    <WorldMaterialsDialog
      v-if="materialsTarget"
      :world-id="materialsTarget.id"
      :world-name="materialsTarget.name"
      @close="materialsTarget = null"
      @created="editCopiedMaterials"
    />

    <Modal v-if="previewCard" :title="previewCard.name" @close="closePreview">
      <p class="muted">
        {{ sourceLabel(previewCard) }} · {{ languageLabel(previewCard) }} · {{ copy.materialCount.replace('{count}', String(previewCard.lorebookCount)) }}
      </p>
      <WorldDefinitionEditor
        :world-id="previewCard.id"
        :mode="metadataEdit ? 'edit' : 'view'"
        :source="previewCard.source"
        :initial-name="previewCard.name"
        :initial-description="previewCard.description"
        :initial-language="previewCard.language"
        @saved="onDefinitionSaved"
        @cloned="onDefinitionCloned"
        @cancel="closePreview"
      />

      <div v-if="!metadataEdit" class="world-style-editor">
        <h3>{{ t('worldsGmStyle') }}</h3>
        <template v-if="canEditStyle && !metadataEdit">
          <label>
            <span>{{ t('worldsGmStyleTone') }}</span>
            <input v-model="styleForm.tone" type="text" maxlength="120">
            <small class="muted">{{ t('worldsGmStyleToneHint') }}</small>
          </label>
          <label>
            <span>{{ t('worldsGmStyleVerbosity') }}</span>
            <select v-model="styleForm.verbosity">
              <option value="brief">{{ t('worldsVerbosityBrief') }}</option>
              <option value="normal">{{ t('worldsVerbosityNormal') }}</option>
              <option value="detailed">{{ t('worldsVerbosityDetailed') }}</option>
            </select>
          </label>
          <label>
            <span>{{ t('gmStylePace') }}</span>
            <select v-model="styleForm.pace">
              <option value="slow">{{ t('gmStylePaceSlow') }}</option>
              <option value="normal">{{ t('gmStylePaceNormal') }}</option>
              <option value="fast">{{ t('gmStylePaceFast') }}</option>
            </select>
          </label>
          <label>
            <span>{{ t('worldsGmStyleCustom') }}</span>
            <textarea v-model="styleForm.custom_instructions" rows="5" maxlength="2000" />
            <small class="muted">{{ t('worldsGmStyleCustomHint') }}</small>
          </label>
        </template>
        <p v-else class="notice">{{ t('worldsGmStyleLocked') }}</p>
      </div>

      <template #actions>
        <button @click="closePreview">{{ t('close') }}</button>
        <button v-if="previewCard && previewCard.source === 'user' && !metadataEdit" @click="openMetadataEditor(previewCard)">{{ t('worldsActionEdit') }}</button>
        <button v-if="previewCard && previewCard.source === 'user'" class="danger" @click="deleteWorld(previewCard)">
          {{ t('worldsActionDelete') }}
        </button>
        <template v-if="canEditStyle && !metadataEdit">
          <button @click="resetStyle">{{ t('worldsGmStyleReset') }}</button>
          <button class="primary" :disabled="styleBusy" @click="saveStyle">{{ t('worldsGmStyleSave') }}</button>
        </template>
      </template>
    </Modal>
  </section>
</template>
