<script setup lang="ts">
import { computed } from 'vue'
import type { Dnd5eActorDisplay, Dnd5eEntityDisplay, Dnd5eItemDisplay } from '@/api/dnd5eImport'
import { useLocale } from '@/composables/useLocale'
import { dnd5eImportCopy } from './copy'

defineProps<{ display: Dnd5eActorDisplay }>()
const { locale } = useLocale()
const copy = computed(() => dnd5eImportCopy(locale.value).display)

const abilityIds = ['str', 'dex', 'con', 'int', 'wis', 'cha'] as const
const abilityNames = computed(() => dnd5eImportCopy(locale.value).selections.abilities)

function entityLabel(entity: Dnd5eEntityDisplay | null): string {
  if (!entity) return '—'
  return entity.name || entity.raw_name || entity.raw_identifier || '—'
}

function proficiencyLabel(value: number): string {
  if (value >= 2) return copy.value.proficiencyExpert
  if (value >= 1) return copy.value.proficiencyFull
  if (value > 0) return copy.value.proficiencyHalf
  return copy.value.proficiencyNone
}

function itemMeta(item: Dnd5eItemDisplay): string[] {
  const parts: string[] = []
  if (item.quantity !== null) parts.push(`${copy.value.quantity} ${item.quantity}`)
  if (item.weight && item.weight.value !== null && item.weight.value !== undefined) {
    parts.push(`${copy.value.weight} ${item.weight.value} ${item.weight.units || ''}`.trim())
  }
  if (item.price && item.price.value !== null && item.price.value !== undefined) {
    parts.push(`${copy.value.price} ${item.price.value} ${item.price.denomination || ''}`.trim())
  }
  if (item.damage.formula) parts.push(`${copy.value.damage} ${item.damage.formula} (${item.damage.types.join(', ')})`)
  if (item.armor.value !== null) parts.push(`${copy.value.armor} ${item.armor.value}`)
  return parts
}
</script>

<template>
  <section class="dnd5e-display" data-testid="dnd5e-import-display">
    <h3>{{ copy.title }}</h3>
    <header class="dnd5e-display-hero">
      <div>
        <b data-testid="dnd5e-display-name">{{ display.name }}</b>
        <span>{{ display.type }} · {{ copy.level }} {{ display.level ?? '—' }} · {{ copy.alignment }} {{ display.alignment || '—' }}</span>
      </div>
    </header>

    <dl class="dnd5e-display-identity">
      <div>
        <dt>{{ copy.species }}</dt>
        <dd>
          {{ entityLabel(display.species) }}
          <code v-if="display.species?.ref" class="dnd5e-ref">{{ display.species.ref }}</code>
          <span v-else-if="display.species" class="dnd5e-unmapped">{{ copy.savedNotRunnable }}</span>
        </dd>
      </div>
      <div>
        <dt>{{ copy.background }}</dt>
        <dd>
          {{ entityLabel(display.background) }}
          <code v-if="display.background?.ref" class="dnd5e-ref">{{ display.background.ref }}</code>
          <span v-else-if="display.background" class="dnd5e-unmapped">{{ copy.savedNotRunnable }}</span>
        </dd>
      </div>
      <div v-for="row in display.class" :key="row.raw_identifier || row.raw_name">
        <dt>{{ copy.class }}</dt>
        <dd>
          {{ row.name || row.raw_name }} ({{ row.levels ?? '—' }})
          <code v-if="row.ref" class="dnd5e-ref">{{ row.ref }}</code>
          <span v-else class="dnd5e-unmapped">{{ copy.savedNotRunnable }}</span>
        </dd>
      </div>
    </dl>

    <div class="dnd5e-display-stats">
      <article><span>{{ copy.hp }}</span><b>{{ display.hp.value ?? '—' }} / {{ display.hp.max ?? '—' }}</b></article>
      <article><span>{{ copy.ac }}</span><b>{{ display.armor_class ?? '—' }}</b></article>
      <article><span>{{ copy.speed }}</span><b>{{ display.speed ?? '—' }}</b></article>
    </div>

    <div class="dnd5e-display-group">
      <h4>{{ copy.abilities }}</h4>
      <div class="dnd5e-ability-grid">
        <article v-for="id in abilityIds" :key="id">
          <span>{{ abilityNames[id] || id }}</span>
          <b>{{ display.abilities[id]?.score ?? '—' }}</b>
        </article>
      </div>
    </div>

    <div class="dnd5e-display-group">
      <h4>{{ copy.skills }}</h4>
      <ul class="dnd5e-display-list">
        <li v-for="row in display.skills" :key="row.id">
          {{ row.id }} · {{ proficiencyLabel(row.proficiency) }}
          <code v-if="row.ref" class="dnd5e-ref">{{ row.ref }}</code>
          <span v-else class="dnd5e-unmapped">{{ copy.savedNotRunnable }}</span>
        </li>
      </ul>
    </div>

    <div class="dnd5e-display-group">
      <h4>{{ copy.languages }}</h4>
      <ul class="dnd5e-display-list">
        <li v-for="row in display.languages" :key="row.key">
          <template v-if="row.key === 'custom'">{{ copy.custom }}: {{ row.text }}</template>
          <template v-else>{{ row.key }}</template>
          <code v-if="row.ref" class="dnd5e-ref">{{ row.ref }}</code>
          <span v-else class="dnd5e-unmapped">{{ copy.savedNotRunnable }}</span>
        </li>
      </ul>
    </div>

    <div class="dnd5e-display-group">
      <h4>{{ copy.items }}</h4>
      <p v-if="!display.items.length" class="dnd5e-display-empty">{{ copy.noItems }}</p>
      <ul v-else class="dnd5e-item-list">
        <li v-for="item in display.items" :key="`${item.identifier}:${item.name}`" data-testid="dnd5e-display-item">
          <div class="dnd5e-item-head">
            <b>{{ item.name }}</b>
            <span class="dnd5e-item-type">{{ item.type }}</span>
            <code v-if="item.catalog_ref" class="dnd5e-ref" data-testid="dnd5e-item-mapped">{{ item.catalog_ref }}</code>
            <span v-else class="dnd5e-unmapped" data-testid="dnd5e-item-unmapped">{{ copy.savedNotRunnable }}</span>
          </div>
          <p v-if="itemMeta(item).length" class="dnd5e-item-meta">{{ itemMeta(item).join(' · ') }}</p>
          <p v-if="item.properties.length" class="dnd5e-item-meta">{{ copy.properties }}: {{ item.properties.join(', ') }}</p>
        </li>
      </ul>
    </div>
  </section>
</template>

<style scoped>
.dnd5e-display { display: grid; gap: .7rem; padding: .9rem; border: 1px solid color-mix(in srgb, var(--df-accent) 26%, var(--df-border-soft)); border-radius: var(--df-radius-md); background: var(--df-surface-2); min-width: 0; overflow-wrap: anywhere; }
.dnd5e-display h3 { margin: 0; color: var(--df-accent-strong); font-size: 1rem; }
.dnd5e-display-hero b { display: block; font-size: 1.15rem; color: var(--df-text); }
.dnd5e-display-hero span { color: var(--df-text-muted); font-size: .8rem; }
.dnd5e-display-identity { display: grid; gap: .4rem; margin: 0; }
.dnd5e-display-identity div { display: flex; gap: .5rem; flex-wrap: wrap; align-items: baseline; }
.dnd5e-display-identity dt { min-width: 3.5rem; color: var(--df-text-muted); font-size: .78rem; }
.dnd5e-display-identity dd { margin: 0; color: var(--df-text-secondary); font-size: .85rem; }
.dnd5e-display-stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: .5rem; }
.dnd5e-display-stats article { display: grid; gap: .2rem; padding: .6rem; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-sm); background: var(--df-surface-1); text-align: center; }
.dnd5e-display-stats span { color: var(--df-text-muted); font-size: .72rem; }
.dnd5e-display-stats b { color: var(--df-text); font-size: 1rem; }
.dnd5e-display-group { display: grid; gap: .4rem; }
.dnd5e-display-group h4 { margin: 0; color: var(--df-text); font-size: .85rem; }
.dnd5e-ability-grid { display: grid; grid-template-columns: repeat(6, 1fr); gap: .4rem; }
.dnd5e-ability-grid article { display: grid; gap: .15rem; padding: .45rem; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-sm); background: var(--df-surface-1); text-align: center; }
.dnd5e-ability-grid span { color: var(--df-text-muted); font-size: .7rem; }
.dnd5e-ability-grid b { color: var(--df-text); }
.dnd5e-display-list { display: grid; gap: .25rem; margin: 0; padding-left: 1.2rem; color: var(--df-text-secondary); font-size: .8rem; }
.dnd5e-display-empty { margin: 0; color: var(--df-text-muted); font-size: .8rem; }
.dnd5e-item-list { display: grid; gap: .5rem; margin: 0; padding: 0; list-style: none; }
.dnd5e-item-list li { display: grid; gap: .25rem; padding: .55rem .7rem; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-sm); background: var(--df-surface-1); }
.dnd5e-item-head { display: flex; align-items: center; gap: .5rem; flex-wrap: wrap; }
.dnd5e-item-head b { color: var(--df-text); font-size: .88rem; }
.dnd5e-item-type { color: var(--df-text-muted); font-size: .72rem; }
.dnd5e-item-meta { margin: 0; color: var(--df-text-muted); font-size: .75rem; }
.dnd5e-ref { padding: .05rem .4rem; border-radius: 999px; background: color-mix(in srgb, var(--df-accent) 14%, var(--df-surface-raised)); color: var(--df-accent-strong); font-size: .7rem; overflow-wrap: anywhere; }
.dnd5e-unmapped { padding: .05rem .4rem; border-radius: 999px; border: 1px solid var(--df-warning); color: var(--df-warning); font-size: .7rem; }
@media (max-width: 640px) { .dnd5e-ability-grid { grid-template-columns: repeat(3, 1fr); } }
</style>