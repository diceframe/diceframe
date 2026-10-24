<script setup lang="ts">
/**
 * 邀请 / 接管二维码：把加入链接同时以二维码和文本两种形态给出。
 *
 * 地址不再由对局页算死后传进来。原因是同一个问题「别的设备该访问哪个地址」在
 * 「手机扫码登录」那边早就有答案了——服务端探测的可达地址清单——而这里却只会用
 * public_base_url，没配就退回 location.origin，于是 GM 在本机开发时拿到一条
 * localhost 链接，还得被红字提示引去设置里手填。两处问同一件事，就该用同一份
 * 清单：这里复用 useReachableAddresses，让 GM 直接从候选里选。
 *
 * 服务端给的是 host 而不是完整 URL，端口沿用浏览器打开前端用的那个——独立前端与
 * 后端不同端口，内置前端同端口，换 host 这一种做法同时覆盖两种部署。
 *
 * 外壳（Modal + AddressPicker + QrStage）与扫码登录弹窗共用，两处扫码流程因此是
 * 同一套排版；「好友需要能访问该地址」的说明仍是静态辅助信息，不做网络探测，也
 * 不阻止复制。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import AddressPicker from '@/components/common/AddressPicker.vue'
import QrStage from '@/components/common/QrStage.vue'
import Modal from '@/components/ui/Modal.vue'
import { currentBackendUrl } from '@/api/connection'
import { useLocale } from '@/composables/useLocale'
import { useReachableAddresses } from '@/composables/useReachableAddresses'
import { useToast } from '@/composables/useToast'
import { useSettingsStore } from '@/stores/useSettingsStore'
import { copyToClipboard } from '@/utils/clipboard'
import {
  buildJoinLink,
  currentOrigin,
  isLoopbackUrl,
  normalizePublicBaseUrl,
  withHost,
} from '@/utils/shareLink'

const props = defineProps<{
  gameKey: string
  /** 单个席位的接管链接带该席位的凭证（GM 刚签发）；整局邀请不带 */
  seatToken?: string
  title: string
  hint?: string
}>()
const emit = defineEmits<{ close: [] }>()

const { t, locale } = useLocale()
const toast = useToast()
const router = useRouter()
const settings = useSettingsStore()
const { hosts, loading: addressesLoading, load: loadReachable } = useReachableAddresses()

const inviteHelpUrl = computed(() =>
  locale.value.startsWith('zh')
    ? 'https://github.com/diceframe/diceframe-content/blob/main/docs/zh/guide.md#外网联机'
    : 'https://github.com/diceframe/diceframe-content/blob/main/docs/en/guide.md#multiplayer',
)

const selectedBase = ref('')
// 候选地址回来之前不出码：默认值算出来之前的链接必然是本机 origin，先亮一条
// localhost 再换成局域网地址，等于让 GM 白看一眼「仅本机可访问」的红字。
const resolved = ref(false)

const frontendOrigin = computed(() => currentOrigin())

/** GM 明确配过的对外入口；配了它就是玩家该走的地址，排候选首位。 */
const configuredBase = computed(() => {
  const raw = String(settings.config.public_base_url || '').trim()
  return raw ? normalizePublicBaseUrl(raw) : ''
})

type BaseOption = { label: string; value: string; host?: string }

const baseOptions = computed<BaseOption[]>(() => {
  const candidates: { value: string; host?: string }[] = []
  if (configuredBase.value) candidates.push({ value: configuredBase.value })
  // 服务端按「局域网优先、回环垫底」给出 host，这个顺序直接决定默认选中哪个。
  for (const host of hosts.value) {
    const url = withHost(frontendOrigin.value, host)
    if (url) candidates.push({ value: url, host })
  }
  if (frontendOrigin.value) candidates.push({ value: frontendOrigin.value })

  const options: BaseOption[] = []
  const seen = new Set<string>()
  for (const entry of candidates) {
    if (seen.has(entry.value)) continue
    seen.add(entry.value)
    options.push({ label: entry.value, value: entry.value, host: entry.host })
  }
  return options
})

const selectedHost = computed(
  () => baseOptions.value.find((option) => option.value === selectedBase.value)?.host,
)

const link = computed(() => {
  const backend = currentBackendUrl()
  // 换了 host 就连 server 参数一起换：独立前端部署下玩家的设备既要打得开前端，
  // 也要连得上后端，两个地址必须指向同一台机器的同一张网卡。
  const server = backend && selectedHost.value ? withHost(backend, selectedHost.value) : backend
  return buildJoinLink(
    props.gameKey,
    selectedBase.value || undefined,
    props.seatToken,
    server || undefined,
  )
})

/** 选中的地址只指向本机时，其它设备必然打不开——提示而已，不拦复制。 */
const localOnly = computed(() => resolved.value && isLoopbackUrl(link.value))

/**
 * 复制与出码共用同一个闸门。
 *
 * selectedBase 还是空串时，buildJoinLink 会退回当前浏览器 origin——GM 在本机开发
 * 时那就是一条 localhost 链接。二维码已经等到 resolved 才出，复制按钮如果不等，
 * 「正在解析地址」的那一两秒里点一下拿到的就是这条半成品链接，等候选回来才悄悄
 * 换成 192.168.x.x，而剪贴板里的已经错了。
 *
 * 刷新候选期间同样关掉：此时屏幕上还是上一轮解析好的地址，不让复制比让两者可能
 * 对不上更省事。显示内容、复制内容、selectedBase 始终是同一个值。
 */
const canCopy = computed(() => resolved.value && !addressesLoading.value)

/** 默认给一个别的设备真打得开的地址：本机 origin 是回环时才换成局域网候选。 */
function pickDefaultBase() {
  if (baseOptions.value.some((option) => option.value === selectedBase.value)) return
  if (configuredBase.value) {
    selectedBase.value = configuredBase.value
    return
  }
  const lan = baseOptions.value.find((option) => option.host && !isLoopbackUrl(option.value))
  selectedBase.value =
    (isLoopbackUrl(frontendOrigin.value) && lan?.value) || frontendOrigin.value
}

async function refresh() {
  // 候选接口仅对 owner 会话开放；拿不到就只剩本机 origin，二维码照样要出得来。
  await loadReachable()
  pickDefaultBase()
  resolved.value = true
}

onMounted(async () => {
  // public_base_url 决定首选入口。对局页之外可能还没人拉过 config。
  if (!Object.keys(settings.config).length && !settings.loading) {
    await settings.load().catch(() => undefined)
  }
  await refresh()
})

async function copy() {
  // 不只依赖 UI 的 disabled：按钮之外还有键盘、脚本和未来的调用方。
  if (!canCopy.value) return
  await copyToClipboard(link.value)
  toast.success(t('inviteCopied'))
}

function openShareSettings() {
  emit('close')
  router.push({ name: 'settings' })
}
</script>

<template>
  <Modal :title="title" dialog-class="qr-share-dialog" @close="emit('close')">
    <div class="invite-guidance">
      <p v-if="hint" class="invite-desc">{{ hint }}</p>
      <a
        class="invite-multiplayer-help"
        data-testid="invite-multiplayer-help"
        :href="inviteHelpUrl"
        target="_blank"
        rel="noopener noreferrer"
      >{{ t('inviteMultiplayerHelp') }}</a>
    </div>

    <AddressPicker
      v-model:value="selectedBase"
      :options="baseOptions"
      :label="t('inviteAddressLabel')"
      :placeholder="t('inviteAddressPlaceholder')"
      :loading="addressesLoading"
      @refresh="refresh"
    >
      <template #hint>
        <p class="invite-address-hint">
          <span aria-hidden="true">ⓘ</span>
          {{ t('inviteAddressHint') }}
          <button type="button" class="invite-address-settings" @click="openShareSettings">
            {{ t('inviteAddressSettingsLink') }}
          </button>
        </p>
        <p v-if="localOnly" class="invite-address-local">{{ t('inviteLocalOnlyWarning') }}</p>
      </template>
    </AddressPicker>

    <QrStage :value="resolved ? link : ''">
      <template #placeholder>
        <p class="muted">{{ t('inviteAddressResolving') }}</p>
      </template>
      <div v-if="resolved" class="invite-qr-meta">
        <p class="invite-scan-hint">{{ t('inviteScanHint') }}</p>
        <!-- 扫码之外仍要留可复制的原文：微信/QQ 里发链接比拍屏幕现实得多 -->
        <code class="invite-link">{{ link }}</code>
      </div>
    </QrStage>

    <template #actions>
      <button @click="emit('close')">{{ t('close') }}</button>
      <button class="primary" :disabled="!canCopy" @click="copy">{{ t('inviteCopyLink') }}</button>
    </template>
  </Modal>
</template>

<style scoped>
.invite-guidance{display:grid;gap:10px;margin-bottom:18px}
.invite-desc{margin:0;color:var(--df-text-muted);font-size:13px;line-height:1.6}
.invite-address-settings{margin-left:4px;padding:0;border:0;background:none;color:var(--df-accent);font-size:12px;text-decoration:underline;cursor:pointer}
.invite-address-local{color:var(--df-danger,var(--df-text-muted))}
.invite-qr-meta{display:flex;flex-direction:column;gap:6px;width:100%}
.invite-scan-hint{margin:0;color:var(--df-text-muted);font-size:13px}
.invite-link{display:block;padding:8px 10px;border-radius:6px;background:color-mix(in srgb,var(--df-accent) 10%,transparent);font-family:var(--df-font-mono);font-size:12px;word-break:break-all;line-height:1.6;text-align:left}
.invite-multiplayer-help{display:inline-flex;align-items:center;justify-content:center;justify-self:start;min-height:36px;padding:0 14px;border:1px solid var(--df-border-soft);border-radius:var(--df-radius-sm);background:var(--df-surface-2);color:var(--df-text);font-size:13px;font-weight:650;text-decoration:none;transition:filter .12s,background .12s,border-color .12s}
.invite-multiplayer-help:hover{filter:brightness(1.08);border-color:var(--df-accent)}
</style>
