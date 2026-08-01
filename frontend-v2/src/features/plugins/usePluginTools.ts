import { ref, type Ref } from 'vue'
import { errorMessage } from '@/api/client'
import { pluginApi } from '@/api/plugins'
import { useLocale } from '@/composables/useLocale'
import { useToast } from '@/composables/useToast'
import type { PluginToolDescriptor } from '@/api/types'

export function usePluginTools(busy: Ref<string>) {
  const toast = useToast()
  const { t } = useLocale()
  const tools = ref<PluginToolDescriptor[]>([])
  const toolInputs = ref<Record<string, string>>({})
  const toolResults = ref<Record<string, string>>({})
  const toolsLoading = ref(false)

  const toolKey = (tool: PluginToolDescriptor) => `${tool.plugin_id}:${tool.name}`

  async function loadTools() {
    toolsLoading.value = true
    try {
      const response = await pluginApi.tools()
      if (!response.ok) throw new Error(response.error || t('pluginToolsLoadFailed'))
      tools.value = response.tools || []
      for (const tool of tools.value) {
        const key = toolKey(tool)
        if (toolInputs.value[key] === undefined) toolInputs.value[key] = '{}'
      }
    } catch (error: unknown) {
      tools.value = []
      toast.error(errorMessage(error))
    } finally {
      toolsLoading.value = false
    }
  }

  function setToolInput(tool: PluginToolDescriptor, value: string) {
    toolInputs.value[toolKey(tool)] = value
  }

  async function invokeTool(tool: PluginToolDescriptor) {
    const key = toolKey(tool)
    if (!window.confirm(t('confirmPluginToolInvoke', { name: tool.title || tool.name, plugin: tool.plugin_name }))) return
    let argumentsValue: unknown
    try {
      argumentsValue = JSON.parse(toolInputs.value[key] || '{}')
    } catch {
      toast.error(t('pluginToolArgumentsInvalid'))
      return
    }
    if (!argumentsValue || typeof argumentsValue !== 'object' || Array.isArray(argumentsValue)) {
      toast.error(t('pluginToolArgumentsInvalid'))
      return
    }
    busy.value = `tool:${key}`
    try {
      const response = await pluginApi.invokeTool(
        tool.plugin_id,
        tool.name,
        argumentsValue as Record<string, unknown>,
      )
      if (!response.ok) throw new Error(response.error || t('pluginToolInvokeFailed'))
      toolResults.value[key] = JSON.stringify(response.result || {}, null, 2)
      toast.success(t('pluginToolInvokeSucceeded', { name: tool.title || tool.name }))
    } catch (error: unknown) {
      toolResults.value[key] = errorMessage(error)
      toast.error(errorMessage(error))
    } finally {
      busy.value = ''
    }
  }

  return {
    tools,
    toolInputs,
    toolResults,
    toolsLoading,
    toolKey,
    loadTools,
    setToolInput,
    invokeTool,
  }
}
