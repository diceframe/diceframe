import { h, type VNodeChild } from 'vue'
import type { DialogOptions } from 'naive-ui'
import { i18n, type MessageKey } from '@/i18n'
import { getDialog } from './useNaiveBridge'

export interface ConfirmOptions {
  title?: string
  content?: string | (() => VNodeChild)
  contentClass?: string | string[]
  dialogClass?: string | string[]
  positiveText?: string
  negativeText?: string
  type?: 'info' | 'success' | 'warning' | 'error'
}

export function useConfirm() {
  const api = getDialog()
  // 全局 composer：确认框也能在组件 setup 之外触发（例如安装完成后的全局
  // 「是否启用」询问），不依赖调用方处于某个组件的上下文。
  const t = (key: MessageKey, params?: Record<string, string | number>) => i18n.global.t(key, params || {})
  function confirm(opts: ConfirmOptions): Promise<boolean> {
    return new Promise<boolean>((resolve) => {
      const danger = opts.type === 'error'
      const title = opts.title?.trim() || (danger ? t('confirmDangerTitle') : t('confirmTitle'))
      const cfg: DialogOptions = {
        class: opts.dialogClass,
        title: () => h('div', { class: ['confirm-title', danger ? 'danger' : ''] }, title),
        contentClass: opts.contentClass
          ? ['confirm-content', ...(Array.isArray(opts.contentClass) ? opts.contentClass : [opts.contentClass])]
          : undefined,
        content: () => typeof opts.content === 'function'
          ? opts.content()
          : h('div', { class: 'confirm-content' }, opts.content || t('confirmContent')),
        positiveText: opts.positiveText ?? (danger ? t('confirmDelete') : t('ok')),
        negativeText: opts.negativeText ?? t('cancel'),
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
