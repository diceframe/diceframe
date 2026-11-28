<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { fetchTacticalScene, type TacticalSceneTokenDocument, type TacticalSceneWriteResponse } from '@/api/tacticalScene'
import { useLocale } from '@/composables/useLocale'
import { tacticalTokenCopy } from './copy'
import { isGmSceneDocument } from './useSceneEditor'
import { useTokenBinding, type TokenBindKind, type TokenBindTarget } from './useTokenBinding'
import { tokenActorStatus } from './tokenDisplay'

const props = defineProps<{
  gameKey: string
  sceneId: string
  token: TacticalSceneTokenDocument
  revision: number
}>()

const emit = defineEmits<{
  close: []
  bound: [response: TacticalSceneWriteResponse]
}>()

const { locale } = useLocale()
const copy = computed(() => tacticalTokenCopy(locale.value))

const gameKeyRef = computed(() => props.gameKey)
const binding = useTokenBinding(gameKeyRef)

const workingToken = ref<TacticalSceneTokenDocument>(props.token)
const workingRevision = ref(props.revision)
const selectedKey = ref('')
const lastAction = ref<'bind' | 'unbind' | null>(null)
const success = ref('')

// The dialog owns its working token/revision after mount: a successful write
// advances them from the write response, and a stale conflict reloads them.
// The parent re-reading the board must not reset this state mid-dialog.

const selected = computed<TokenBindTarget | null>(() =>
  binding.targets.value.find(target => `${target.kind}:${target.id}` === selectedKey.value) || null)

const groups = computed(() => {
  const order: { kind: TokenBindKind; label: string }[] = [
    { kind: 'player', label: copy.value.bindGroupPlayers },
    { kind: 'enemy', label: copy.value.bindGroupEnemies },
    { kind: 'companion', label: copy.value.bindGroupCompanions },
  ]
  return order
    .map(group => ({ ...group, items: binding.targets.value.filter(target => target.kind === group.kind) }))
    .filter(group => group.items.length > 0)
})

const currentStatus = computed(() => tokenActorStatus(workingToken.value.actor_ref))
const hasBinding = computed(() => !!workingToken.value.actor_ref)

function isSelected(target: TokenBindTarget): boolean {
  return selectedKey.value === `${target.kind}:${target.id}`
}

function applyResponse(response: TacticalSceneWriteResponse, message: string) {
  workingRevision.value = response.revision
  const fresh = response.scene?.placeables.tokens[workingToken.value.canonical_id]
  if (fresh) workingToken.value = fresh
  selectedKey.value = ''
  success.value = message
  emit('bound', response)
}

async function submitBind() {
  const target = selected.value
  if (!target || binding.saving.value) return
  lastAction.value = 'bind'
  success.value = ''
  try {
    const response = await binding.bind(props.sceneId, workingToken.value, target.kind, target.id, workingRevision.value)
    applyResponse(response, copy.value.bindSuccess)
  } catch {
    // saveError / conflict are surfaced by the composable.
  }
}

async function submitUnbind() {
  if (!hasBinding.value || binding.saving.value) return
  lastAction.value = 'unbind'
  success.value = ''
  try {
    const response = await binding.unbind(props.sceneId, workingToken.value, workingRevision.value)
    applyResponse(response, copy.value.unbindSuccess)
  } catch {
    // surfaced below
  }
}

function retry() {
  if (lastAction.value === 'unbind') void submitUnbind()
  else void submitBind()
}

async function reloadToken() {
  try {
    const fresh = await fetchTacticalScene(props.gameKey, props.sceneId)
    if (isGmSceneDocument(fresh.scene)) {
      const token = fresh.scene.placeables.tokens[workingToken.value.canonical_id]
      if (token) workingToken.value = token
      workingRevision.value = fresh.revision
    }
    binding.conflict.value = false
    binding.saveError.value = ''
    selectedKey.value = ''
  } catch {
    // Keep the conflict banner; the user can retry.
  }
}

onMounted(() => { void binding.loadTargets() })
</script>

<template>
  <div class="editor-dialog-backdrop" data-testid="token-bind-dialog" @click.self="emit('close')">
    <section class="editor-dialog token-bind-dialog" role="dialog" :aria-label="copy.bindTitle">
      <h2>{{ copy.bindTitle }}</h2>
      <p class="editor-muted">{{ copy.bindHint }}</p>
      <p class="editor-muted token-bind-current" data-testid="token-bind-current">
        {{ copy.bindCurrent }}:
        <code>{{ workingToken.canonical_id }}</code> ·
        <span v-if="currentStatus === 'bound'">{{ workingToken.actor_ref?.kind }}:{{ workingToken.actor_ref?.id }}</span>
        <span v-else-if="currentStatus === 'not_imported'">{{ copy.actorNotImported }}</span>
        <span v-else-if="currentStatus === 'unbound'">{{ copy.actorUnbound }}</span>
        <span v-else>{{ workingToken.actor_ref?.kind }}:{{ workingToken.actor_ref?.id }}</span>
      </p>

      <div v-if="binding.conflict.value" class="editor-conflict" role="alert" data-testid="token-bind-conflict">
        <strong>{{ copy.conflictTitle }}</strong>
        <p>{{ copy.conflictHint }}</p>
        <button type="button" data-testid="token-bind-conflict-reload" @click="reloadToken()">{{ copy.conflictReload }}</button>
      </div>
      <p v-else-if="binding.saveError.value" class="editor-field-error" role="alert" data-testid="token-bind-error">
        {{ binding.saveError.value }}
        <button type="button" data-testid="token-bind-retry" @click="retry()">{{ copy.retry }}</button>
      </p>

      <p v-if="success" class="token-bind-success" role="status" data-testid="token-bind-success">{{ success }}</p>

      <section class="token-bind-targets">
        <h3>{{ copy.bindTarget }}</h3>
        <p v-if="binding.loading.value" class="editor-muted" role="status" data-testid="token-bind-loading">{{ copy.bindLoading }}</p>
        <p v-else-if="binding.loadError.value" class="editor-field-error" role="alert" data-testid="token-bind-load-error">
          {{ copy.bindLoadError }}: {{ binding.loadError.value }}
          <button type="button" @click="binding.loadTargets()">{{ copy.retry }}</button>
        </p>
        <p v-else-if="!groups.length" class="editor-muted" data-testid="token-bind-no-targets">{{ copy.bindNoTargets }}</p>
        <div v-else class="token-bind-groups">
          <fieldset v-for="group in groups" :key="group.kind" class="token-bind-group">
            <legend>{{ group.label }}</legend>
            <label
              v-for="target in group.items"
              :key="`${target.kind}:${target.id}`"
              class="token-bind-option"
              :class="{ selected: isSelected(target) }"
            >
              <input
                v-model="selectedKey"
                type="radio"
                name="token-bind-target"
                :value="`${target.kind}:${target.id}`"
                :data-testid="`token-bind-target-${target.kind}-${target.id}`"
              >
              <span class="token-bind-option-label">{{ target.label }}</span>
              <code class="token-bind-option-id">{{ target.id }}</code>
            </label>
          </fieldset>
        </div>
        <p v-if="!selectedKey && !binding.loading.value && groups.length" class="editor-muted">{{ copy.bindSelectTarget }}</p>
      </section>

      <div class="editor-dialog-actions">
        <button
          type="button"
          class="token-bind-submit"
          :disabled="!selected || binding.saving.value"
          data-testid="token-bind-submit"
          @click="submitBind()"
        >{{ binding.saving.value ? copy.binding : copy.bindSubmit }}</button>
        <button
          type="button"
          :disabled="!hasBinding || binding.saving.value"
          data-testid="token-bind-unbind"
          @click="submitUnbind()"
        >{{ binding.saving.value ? copy.unbinding : copy.unbind }}</button>
        <button type="button" data-testid="token-bind-cancel" @click="emit('close')">{{ copy.cancel }}</button>
      </div>
    </section>
  </div>
</template>