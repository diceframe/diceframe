import { describe, expect, it } from 'vitest'

import { de } from '../src/i18n/messages/de'
import { en } from '../src/i18n/messages/en'
import { ja } from '../src/i18n/messages/ja'
import { zhCN } from '../src/i18n/messages/zh-CN'

const locales = { 'zh-CN': zhCN, en, ja, de } as const

const storyboardKeys = [
  'imagegenAutoStoryboard',
  'imagegenAutoStoryboardHelp',
  'storyboardAnalyze',
  'storyboardReanalyze',
  'storyboardApplyCandidate',
  'storyboardNotAnalyzed',
  'storyboardCandidateHint',
  'storyboardCandidateTitle',
  'storyboardNoParticipants',
  'storyboardAnalyzingHint',
  'storyboardCandidateReady',
  'storyboardDraftLoaded',
  'storyboardAutomatic',
  'storyboardAutomaticHint',
  'storyboardAnalyzeCount',
  'storyboardPanelCountMismatch',
  'storyboardCountNeedsAnalysis',
  'storyboardParticipantsEditPlaceholder',
  'combineAvatarReferences',
] as const

const buttonKeys = [
  'storyboardAnalyze',
  'storyboardReanalyze',
  'storyboardApplyCandidate',
  'storyboardAnalyzeCount',
] as const

function placeholders(value: string): string[] {
  return [...value.matchAll(/\{[^}]+\}/g)].map(match => match[0]).sort()
}

describe('storyboard locale copy', () => {
  it('keeps the complete storyboard message set in every locale', () => {
    for (const messages of Object.values(locales)) {
      for (const key of storyboardKeys) expect(messages[key]).toBeTruthy()
    }
  })

  it('keeps interpolation placeholders aligned with Chinese', () => {
    for (const key of storyboardKeys) {
      const expected = placeholders(zhCN[key])
      for (const messages of Object.values(locales)) {
        expect(placeholders(messages[key])).toEqual(expected)
      }
    }
  })

  it('keeps copy within the controls used by the storyboard dialog', () => {
    for (const messages of Object.values(locales)) {
      for (const key of buttonKeys) expect(messages[key].length).toBeLessThanOrEqual(32)
      expect(messages.storyboardParticipantsEditPlaceholder.length).toBeLessThanOrEqual(40)
      expect(messages.combineAvatarReferences.length).toBeLessThanOrEqual(64)
      expect(messages.imagegenAutoStoryboardHelp.length).toBeLessThanOrEqual(180)
    }
  })
})
