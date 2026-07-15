import { h } from 'vue'
import type { DialogOptions } from 'naive-ui'
import { getDialog } from './useNaiveBridge'

export interface ConfirmOptions {
  title?: string
  content?: string
  positiveText?: string
  negativeText?: string
  type?: 'info' | 'success' | 'warning' | 'error'
}

export function useConfirm() {
  const api = getDialog()
  function confirm(opts: ConfirmOptions): Promise<boolean> {
    return new Promise<boolean>((resolve) => {
      const danger = opts.type === 'error'
      const title = opts.title?.trim() || (danger ? '危险操作确认' : '确认操作')
      const cfg: DialogOptions = {
        title: () => h('div', { class: ['confirm-title', danger ? 'danger' : ''] }, title),
        content: () => h('div', { class: 'confirm-content' }, opts.content || '请确认是否继续。'),
        positiveText: opts.positiveText ?? (danger ? '确认删除' : '确定'),
        negativeText: opts.negativeText ?? '取消',
        maskClosable: false,
        closeOnEsc: true,
        positiveButtonProps: danger ? { type: 'error' } : { type: 'primary' },
        negativeButtonProps: { secondary: true },
        onPositiveClick: () => resolve(true),
        onNegativeClick: () => resolve(false),
        onMaskClick: () => resolve(false),
        onClose: () => resolve(false),
      }
      switch (opts.type) {
        case 'error': api.error(cfg); break
        case 'success': api.success(cfg); break
        case 'info': api.info(cfg); break
        default: api.warning(cfg)
      }
    })
  }
  return { confirm }
}
