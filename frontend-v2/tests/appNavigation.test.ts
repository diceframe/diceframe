import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import {
  appNavGroups,
  appNavItems,
  navGroupForRoute,
  navGroupItems,
  primaryNavItemIds,
} from '@/navigation/appNavigation'

describe('app navigation', () => {
  it('derives desktop and mobile navigation from one canonical route list', () => {
    const groupedIds = appNavGroups.flatMap(group => navGroupItems(group).map(item => item.id))
    const visibleIds = [...primaryNavItemIds, ...groupedIds]

    expect(new Set(visibleIds).size).toBe(visibleIds.length)
    expect(new Set(visibleIds)).toEqual(new Set(appNavItems.map(item => item.id)))
  })

  it('keeps content and management route identity independent of labels', () => {
    expect(appNavGroups.find(group => group.id === 'content')?.itemIds)
      .toEqual(['lorebook', 'worlds', 'adventures', 'modules', 'rules'])
    expect(appNavGroups.find(group => group.id === 'content')?.defaultItemId).toBe('lorebook')
    expect(appNavGroups.find(group => group.id === 'management')?.itemIds)
      .toEqual(['memory', 'logs', 'plugins', 'settings'])
    expect(appNavGroups.find(group => group.id === 'management')?.defaultItemId).toBe('settings')
    expect(navGroupForRoute('worlds')).toBe('content')
    expect(navGroupForRoute('modules')).toBe('content')
    expect(navGroupForRoute('settings')).toBe('management')
    expect(navGroupForRoute('overview')).toBeNull()
  })

  it('keeps navigation styling out of the generic layout stylesheet', () => {
    const layoutCss = readFileSync(resolve(process.cwd(), 'src/styles/v2/layout.css'), 'utf8')
    const navigationCss = readFileSync(resolve(process.cwd(), 'src/styles/v2/navigation.css'), 'utf8')
    const entryCss = readFileSync(resolve(process.cwd(), 'src/styles/v2.css'), 'utf8')

    expect(layoutCss).not.toContain('.desktop-nav')
    expect(layoutCss).not.toContain('.mobile-bottom-nav')
    expect(navigationCss).not.toContain('.desktop-nav-menu')
    expect(navigationCss).not.toContain('.mobile-nav-panel')
    expect(entryCss).toContain("@import './v2/navigation.css';")
  })
})
