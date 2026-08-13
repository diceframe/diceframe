<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { NIcon } from 'naive-ui'
import { CloseOutline, MapOutline, SearchOutline } from '@vicons/ionicons5'
import type { MapData, MapLocation } from '@/api/types'
import { useLocale } from '@/composables/useLocale'
import MapGraph from './MapGraph.vue'

const props = defineProps<{ map?: MapData | null; currentScene?: string }>()
const emit = defineEmits<{ close: [] }>()
const { t } = useLocale()

const query = ref('')
const selectedId = ref('')
const locations = computed(() => props.map?.locations || [])
const normalizedQuery = computed(() => query.value.trim().toLocaleLowerCase())
const filteredLocations = computed(() => {
  if (!normalizedQuery.value) return locations.value
  return locations.value.filter(location => {
    const haystack = [
      location.name,
      location.content,
      ...(location.keywords || []),
    ].join(' ').toLocaleLowerCase()
    return haystack.includes(normalizedQuery.value)
  })
})
const selectedLocation = computed(() => locations.value.find(location => locationId(location) === selectedId.value))
const connectedLocations = computed(() => {
  const refs = selectedLocation.value?.connected_to || []
  return refs.map(reference => locations.value.find(location => (
    locationId(location) === String(reference) || location.name === String(reference)
  ))).filter((location): location is MapLocation => Boolean(location))
})
const mapTitle = computed(() => props.map?.active_map?.name || t('mapTitle'))

function locationId(location: MapLocation): string {
  return String(location.id ?? location.name ?? '')
}

function selectLocation(location: MapLocation) {
  selectedId.value = locationId(location)
}

function selectConnected(location: MapLocation) {
  selectLocation(location)
  query.value = ''
}

function sourceLabel(location: MapLocation): string {
  return location.source === 'plugin'
    ? (location.plugin_name || t('mapSourcePlugin'))
    : t('mapSourceLorebook')
}

function close() {
  emit('close')
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') close()
}

let previousBodyOverflow = ''
onMounted(() => {
  previousBodyOverflow = document.body.style.overflow
  document.body.style.overflow = 'hidden'
  window.addEventListener('keydown', onKeydown)
})
onBeforeUnmount(() => {
  document.body.style.overflow = previousBodyOverflow
  window.removeEventListener('keydown', onKeydown)
})

watch(
  () => `${props.map?.current_location_id || ''}:${props.map?.locations?.length || 0}`,
  () => {
    const preferred = String(props.map?.current_location_id || '')
    const current = locations.value.find(location => locationId(location) === preferred)
      || locations.value[0]
    selectedId.value = current ? locationId(current) : ''
  },
  { immediate: true },
)
</script>

<template>
  <Teleport to="body">
    <div class="map-workspace-overlay" @click.self="close">
      <section class="map-workspace-shell" role="dialog" aria-modal="true" :aria-label="mapTitle">
        <header class="map-workspace-header">
          <div class="map-workspace-title">
            <span class="map-workspace-title-icon" aria-hidden="true">
              <NIcon :component="MapOutline" />
            </span>
            <div class="map-workspace-title-copy">
              <span>{{ t('mapWorkspaceTitle') }}</span>
              <h2>{{ mapTitle }}</h2>
            </div>
          </div>
          <div class="map-workspace-summary">
            <span>{{ t('mapLocationCount', { count: locations.length }) }}</span>
            <span v-if="map?.active_map?.plugin_name">{{ map.active_map.plugin_name }}</span>
          </div>
          <button class="map-workspace-close" :aria-label="t('close')" :title="t('close')" @click="close">
            <NIcon :component="CloseOutline" />
          </button>
        </header>

        <div class="map-workspace-main">
          <div class="map-workspace-canvas">
            <MapGraph
              :map="map"
              :current-scene="currentScene"
              variant="workspace"
              :selected-location-id="selectedId"
              @location-select="selectLocation"
            />
          </div>

          <aside class="map-location-panel">
            <label class="map-search">
              <NIcon :component="SearchOutline" />
              <input v-model="query" :placeholder="t('mapSearchPlaceholder')">
            </label>

            <div class="map-location-list" :aria-label="t('mapLocations')">
              <button
                v-for="location in filteredLocations"
                :key="locationId(location)"
                :class="['map-location-list-item', { active: locationId(location) === selectedId }]"
                @click="selectLocation(location)"
              >
                <img v-if="location.icon_url" :src="location.icon_url" alt="">
                <span v-else class="map-location-marker">◆</span>
                <span><strong>{{ location.name }}</strong><small>{{ sourceLabel(location) }}</small></span>
                <b v-if="locationId(location) === map?.current_location_id" :title="t('mapCurrentLocation')">★</b>
              </button>
              <p v-if="!filteredLocations.length" class="muted">{{ t('mapNoSearchResults') }}</p>
            </div>

            <article v-if="selectedLocation" class="map-location-detail">
              <img v-if="selectedLocation.image_url" :src="selectedLocation.image_url" :alt="selectedLocation.name" class="map-location-image">
              <div class="map-location-detail-head">
                <div>
                  <span>{{ sourceLabel(selectedLocation) }}</span>
                  <h3>{{ selectedLocation.name }}</h3>
                </div>
                <span v-if="locationId(selectedLocation) === map?.current_location_id" class="map-current-badge">{{ t('mapCurrentLocation') }}</span>
              </div>
              <p>{{ selectedLocation.content || t('mapNoDescription') }}</p>
              <div v-if="connectedLocations.length" class="map-location-section">
                <strong>{{ t('mapConnections') }}</strong>
                <div class="map-location-chips">
                  <button v-for="location in connectedLocations" :key="locationId(location)" @click="selectConnected(location)">{{ location.name }}</button>
                </div>
              </div>
              <div v-if="selectedLocation.keywords?.length" class="map-location-section">
                <strong>{{ t('mapKeywords') }}</strong>
                <div class="map-location-chips muted"><span v-for="keyword in selectedLocation.keywords" :key="keyword">{{ keyword }}</span></div>
              </div>
            </article>
          </aside>
        </div>
      </section>
    </div>
  </Teleport>
</template>
