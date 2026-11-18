import { describe, expect, it } from 'vitest'
import { templateSource } from '../src/features/creator/templates'

describe('Creator workspace templates', () => {
  it('creates a catalog adventure with the real adventure directory contract', () => {
    const files = templateSource('adventure', '灯塔冒险')
    const manifest = JSON.parse(files['plugin.json'])
    expect(manifest.content_profile).toBe('adventure-module')
    expect(manifest.content_delivery_mode).toBe('catalog')
    expect(manifest.adventure_packages).toEqual(['adventures/pack-706f-5854-5192-9669'])
    expect(files['adventures/pack-706f-5854-5192-9669/manifest.json']).toContain('diceframe:adventure-graph-v2')
    expect(files['adventures/pack-706f-5854-5192-9669/adventure.json']).toContain('"start_node_ids"')
  })

  it('ships a package entity and a valid locale overlay so the scaffold builds', () => {
    // 包校验 fail-closed：声明的 zh-CN 必须有真实目录与合法覆盖文件，
    // 覆盖目标必须是存在的实体。脚手架缺任何一件都构建不出 .dfplugin。
    const files = templateSource('adventure', '灯塔冒险')
    const root = 'adventures/pack-706f-5854-5192-9669'
    const entity = JSON.parse(files[`${root}/content/npcs/guide.json`])
    expect(entity.schema_version).toBe(1)
    expect(entity.kind).toBe('npc')
    expect(entity.automation_level).toBe('guided')
    expect(entity.source_ref).toContain('creator:')
    const overlay = JSON.parse(files[`${root}/locales/zh-CN/guide.json`])
    expect(overlay.locale_schema_version).toBe(1)
    expect(overlay.locale).toBe('zh-CN')
    expect(overlay.target).toEqual({ kind: 'npc', id: 'guide' })
    expect(typeof overlay.fields.name).toBe('string')
  })

  it('creates an installable rules content contribution', () => {
    const files = templateSource('rule', 'Starter Rules')
    const manifest = JSON.parse(files['plugin.json'])
    expect(manifest.contributes.rules).toEqual(['content/rules/*.json'])
    expect(files['content/rules/starter-rules.json']).toContain('"check_mechanic"')
  })

  it('creates a transparent gameplay plan and a runnable tool starter', () => {
    const gameplay = templateSource('gameplay', 'Scene Flow')
    expect(gameplay['docs/gameplay-design.md']).toContain('TurnDriver')

    const toolkit = templateSource('toolkit', 'Dice Helper')
    const manifest = JSON.parse(toolkit['plugin.json'])
    expect(manifest.plugin_type).toBe('tool')
    expect(toolkit['runtime/server.py']).toContain('runtime.run()')
    expect(toolkit['runtime/server.py']).toContain('roll_dice')
  })
})
