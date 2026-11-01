<script setup lang="ts">
// Gal play view (track P). The v0 preview still plays through the tabletop
// view; P9 replaces this delegation with the gal presentation layer built in
// this feature folder, without touching features/play.
import PlayView from '@/features/play/PlayView.vue'

defineOptions({ name: 'GalPlayView' })
</script>
<template>
  <PlayView />
</template>
