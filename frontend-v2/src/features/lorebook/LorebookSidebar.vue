<script setup lang="ts">
import { computed, ref } from 'vue'
import { useLocale } from '@/composables/useLocale'

export interface LorebookCard { id:string; name:string; scope?:string; primary?:boolean; enabled?:boolean }

const props = defineProps<{ books: LorebookCard[]; activeId?: string; busy?: boolean }>()
const emit = defineEmits<{
  select:[id:string]
  create:[]
  rename:[book: LorebookCard]
  remove:[book: LorebookCard]
  'toggle-enabled':[book: LorebookCard]
  bindings:[book: LorebookCard]
  import:[]
  export:[]
}>()

const { t } = useLocale()

// 「全部」之外的每个 scope 都对应 binding 的 canonical scope_kind，UI 不发明新 scope。
const SCOPES = ['all', 'world', 'game', 'character', 'global'] as const
type Scope = typeof SCOPES[number]

const search = ref('')
const scope = ref<Scope>('all')

const scopeLabels = computed<Record<Scope, string>>(() => ({
  all: t('loreScopeAll'),
  world: t('loreScopeWorld'),
  game: t('loreScopeGame'),
  character: t('loreScopeCharacter'),
  global: t('loreScopeGlobal'),
}))

const visibleBooks = computed(() => {
  const needle = search.value.trim().toLowerCase()
  return props.books.filter(book => {
    if (scope.value !== 'all' && String(book.scope || '') !== scope.value) return false
    if (!needle) return true
    return book.name.toLowerCase().includes(needle) || book.id.toLowerCase().includes(needle)
  })
})

function scopeLabel(book: LorebookCard): string {
  const value = String(book.scope || '')
  return value ? (scopeLabels.value[value as Scope] || value) : t('loreBooksUnbound')
}
</script>

<template>
  <aside class="lorebook-sidebar" :aria-label="t('loreBooksTitle')">
    <div class="lorebook-sidebar__title">{{ t('loreBooksTitle') }}</div>

    <div class="lorebook-sidebar__filters">
      <input
        v-model="search"
        class="lorebook-sidebar__search"
        type="search"
        :placeholder="t('loreBooksSearchPlaceholder')"
        :aria-label="t('loreBooksSearchPlaceholder')"
      >
      <select v-model="scope" class="lorebook-sidebar__scope" :aria-label="t('loreBooksScopeAria')">
        <option v-for="value in SCOPES" :key="value" :value="value">{{ scopeLabels[value] }}</option>
      </select>
    </div>

    <div class="lorebook-sidebar__actions">
      <button class="lorebook-sidebar__new" :disabled="busy" @click="emit('create')">{{ t('loreBooksCreate') }}</button>
      <button :disabled="busy" @click="emit('import')">{{ t('import') }}</button>
      <button :disabled="busy" @click="emit('export')">{{ t('export') }}</button>
    </div>

    <ul class="lorebook-sidebar__list">
      <li v-for="book in visibleBooks" :key="book.id" class="lorebook-sidebar__row">
        <button
          class="lorebook-sidebar__item"
          :class="{ active: book.id === activeId, disabled: book.enabled === false }"
          @click="emit('select', book.id)"
        >
          <span class="lorebook-sidebar__name">{{ book.name }}</span>
          <small v-if="book.primary" class="lorebook-sidebar__badge">{{ t('loreBooksPrimaryBadge') }}</small>
          <small class="lorebook-sidebar__scope-badge">{{ scopeLabel(book) }}</small>
          <small class="lorebook-sidebar__state">{{ book.enabled === false ? t('loreBooksStateDisabled') : t('loreBooksStateEnabled') }}</small>
        </button>
        <div class="lorebook-sidebar__row-actions">
          <button :disabled="busy" :aria-label="t('loreBooksRenameAria', { name: book.name })" @click="emit('rename', book)">{{ t('loreBooksRename') }}</button>
          <button :disabled="busy" :aria-label="t('loreBooksBindAria', { name: book.name })" @click="emit('bindings', book)">{{ t('loreBooksBindings') }}</button>
          <button
            :disabled="busy"
            :aria-label="t('loreBooksToggleAria', { action: book.enabled === false ? t('loreBooksEnable') : t('loreBooksDisable'), name: book.name })"
            @click="emit('toggle-enabled', book)"
          >{{ book.enabled === false ? t('loreBooksEnable') : t('loreBooksDisable') }}</button>
          <!-- primary world book 不可删除：删掉它世界就没有主世界书了 -->
          <button
            v-if="!book.primary"
            class="danger"
            :disabled="busy"
            :aria-label="t('loreBooksDeleteAria', { name: book.name })"
            @click="emit('remove', book)"
          >{{ t('loreBooksDelete') }}</button>
        </div>
      </li>
    </ul>

    <p v-if="!books.length" class="muted">{{ t('loreBooksEmptyAll') }}</p>
    <p v-else-if="!visibleBooks.length" class="muted">{{ t('loreBooksEmptyFilter') }}</p>
  </aside>
</template>
