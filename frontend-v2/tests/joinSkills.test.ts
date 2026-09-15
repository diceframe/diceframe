import { describe, expect, it } from 'vitest'
import { joinSkillsPayload } from '../src/features/player/joinSkills'

describe('joinSkillsPayload（现场建卡提交投影）', () => {
  it('keeps the effect the player typed', () => {
    const payload = joinSkillsPayload([
      { name: '火焰球', value: 80, effect: '向目标发射火球。' },
    ])
    expect(payload).toEqual([
      { name: '火焰球', value: 80, effect: '向目标发射火球。' },
    ])
    // 提交前必须真的带在 payload 里（而不是被二次 map 丢掉）。
    expect(payload[0].effect).toBe('向目标发射火球。')
  })

  it('never writes an empty effect field', () => {
    const payload = joinSkillsPayload([
      { name: '侦查', value: 45, effect: '' },
      { name: '聆听', value: 30, effect: '   ' },
    ])
    expect(payload).toEqual([
      { name: '侦查', value: 45 },
      { name: '聆听', value: 30 },
    ])
    expect('effect' in payload[0]).toBe(false)
    expect('effect' in payload[1]).toBe(false)
  })

  it('trims and caps the effect at 500 characters', () => {
    const payload = joinSkillsPayload([
      { name: '火焰球', value: 80, effect: `  ${'火'.repeat(600)}  ` },
    ])
    expect(payload[0].effect).toHaveLength(500)
  })

  it('drops nameless rows and converts numeric input strings', () => {
    const payload = joinSkillsPayload([
      { name: '   ', value: 20 },
      { name: '潜行', value: '55' },
      { name: '追踪', value: '' },
    ])
    expect(payload).toEqual([
      { name: '潜行', value: 55 },
      { name: '追踪', value: undefined },
    ])
  })
})
