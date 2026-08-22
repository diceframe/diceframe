import { describe, expect, it } from 'vitest'

import { gameSseEffect, updatedGameSseCursor } from '@/composables/gameSse'

describe('game SSE reconnect state', () => {
  it('records a baseline without scheduling another HTTP refresh', () => {
    expect(gameSseEffect({ type: 'baseline' })).toBe('baseline')
  })

  it('keeps the last event cursor across a reconnect until a newer event arrives', () => {
    const cursor = 'r12.p2.a0123456789.s9876543210'
    expect(updatedGameSseCursor('', cursor)).toBe(cursor)
    expect(updatedGameSseCursor(cursor, '')).toBe(cursor)
  })

  it('continues to refresh for real public and private state events', () => {
    expect(gameSseEffect({ type: 'public_actions' })).toBe('refresh')
    expect(gameSseEffect({ type: 'private' })).toBe('refresh')
    expect(gameSseEffect({ type: 'refresh' })).toBe('refresh')
  })

  it('keeps streaming narration updates out of the full refresh path', () => {
    expect(gameSseEffect({ type: 'narration_delta', text: 'new' })).toBe('narration-delta')
    expect(gameSseEffect({ type: 'narration_reset' })).toBe('narration-reset')
  })
})
