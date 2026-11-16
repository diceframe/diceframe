import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import {
  appNavGroups,
  appNavItems,
  navItem,
  navGroupForRoute,
  navGroupItems,
  isNavItemActive,
  primaryNavItemIds,
  playContextQuery,
} from '@/navigation/appNavigation'

describe('app navigation', () => {
  it('derives desktop and mobile navigation from one canonical route list', () => {
    const groupedIds = appNavGroups.flatMap(group => navGroupItems(group).map(item => item.id))
    const visibleIds = [...primaryNavItemIds, ...groupedIds]

    expect(new Set(visibleIds).size).toBe(visibleIds.length)
    const detailRouteIds = new Set(['worlds', 'adventures'])
    expect(new Set(visibleIds)).toEqual(new Set(appNavItems.filter(item => !detailRouteIds.has(item.id)).map(item => item.id)))
  })

  it('keeps content and management route identity independent of labels', () => {
    expect(appNavGroups.find(group => group.id === 'content')?.itemIds)
      .toEqual(['library', 'creator', 'modules', 'rules', 'lorebook'])
    expect(appNavGroups.find(group => group.id === 'content')?.defaultItemId).toBe('library')
    expect(appNavGroups.find(group => group.id === 'management')?.itemIds)
      .toEqual(['memory', 'logs', 'plugins', 'settings'])
    expect(appNavGroups.find(group => group.id === 'management')?.defaultItemId).toBe('settings')
    expect(navGroupForRoute('worlds')).toBe('content')
    expect(navGroupForRoute('lorebook')).toBe('content')
    expect(navGroupForRoute('modules')).toBe('content')
    expect(navGroupForRoute('library')).toBe('content')
    expect(navGroupForRoute('adventures')).toBe('content')
    expect(navGroupForRoute('creator')).toBe('content')
    expect(navGroupForRoute('settings')).toBe('management')
    expect(navGroupForRoute('overview')).toBeNull()
  })

  it('keeps detail and author routes highlighted through their owning entry', () => {
    expect(isNavItemActive('lorebook', 'lorebook')).toBe(true)
    expect(isNavItemActive('lorebook', 'worlds')).toBe(true)
    expect(isNavItemActive('modules', 'module-detail')).toBe(true)
    expect(isNavItemActive('library', 'adventures')).toBe(true)
    expect(isNavItemActive('creator', 'creator')).toBe(true)
    expect(isNavItemActive('worlds', 'adventures')).toBe(false)
  })

  it('uses the product entry labels for the primary routes', () => {
    expect(primaryNavItemIds).toEqual(['overview', 'play', 'characters'])
    expect(navItem('overview').labelKey).toBe('navOverview')
    expect(navItem('characters').labelKey).toBe('navCharacters')
    expect(navItem('play').labelKey).toBe('navPlay')
    expect(navItem('library').labelKey).toBe('navContentLibrary')
  })

  it('keeps navigation styling out of the generic layout stylesheet', () => {
    const layoutCss = readFileSync(resolve(process.cwd(), 'src/styles/pages/play.css'), 'utf8')
    const navigationCss = readFileSync(resolve(process.cwd(), 'src/styles/navigation.css'), 'utf8')
    const entryCss = readFileSync(resolve(process.cwd(), 'src/styles/index.css'), 'utf8')

    expect(layoutCss).not.toContain('.desktop-nav')
    expect(layoutCss).not.toContain('.mobile-bottom-nav')
    expect(navigationCss).not.toContain('.desktop-nav-menu')
    expect(navigationCss).not.toContain('.mobile-nav-panel')
    expect(entryCss).toContain("@import './navigation.css';")
  })

  it('preserves only the play identity context for top-level return navigation', () => {
    expect(playContextQuery({ game: 'web|run|gm', user: 'alice', share: '1', intent: 'stale' }, ''))
      .toEqual({ game: 'web|run|gm', user: 'alice', share: '1' })
    expect(playContextQuery({ game: 'web|run|gm', user: ['alice', 'ignored'], share: ['1'] }, ''))
      .toEqual({ game: 'web|run|gm', user: 'alice', share: '1' })
    expect(playContextQuery({ user: 'alice', share: '1' }, ''))
      .toBeNull()
    expect(playContextQuery({ user: 'alice', share: '1' }, 'web|run|gm'))
      .toEqual({ game: 'web|run|gm', user: 'alice', share: '1' })
  })
})
