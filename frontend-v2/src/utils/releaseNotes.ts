/**
 * Release 更新日志的 locale 选择器。
 *
 * Release 正文保持中英双语单文维护，用稳定的二级标题分段（`## 中文` /
 * `## English`）。这里只做展示片段选择，不改动获取与缓存；识别不到分段时
 * 整段原文回退，任何情况下不返回空文本。
 */

export type ReleaseNotesLanguage = 'zh' | 'en' | 'raw'

export interface ReleaseNotesSelection {
  language: ReleaseNotesLanguage
  text: string
}

const ZH_HEADING = /^##\s*(?:中文|chinese|更新日志)\s*$/i
const EN_HEADING = /^##\s*(?:english|changelog)\s*$/i

interface ReleaseSection {
  heading: string
  body: string
}

function splitSections(markdown: string): { intro: string; sections: ReleaseSection[] } {
  const lines = markdown.split(/\r?\n/)
  const intro: string[] = []
  const sections: ReleaseSection[] = []
  let current: ReleaseSection | null = null
  for (const line of lines) {
    const heading = /^##\s+(.+?)\s*$/.exec(line)
    if (heading) {
      if (current) sections.push(current)
      current = { heading: heading[1], body: '' }
      continue
    }
    if (current) current.body += (current.body ? '\n' : '') + line
    else intro.push(line)
  }
  if (current) sections.push(current)
  return {
    intro: intro.join('\n').trim(),
    sections,
  }
}

function isZhLocale(locale: string): boolean {
  return String(locale || '').toLowerCase().startsWith('zh')
}

/**
 * zh 界面：中文段（含标题与导语）→ 英文段 → 原始全文。
 * 其它界面：英文段 → 原始全文。
 */
export function selectReleaseNotes(body: string | null | undefined, locale: string): ReleaseNotesSelection {
  const raw = String(body ?? '').replace(/\r\n/g, '\n').trim()
  if (!raw) return { language: 'raw', text: '' }

  const { intro, sections } = splitSections(raw)
  const zh = sections.find((section) => ZH_HEADING.test(`## ${section.heading}`))
  const en = sections.find((section) => EN_HEADING.test(`## ${section.heading}`))

  if (isZhLocale(locale)) {
    if (zh) {
      const head = intro ? `${intro}\n\n` : ''
      return { language: 'zh', text: `${head}## ${zh.heading}${zh.body ? `\n${zh.body.trim()}` : ''}`.trim() }
    }
    if (en) return { language: 'en', text: `## ${en.heading}${en.body ? `\n${en.body.trim()}` : ''}`.trim() }
    return { language: 'raw', text: raw }
  }

  if (en) return { language: 'en', text: `## ${en.heading}${en.body ? `\n${en.body.trim()}` : ''}`.trim() }
  return { language: 'raw', text: raw }
}
