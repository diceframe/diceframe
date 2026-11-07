import { describe, expect, it } from 'vitest'
import { resolveLoreManageTarget } from '../src/features/create/loreManageTarget'

const library = ['harbor', 'custom_harbor_2']

describe('resolveLoreManageTarget', () => {
  it('opens the library world when the inherited world is editable', () => {
    expect(resolveLoreManageTarget({ source: '__builtin__', worldId: 'harbor', editableWorldIds: library }))
      .toEqual({ kind: 'world', worldId: 'harbor' })
  })

  it('opens the copied source world for an independent world', () => {
    expect(resolveLoreManageTarget({ source: 'copy:harbor', worldId: 'custom_harbor_2', editableWorldIds: library }))
      .toEqual({ kind: 'world', worldId: 'harbor' })
  })

  it('sends a package world to the pack page instead of a world the library cannot open', () => {
    // 内容包自带的世界（如 frost_pass）只存在于 /world-templates，不是 /worlds 的一行；
    // 直接跳 ?world=frost_pass 会落到「世界不存在或当前不可用」。
    expect(resolveLoreManageTarget({
      source: '__builtin__', worldId: 'frost_pass', editableWorldIds: library, moduleId: 'dnd-frostwhisper',
    })).toEqual({ kind: 'module', moduleId: 'dnd-frostwhisper' })
  })

  it('reports a package world without a pack as not editable', () => {
    expect(resolveLoreManageTarget({ source: '__builtin__', worldId: 'frost_pass', editableWorldIds: library }))
      .toEqual({ kind: 'none' })
  })

  it('sends an empty library world to the pack that actually holds its content', () => {
    // 每个世界都有一本自动创建的空主册；内容全在包里时，世界页只能给一个
    // 空编辑器，真正的 18 条在包内资料册里。
    expect(resolveLoreManageTarget({
      source: '__builtin__', worldId: 'harbor', editableWorldIds: library,
      moduleId: 'dnd-embervault', worldHasContent: false,
    })).toEqual({ kind: 'module', moduleId: 'dnd-embervault' })
  })

  it('keeps the library world when its own books hold entries', () => {
    expect(resolveLoreManageTarget({
      source: '__builtin__', worldId: 'harbor', editableWorldIds: library,
      moduleId: 'dnd-embervault', worldHasContent: true,
    })).toEqual({ kind: 'world', worldId: 'harbor' })
  })

  it('keeps the library world while the book list is still loading', () => {
    expect(resolveLoreManageTarget({
      source: '__builtin__', worldId: 'harbor', editableWorldIds: library,
      moduleId: 'dnd-embervault', worldHasContent: undefined,
    })).toEqual({ kind: 'world', worldId: 'harbor' })
  })

  it('has no target for a blank world or an empty selection', () => {
    expect(resolveLoreManageTarget({ source: '__blank__', worldId: 'harbor', editableWorldIds: library, moduleId: 'pack' }))
      .toEqual({ kind: 'none' })
    expect(resolveLoreManageTarget({ source: '__builtin__', worldId: '', editableWorldIds: library }))
      .toEqual({ kind: 'none' })
  })
})
