<script setup lang="ts">
import { computed } from 'vue'
import { useLocale } from '@/composables/useLocale'
import { FOUNDRY_CONVERSION_LEVELS, type FoundryConversionReport } from '@/api/tacticalScene'
import { tacticalImportCopy } from './copy'

const props = defineProps<{ report: FoundryConversionReport; scrubbedFields?: string[] }>()
const { locale } = useLocale()
const copy = computed(() => tacticalImportCopy(locale.value))
const groups = computed(() => FOUNDRY_CONVERSION_LEVELS
  .map(level => ({ level, issues: props.report.issues.filter(issue => issue.level === level) }))
  .filter(group => group.issues.length > 0))
</script>

<template>
  <section class="foundry-report" data-testid="foundry-conversion-report">
    <h3>{{ copy.reportTitle }}</h3>
    <p class="foundry-report-level">
      {{ copy.levelLabel }}:
      <span class="foundry-level-badge" :class="`foundry-level-${report.level}`" data-testid="foundry-level-badge">{{ copy.levels[report.level] }}</span>
    </p>
    <p v-if="report.contains_script" class="error-banner" role="alert" data-testid="foundry-script-warning">{{ copy.containsScript }}</p>

    <template v-if="groups.length">
      <h4>{{ copy.issues }}</h4>
      <div v-for="group in groups" :key="group.level" class="foundry-issue-group" :data-testid="`foundry-issues-${group.level}`">
        <p class="foundry-issue-heading">{{ copy.levels[group.level] }} · {{ group.issues.length }}</p>
        <ul>
          <li v-for="(issue, index) in group.issues" :key="`${issue.path}-${index}`">
            <code>{{ issue.path }}</code> — {{ issue.detail }}
          </li>
        </ul>
      </div>
    </template>
    <p v-else class="foundry-report-empty">{{ copy.noIssues }}</p>

    <template v-if="report.warnings.length">
      <h4>{{ copy.warnings }}</h4>
      <ul data-testid="foundry-warnings"><li v-for="(warning, index) in report.warnings" :key="index">{{ warning }}</li></ul>
    </template>

    <template v-if="report.missing_references.length">
      <h4>{{ copy.missingReferences }}</h4>
      <ul data-testid="foundry-missing-references">
        <li v-for="(reference, index) in report.missing_references" :key="index">
          {{ reference.kind }} · <code>{{ reference.path }}</code><template v-if="reference.external_id"> · {{ reference.external_id }}</template>
        </li>
      </ul>
    </template>

    <details v-if="report.preserved_fields.length" class="foundry-report-detail">
      <summary>{{ copy.preservedFields }} · {{ report.preserved_fields.length }}</summary>
      <p>{{ report.preserved_fields.join(', ') }}</p>
    </details>
    <details v-if="report.unknown_fields.length" class="foundry-report-detail">
      <summary>{{ copy.unknownFields }} · {{ report.unknown_fields.length }}</summary>
      <p>{{ report.unknown_fields.join(', ') }}</p>
    </details>
    <details v-if="scrubbedFields?.length" class="foundry-report-detail" data-testid="foundry-scrubbed-fields">
      <summary>{{ copy.scrubbedFields }} · {{ scrubbedFields.length }}</summary>
      <p>{{ scrubbedFields.join(', ') }}</p>
    </details>
  </section>
</template>

<style scoped>
.foundry-report { display: grid; gap: .7rem; min-width: 0; overflow-wrap: anywhere; padding: .9rem; border: 1px solid color-mix(in srgb, var(--df-accent) 26%, var(--df-border-soft)); border-radius: var(--df-radius-md); background: var(--df-surface-2); }
h3 { margin: 0; color: var(--df-accent-strong); font-size: 1rem; }
h4, ul { margin: 0; }
h4 { color: var(--df-text-secondary); font-size: .85rem; }
ul { padding-left: 1.2rem; color: var(--df-text-secondary); font-size: .82rem; }
.foundry-report-level { margin: 0; color: var(--df-text-secondary); font-size: .85rem; }
.foundry-level-badge { display: inline-flex; padding: .2rem .5rem; border-radius: 999px; border: 1px solid var(--df-border-soft); background: var(--df-surface-1); color: var(--df-text); font-size: .72rem; font-weight: 700; }
.foundry-level-contains_script, .foundry-level-unknown_future_structure, .foundry-level-unsupported_ruleset { border-color: var(--df-danger, var(--df-warning)); color: var(--df-danger, var(--df-warning)); }
.foundry-issue-group { display: grid; gap: .3rem; padding: .55rem .65rem; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-sm); background: var(--df-surface-1); }
.foundry-issue-heading { margin: 0; color: var(--df-text); font-size: .8rem; font-weight: 700; }
.foundry-report-empty { margin: 0; color: var(--df-text-muted); font-size: .82rem; }
code { padding: 0 .25rem; border-radius: var(--df-radius-sm); background: var(--df-control-bg); color: var(--df-text-secondary); font-size: .78rem; }
.foundry-report-detail { border-top: 1px solid var(--df-border-soft); padding-top: .6rem; }
.foundry-report-detail summary { color: var(--df-text-secondary); cursor: pointer; font-weight: 650; font-size: .85rem; }
.foundry-report-detail p { margin: .5rem 0 0; color: var(--df-text-muted); font-size: .82rem; }
</style>