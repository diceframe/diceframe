<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { NCollapse, NCollapseItem, NButton, NInput, NSwitch, NSelect, NInputNumber, NTag, NSpin, NTabs, NTabPane } from 'naive-ui'
import { api, errorMessage } from '@/api/client'
import { useToast } from '@/composables/useToast'
import type { PluginInfo, PluginField } from '@/api/types'
import NapcatGuide from '@/components/plugins/NapcatGuide.vue'

const toast = useToast()
const plugins = ref<PluginInfo[]>([])
const expandedPluginNames = ref<string[]>([])
const loading = ref(false)
const busy = ref('')

async function load() {
  loading.value = true
  try {
    const r = await api<{ plugins: PluginInfo[] }>('/plugins')
    plugins.value = r.plugins || []
    if (!expandedPluginNames.value.length) expandedPluginNames.value = plugins.value.map(p => p.id)
  } catch (e: unknown) {
    toast.error(errorMessage(e))
  } finally {
    loading.value = false
  }
}

function ordered(p: PluginInfo): [string, PluginField][] {
  return Object.entries(p.schema?.properties || {}).sort((a, b) => (a[1].ui?.order || 0) - (b[1].ui?.order || 0))
}
function value(p: PluginInfo, key: string, field: PluginField): unknown {
  const v = p.config?.[key]
  return typeof v === 'object' && field.ui?.sensitive ? '' : v ?? field.default ?? ''
}
function textValue(p: PluginInfo, key: string, field: PluginField): string {
  const v = value(p, key, field)
  return typeof v === 'string' ? v : v === undefined || v === null ? '' : String(v)
}
function selectValue(p: PluginInfo, key: string, field: PluginField): string | number | null {
  const v = value(p, key, field)
  return typeof v === 'string' || typeof v === 'number' ? v : null
}
function numberValue(p: PluginInfo, key: string, field: PluginField): number | null {
  const v = value(p, key, field)
  return typeof v === 'number' ? v : v === '' || v === null || v === undefined ? null : Number(v)
}
function set(p: PluginInfo, key: string, v: unknown) {
  if (!p.config) p.config = {}
  p.config[key] = v
}
function listValue(p: PluginInfo, key: string, field: PluginField): string[] {
  const v = value(p, key, field)
  return Array.isArray(v) ? v : []
}
function secretPlaceholder(p: PluginInfo, key: string, field: PluginField): string {
  const v = p.config?.[key] as { configured?: boolean; masked?: string } | undefined
  return field.ui?.sensitive && v?.configured ? `已配置 ${v.masked}，留空不修改` : ''
}
function showGroup(fields: [string, PluginField][], index: number): boolean {
  const group = fields[index][1].ui?.group
  return !!group && (index === 0 || fields[index - 1][1].ui?.group !== group)
}
function parseList(input: string): string[] {
  return Array.from(new Set(input.split(/[\n,]+/).map(x => x.trim()).filter(Boolean)))
}
function validate(p: PluginInfo): string {
  for (const [key, field] of ordered(p)) {
    const v = value(p, key, field)
    if (field.type === 'number' || field.type === 'integer') {
      const n = Number(v)
      if (field.exclusiveMinimum !== undefined && n <= field.exclusiveMinimum) return `${field.title || key} 必须大于 ${field.exclusiveMinimum}`
      if (field.minimum !== undefined && n < field.minimum) return `${field.title || key} 不能小于 ${field.minimum}`
      if (field.maximum !== undefined && n > field.maximum) return `${field.title || key} 不能大于 ${field.maximum}`
    }
    if (field.type === 'string') {
      const s = String(v || '')
      if (field.minLength !== undefined && s.length > 0 && s.length < field.minLength) return `${field.title || key} 至少 ${field.minLength} 位`
      if (field.maxLength !== undefined && s.length > field.maxLength) return `${field.title || key} 最多 ${field.maxLength} 位`
    }
  }
  return ''
}
async function save(p: PluginInfo) {
  const err = validate(p)
  if (err) { toast.error(err); return }
  busy.value = p.id
  try {
    const payload: Record<string, unknown> = {}
    for (const [key, field] of ordered(p)) {
      const current = p.config?.[key]
      if (field.ui?.sensitive) {
        if (typeof current === 'string' && current.trim()) payload[key] = current
      } else if (current !== undefined) {
        payload[key] = current
      }
    }
    await api(`/plugins/${encodeURIComponent(p.id)}/config`, { method: 'PUT', body: JSON.stringify(payload) })
    toast.success(`${p.name} 已保存`)
    await load()
  } catch (e: unknown) {
    toast.error(errorMessage(e))
  } finally {
    busy.value = ''
  }
}
async function restart(p: PluginInfo) {
  busy.value = p.id
  try {
    await api(`/plugins/${encodeURIComponent(p.id)}/restart`, { method: 'POST' })
    toast.success(`${p.name} 已请求重启`)
    await load()
  } catch (e: unknown) {
    toast.error(errorMessage(e))
  } finally {
    busy.value = ''
  }
}
async function clearCardCache(p: PluginInfo) {
  if (!window.confirm('确定清理 QQ 卡片缓存吗？只会删除 data/bot/cards 里的临时 card_*.png。')) return
  busy.value = `${p.id}:card-cache`
  try {
    const r = await api<{ deleted?: number; bytes_deleted?: number }>(`/plugins/${encodeURIComponent(p.id)}/card-cache/clear`, { method: 'POST' })
    const deleted = r.deleted || 0
    const mb = ((r.bytes_deleted || 0) / 1024 / 1024).toFixed(2)
    toast.success(`已清理 ${deleted} 张卡片，释放 ${mb} MB`)
  } catch (e: unknown) {
    toast.error(errorMessage(e))
  } finally {
    busy.value = ''
  }
}
async function toggleRunning(p: PluginInfo, on: boolean) {
  busy.value = p.id
  try {
    await api(`/plugins/${encodeURIComponent(p.id)}/${on ? 'start' : 'stop'}`, { method: 'POST' })
    toast.success(`${p.name} 已${on ? '启动' : '停止'}`)
    await load()
  } catch (e: unknown) {
    toast.error(errorMessage(e))
  } finally {
    busy.value = ''
  }
}
onMounted(load)
</script>

<template>
  <NSpin :show="loading">
    <p v-if="!plugins.length" class="muted">暂无可用插件。</p>

    <NCollapse v-model:expanded-names="expandedPluginNames">
      <NCollapseItem v-for="p in plugins" :key="p.id" :name="p.id" class="plugin-collapsible">
        <template #header>
          <div class="plugin-head">
            <h3>{{ p.name }}</h3>
            <p class="muted">{{ p.description }}</p>
          </div>
        </template>
        <template #header-extra>
          <div class="plugin-extra" @click.stop>
            <NTag :type="p.running ? 'success' : 'default'" size="small">{{ p.status }}</NTag>
            <NSwitch :value="p.running" :disabled="busy === p.id" @update:value="toggleRunning(p, $event)" />
          </div>
        </template>

      <NTabs type="line" animated class="plugin-tabs">
        <NTabPane name="config" tab="配置">
          <div class="plugin-form-grid">
            <template v-for="(entry, i) in ordered(p)" :key="entry[0]">
              <h4 v-if="showGroup(ordered(p), i)" class="field-group">{{ entry[1].ui?.group }}</h4>
              <div class="field" :class="{ 'field-wide': entry[1].type === 'array' }">
                <label v-if="entry[1].type === 'boolean'" class="switch-label">
                  <NSwitch :value="!!value(p, entry[0], entry[1])" :aria-label="entry[1].title || entry[0]" @update:value="set(p, entry[0], $event)" />
                  <span>{{ entry[1].title || entry[0] }}</span>
                </label>
                <label v-else class="input-label">
                  <span class="field-title">{{ entry[1].title || entry[0] }}</span>
                  <NSelect
                    v-if="entry[1].enum"
                    :value="selectValue(p, entry[0], entry[1])"
                    :options="(entry[1].enum || []).map(x => ({ label: x, value: x }))"
                    @update:value="set(p, entry[0], $event)"
                  />
                  <NInput
                    v-else-if="entry[1].type === 'array'"
                    type="textarea"
                    :rows="4"
                    :input-props="{ 'aria-label': entry[1].title || entry[0] }"
                    :value="listValue(p, entry[0], entry[1]).join('\n')"
                    placeholder="每行一个，或用逗号分隔（自动去重）"
                    @update:value="set(p, entry[0], parseList($event))"
                  />
                  <NInput
                    v-else-if="entry[1].ui?.sensitive"
                    type="password"
                    show-password-on="click"
                    :placeholder="secretPlaceholder(p, entry[0], entry[1])"
                    :value="textValue(p, entry[0], entry[1])"
                    @update:value="set(p, entry[0], $event)"
                  />
                  <NInputNumber
                    v-else-if="entry[1].type === 'number' || entry[1].type === 'integer'"
                    :value="numberValue(p, entry[0], entry[1])"
                    @update:value="set(p, entry[0], $event)"
                  />
                  <NInput
                    v-else
                    :value="textValue(p, entry[0], entry[1])"
                    @update:value="set(p, entry[0], $event)"
                  />
                </label>
                <small v-if="entry[1].description" class="muted">{{ entry[1].description }}</small>
              </div>
            </template>
          </div>
        </NTabPane>
        <NTabPane v-if="p.id === 'qq-napcat'" name="guide" tab="说明文档">
          <NapcatGuide />
        </NTabPane>
      </NTabs>

      <div class="actions-row">
        <NButton type="primary" :loading="busy === p.id" @click="save(p)">保存配置</NButton>
        <NButton :loading="busy === p.id" @click="restart(p)">重启插件</NButton>
        <NButton v-if="p.id === 'qq-napcat'" secondary :loading="busy === `${p.id}:card-cache`" @click="clearCardCache(p)">清理卡片缓存</NButton>
      </div>
      <p class="muted hint">修改令牌 / 连接参数后，需重启插件才会生效。</p>
      </NCollapseItem>
    </NCollapse>
  </NSpin>
</template>

<style scoped>
.plugin-head h3 {
  margin: 0;
}

.plugin-head p {
  margin: 4px 0 0;
}

.plugin-extra,
.actions-row {
  display: flex;
  gap: 10px;
  align-items: center;
  flex-wrap: wrap;
}

.plugin-tabs {
  margin-top: 4px;
}

.plugin-form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(260px, 1fr));
  gap: 14px 18px;
  align-items: start;
}

.field-group {
  grid-column: 1 / -1;
  margin: 10px 0 -2px;
  padding-top: 10px;
  border-top: 1px solid rgba(255, 255, 255, .08);
  color: var(--gold-2, #d99b45);
  font-size: 14px;
}

.field-group:first-child {
  margin-top: 0;
  padding-top: 0;
  border-top: none;
}

.field {
  min-width: 0;
}

.field-wide {
  grid-column: 1 / -1;
}

.input-label {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.switch-label {
  display: flex;
  gap: 10px;
  align-items: center;
  min-height: 34px;
}

.field-title {
  font-size: 13px;
  color: var(--text, #d7d1c5);
}

.field small {
  display: block;
  margin-top: 5px;
  line-height: 1.45;
}

.actions-row {
  margin-top: 16px;
}

.hint {
  margin-top: 8px;
}

@media (max-width: 860px) {
  .plugin-form-grid {
    grid-template-columns: 1fr;
  }
}
</style>
