import { computed, ref, type Ref } from 'vue'
import { api, errorMessage } from '@/api/client'
import { pluginApi } from '@/api/plugins'
import { useLocale } from '@/composables/useLocale'
import { useToast } from '@/composables/useToast'
import type { CharacterCard, RuleSummary, WorldListResponse } from '@/api/types'
import { uploadSceneImage } from '@/api/sceneImages'
import { uploadMapBackground } from '@/api/mapBackgrounds'
import { fileToBase64 } from '@/utils/characterImport'

export function usePluginExport(busy: Ref<string>) {
  const toast = useToast()
  const { t } = useLocale()
  const worlds = ref<WorldListResponse['worlds']>([])
  const cards = ref<CharacterCard[]>([])
  const rules = ref<RuleSummary[]>([])
  const loading = ref(false)
  const packId = ref('')
  const packName = ref('')
  const packVersion = ref('0.1.0')
  const packDescription = ref('')
  const selectedWorldId = ref('')
  const selectedRuleId = ref('')
  const selectedCardIds = ref<string[]>([])
  const includePortraits = ref(true)
  const includeSceneImages = ref(true)
  const includeMap = ref(true)
  const worldSceneImageFile = ref<File | null>(null)
  const ruleSceneImageFile = ref<File | null>(null)
  const mapBackgroundFile = ref<File | null>(null)
  const mapIconFiles = ref<File[]>([])

  const worldOptions = computed(() => (worlds.value || []).map(world => {
    const id = String(world?.id || world?.world_id || '')
    return { label: String(world?.name || world?.world_name || id), value: id }
  }).filter(item => item.value))
  const ruleOptions = computed(() => rules.value.map(rule => ({
    label: String(rule.rule_name || rule.rule_id),
    value: rule.rule_id,
  })))
  const cardOptions = computed(() => cards.value.map(card => ({
    label: String(card.character_name || card.id || t('unnamed')),
    value: String(card.id || ''),
  })).filter(item => item.value))

  async function loadAuthorData() {
    loading.value = true
    try {
      const [worldRes, cardRes, ruleRes] = await Promise.all([
        pluginApi.worlds(),
        api<{ cards: CharacterCard[] }>('/character-cards'),
        api<{ rules: RuleSummary[] }>('/rules'),
      ])
      worlds.value = worldRes.worlds || []
      cards.value = cardRes.cards || []
      rules.value = ruleRes.rules || []
    } catch (error: unknown) {
      toast.error(errorMessage(error))
    } finally {
      loading.value = false
    }
  }

  async function exportPack(flat = false) {
    if (!packId.value.trim() || !packName.value.trim()) {
      toast.error(t('exportPackNeedIdName'))
      return
    }
    if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(packId.value.trim())) {
      toast.error(t('exportPackIdInvalid'))
      return
    }
    if (!selectedWorldId.value && !selectedRuleId.value && selectedCardIds.value.length === 0) {
      toast.error(t('exportPackNeedContent'))
      return
    }
    busy.value = 'export-pack'
    try {
      if (mapIconFiles.value.length > 128) throw new Error(t('exportMapIconLimit'))
      const shouldExportMap = includeMap.value && Boolean(selectedWorldId.value)
      const [worldSceneImage, ruleSceneImage, mapBackground, mapIcons] = await Promise.all([
        includeSceneImages.value && selectedWorldId.value && worldSceneImageFile.value
          ? uploadSceneImage(worldSceneImageFile.value) : undefined,
        includeSceneImages.value && selectedRuleId.value && ruleSceneImageFile.value
          ? uploadSceneImage(ruleSceneImageFile.value) : undefined,
        shouldExportMap && mapBackgroundFile.value
          ? uploadMapBackground(mapBackgroundFile.value) : undefined,
        shouldExportMap
          ? Promise.all(mapIconFiles.value.map(async file => ({
            id: file.name.replace(/\.[^.]+$/, ''),
            file_name: file.name,
            file_data: await fileToBase64(file),
          })))
          : [],
      ])
      const response = await pluginApi.exportContent({
        plugin_id: packId.value.trim(),
        name: packName.value.trim(),
        version: packVersion.value.trim() || '0.1.0',
        description: packDescription.value.trim(),
        world_id: selectedWorldId.value,
        card_ids: selectedCardIds.value,
        rule_id: selectedRuleId.value,
        flat,
        include_portraits: includePortraits.value,
        include_scene_images: includeSceneImages.value,
        world_scene_image: worldSceneImage,
        rule_scene_image: ruleSceneImage,
        include_map: shouldExportMap,
        map_background: mapBackground,
        map_icons: mapIcons,
      })
      const blob = await response.blob()
      const disposition = response.headers.get('Content-Disposition') || ''
      const match = disposition.match(/filename="?([^"]+)"?/)
      const filename = match?.[1] || `${packId.value}.dfplugin`
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = filename
      anchor.click()
      URL.revokeObjectURL(url)
      toast.success(t('exportPackDone', { filename }))
    } catch (error: unknown) {
      toast.error(errorMessage(error))
    } finally {
      busy.value = ''
    }
  }

  return {
    worlds, cards, rules, loading,
    packId, packName, packVersion, packDescription,
    selectedWorldId, selectedRuleId, selectedCardIds,
    includePortraits,
    includeSceneImages, worldSceneImageFile, ruleSceneImageFile,
    includeMap, mapBackgroundFile, mapIconFiles,
    worldOptions, ruleOptions, cardOptions,
    loadAuthorData, exportPack,
  }
}
