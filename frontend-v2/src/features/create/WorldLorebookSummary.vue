<script setup lang="ts">
import { computed, onScopeDispose, ref, watch } from 'vue'
import { api } from '@/api/client'
import { useLocale } from '@/composables/useLocale'
import { projectCreationLorebooks, type CreationLorebook } from './creationLorebooks'

const props = withDefaults(defineProps<{
  worldId: string
  source: string
  manageHref: string
  /** `world` opens the library world; `module` opens the pack's resources; `none` explains why editing is closed. */
  manageKind?: 'world' | 'module' | 'none'
  modelValue?: string[]
  extraBooks?: CreationLorebook[]
}>(), { manageKind: 'world' })
const emit = defineEmits<{ 'update:modelValue': [value: string[]]; 'update:books': [value: CreationLorebook[]]; ready: [value: boolean] }>()
const { locale } = useLocale()
const copy = computed(() => {
  const all = {
    'zh-CN': { title: '本局使用的资料来源', loading: '正在读取资料册…', unavailable: '暂时无法确认资料册，请重试。', retry: '重试', manage: '编辑这个世界的资料册', empty: '目前没有匹配的已保存资料册。', primary: '主册', world: '当前世界', global: '全局共用', reference: '引用资料', pack: '内容包', active: '本局会使用', inactive: '本局不使用', entries: '条', noEntries: '暂无条目', carried: '随世界带入', packHint: '开局时加入书库', inherit: '沿用当前世界的资料册；勾选或取消只影响本局。', blank: '将创建空白的独立世界；下方仍可选择共用资料。', referenced: '将创建独立世界，只读取来源世界的资料册；条目不会复制。', limitation: '上方可勾选本局真正要使用的资料册，不会改动原世界。', shared: '编辑来源资料册会影响同样引用它的其他游戏。', managePack: '查看包内资料', readonlySource: '这个世界来自内容包或内置模板，资料册随来源只读；可在内容包页查看或创建用户副本。' },
    en: { title: 'Books used by this game', loading: 'Loading books…', unavailable: 'Books could not be confirmed. Please retry.', retry: 'Retry', manage: 'Manage the source world’s books', empty: 'No matching saved books yet.', primary: 'Primary', world: 'World', global: 'Global', reference: 'Primary reference', pack: 'Content pack', active: 'Will use', inactive: 'Inactive', entries: 'entries', noEntries: 'No entries yet', carried: 'Comes with the world', packHint: 'Added to your books when the game starts', inherit: 'Enabled books are selected by default; global books still follow their scope.', blank: 'The new world has a blank primary book; enabled global books still apply.', referenced: 'Create an independent world and reference selected books from the source; entries are not copied.', limitation: 'Enabled books are selected by default; you can choose a subset for this game.', shared: 'Changes to source books affect other games that reference them.', managePack: 'Open the pack’s resources', readonlySource: 'This world comes from a content pack or built-in template, so its books are read-only at the source. Inspect them on the pack page or create a user copy.' },
    de: { title: 'Bücher für dieses Spiel', loading: 'Bücher werden geladen…', unavailable: 'Bücher konnten nicht bestätigt werden.', retry: 'Erneut versuchen', manage: 'Bücher der Quellwelt verwalten', empty: 'Keine passenden gespeicherten Bücher.', primary: 'Hauptbuch', world: 'Welt', global: 'Global', reference: 'Hauptbuch-Verweis', pack: 'Inhaltspaket', active: 'Wird verwendet', inactive: 'Inaktiv', entries: 'Einträge', noEntries: 'Noch keine Einträge', carried: 'Kommt mit der Welt', packHint: 'Wird beim Spielstart zur Bibliothek hinzugefügt', inherit: 'Aktivierte Bücher werden standardmäßig ausgewählt; globale Bücher folgen ihrem Geltungsbereich.', blank: 'Die neue Welt hat ein leeres Hauptbuch; globale Bücher gelten weiterhin.', referenced: 'Eine eigene Welt wird erstellt, die auf ausgewählte Bücher der Quelle verweist; Einträge werden nicht kopiert.', limitation: 'Aktivierte Bücher sind vorausgewählt; du kannst für dieses Spiel eine Auswahl treffen.', shared: 'Änderungen an Quellbüchern betreffen andere Spiele, die darauf verweisen.', managePack: 'Paketressourcen öffnen', readonlySource: 'Diese Welt stammt aus einem Inhaltspaket oder einer integrierten Vorlage; ihre Bücher sind an der Quelle schreibgeschützt. Sieh sie auf der Paketseite ein oder erstelle eine eigene Kopie.' },
    ja: { title: 'このゲームで使う資料集', loading: '資料集を読み込み中…', unavailable: '資料集を確認できません。', retry: '再試行', manage: '参照元の世界の資料集を管理', empty: '一致する保存済み資料集はありません。', primary: 'メイン', world: '世界', global: '全体', reference: 'メイン参照', pack: 'コンテンツパック', active: '使用', inactive: '無効', entries: '項目', noEntries: '項目はまだありません', carried: '世界と一緒に適用', packHint: 'ゲーム開始時にライブラリへ追加', inherit: '有効な資料集を初期選択し、全体資料集は範囲に従います。', blank: '新しい世界には空白のメイン資料集があり、全体の資料集も使用されます。', referenced: '独立した世界を作り、参照元から選んだ資料集を参照します。項目はコピーされません。', limitation: '有効な資料集が初期選択され、このゲーム用に絞り込めます。', shared: '参照元の変更は、同じ資料集を参照する他のゲームにも影響します。', managePack: 'パック内の資料を見る', readonlySource: 'この世界はコンテンツパックまたは組み込みテンプレートのため、資料集は参照元では読み取り専用です。パックのページで確認するか、ユーザー用の複製を作成してください。' },
    ru: { title: 'Книги для этой игры', loading: 'Загрузка книг…', unavailable: 'Не удалось подтвердить книги.', retry: 'Повторить', manage: 'Книги исходного мира', empty: 'Подходящих сохранённых книг нет.', primary: 'Основная', world: 'Мир', global: 'Глобальная', reference: 'Ссылка на основную', pack: 'Контент-пакет', active: 'Используется', inactive: 'Неактивна', entries: 'записей', noEntries: 'Записей пока нет', carried: 'Идёт вместе с миром', packHint: 'Добавляется в библиотеку при старте игры', inherit: 'Активные книги выбраны по умолчанию, глобальные книги следуют области действия.', blank: 'Новый мир имеет пустую основную книгу; глобальные книги всё ещё используются.', referenced: 'Создаётся отдельный мир со ссылками на выбранные книги источника; записи не копируются.', limitation: 'Активные книги выбраны заранее; для этой игры можно оставить только нужные.', shared: 'Изменения исходных книг затрагивают другие игры, ссылающиеся на них.', managePack: 'Ресурсы пакета', readonlySource: 'Этот мир поставляется контент-пакетом или встроенным шаблоном, поэтому его книги доступны только для чтения. Откройте их на странице пакета или создайте пользовательскую копию.' },
  }
  return all[locale.value as keyof typeof all] || all.en
})
const explainer = computed(() => ({
  'zh-CN': '选择只影响本局；编辑资料册会影响其他引用它的游戏。',
  en: 'Selection affects only this game; editing books affects other games that reference them.',
  de: 'Die Auswahl gilt nur für dieses Spiel; Buchänderungen betreffen andere Spiele mit denselben Verweisen.',
  ja: '選択はこのゲームだけに適用されます。資料集の編集は同じ資料を参照する他のゲームにも影響します。',
  ru: 'Выбор действует только в этой игре; изменения книг влияют на другие игры со ссылками на них.',
}[locale.value] || 'Selection affects only this game; editing books affects other games that reference them.'))
const books = ref<CreationLorebook[]>([])
const loading = ref(false)
const failed = ref(false)
// Module-declared books (issue #700): catalog candidates supplied by the page.
// They merge with saved books for both display and the emitted selection model.
const mergedBooks = computed(() => [...books.value, ...(props.extraBooks || [])])
const sourceWorld = computed(() => props.source.startsWith('copy:') ? props.source.slice(5) : props.source === '__builtin__' ? props.worldId : '')
const rows = computed(() => projectCreationLorebooks(mergedBooks.value, props.worldId, props.source))
const hint = computed(() => props.source === '__blank__' ? copy.value.blank : props.source.startsWith('copy:') ? copy.value.referenced : copy.value.inherit)
const selectionSummary = computed(() => {
  if (loading.value) return copy.value.loading
  if (failed.value) return copy.value.unavailable
  const count = rows.value.filter(book => book.active && book.selectable && (props.modelValue === undefined || props.modelValue.includes(book.id))).length
  const labels = {
    'zh-CN': { selected: `已选 ${count} 册`, blank: '新世界 · 空白资料', reference: '新世界 · 引用资料' },
    en: { selected: `${count} books selected`, blank: 'New world · blank books', reference: 'New world · referenced books' },
    de: { selected: `${count} Bücher ausgewählt`, blank: 'Neue Welt · leere Bücher', reference: 'Neue Welt · Buchverweise' },
    ja: { selected: `${count} 冊を選択`, blank: '新しい世界 · 空白の資料', reference: '新しい世界 · 資料を参照' },
    ru: { selected: `Выбрано книг: ${count}`, blank: 'Новый мир · пустые книги', reference: 'Новый мир · ссылки на книги' },
  }
  const label = labels[locale.value as keyof typeof labels] || labels.en
  return props.source === '__blank__' ? `${label.blank} · ${label.selected}` : props.source.startsWith('copy:') ? `${label.reference} · ${label.selected}` : label.selected
})
const compactCopy = computed(() => ({
  'zh-CN': { title: '本局资料', adjust: '调整' }, en: { title: 'Game books', adjust: 'Adjust' },
  de: { title: 'Spielbücher', adjust: 'Anpassen' }, ja: { title: 'このゲームの資料', adjust: '変更' }, ru: { title: 'Книги игры', adjust: 'Изменить' },
}[locale.value] || { title: 'Game books', adjust: 'Adjust' }))
let epoch = 0
async function load() {
  const request = ++epoch
  books.value = []
  emit('ready', false)
  loading.value = true
  failed.value = false
  try {
    const response = await api<{ books: CreationLorebook[] }>(`/lorebooks?world_id=${encodeURIComponent(sourceWorld.value)}`)
    if (!Array.isArray(response.books)) throw new Error('Invalid book list')
    if (request === epoch) {
      books.value = response.books
      lastAutoSelection = null
      publishSelection()
      emit('ready', true)
    }
  } catch {
    if (request === epoch) failed.value = true
  } finally {
    if (request === epoch) loading.value = false
  }
}
// 最近一次「自动默认选择」的快照：modelValue 与它一致说明用户没有动过，
// 后到的候选（例如模块声明的资料册）仍可并入默认选择；一旦用户勾选/取消
// 过（或恢复草稿），modelValue 必然偏离快照，按用户选择对待。
let lastAutoSelection: string[] | null = null
function sameSelection(a: string[] | undefined, b: string[] | null): boolean {
  if (!a || b === null || a.length !== b.length) return false
  return a.every((id, index) => id === b[index])
}
function publishSelection() {
  const projected = projectCreationLorebooks(mergedBooks.value, props.worldId, props.source)
  emit('update:books', projected)
  const activeIds = projected.filter(book => book.active).map(book => book.id)
  const prior = props.modelValue
  const isAuto = prior === undefined || sameSelection(prior, lastAutoSelection)
  const next = isAuto ? activeIds : (prior || []).filter(id => activeIds.includes(id))
  lastAutoSelection = isAuto ? [...next] : null
  emit('update:modelValue', next)
}
watch(() => props.extraBooks, () => { if (epoch && !loading.value) publishSelection() })
function toggleBook(bookId: string, event: Event) {
  const book = rows.value.find(row => row.id === bookId)
  if (!book?.selectable) return
  const checked = (event.target as HTMLInputElement | null)?.checked === true
  const next = new Set(props.modelValue || [])
  if (checked) next.add(bookId)
  else next.delete(bookId)
  emit('update:modelValue', [...next])
}
watch([sourceWorld, () => props.source], load, { immediate: true })
onScopeDispose(() => { ++epoch })
</script>

<template>
  <div class="create-lorebook-settings">
  <details class="create-lorebook-summary" data-testid="create-lorebook-summary">
    <summary class="create-setup-summary"><span>{{ compactCopy.title }}</span><strong aria-live="polite">{{ selectionSummary }}</strong><span class="create-setup-adjust">{{ compactCopy.adjust }} <span aria-hidden="true">⌄</span></span></summary>
    <div class="create-lorebook-body">
    <slot name="source-settings" />
    <p v-if="source !== '__builtin__'">{{ hint }}</p>
    <ul v-if="!loading && !failed && rows.length">
      <li v-for="book in rows" :key="book.id" :data-active="book.active" :data-placeholder="book.placeholder">
        <label v-if="book.active && book.selectable" class="create-lorebook-check">
          <input type="checkbox" :checked="props.modelValue?.includes(book.id)" @change="toggleBook(book.id, $event)" />
          <span>
            <b>{{ book.name }}</b>
            <small>
              {{ copy[book.scope] }}<template v-if="book.primary"> · {{ copy.primary }}</template>
              <template v-if="book.placeholder"> · {{ copy.noEntries }}</template>
              <template v-else-if="typeof book.entry_count === 'number'"> · {{ book.entry_count }} {{ copy.entries }}</template>
              <template v-if="book.scope === 'pack' && book.catalog !== false"> · {{ copy.packHint }}</template>
            </small>
          </span>
        </label>
        <span v-else>
          <b>{{ book.name }}</b>
          <small>
            {{ copy[book.scope] }}<template v-if="book.primary"> · {{ copy.primary }}</template>
            <template v-if="book.placeholder"> · {{ copy.noEntries }}</template>
            <template v-else-if="typeof book.entry_count === 'number'"> · {{ book.entry_count }} {{ copy.entries }}</template>
          </small>
        </span>
        <span v-if="book.placeholder" class="create-lorebook-state create-lorebook-carried">{{ copy.carried }}</span>
        <span v-else class="create-lorebook-state">{{ book.active && props.modelValue?.includes(book.id) ? copy.active : copy.inactive }}</span>
      </li>
    </ul>
    <p v-else-if="!loading && !failed">{{ copy.empty }}</p>
    <footer><small>{{ explainer }}</small><a v-if="sourceWorld && manageHref && manageKind !== 'none'" :href="manageHref">{{ manageKind === 'module' ? copy.managePack : copy.manage }} ↗</a><span v-else-if="sourceWorld && manageKind === 'none'" class="create-lorebook-readonly">{{ copy.readonlySource }}</span></footer>
    </div>
  </details>
  <div v-if="failed" class="create-lorebook-error" role="alert">{{ copy.unavailable }} <button type="button" @click="load">{{ copy.retry }}</button></div>
  </div>
</template>

<style scoped>
.create-lorebook-summary{min-width:0;border:1px solid var(--df-border-soft);border-radius:var(--df-radius-sm);background:var(--df-control-bg)}
.create-lorebook-body{padding:0 14px 14px}
footer{display:flex;justify-content:space-between;align-items:center;gap:8px 16px;flex-wrap:wrap;margin-top:12px}
footer a{font-size:12px;color:var(--df-interactive-strong)}
footer .create-lorebook-readonly{max-width:52%;color:var(--df-text-muted);font-size:11px;line-height:1.6}
p{margin:8px 0;color:var(--df-text-secondary);font-size:12px}.create-lorebook-explainer{color:var(--df-accent-strong);font-weight:600}
ul{list-style:none;margin:10px 0;padding:0;display:grid;gap:7px}
li{display:flex;justify-content:space-between;align-items:center;gap:10px;padding:7px 0;border-top:1px solid var(--df-border-soft)}
.create-lorebook-check{display:flex;align-items:flex-start;gap:8px;min-width:0;cursor:pointer}.create-lorebook-check input{margin-top:3px;accent-color:var(--df-interactive-strong)}
li>span:first-child{min-width:0}li b{display:block;overflow-wrap:anywhere;font-size:12px}small{display:block;color:var(--df-text-muted);font-size:11px;line-height:1.6}
.create-lorebook-state{color:var(--df-success);font-size:11px;white-space:nowrap}li[data-active=false] .create-lorebook-state{color:var(--df-text-muted)}
.create-lorebook-carried{color:var(--df-text-muted)}
button{margin-left:6px}
.create-lorebook-error{margin-top:8px;font-size:12px;color:var(--df-text-secondary)}
</style>
