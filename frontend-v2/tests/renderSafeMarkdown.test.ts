import { describe, expect, it } from 'vitest'

import { renderSafeMarkdown } from '../src/utils/markdown'

describe('renderSafeMarkdown', () => {
  it('renders markdown for announcement content', () => {
    const html = renderSafeMarkdown('# 公告\n\n第一行公告内容。')
    expect(html).toContain('公告')
    expect(html).toContain('第一行公告内容')
  })

  it('strips dangerous HTML from announcement content', () => {
    const html = renderSafeMarkdown(
      '# 公告\n<script>alert("x")</script>\n<img src="x" onerror="alert(1)">\n<a href="javascript:alert(1)">bad</a>',
    )
    expect(html).not.toContain('<script')
    expect(html).not.toContain('onerror')
    expect(html).not.toContain('javascript:')
    expect(html).toContain('公告')
  })
})

describe('renderSafeMarkdown for third-party text', () => {
  it('drops form controls and inline styles so a page cannot be covered or phished', () => {
    const html = renderSafeMarkdown(
      '<form action="https://evil.example"><input type="password"><button>Sign in</button></form>\n'
      + '<div style="position:fixed;inset:0">overlay</div>',
    )
    expect(html).not.toMatch(/<form|<input|<button/)
    expect(html).not.toContain('style=')
    expect(html).toContain('overlay')
  })

  it('opens external links outside the app', () => {
    const html = renderSafeMarkdown('[notes](https://example.com/notes) and [here](#/settings)')
    expect(html).toContain('href="https://example.com/notes"')
    expect(html).toMatch(/<a [^>]*href="https:\/\/example\.com\/notes"[^>]*target="_blank"/)
    expect(html).toMatch(/rel="noopener noreferrer"/)
    expect(html).not.toMatch(/href="#\/settings"[^>]*target=/)
  })

  it('can refuse remote images for text fetched through a mirror', () => {
    const markdown = '![pixel](https://tracker.example/p.png)'
    expect(renderSafeMarkdown(markdown)).toContain('<img')
    expect(renderSafeMarkdown(markdown, { images: false })).not.toContain('<img')
  })
})
