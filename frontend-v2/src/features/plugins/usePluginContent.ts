import { computed, ref, type Ref } from 'vue'
import { errorMessage } from '@/api/client'
import { pluginApi } from '@/api/plugins'
import { useLocale } from '@/composables/useLocale'
import { useToast } from '@/composables/useToast'
import type { MessageKey } from '@/i18n'
import type { PluginContentResource, WorldListResponse } from '@/api/types'

const CONTENT_GROUPS = [
  { key: 'character_template', labelKey: 'contentGroupCharacterTemplate' },
  { key: 'npc', labelKey: 'contentGroupNpc' },
  { key: 'item', labelKey: 'contentGroupItem' },
  { key: 'spell', labelKey: 'contentGroupSpell' },
  { key: 'class', labelKey: 'contentGroupClass' },
] satisfies { key: string; labelKey: MessageKey }[]

export function usePluginContent(busy: Ref<string>) {
  const toast = useToast()
  const { t } = useLocale()
  const contentResources = ref<Record<string, PluginContentResource[]>>({})
  const worlds = ref<WorldListResponse['worlds']>([])
  const contentLoading = ref(false)
  const contentTargetWorldId = ref('')

  const contentGroups = computed(() => CONTENT_GROUPS.map(group => ({
    ...group,
    items: contentResources.value[group.key] || [],
  })))
  const worldOptions = computed(() => (worlds.value || []).map(world => {
    const id = String(world.id || world.world_id || '')
    return {
      label: String(world.name || world.world_name || id),
      value: id,
    }
  }).filter(item => item.value))

  async function loadContentResources() {
    contentLoading.value = true
    try {
      const response = await pluginApi.content()
      if (!response.ok) throw new Error(response.error || t('pluginContentLoadFailed'))
      contentResources.value = response.resources || {}
    } catch (error: unknown) {
      toast.error(errorMessage(error))
    } finally {
      contentLoading.value = false
    }
  }

  async function loadWorlds() {
    try {
      const response = await pluginApi.worlds()
      worlds.value = response.worlds || []
      if (!contentTargetWorldId.value && worldOptions.value.length) {
        contentTargetWorldId.value = String(worldOptions.value[0].value)
      }
    } catch (error: unknown) {
      toast.error(errorMessage(error))
    }
  }

  const contentTitle = (item: PluginContentResource) =>
    String(item.character_name || item.name || item.id || t('unnamed'))

  const contentSubtitle = (item: PluginContentResource) =>
    [item.plugin_name || item.plugin_id || '', item.description || ''].filter(Boolean).join(' · ')

  async function importContent(kind: string, item: PluginContentResource) {
    if (kind !== 'character_template' && !contentTargetWorldId.value) {
      toast.error(t('selectLorebookTarget'))
      return
    }
    const key = `${kind}:${item.plugin_id}:${item.id || item.name || item.character_name}`
    busy.value = key
    try {
      const response = await pluginApi.importContent(
        kind,
        item.id,
        item.plugin_id || '',
        kind === 'character_template' ? '' : contentTargetWorldId.value,
      )
      if (!response.ok) throw new Error(response.error || t('importFailed'))
      toast.success(kind === 'character_template' ? t('importedCharacterLibrary') : t('importedLorebook'))
    } catch (error: unknown) {
      toast.error(errorMessage(error))
    } finally {
      busy.value = ''
    }
  }

  return {
    contentGroups,
    contentLoading,
    contentTargetWorldId,
    worldOptions,
    loadContentResources,
    loadWorlds,
    contentTitle,
    contentSubtitle,
    importContent,
  }
}
