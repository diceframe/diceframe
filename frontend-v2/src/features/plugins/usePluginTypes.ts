import { computed, ref } from 'vue'
import { pluginApi, type PluginTypeInfo } from '@/api/plugins'
import { useLocale } from '@/composables/useLocale'
import type { MessageKey } from '@/i18n'

function typeLabelKey(typeId: string): string {
  // 约定：content-pack -> pluginTypeContentPack（与 i18n 键对齐）
  return 'pluginType' + typeId.split('-').map(s => s.charAt(0).toUpperCase() + s.slice(1)).join('')
}

export function usePluginTypes() {
  const { t } = useLocale()
  const types = ref<PluginTypeInfo[]>([])

  async function loadTypes() {
    const res = await pluginApi.listTypes()
    types.value = res.types
  }

  function toFilter(item: PluginTypeInfo): { value: string; labelKey: MessageKey } {
    return { value: item.id, labelKey: typeLabelKey(item.id) as MessageKey }
  }

  // 筛选按钮：filterable 类型按 filter_order 升序（后端 descriptor 单一来源驱动）
  const pluginTypeFilters = computed(() =>
    types.value
      .filter(item => item.filterable)
      .sort((a, b) => a.filter_order - b.filter_order)
      .map(toFilter),
  )

  // 插件市场的筛选条：content-pack 在模组库安装，市场里不提供这个类型。
  const marketTypeFilters = computed(() => pluginTypeFilters.value.filter(f => f.value !== 'content-pack'))

  function pluginTypeLabel(type?: string): string {
    if (!type) return t('uncategorized')
    const key = typeLabelKey(type) as MessageKey
    const translated = t(key)
    // vue-i18n 缺失 key 时返回 key 本身，此时回退到原始类型 id
    return translated !== key ? translated : type
  }

  return { types, pluginTypeFilters, marketTypeFilters, pluginTypeLabel, loadTypes }
}
