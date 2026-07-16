<script setup lang="ts">
import { computed, onMounted, ref, watch, type Component } from 'vue'
import { NButton, NInput, NInputNumber, NSwitch, NTag, NIcon, NCollapse, NCollapseItem, NSpin } from 'naive-ui'
import {
  ServerOutline, CubeOutline, CloudDownloadOutline, ExtensionPuzzleOutline,
  LockClosedOutline, OptionsOutline, InformationCircleOutline, ShareSocialOutline,
} from '@vicons/ionicons5'
import { useSettingsStore } from '@/stores/useSettingsStore'
import { useToast } from '@/composables/useToast'
import { useConfirm } from '@/composables/useConfirm'
import { useUpdateCheck } from '@/composables/useUpdateCheck'
import { errorMessage } from '@/api/client'
import type { SecretKey } from '@/stores/useSettingsStore'
import type { AppConfig, SecretField, TestResult } from '@/api/types'
import AppPage from '@/components/common/AppPage.vue'
import TestResultCard from '@/components/admin/TestResultCard.vue'
import ApiHelpButton from '@/components/common/ApiHelpButton.vue'
import PluginSettings from '../plugins/PluginSettings.vue'

type SectionId = 'api' | 'memory' | 'network' | 'sharing' | 'plugins' | 'access' | 'advanced' | 'about'
type StatusTone = 'default' | 'success' | 'warning' | 'error' | 'info'
type SystemStatusItem = { label: string; value: string; detail: string; tone: StatusTone }

const store = useSettingsStore()
const toast = useToast()
const { confirm } = useConfirm()
const { updateInfo, updateChecking, checkForUpdates } = useUpdateCheck()

const section = ref<SectionId>('api')
const sections: { id: SectionId; label: string; icon: Component }[] = [
  { id: 'api', label: '模型接口', icon: ServerOutline },
  { id: 'memory', label: '向量记忆', icon: CubeOutline },
  { id: 'network', label: '网络代理', icon: CloudDownloadOutline },
  { id: 'sharing', label: '分享地址', icon: ShareSocialOutline },
  { id: 'plugins', label: '插件', icon: ExtensionPuzzleOutline },
  { id: 'access', label: '访问密码', icon: LockClosedOutline },
  { id: 'advanced', label: '高级参数', icon: OptionsOutline },
  { id: 'about', label: '关于', icon: InformationCircleOutline },
]

const testing = ref(false)
const testResult = ref<TestResult | null>(null)
const testKind = ref<'model' | 'embedding' | 'proxy' | ''>('')

const password = ref('')
const password2 = ref('')
const locationOrigin = typeof window === 'undefined' ? 'http://localhost' : window.location.origin

const proxySourceLabel = computed(() => {
  const s = store.config.proxy_source
  if (s === 'config') return '配置文件'
  if (s === 'env') return '环境变量'
  if (s === 'disabled') return '已禁用'
  return '未配置'
})
const proxyFormatLabel = computed(() => (store.config.proxy_supported ? '格式可用' : '格式不支持'))
const updateTagType = computed<StatusTone>(() => {
  if (!updateInfo.value) return 'default'
  if (!updateInfo.value.ok) return 'error'
  if (updateInfo.value.no_release) return 'info'
  return updateInfo.value.update_available ? 'warning' : 'success'
})
const updateTagLabel = computed(() => {
  if (!updateInfo.value) return '未检查'
  if (!updateInfo.value.ok) return '检查失败'
  if (updateInfo.value.no_release) return '暂无发布版'
  return updateInfo.value.update_available ? '发现新版本' : '已是最新版'
})
function hasSecret(key: SecretKey, field?: SecretField) {
  return Boolean(store.secrets[key]?.trim() || field?.configured)
}
function apiFormatLabel(value?: unknown) {
  return value === 'anthropic' ? 'Anthropic' : 'OpenAI 兼容'
}

const systemStatusItems = computed<SystemStatusItem[]>(() => {
  const c = store.config
  const mainReady = Boolean(c.base_url && c.model && hasSecret('api_key', c.api_key))
  const fallbackSlots = [
    { name: '备用 1', enabled: !!c.fallback1_enabled, model: c.fallback1_model, ready: Boolean(c.fallback1_base_url && c.fallback1_model && hasSecret('fallback1_api_key', c.fallback1_api_key)) },
    { name: '备用 2', enabled: !!c.fallback2_enabled, model: c.fallback2_model, ready: Boolean(c.fallback2_base_url && c.fallback2_model && hasSecret('fallback2_api_key', c.fallback2_api_key)) },
  ]
  const enabledFallbacks = fallbackSlots.filter(item => item.enabled)
  const readyFallbacks = enabledFallbacks.filter(item => item.ready)
  const embeddingReady = Boolean(c.embedding_enabled && c.embedding_base_url && c.embedding_model && hasSecret('embedding_api_key', c.embedding_api_key))
  const proxyEnabled = !!c.proxy_enabled
  return [
    {
      label: '主模型',
      value: mainReady ? '配置完整' : '待补全',
      detail: `${apiFormatLabel(c.api_format)} · ${c.model || '未设置模型'} · ${c.base_url || '未设置接口'} · ${hasSecret('api_key', c.api_key) ? 'Key 已配置' : 'Key 缺失'}`,
      tone: mainReady ? 'success' : 'warning',
    },
    {
      label: '备用回退',
      value: enabledFallbacks.length ? `${readyFallbacks.length}/${enabledFallbacks.length} 路可用` : '未启用',
      detail: enabledFallbacks.length ? enabledFallbacks.map(item => `${item.name}: ${item.model || '未设置模型'}`).join(' · ') : '可在模型接口中展开备用模型回退',
      tone: !enabledFallbacks.length ? 'default' : readyFallbacks.length === enabledFallbacks.length ? 'success' : 'warning',
    },
    {
      label: '向量记忆',
      value: c.embedding_enabled ? (embeddingReady ? '已启用' : '配置不完整') : '未启用',
      detail: `${c.embedding_model || '未设置模型'} · ${c.embedding_base_url || '未设置接口'} · 输入上限 ${c.embedding_max_input || '自动'}`,
      tone: c.embedding_enabled ? (embeddingReady ? 'success' : 'warning') : 'default',
    },
    {
      label: '网络代理',
      value: proxyEnabled ? '已启用' : '未启用',
      detail: `${proxySourceLabel.value} · ${proxyFormatLabel.value}${c.proxy_url ? ` · ${c.proxy_url}` : ''}`,
      tone: proxyEnabled ? (c.proxy_supported === false ? 'error' : 'info') : 'default',
    },
    {
      label: '访问控制',
      value: c.access_password?.configured ? '已设置密码' : '未设置密码',
      detail: c.access_password?.configured ? `当前凭证 ${c.access_password.masked}` : '本机访问不需要登录密码',
      tone: c.access_password?.configured ? 'success' : 'default',
    },
  ]
})

onMounted(() => store.load())
watch(section, () => {
  const sc = document.querySelector('.n-layout-scroll-container') as HTMLElement | null
  sc?.scrollTo({ top: 0 })
})

function setStr(key: keyof AppConfig, v: string | number) { (store.config as Record<string, unknown>)[key] = String(v).trim() }
function setSecret(key: SecretKey, v: string | number) { store.secrets[key] = String(v).trim() }
function eventValue(event: Event) { return (event.target as HTMLSelectElement | null)?.value || '' }
function setNum(key: keyof AppConfig, v: string | number | null) {
  if (v === null || v === '') { (store.config as Record<string, unknown>)[key] = 0; return }
  ;(store.config as Record<string, unknown>)[key] = Number(v) || 0
}
function setBool(key: keyof AppConfig, v: string | number | boolean) { (store.config as Record<string, unknown>)[key] = Boolean(v) }

async function save(keys: string[], secretKeys: SecretKey[] = []) {
  try {
    await store.saveSection(keys, secretKeys)
    toast.success('配置已保存')
  } catch (e: unknown) {
    toast.error(errorMessage(e))
  }
}

async function runTest(kind: 'model' | 'embedding' | 'proxy') {
  testing.value = true
  testResult.value = null
  testKind.value = kind
  try {
    testResult.value = await store.test(kind)
  } catch (e: unknown) {
    toast.error(errorMessage(e))
  } finally {
    testing.value = false
  }
}

async function savePassword() {
  if (password.value.length < 6) { toast.error('访问密码至少 6 位'); return }
  if (password.value !== password2.value) { toast.error('两次输入不一致'); return }
  try {
    await store.saveAccessPassword(password.value)
    toast.success('访问密码已更新')
    password.value = password2.value = ''
  } catch (e: unknown) {
    toast.error(errorMessage(e))
  }
}

async function clearProxy() {
  const ok = await confirm({
    title: '关闭并清空代理',
    content: '将关闭代理并清空已配置的代理地址，确认继续？',
    type: 'warning',
    positiveText: '关闭并清空',
  })
  if (!ok) return
  try {
    await store.clearProxy()
    toast.success('代理已关闭并清空')
  } catch (e: unknown) {
    toast.error(errorMessage(e))
  }
}

async function checkUpdate() {
  try {
    const result = await checkForUpdates(true)
    if (!result?.ok) {
      toast.error(result?.error || '检查更新失败')
    } else if (result.no_release) {
      toast.success('仓库暂无公开 Release')
    } else if (result.update_available) {
      toast.success(`发现新版本 ${result.latest?.tag_name || result.latest?.version || ''}`)
    } else {
      toast.success('当前已是最新版')
    }
  } catch (e: unknown) {
    toast.error(errorMessage(e))
  }
}

function openUpdateUrl() {
  const url = updateInfo.value?.release_url || updateInfo.value?.releases_url || updateInfo.value?.source_url
  if (url) window.open(url, '_blank', 'noopener')
}
</script>

<template>
  <AppPage title="设置" subtitle="模型接口、生成参数与系统配置">
    <template #actions>
      <NButton :loading="store.loading" @click="store.load()">刷新</NButton>
    </template>

    <p v-if="store.error" class="error-banner">{{ store.error }}</p>
    <section class="system-status-grid" aria-label="系统状态总览">
      <article v-for="item in systemStatusItems" :key="item.label" class="system-status-card">
        <div class="system-status-head">
          <span>{{ item.label }}</span>
          <NTag :type="item.tone" size="small" round>{{ item.value }}</NTag>
        </div>
        <p>{{ item.detail }}</p>
      </article>
    </section>
    <div class="settings-layout">
      <aside class="settings-nav">
        <button
          v-for="s in sections"
          :key="s.id"
          :class="['nav-item', { active: section === s.id }]"
          @click="section = s.id"
        >
          <NIcon :component="s.icon" />
          <span>{{ s.label }}</span>
        </button>
      </aside>

      <div class="settings-content">
        <NSpin :show="store.loading">
          <!-- 模型接口 -->
          <div v-show="section === 'api'" class="settings-pane">
            <div class="api-head-row"><h3>主模型接口</h3><ApiHelpButton /></div>
            <div class="form-row">
              <label>接口格式</label>
              <select :value="store.config.api_format ?? 'openai'" @change="setStr('api_format', eventValue($event))">
                <option value="openai">OpenAI 兼容</option>
                <option value="anthropic">Anthropic</option>
              </select>
            </div>
            <div class="form-row">
              <label>Base URL</label>
              <NInput
                :value="store.config.base_url ?? ''"
                :placeholder="store.config.api_format === 'anthropic' ? 'https://api.anthropic.com' : 'https://api.openai.com/v1'"
                @update:value="setStr('base_url', $event)"
              />
            </div>
            <div class="form-row">
              <label>API Key</label>
              <NInput
                :value="store.secrets.api_key ?? ''"
                type="password"
                show-password-on="click"
                :placeholder="store.config.api_key?.configured ? `已配置 ${store.config.api_key.masked}，留空不修改` : ''"
                @update:value="setSecret('api_key', $event)"
              />
            </div>
            <div class="form-row">
              <label>模型</label>
              <NInput
                :value="store.config.model ?? ''"
                :placeholder="store.config.api_format === 'anthropic' ? 'claude-3-5-sonnet-latest' : 'gpt-4o-mini'"
                @update:value="setStr('model', $event)"
              />
            </div>
            <div class="actions-row">
              <NButton type="primary" @click="save(['api_format', 'base_url', 'model'], ['api_key'])">保存</NButton>
              <NButton :loading="testing && testKind === 'model'" @click="runTest('model')">测试连接</NButton>
            </div>
            <TestResultCard v-if="testKind === 'model' && testResult" :result="testResult" kind="model" />

            <NCollapse :default-expanded-names="[]">
              <NCollapseItem title="备用模型回退" name="fallback">
                <div class="form-row"><label>备用 1</label><div class="switch-inline"><NSwitch :value="!!store.config.fallback1_enabled" @update:value="setBool('fallback1_enabled', $event)" /><span>启用</span></div></div>
                <div class="form-row"><label>接口格式</label><select :value="store.config.fallback1_api_format ?? 'openai'" @change="setStr('fallback1_api_format', eventValue($event))"><option value="openai">OpenAI 兼容</option><option value="anthropic">Anthropic</option></select></div>
                <div class="form-row"><label>Base URL</label><NInput :value="store.config.fallback1_base_url ?? ''" @update:value="setStr('fallback1_base_url', $event)" /></div>
                <div class="form-row"><label>API Key</label><NInput :value="store.secrets.fallback1_api_key ?? ''" type="password" show-password-on="click" @update:value="setSecret('fallback1_api_key', $event)" /></div>
                <div class="form-row"><label>模型</label><NInput :value="store.config.fallback1_model ?? ''" @update:value="setStr('fallback1_model', $event)" /></div>
                <div class="form-row"><label>备用 2</label><div class="switch-inline"><NSwitch :value="!!store.config.fallback2_enabled" @update:value="setBool('fallback2_enabled', $event)" /><span>启用</span></div></div>
                <div class="form-row"><label>接口格式</label><select :value="store.config.fallback2_api_format ?? 'openai'" @change="setStr('fallback2_api_format', eventValue($event))"><option value="openai">OpenAI 兼容</option><option value="anthropic">Anthropic</option></select></div>
                <div class="form-row"><label>Base URL</label><NInput :value="store.config.fallback2_base_url ?? ''" @update:value="setStr('fallback2_base_url', $event)" /></div>
                <div class="form-row"><label>API Key</label><NInput :value="store.secrets.fallback2_api_key ?? ''" type="password" show-password-on="click" @update:value="setSecret('fallback2_api_key', $event)" /></div>
                <div class="form-row"><label>模型</label><NInput :value="store.config.fallback2_model ?? ''" @update:value="setStr('fallback2_model', $event)" /></div>
                <div class="actions-row">
                  <NButton type="primary" @click="save(['fallback1_enabled', 'fallback1_api_format', 'fallback1_base_url', 'fallback1_model', 'fallback2_enabled', 'fallback2_api_format', 'fallback2_base_url', 'fallback2_model'], ['fallback1_api_key', 'fallback2_api_key'])">保存备用模型</NButton>
                </div>
              </NCollapseItem>
            </NCollapse>
          </div>

          <!-- 向量记忆 -->
          <div v-show="section === 'memory'" class="settings-pane">
            <h3>向量记忆</h3>
            <div class="form-row"><label>向量记忆</label><div class="switch-inline"><NSwitch :value="!!store.config.embedding_enabled" @update:value="setBool('embedding_enabled', $event)" /><span>启用</span></div></div>
            <div class="form-row"><label>向量接口</label><NInput :value="store.config.embedding_base_url ?? ''" @update:value="setStr('embedding_base_url', $event)" /></div>
            <div class="form-row">
              <label>API Key</label>
              <NInput
                :value="store.secrets.embedding_api_key ?? ''"
                type="password"
                show-password-on="click"
                :placeholder="store.config.embedding_api_key?.configured ? `已配置 ${store.config.embedding_api_key.masked}，留空不修改` : ''"
                @update:value="setSecret('embedding_api_key', $event)"
              />
            </div>
            <div class="form-row"><label>模型</label><NInput :value="store.config.embedding_model ?? ''" @update:value="setStr('embedding_model', $event)" /></div>
            <div class="form-row"><label>最大输入</label><NInputNumber :value="store.config.embedding_max_input ?? 0" @update:value="setNum('embedding_max_input', $event)" style="width:100%" /></div>
            <p class="form-hint">最大输入：单条文本字符上限，超过会截断。填 0 = 按模型名自动推断（如 bge-large-zh 500、nomic-embed-text 8000）。</p>
            <div class="actions-row">
              <NButton type="primary" @click="save(['embedding_enabled', 'embedding_base_url', 'embedding_model', 'embedding_max_input'], ['embedding_api_key'])">保存</NButton>
              <NButton :loading="testing && testKind === 'embedding'" @click="runTest('embedding')">测试向量连接</NButton>
            </div>
            <TestResultCard v-if="testKind === 'embedding' && testResult" :result="testResult" kind="embedding" />
          </div>

          <!-- 网络代理 -->
          <div v-show="section === 'network'" class="settings-pane">
            <h3>网络代理</h3>
            <div class="proxy-status">
              <NTag :type="store.config.proxy_source ? 'info' : 'default'" size="small">来源: {{ proxySourceLabel }}</NTag>
              <NTag :type="store.config.proxy_supported ? 'success' : 'error'" size="small">格式: {{ proxyFormatLabel }}</NTag>
            </div>
            <div class="form-row"><label>代理</label><div class="switch-inline"><NSwitch :value="!!store.config.proxy_enabled" @update:value="setBool('proxy_enabled', $event)" /><span>启用</span></div></div>
            <div class="form-row">
              <label>代理地址</label>
              <NInput :value="store.secrets.proxy_url ?? ''" :placeholder="store.config.proxy_url || 'http://127.0.0.1:端口'" @update:value="setSecret('proxy_url', $event)" />
            </div>
            <div class="actions-row">
              <NButton type="primary" @click="save(['proxy_enabled'], ['proxy_url'])">保存</NButton>
              <NButton :loading="testing && testKind === 'proxy'" @click="runTest('proxy')">测试连接</NButton>
              <NButton @click="clearProxy">关闭并清空</NButton>
            </div>
            <p class="muted">代理地址留空则保留旧值；"关闭并清空"会同时关闭代理并清除已配置地址。</p>
            <TestResultCard v-if="testKind === 'proxy' && testResult" :result="testResult" kind="proxy" />
          </div>

          <!-- 分享地址 -->
          <div v-show="section === 'sharing'" class="settings-pane">
            <h3>分享链接地址</h3>
            <p class="muted">邀请链接默认使用当前浏览器地址。公网、NAS、Docker 或反向代理部署时，在这里填写玩家真正能访问到的外部地址。</p>
            <div class="form-row">
              <label>公开访问地址</label>
              <NInput
                :value="store.config.public_base_url ?? ''"
                placeholder="例如 https://trpg.example.com 或 http://nas.local:18000"
                @update:value="setStr('public_base_url', $event)"
              />
            </div>
            <p class="form-hint">留空：使用当前页面地址（{{ locationOrigin }}）。不要填写 /#/join，系统会自动拼接邀请路径；如果部署在反向代理子路径，可以填写 https://example.com/trpg。</p>
            <div class="actions-row">
              <NButton type="primary" @click="save(['public_base_url'])">保存分享地址</NButton>
            </div>
          </div>
          <!-- 插件 -->
          <div v-show="section === 'plugins'" class="settings-pane">
            <PluginSettings />
          </div>

          <!-- 访问密码 -->
          <div v-show="section === 'access'" class="settings-pane">
            <h3>访问密码</h3>
            <p class="muted">设置后所有页面访问需登录；留空表示不启用。修改密码后会自动更新本地登录凭证。</p>
            <div class="form-row"><label>新密码</label><NInput v-model:value="password" type="password" show-password-on="click" placeholder="至少 6 位" /></div>
            <div class="form-row"><label>再次输入</label><NInput v-model:value="password2" type="password" show-password-on="click" /></div>
            <div class="actions-row">
              <NButton type="primary" @click="savePassword">保存密码</NButton>
            </div>
            <p v-if="store.config.access_password?.configured" class="muted">当前已设置访问密码（{{ store.config.access_password.masked }}）</p>
          </div>

          <!-- 高级参数 -->
          <div v-show="section === 'advanced'" class="settings-pane">
            <h3>生成参数</h3>
            <div class="form-row"><label>叙事 Token</label><NInputNumber :value="store.config.narrative_max_tokens ?? 0" @update:value="setNum('narrative_max_tokens', $event)" style="width:100%" /></div>
            <div class="form-row"><label>角色生成 Token</label><NInputNumber :value="store.config.character_gen_max_tokens ?? 0" @update:value="setNum('character_gen_max_tokens', $event)" style="width:100%" /></div>
            <div class="form-row"><label>摘要 Token</label><NInputNumber :value="store.config.summary_max_tokens ?? 0" @update:value="setNum('summary_max_tokens', $event)" style="width:100%" /></div>
            <div class="form-row"><label>简报 Token</label><NInputNumber :value="store.config.brief_max_tokens ?? 0" @update:value="setNum('brief_max_tokens', $event)" style="width:100%" /></div>
            <div class="form-row"><label>分析 Token</label><NInputNumber :value="store.config.analysis_max_tokens ?? 0" @update:value="setNum('analysis_max_tokens', $event)" style="width:100%" /></div>
            <div class="form-row"><label>文本生成 Token</label><NInputNumber :value="store.config.text_gen_max_tokens ?? 0" @update:value="setNum('text_gen_max_tokens', $event)" style="width:100%" /></div>
            <div class="actions-row">
              <NButton type="primary" @click="save(['narrative_max_tokens', 'character_gen_max_tokens', 'summary_max_tokens', 'brief_max_tokens', 'analysis_max_tokens', 'text_gen_max_tokens'])">保存</NButton>
            </div>
          </div>

          <!-- 关于 -->
          <div v-show="section === 'about'" class="settings-pane about">
            <section class="about-card">
              <h3>关于 DiceFrame</h3>
              <p>DiceFrame 是一张可以自己开起来的 AI 跑团桌。你只要给出一个想玩的世界，AI 就能当 GM 带大家进入故事；玩家在浏览器或群聊里说“我想做什么”，剧情、骰子、角色状态和前情都会被一起整理好。</p>
              <p>它适合熟人小团、临时脑洞、长期连载，也适合一个人先试跑世界。你可以玩奇幻、克苏鲁、赛博朋克、武侠仙侠，也可以把规则和世界书换成自己的味道。</p>
              <h4>你可以用它做什么</h4>
              <ul>
                <li>一句话创建冒险：从“凡人修仙传风格”到“雨夜赛博追凶”都能开局</li>
                <li>和朋友一起玩：分享网页链接，或把群聊接进同一局</li>
                <li>少操心杂务：角色卡、骰子、道具、金币、前情提要和私密感知都会帮你记</li>
                <li>保留桌游味道：规则是建议不是牢笼，GM 和玩家仍然可以自由裁定</li>
                <li>慢慢养成自己的团：世界书、规则模板和角色都可以继续改、继续玩</li>
              </ul>
              <h4>免责声明</h4>
              <p class="muted">本工具由 AI 生成叙事内容，可能包含不恰当或冒犯性文本，请自行甄别；内容仅供娱乐，不代表开发者立场。请遵守当地法律法规与所用模型的条款。</p>
              <h4>联系</h4>
              <p>项目地址：<a href="https://github.com/EOEOY/diceframe" target="_blank" rel="noopener">EOEOY/diceframe</a></p>
              <p>问题反馈：<a href="https://github.com/EOEOY/diceframe/issues" target="_blank" rel="noopener">提交 Issue</a></p>
              <p>QQ 群：1060613588</p>
            </section>
            <section class="update-card" aria-label="版本更新">
              <div class="update-card-head">
                <div>
                  <h4>版本更新</h4>
                </div>
                <NTag :type="updateTagType" size="small" round>{{ updateTagLabel }}</NTag>
              </div>
              <div class="update-meta">
                <span>当前版本：{{ updateInfo?.current_version || '点击检查后显示' }}</span>
                <span v-if="updateInfo?.latest">最新版本：{{ updateInfo.latest.tag_name || updateInfo.latest.version }}</span>
                <span v-if="updateInfo?.latest?.published_at">发布时间：{{ updateInfo.latest.published_at.slice(0, 10) }}</span>
              </div>
              <p v-if="updateInfo?.error" class="muted">检查失败：{{ updateInfo.error }}</p>
              <p v-else-if="updateInfo?.no_release" class="muted">{{ updateInfo.message || '仓库暂无公开 Release。' }}</p>
              <p v-else-if="updateInfo?.update_available" class="muted">有新版可用。升级前保留 data/ 目录；Docker 用户请用新版源码或镜像重新部署。</p>
              <div v-if="updateInfo?.latest?.body" class="update-notes">
                <strong>更新日志</strong>
                <pre>{{ updateInfo.latest.body }}</pre>
              </div>
              <div class="actions-row">
                <NButton :loading="updateChecking" @click="checkUpdate">检查更新</NButton>
                <NButton :disabled="!updateInfo?.release_url && !updateInfo?.releases_url && !updateInfo?.source_url" @click="openUpdateUrl">打开发布页</NButton>
              </div>
            </section>
            <section class="sponsor-card" aria-label="支持项目">
              <div>
                <h4>支持项目</h4>
                <p>如果 DiceFrame 对你有帮助，欢迎支持项目继续维护。</p>
              </div>
              <img src="/sponsor-wechat-qr.png" alt="微信赞助二维码" loading="lazy">
            </section>
          </div>
        </NSpin>
      </div>
    </div>
  </AppPage>
</template>
