import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import { i18n } from '../src/i18n'
import ScriptImportDialog from '../src/features/adventures/ScriptImportDialog.vue'

// m3-02 emits these two adventure draft warnings when a scene row cannot be
// converted (no detectable version / malformed) or carries executable content.
// The dialog must localize them instead of showing the raw code.
function payload(warnings: string[]) {
  return {
    ok: true, plan_digest: 'sha256:test', blockers: [], warnings,
    commit_plan: { unresolved_references: [] },
    draft: {
      directory_id: 'demo', references: [],
      files: {
        'manifest.json': { adventure_id: 'user:demo', version: '1.0.0', world_policy: 'portable', recommended_world_id: '' },
        'adventure.json': { id: 'demo', name: '雾港', start_node_ids: ['a'], chapters: [{ id: 'ch', name: '开场' }], nodes: [{ id: 'a', chapter_id: 'ch', type: 'scene', name: '抵达', description: '原文', visibility: 'gm', transitions: [] }] },
      },
    },
  }
}

function open(warnings: string[]) {
  return mount(ScriptImportDialog, {
    props: { initialPreview: payload(warnings) as never },
    global: { plugins: [i18n], stubs: { Modal: { template: '<div><slot /></div>' } } },
  })
}

afterEach(() => { i18n.global.locale.value = 'en' })

describe('adventure import scene warnings', () => {
  it('localizes externalImportSceneUnconverted in zh-CN', () => {
    i18n.global.locale.value = 'zh-CN'
    const wrapper = open(['externalImportSceneUnconverted'])
    const list = wrapper.get('.script-warning-list')
    expect(list.text()).toContain('未转换为战术场景')
    expect(list.text()).not.toContain('externalImportSceneUnconverted')
  })

  it('localizes externalImportSceneScriptsOmitted in zh-CN', () => {
    i18n.global.locale.value = 'zh-CN'
    const wrapper = open(['externalImportSceneScriptsOmitted'])
    const list = wrapper.get('.script-warning-list')
    expect(list.text()).toContain('已跳过转换')
    expect(list.text()).not.toContain('externalImportSceneScriptsOmitted')
  })

  it('localizes both scene warnings in English', () => {
    const wrapper = open(['externalImportSceneUnconverted', 'externalImportSceneScriptsOmitted'])
    const list = wrapper.get('.script-warning-list')
    expect(list.text()).toContain('were not converted to tactical scenes')
    expect(list.text()).toContain('never executed')
  })
})