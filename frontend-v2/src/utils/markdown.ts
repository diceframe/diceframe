import { marked } from 'marked'
import DOMPurify from 'dompurify'

// Rendered Markdown is read-only content (announcements, release notes that
// may arrive through a mirror, assistant replies, plugin docs). It may format
// text and link out, but never collect input, restyle or cover the page.
const FORBID_TAGS = ['form', 'input', 'button', 'textarea', 'select', 'option']
const FORBID_ATTR = ['style']

// External links open outside the app instead of navigating it away.
DOMPurify.addHook('afterSanitizeAttributes', (node) => {
  if (node.tagName === 'A' && /^https?:/i.test(node.getAttribute('href') || '')) {
    node.setAttribute('target', '_blank')
    node.setAttribute('rel', 'noopener noreferrer')
  }
})

export interface SafeMarkdownOptions {
  /** Keep `<img>`; off for text fetched from third parties (no tracking pixels). */
  images?: boolean
}

export function renderSafeMarkdown(markdown: string, options: SafeMarkdownOptions = {}): string {
  const raw = marked.parse(markdown || '', { async: false }) as string
  return DOMPurify.sanitize(raw, {
    FORBID_TAGS: options.images === false ? [...FORBID_TAGS, 'img'] : FORBID_TAGS,
    FORBID_ATTR,
  })
}
