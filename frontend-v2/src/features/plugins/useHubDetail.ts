import { onScopeDispose, ref, watch, type Ref } from 'vue'
import { errorMessage } from '@/api/client'
import { pluginApi } from '@/api/plugins'
import { useLocale } from '@/composables/useLocale'
import { useToast } from '@/composables/useToast'
import { renderSafeMarkdown } from '@/utils/markdown'
import type { HubRatingSummary, PluginMarketplaceItem } from '@/api/types'

// The Hub describes the listing, not this instance. Installation state comes
// from the local marketplace entry and must survive the Hub detail payload.
const LOCAL_INSTALL_FIELDS = ['installed', 'installed_version', 'installable', 'verification_error'] as const

function localInstallState(item: PluginMarketplaceItem): Partial<PluginMarketplaceItem> {
  const state: Partial<PluginMarketplaceItem> = {}
  for (const field of LOCAL_INSTALL_FIELDS) {
    if (item[field] !== undefined) Object.assign(state, { [field]: item[field] })
  }
  return state
}

/**
 * Hub detail, README, ratings and likes for one marketplace entry. Shared by
 * the plugin marketplace and the online content packages shelf: both are
 * keyed by plugin id on the Hub, whatever the plugin type.
 */
export function useHubDetail(busy: Ref<string>) {
  const toast = useToast()
  const { t } = useLocale()
  const hubDetail = ref<PluginMarketplaceItem | null>(null)
  const hubReadmeHtml = ref('')
  const hubDetailOpen = ref(false)
  const hubDetailLoading = ref(false)
  const hubReadmeLoading = ref(false)
  const hubRating = ref<number | null>(null)
  const hubRatingSummary = ref<HubRatingSummary | null>(null)
  let hubDetailController: AbortController | null = null
  let hubDetailRequestId = 0

  function cancelHubDetailLoad() {
    hubDetailRequestId += 1
    hubDetailController?.abort()
    hubDetailController = null
    hubDetailLoading.value = false
    hubReadmeLoading.value = false
  }

  function isAbortError(error: unknown): boolean {
    return error instanceof Error && error.name === 'AbortError'
  }

  watch(hubDetailOpen, open => {
    if (!open) cancelHubDetailLoad()
  })

  onScopeDispose(cancelHubDetailLoad)

  async function openHubDetail(item: PluginMarketplaceItem) {
    cancelHubDetailLoad()
    const controller = new AbortController()
    hubDetailController = controller
    const requestId = ++hubDetailRequestId
    const isCurrentRequest = () => (
      requestId === hubDetailRequestId && hubDetailOpen.value && !controller.signal.aborted
    )
    hubDetail.value = item
    hubReadmeHtml.value = ''
    hubReadmeLoading.value = false
    hubRating.value = null
    hubRatingSummary.value = null
    hubDetailOpen.value = true
    hubDetailLoading.value = true
    try {
      const ratingsRequest = pluginApi.hubRatings(item.id, controller.signal)
      void ratingsRequest.then(ratings => {
        if (isCurrentRequest()) hubRatingSummary.value = ratings
      }).catch(() => undefined)

      const detail = await pluginApi.hubDetail(item.id, controller.signal)
      if (!isCurrentRequest()) return
      // Hub 把"当前安装实例是否已点赞/已评分"放在 detail.viewer 里（installation 维度）。
      // 归一化到顶层 liked / own_rating，让弹窗与交互逻辑统一使用这两个字段。
      const viewer = detail.viewer
      detail.liked = Boolean(viewer?.liked)
      detail.own_rating = viewer?.rating ?? null
      hubDetail.value = { ...detail, ...localInstallState(item) }
      hubRating.value = detail.own_rating?.stars ?? null
      hubDetailLoading.value = false
      // README 走 Hub → 磁盘缓存 → 作者 GitHub Raw 三层兜底，始终请求，
      // 由后端返回 Hub 已清洗 HTML 或 GitHub Raw Markdown。
      hubReadmeLoading.value = true
      void pluginApi.hubReadme(item.id, controller.signal)
        .then(readme => {
          if (!isCurrentRequest()) return
          const html = readme.html || ''
          const markdown = readme.markdown || ''
          if (html) {
            hubReadmeHtml.value = html
          } else if (markdown) {
            hubReadmeHtml.value = renderSafeMarkdown(markdown)
          } else {
            hubReadmeHtml.value = ''
          }
        })
        .catch(() => undefined)
        .finally(() => {
          if (isCurrentRequest()) hubReadmeLoading.value = false
        })
    } catch (error: unknown) {
      if (isCurrentRequest() && !isAbortError(error)) toast.error(errorMessage(error))
    } finally {
      if (isCurrentRequest()) hubDetailLoading.value = false
    }
  }

  async function toggleHubLike() {
    const detail = hubDetail.value
    if (!detail) return
    busy.value = `hub-like:${detail.id}`
    try {
      const next = !detail.liked
      await pluginApi.setHubLike(detail.id, next)
      detail.liked = next
      if (detail.stats) {
        detail.stats.likes = Math.max(0, Number(detail.stats.likes || 0) + (next ? 1 : -1))
      }
    } catch (error: unknown) {
      toast.error(errorMessage(error))
    } finally {
      busy.value = ''
    }
  }

  async function saveHubRating(value: number | null) {
    const detail = hubDetail.value
    if (!detail) return
    busy.value = `hub-rating:${detail.id}`
    try {
      await pluginApi.setHubRating(detail.id, value)
      hubRating.value = value
      detail.own_rating = value === null ? null : { stars: value, tags: [] }
      toast.success(t('hubRatingSaved'))
    } catch (error: unknown) {
      toast.error(errorMessage(error))
    } finally {
      busy.value = ''
    }
  }

  return {
    hubDetail,
    hubReadmeHtml,
    hubDetailOpen,
    hubDetailLoading,
    hubReadmeLoading,
    hubRating,
    hubRatingSummary,
    openHubDetail,
    toggleHubLike,
    saveHubRating,
  }
}
