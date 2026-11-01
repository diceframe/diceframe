<script setup lang="ts">
defineProps<{ label: string; hint?: string; active?: boolean; disabled?: boolean }>()
</script>

<template>
  <button
    type="button"
    :class="['action-tile', { active }]"
    :disabled="disabled"
    :aria-pressed="active"
  >
    <span class="tile-icon"><slot name="icon" /></span>
    <strong>{{ label }}</strong>
    <small v-if="hint">{{ hint }}</small>
  </button>
</template>

<style scoped>
.action-tile { display: grid; justify-items: center; align-content: center; gap: 4px; min-width: 0; min-height: 86px; padding: 10px 8px; border: 1px solid rgb(120 150 175 / 40%); border-radius: 12px; background: linear-gradient(180deg, rgb(24 38 54 / 96%), rgb(13 21 31 / 96%)); color: #e8eef2; cursor: pointer; }
.action-tile:hover:not(:disabled) { border-color: #7fb4d6; background: linear-gradient(180deg, rgb(32 52 74 / 96%), rgb(16 27 40 / 96%)); }
.action-tile.active { border-color: #e4b75e; background: linear-gradient(180deg, rgb(88 62 22 / 70%), rgb(34 26 14 / 96%)); box-shadow: 0 0 0 1px rgb(228 183 94 / 55%), 0 0 16px rgb(228 183 94 / 28%); }
.action-tile:disabled { opacity: .45; cursor: not-allowed; }
.action-tile:focus-visible { outline: 3px solid #e4b75e; outline-offset: 2px; }
.tile-icon { display: grid; place-items: center; height: 30px; color: #8fd0ff; font-size: 28px; }
.action-tile.active .tile-icon { color: #f4c969; }
.action-tile strong { font-size: 14px; }
.action-tile small { max-width: 100%; overflow: hidden; color: #9aa9b4; font-size: 10px; text-overflow: ellipsis; white-space: nowrap; }
</style>
