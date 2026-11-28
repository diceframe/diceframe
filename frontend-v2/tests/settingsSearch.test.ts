import { describe, expect, it } from 'vitest'
import { searchSettings, settingsSearchTarget, revealSettingTarget, settingsGroupIds, settingsGroupFor, settingsSearchCopy } from '../src/features/admin/settings/settingsSearch'

describe('settings search', () => {
  it('finds aliases and reports the category path without inspecting configuration values', () => {
    expect(searchSettings('朗读')[0]).toMatchObject({ id: 'tts', section: 'general', path: ['General', 'Text to speech'] })
    expect(searchSettings('API key')[0]).toMatchObject({ id: 'providers', section: 'api' })
    expect(searchSettings('额度')[0]).toMatchObject({ id: 'agent-budget', section: 'general' })
  })

  it('keeps everyday, voice, Agent and maintenance preferences together without extra category headings', () => {
    expect(settingsGroupIds).toEqual(['ai', 'connection', 'security', 'general'])
    for (const section of ['general', 'appearance', 'about'] as const) {
      expect(settingsGroupFor(section)).toBe('general')
    }
    expect(settingsSearchCopy('zh-CN').placeholder).toBe('搜索')
    expect(settingsSearchCopy('en').placeholder).toBe('Search')
    expect(settingsSearchTarget('agent-budget', 'zh-CN')?.path[0]).toBe('通用')
    expect(settingsSearchTarget('logs', 'zh-CN')?.path[0]).toBe('通用')
    for (const id of ['tts', 'agent', 'agent-budget', 'logs', 'test-timeout']) {
      expect(settingsSearchTarget(id)?.section).toBe('general')
    }
    for (const id of ['tts-model', 'asr']) expect(settingsSearchTarget(id)?.section).toBe('models')
  })

  it('localizes results, falls back to English, matches words and hides unavailable sections', () => {
    expect(searchSettings('api key', 'zh-CN')[0]?.label).toBe('AI 服务商')
    expect(searchSettings('api   key', 'de')[0]?.label).toBe('AI providers')
    expect(searchSettings('backend', 'en')).toEqual([])
    expect(searchSettings('backend', 'en', true)[0]?.section).toBe('connection')
    expect(searchSettings('生图提示词')[0]?.target).toBe('settings-imagegen-prompts')
    expect(searchSettings('推理强度', 'zh-CN')[0]).toMatchObject({ id: 'reasoning', section: 'models', target: 'settings-reasoning-effort' })
    expect(searchSettings('reasoning', 'en')[0]?.label).toBe('Reasoning effort')
    expect(settingsSearchTarget('imagegen-prompts')?.target).toBe('settings-imagegen-prompts')
    expect(settingsSearchTarget('connection')).toBeUndefined()
    expect(settingsSearchTarget('#malicious-selector')).toBeUndefined()
  })

  it('opens enclosing disclosures and focuses the actual setting', () => {
    document.body.innerHTML = '<details><summary>More</summary><section id="target"><input /></section></details>'
    const target = document.getElementById('target')!
    target.scrollIntoView = () => {}
    revealSettingTarget(target)
    expect(document.querySelector('details')!.open).toBe(true)
    expect(document.activeElement).toBe(target.querySelector('input'))
    expect(target.classList.contains('settings-search-highlight')).toBe(true)
    document.body.innerHTML = ''
  })

  it('returns no results for blank and unknown input', () => {
    expect(searchSettings('')).toEqual([])
    expect(searchSettings('definitely-not-a-setting')).toEqual([])
  })

  it('routes update searches to About and rollback searches to General maintenance', () => {
    expect(settingsSearchTarget('about')).toMatchObject({ section: 'about', target: 'settings-update' })
    expect(searchSettings('回退', 'zh-CN')[0]).toMatchObject({ section: 'general', target: 'settings-rollback' })
  })
})
