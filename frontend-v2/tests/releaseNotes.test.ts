import { describe, expect, it } from 'vitest'
import { selectReleaseNotes } from '../src/utils/releaseNotes'

const BILINGUAL = [
  '# DiceFrame v2.6.1',
  '',
  '> 正式版本。自 v2.6.0 以来跨度很大。',
  '',
  '## 中文',
  '',
  '### 新增内容',
  '',
  '- **权威世界状态**：世界事实成为服务端权威。',
  '',
  '### 下载与校验',
  '',
  '- 校验和见 Release 附件。',
  '',
  '## English',
  '',
  '### Highlights since v2.6.0',
  '',
  '- **WorldState**: authoritative world facts.',
  '',
  '### Downloads',
  '',
  '- See release assets.',
].join('\n')

const ZH_ONLY = ['# DiceFrame v2.6.1', '', '## 中文', '', '- 中文唯一段落'].join('\n')

const NO_SECTIONS = ['v2.6.1', '', '- plain bullet without any headings'].join('\n')

describe('selectReleaseNotes', () => {
  it('zh locale prefers the Chinese section with title and intro', () => {
    const result = selectReleaseNotes(BILINGUAL, 'zh-CN')
    expect(result.language).toBe('zh')
    expect(result.text).toContain('# DiceFrame v2.6.1')
    expect(result.text).toContain('> 正式版本。自 v2.6.0 以来跨度很大。')
    expect(result.text).toContain('## 中文')
    expect(result.text).toContain('权威世界状态')
    expect(result.text).not.toContain('WorldState')
    expect(result.text).not.toContain('## English')
  })

  it('non-zh locale gets the English section without the Chinese intro', () => {
    const result = selectReleaseNotes(BILINGUAL, 'en')
    expect(result.language).toBe('en')
    expect(result.text).toContain('## English')
    expect(result.text).toContain('authoritative world facts')
    expect(result.text).not.toContain('正式版本')
    expect(result.text).not.toContain('## 中文')
  })

  it('zh-TW also resolves to the Chinese section', () => {
    const result = selectReleaseNotes(BILINGUAL, 'zh-TW')
    expect(result.language).toBe('zh')
  })

  it('falls back to the English section for zh when no Chinese section exists', () => {
    const body = BILINGUAL.replace(/## 中文[\s\S]*?(?=## English)/, '')
    const result = selectReleaseNotes(body, 'zh-CN')
    expect(result.language).toBe('en')
    expect(result.text).toContain('## English')
  })

  it('zh-only body falls back to raw text for non-zh locales', () => {
    const result = selectReleaseNotes(ZH_ONLY, 'en')
    expect(result.language).toBe('raw')
    expect(result.text).toContain('## 中文')
  })

  it('bodies without level-2 headings return raw for both locales', () => {
    expect(selectReleaseNotes(NO_SECTIONS, 'zh-CN').language).toBe('raw')
    expect(selectReleaseNotes(NO_SECTIONS, 'ja').language).toBe('raw')
  })

  it('empty bodies never produce content', () => {
    expect(selectReleaseNotes('', 'zh-CN')).toEqual({ language: 'raw', text: '' })
    expect(selectReleaseNotes(null, 'en')).toEqual({ language: 'raw', text: '' })
  })

  it('recognises the Changelog heading aliases', () => {
    const aliased = ['# v1', '', '## 更新日志', '', '- 中文行', '', '## Changelog', '', '- english line'].join('\n')
    expect(selectReleaseNotes(aliased, 'zh-CN').language).toBe('zh')
    expect(selectReleaseNotes(aliased, 'de').language).toBe('en')
  })
})
