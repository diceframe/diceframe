<script setup lang="ts">
import { computed, ref } from 'vue'
import { useLocale } from '@/composables/useLocale'
import { classifyJoinInvitation, joinInvitationErrorKeys } from './joinInvitation'

defineProps<{ peerAvailable: boolean }>()
const emit = defineEmits<{ peer: [code: string]; host: [] }>()
const { t } = useLocale()
const input = ref('')
const invitation = computed(() => classifyJoinInvitation(input.value))

function continuePeer() {
  if (invitation.value.kind === 'peer') emit('peer', invitation.value.code)
}
</script>

<template>
  <section class="friend-join-panel" aria-labelledby="friend-join-title">
    <h2 id="friend-join-title">{{ t('friendJoinTitle') }}</h2>
    <p class="friend-join-hint">{{ t('friendJoinHint') }}</p>
    <label class="friend-join-field">
      <span>{{ t('friendJoinLabel') }}</span>
      <textarea
        v-model="input"
        data-testid="friend-join-input"
        rows="4"
        :placeholder="t('friendJoinPlaceholder')"
        :aria-invalid="invitation.kind === 'invalid'"
        :aria-describedby="invitation.kind === 'invalid' ? 'friend-join-error' : undefined"
        autocomplete="off"
        autocapitalize="off"
        spellcheck="false"
      />
    </label>

    <p v-if="invitation.kind === 'invalid'" id="friend-join-error" class="error-banner" role="alert">
      {{ t(joinInvitationErrorKeys[invitation.reason]) }}
    </p>
    <div v-else-if="invitation.kind === 'link'" class="friend-join-result" role="status">
      <strong>{{ t('friendJoinLinkReady') }}</strong>
      <span class="friend-join-origin">{{ invitation.origin }}</span>
      <a data-testid="friend-join-open-link" class="friend-join-open primary" :href="invitation.url" target="_blank" rel="noopener noreferrer">
        {{ t('friendJoinOpenLink') }}
      </a>
    </div>
    <div v-else-if="invitation.kind === 'peer'" class="friend-join-result" role="status">
      <strong>{{ t('friendJoinPeerReady') }}</strong>
      <button v-if="peerAvailable" data-testid="friend-join-peer" class="primary" type="button" @click="continuePeer">
        {{ t('friendJoinConnect') }}
      </button>
      <p v-else>{{ t('friendJoinPeerUnavailable') }}</p>
    </div>

    <details v-if="peerAvailable" class="friend-join-more">
      <summary>{{ t('friendJoinMoreWays') }}</summary>
      <button data-testid="friend-join-host" type="button" @click="emit('host')">{{ t('friendJoinHost') }}</button>
    </details>
  </section>
</template>

<style scoped>
.friend-join-panel { box-sizing: border-box; width: 100%; min-width: 0; flex: 1; padding: 40px 28px 28px; }
.friend-join-panel h2 { margin: 0 40px 12px 0; font: 750 28px/1.2 var(--df-font-title); }
.friend-join-hint { margin: 0 0 24px; color: var(--df-text-muted); line-height: 1.7; }
.friend-join-field { display: grid; gap: 8px; font-size: 14px; }
.friend-join-field textarea { box-sizing: border-box; width: 100%; resize: vertical; min-height: 110px; max-height: 35dvh; font-family: var(--df-font-mono); overflow-wrap: anywhere; }
.friend-join-result { display: grid; gap: 10px; margin-top: 18px; }
.friend-join-origin { overflow-wrap: anywhere; color: var(--df-text-muted); font-size: 13px; }
.friend-join-open { display: inline-flex; align-items: center; justify-content: center; box-sizing: border-box; min-height: 44px; padding: 10px 18px; border: 1px solid var(--df-accent); border-radius: var(--df-radius-md); color: var(--df-on-accent); background: var(--df-accent); text-decoration: none; font-weight: 700; }
.friend-join-more { margin-top: 26px; padding-top: 18px; border-top: 1px solid var(--df-border); }
.friend-join-more summary { width: fit-content; cursor: pointer; color: var(--df-text-muted); font-size: 13px; }
.friend-join-more button { margin-top: 14px; }
@media (max-width: 760px) {
  .friend-join-panel { padding: 34px 18px calc(24px + env(safe-area-inset-bottom)); }
}
</style>
