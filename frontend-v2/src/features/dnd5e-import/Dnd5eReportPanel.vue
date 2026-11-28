<script setup lang="ts">
import { computed } from 'vue'
import type { Dnd5eConversionReport, Dnd5eMissingEntry, Dnd5eReconciliationEntry } from '@/api/dnd5eImport'
import { useLocale } from '@/composables/useLocale'
import { dnd5eImportCopy } from './copy'

const props = withDefaults(defineProps<{
  report: Dnd5eConversionReport
  reconciliation?: Dnd5eReconciliationEntry[]
  sourceUnresolved?: Dnd5eMissingEntry[]
}>(), { reconciliation: () => [], sourceUnresolved: () => [] })
const { locale } = useLocale()
const copy = computed(() => dnd5eImportCopy(locale.value).report)

const levelLabel = computed(() => copy.value.levels[props.report.level] || props.report.level)
const hasIssues = computed(() => (
  props.report.issues.length > 0
  || props.report.warnings.length > 0
  || props.report.missing_references.length > 0
))

function formatValue(value: unknown): string {
  if (value === null || value === undefined) return '—'
  return typeof value === 'object' ? JSON.stringify(value) : String(value)
}
</script>

<template>
  <section class="dnd5e-report" data-testid="dnd5e-import-report">
    <header class="dnd5e-report-head">
      <h3>{{ copy.title }}</h3>
      <span class="dnd5e-level-badge" :class="`dnd5e-level-${report.level}`" data-testid="dnd5e-level-badge">{{ levelLabel }}</span>
      <span class="dnd5e-report-version">{{ copy.sourceVersion }}: {{ report.source_version }}</span>
    </header>

    <p v-if="report.contains_script" class="dnd5e-report-script" role="alert" data-testid="dnd5e-script-warning">{{ copy.containsScript }}</p>
    <p v-if="!hasIssues" class="dnd5e-report-empty">{{ copy.noIssues }}</p>

    <div v-if="report.unsupported.length" class="dnd5e-report-group" data-testid="dnd5e-unsupported">
      <h4>{{ copy.unsupportedTitle }}</h4>
      <ul>
        <li v-for="entry in report.unsupported" :key="`${entry.path}:${entry.kind}`">
          <code>{{ entry.path }}</code>
          <span class="dnd5e-report-kind">{{ entry.kind }}</span>
          {{ entry.detail }}
        </li>
      </ul>
    </div>

    <div v-if="report.warnings.length" class="dnd5e-report-group" data-testid="dnd5e-warnings">
      <h4>{{ copy.warningsTitle }}</h4>
      <ul><li v-for="warning in report.warnings" :key="warning">{{ warning }}</li></ul>
    </div>

    <div v-if="report.missing_references.length" class="dnd5e-report-group" data-testid="dnd5e-missing-references">
      <h4>{{ copy.missingReferencesTitle }}</h4>
      <ul>
        <li v-for="entry in report.missing_references" :key="`${entry.kind}:${entry.path}`">
          <span class="dnd5e-report-kind">{{ entry.kind }}</span>
          <code>{{ entry.path }}</code>
          <template v-if="entry.external_id"> · {{ entry.external_id }}</template>
        </li>
      </ul>
    </div>

    <details v-if="report.unknown_fields.length" class="dnd5e-report-group" data-testid="dnd5e-unknown-fields">
      <summary>{{ copy.unknownFieldsTitle }} ({{ report.unknown_fields.length }})</summary>
      <ul><li v-for="path in report.unknown_fields" :key="path"><code>{{ path }}</code></li></ul>
    </details>

    <details v-if="report.preserved_fields.length" class="dnd5e-report-group" data-testid="dnd5e-preserved-fields">
      <summary>{{ copy.preservedFieldsTitle }} ({{ report.preserved_fields.length }})</summary>
      <ul><li v-for="path in report.preserved_fields" :key="path"><code>{{ path }}</code></li></ul>
    </details>

    <div v-if="sourceUnresolved.length" class="dnd5e-report-group" data-testid="dnd5e-source-unresolved">
      <h4>{{ dnd5eImportCopy(locale).unresolvedTitle }}</h4>
      <ul>
        <li v-for="entry in sourceUnresolved" :key="entry.field"><code>{{ entry.field }}</code> {{ entry.reason }}</li>
      </ul>
    </div>

    <div v-if="reconciliation.length" class="dnd5e-report-group" data-testid="dnd5e-reconciliation">
      <h4>{{ copy.reconciliationTitle }}</h4>
      <p class="dnd5e-report-hint">{{ copy.reconciliationHint }}</p>
      <table>
        <thead>
          <tr><th>{{ copy.declared }}</th><th>{{ copy.recomputed }}</th><th>{{ copy.note }}</th></tr>
        </thead>
        <tbody>
          <tr v-for="entry in reconciliation" :key="entry.field" data-testid="dnd5e-reconciliation-row">
            <td><code>{{ entry.field }}</code> = {{ formatValue(entry.declared) }}</td>
            <td>{{ formatValue(entry.recomputed) }}</td>
            <td>{{ entry.note }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<style scoped>
.dnd5e-report { display: grid; gap: .6rem; padding: .9rem; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-md); background: var(--df-surface-2); min-width: 0; overflow-wrap: anywhere; }
.dnd5e-report-head { display: flex; align-items: center; gap: .6rem; flex-wrap: wrap; }
.dnd5e-report-head h3 { margin: 0; color: var(--df-accent-strong); font-size: 1rem; }
.dnd5e-report-version { color: var(--df-text-muted); font-size: .75rem; }
.dnd5e-level-badge { padding: .2rem .55rem; border-radius: 999px; border: 1px solid var(--df-border-soft); font-size: .72rem; font-weight: 700; color: var(--df-text-secondary); }
.dnd5e-level-convertible { border-color: var(--df-success-strong); color: var(--df-success-strong); }
.dnd5e-level-saved_not_runnable, .dnd5e-level-degraded_display, .dnd5e-level-unknown_future_structure { border-color: var(--df-warning); color: var(--df-warning); }
.dnd5e-level-missing_external_ref, .dnd5e-level-missing_module_dependency, .dnd5e-level-unsupported_ruleset, .dnd5e-level-contains_script { border-color: var(--df-danger); color: var(--df-danger-strong); }
.dnd5e-report-script { margin: 0; padding: .5rem .7rem; border: 1px solid var(--df-danger); border-radius: var(--df-radius-sm); color: var(--df-danger-strong); font-size: .82rem; }
.dnd5e-report-empty { margin: 0; color: var(--df-text-muted); font-size: .82rem; }
.dnd5e-report-group { display: grid; gap: .4rem; }
.dnd5e-report-group h4, .dnd5e-report-group summary { margin: 0; color: var(--df-text); font-size: .85rem; font-weight: 650; }
.dnd5e-report-group ul { display: grid; gap: .3rem; margin: 0; padding-left: 1.2rem; color: var(--df-text-secondary); font-size: .8rem; }
.dnd5e-report-hint { margin: 0; color: var(--df-text-muted); font-size: .75rem; }
.dnd5e-report-kind { display: inline-block; margin: 0 .3rem; padding: .05rem .4rem; border-radius: 999px; background: var(--df-surface-raised); color: var(--df-text-muted); font-size: .7rem; }
code { padding: 0 .25rem; border-radius: var(--df-radius-sm); background: var(--df-control-bg); color: var(--df-text-secondary); font-size: .75rem; overflow-wrap: anywhere; }
table { width: 100%; border-collapse: collapse; font-size: .78rem; color: var(--df-text-secondary); }
th, td { padding: .35rem .5rem; border: 1px solid var(--df-border-soft); text-align: left; vertical-align: top; }
th { color: var(--df-text-muted); font-weight: 650; background: var(--df-surface-1); }
</style>