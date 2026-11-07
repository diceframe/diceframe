import { errorMessage } from '@/api/client'
import { pluginApi } from '@/api/plugins'
import { i18n, type MessageKey } from '@/i18n'
import { useConfirm } from '@/composables/useConfirm'
import { useToast } from '@/composables/useToast'

export interface InstalledArtifact {
  id: string
  name?: string
  /** Host truth from the install response; `true` means it is already live. */
  enabled?: boolean
}

// 安装可能在任意页面完成后触发（甚至用户已经切走页面），文案一律取全局
// composer，不依赖某个组件的 setup 上下文。
function message(key: MessageKey, params?: Record<string, string | number>): string {
  return i18n.global.t(key, params || {})
}

/**
 * 安装完成后的「是否启用」询问。
 *
 * 宿主安装完一律是禁用状态（`config.enabled` 缺省为 false）：内容包的冒险不会
 * 进入冒险来源，插件也不会注册任何贡献，用户只看到「已安装」却什么都用不了。
 * 启用提示因此不能绑在某个页面组件上——用全局 dialog 询问，用户在插件页或内容
 * 库页安装、装完立刻离开当页，提示都照常出现。
 *
 * 返回是否真的执行了启用，调用方据此决定要不要刷新一次目录。
 */
export async function promptEnableAfterInstall(artifact: InstalledArtifact): Promise<boolean> {
  if (artifact.enabled === true) return false
  const id = String(artifact.id || '').trim()
  if (!id) return false
  const name = String(artifact.name || '').trim() || id
  let confirm: () => Promise<boolean>
  try {
    const { confirm: ask } = useConfirm()
    confirm = () => ask({
      title: message('confirmEnableAfterInstallTitle'),
      content: message('confirmEnableAfterInstall', { name }),
      positiveText: message('enableAfterInstallPositive'),
      negativeText: message('enableAfterInstallLater'),
      type: 'info',
    })
  } catch {
    // 对话框不可用（测试环境未挂载 NaiveBridge）时静默跳过：安装本身已经成功。
    return false
  }
  const ok = await confirm().catch(() => false)
  if (!ok) return false
  let started: { enabled?: boolean; status?: string } | undefined
  try {
    started = await pluginApi.setRunning(id, true) as { enabled?: boolean; status?: string } | undefined
  } catch (error: unknown) {
    notify('error', errorMessage(error))
    return false
  }
  // 宿主对进程型插件的启动失败是「内部捕获 + status=failed」，HTTP 仍是 200：
  // 只看 Promise 成功会把失败的启用报成已启用（review P2）。
  if (started && (started.enabled === false || started.status === 'failed')) {
    notify('error', message('enableAfterInstallFailed', { name }))
    return false
  }
  notify('success', message('pluginNamedEnabled', { name }))
  return true
}

function notify(kind: 'success' | 'error', text: string): void {
  try {
    useToast()[kind](text)
  } catch {
    // 消息条不可用不影响启用结果本身。
  }
}
