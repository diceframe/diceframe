<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useLocale } from '@/composables/useLocale'

export interface LoreImportPreview {
  format: string
  counts: { entries:number; mapped:number; warnings:number; unsupported:number }
  warnings: string[]
  character?: { name:string; book_name?:string; entries:number } | null
}

export interface LoreImportTargetBook { id:string; name:string; primary?:boolean }
export interface LoreImportCharacter { uid:string; name:string }

/** confirm 的结果：导入目标 + binding，两者都必须由用户显式决定。 */
export interface LoreImportDecision {
  bookId: string | null
  binding: { scope_kind:string; scope_id:string; role?:string } | null
}

const props = defineProps<{
  open: boolean
  preview?: LoreImportPreview
  books?: LoreImportTargetBook[]
  characters?: LoreImportCharacter[]
  worldId?: string
  worldName?: string
  gameKey?: string
  primaryBookId?: string
}>()
const emit = defineEmits<{ close:[]; confirm:[decision: LoreImportDecision] }>()

const { t } = useLocale()

type Target = 'new' | 'existing'
type BindingChoice = 'world' | 'game' | 'character' | 'global' | 'none'

const target = ref<Target>('new')
const existingBookId = ref('')
const binding = ref<BindingChoice>('world')
const characterUid = ref('')

const isCharacterCard = computed(() => props.preview?.format === 'character_card_v3')
const characterName = computed(() => props.preview?.character?.name || '')
const characterEntryCount = computed(() => props.preview?.character?.entries ?? 0)
// 这个流程的职责就是把卡里的世界书抽出来：没有内嵌世界书时整段都不该出现，
// 更不该用一个「取消勾选」的假选项把 Import 按钮变灰。
const hasCharacterBook = computed(() => isCharacterCard.value && characterEntryCount.value > 0)
const characterSummary = computed(() =>
  characterName.value
    ? t('loreImportCharacterSummaryNamed', { name: characterName.value, count: characterEntryCount.value })
    : t('loreImportCharacterSummary', { count: characterEntryCount.value }),
)

/** 卡里的名字能对上当前游戏的角色时才有 canonical uid 可绑。 */
const matchedCharacter = computed(() => {
  const name = characterName.value.trim().toLowerCase()
  if (!name) return undefined
  return (props.characters || []).find(item => item.name.trim().toLowerCase() === name)
})

const canBindCharacter = computed(() => (props.characters || []).length > 0)
const canBindGame = computed(() => !!props.gameKey)

// 导入进主世界书是破坏性的（条目会并入当前世界的主世界书），必须显式提示。
const importsIntoPrimaryWorldBook = computed(() =>
  target.value === 'existing' && !!existingBookId.value && existingBookId.value === props.primaryBookId,
)
const primaryWarning = computed(() =>
  t('loreImportPrimaryWarning', { nameHint: props.worldName ? `（${props.worldName}）` : '' }),
)

const needsCharacterUid = computed(() => binding.value === 'character' && !characterUid.value)
const confirmDisabled = computed(() =>
  !props.preview
  || (target.value === 'existing' && !existingBookId.value)
  || needsCharacterUid.value,
)

function resetChoices() {
  // 每次打开（或换了一份 preview）都回到显式默认值，不继承上一次的选择。
  target.value = 'new'
  existingBookId.value = ''
  if (isCharacterCard.value) {
    const matched = matchedCharacter.value
    binding.value = matched ? 'character' : 'world'
    characterUid.value = matched?.uid || ''
  } else {
    binding.value = 'world'
    characterUid.value = ''
  }
}

// immediate：对话框可能在挂载时就已经是打开状态，那种情况下也必须初始化默认值。
watch(
  [() => props.open, () => props.preview],
  ([isOpen]) => { if (isOpen) resetChoices() },
  { immediate: true },
)

watch(binding, choice => {
  if (choice !== 'character') characterUid.value = ''
  else if (!characterUid.value) characterUid.value = matchedCharacter.value?.uid || ''
})

function decide(): LoreImportDecision {
  const bookId = target.value === 'existing' ? existingBookId.value : null
  if (binding.value === 'none') return { bookId, binding: null }
  if (binding.value === 'global') return { bookId, binding: { scope_kind: 'global', scope_id: '' } }
  if (binding.value === 'game') return { bookId, binding: { scope_kind: 'game', scope_id: String(props.gameKey || '') } }
  if (binding.value === 'character') return { bookId, binding: { scope_kind: 'character', scope_id: characterUid.value } }
  return { bookId, binding: { scope_kind: 'world', scope_id: String(props.worldId || '') } }
}
</script>

<template>
  <div v-if="open" class="lore-import-dialog" role="dialog" aria-modal="true" :aria-label="t('loreImportTitle')">
    <div class="lore-import-dialog__panel">
      <h2>{{ t('loreImportTitle') }}</h2>

      <p v-if="preview">
        {{ t('loreImportDetected', { format: preview.format, entries: preview.counts.entries, mapped: preview.counts.mapped }) }}
      </p>
      <ul v-if="preview?.warnings?.length" class="lore-import-dialog__warnings">
        <li v-for="warning in preview.warnings" :key="warning">{{ warning }}</li>
      </ul>
      <p v-if="preview?.counts.unsupported" class="warning">
        {{ t('loreImportUnsupported', { count: preview.counts.unsupported }) }}
      </p>

      <!-- Character Card：先说清「这张卡带了世界书」，再问绑到哪里 -->
      <section v-if="hasCharacterBook" class="lore-import-dialog__character">
        <p class="lore-import-dialog__character-summary">{{ characterSummary }}</p>
        <p v-if="!matchedCharacter" class="muted lore-import-dialog__imported-lore">
          {{ t('loreImportCharacterNoMatch') }}
        </p>
      </section>

      <fieldset class="lore-import-dialog__target">
        <legend>{{ t('loreImportTargetLegend') }}</legend>
        <label>
          <input v-model="target" type="radio" value="new"> {{ t('loreImportTargetNew') }}
        </label>
        <label>
          <input v-model="target" type="radio" value="existing"> {{ t('loreImportTargetExisting') }}
        </label>
        <select
          v-if="target === 'existing'"
          v-model="existingBookId"
          class="lore-import-dialog__book"
          :aria-label="t('loreImportTargetBookAria')"
        >
          <option value="">{{ t('loreImportPickBook') }}</option>
          <option v-for="book in books || []" :key="book.id" :value="book.id">
            {{ book.name }}{{ book.primary ? t('loreImportPrimarySuffix') : '' }}
          </option>
        </select>
        <p v-if="importsIntoPrimaryWorldBook" class="warning lore-import-dialog__primary-warning">
          {{ primaryWarning }}
        </p>
      </fieldset>

      <fieldset class="lore-import-dialog__binding">
        <legend>{{ t('loreImportBindingLegend') }}</legend>
        <label>
          <input v-model="binding" type="radio" value="world">
          {{ t('loreScopeWorld') }}{{ worldName ? `（${worldName}）` : '' }}
        </label>
        <label>
          <input v-model="binding" type="radio" value="game" :disabled="!canBindGame"> {{ t('loreScopeGame') }}
        </label>
        <label>
          <input v-model="binding" type="radio" value="character" :disabled="!canBindCharacter"> {{ t('loreBindingsScopeCharacter') }}
        </label>
        <select
          v-if="binding === 'character'"
          v-model="characterUid"
          class="lore-import-dialog__character-select"
          :aria-label="t('loreBindingsCharacterSelectAria')"
        >
          <option value="">{{ t('loreBindingsPickCharacter') }}</option>
          <option v-for="item in characters || []" :key="item.uid" :value="item.uid">{{ item.name }}</option>
        </select>
        <label>
          <input v-model="binding" type="radio" value="global"> {{ t('loreScopeGlobal') }}
        </label>
        <label>
          <input v-model="binding" type="radio" value="none"> {{ t('loreImportBindingNone') }}
        </label>
      </fieldset>

      <div class="lore-import-dialog__buttons">
        <button @click="emit('close')">{{ t('cancel') }}</button>
        <button :disabled="confirmDisabled" @click="emit('confirm', decide())">{{ t('import') }}</button>
      </div>
    </div>
  </div>
</template>
