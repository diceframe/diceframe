import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

function source(relativePath: string): string {
  return readFileSync(fileURLToPath(new URL(relativePath, import.meta.url)), 'utf8')
}

describe('overflow layout contracts', () => {
  it('keeps the portrait edit hint inside the portrait button', () => {
    const css = source('../src/styles/shared.css')
    const rule = css.match(/\.portrait-edit-button>span:last-child\s*\{([\s\S]*?)\n\}/)?.[1] || ''
    expect(rule).toContain('left: 50%')
    expect(rule).toContain('transform: translateX(-50%)')
    expect(rule).toContain('bottom: 4px')
    expect(rule).toContain('width: max-content')
    expect(rule).not.toContain('min-width: max-content')
    expect(rule).toContain('max-width: calc(100% - 8px)')
    expect(rule).toContain('text-overflow: ellipsis')
  })

  it('clamps long lorebook card text before it can stretch the card out of view', () => {
    const sourceText = source('../src/features/library/ContentLibraryView.vue')
    expect(sourceText).toMatch(/\.lorebook-card\s*\{[^}]*overflow: hidden/)
    expect(sourceText).toMatch(/\.lorebook-card p\s*\{[^}]*-webkit-line-clamp: 4/)
    expect(sourceText).toMatch(/\.lorebook-card h3\s*\{[^}]*-webkit-line-clamp: 2/)
  })
})
