import { mount } from '@vue/test-utils'
import { defineComponent, nextTick, ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const { driveMock, driverMock } = vi.hoisted(() => {
  const driveMock = vi.fn()
  const driverMock = vi.fn((_config: { onDestroyed?: () => void }) => ({ drive: driveMock }))
  return { driveMock, driverMock }
})

vi.mock('driver.js', () => ({ driver: driverMock }))
vi.mock('../src/features/onboarding/tour.css', () => ({}))

import {
  buildAppTourSteps,
  hasSeenAppTour,
  resetAppTourSeen,
  startAppTour,
  useAppTourAutoStart,
} from '../src/features/onboarding/useAppTour'
import { appTourCopy } from '../src/features/onboarding/tourCopy'

function anchor(name: string, root: HTMLElement, visible = true) {
  const element = document.createElement('a')
  element.dataset.tour = name
  element.dataset.visible = visible ? '1' : '0'
  root.appendChild(element)
  return element
}

const isMarkedVisible = (element: Element) => (element as HTMLElement).dataset.visible === '1'

describe('app tour', () => {
  beforeEach(() => {
    localStorage.clear()
    driveMock.mockClear()
    driverMock.mockClear()
    document.body.innerHTML = ''
  })

  afterEach(() => {
    vi.useRealTimers()
    // `running` is module state; end any tour a test left open.
    for (const [config] of driverMock.mock.calls) config.onDestroyed?.()
  })

  it('keeps the welcome step and drops steps whose anchor is not on screen', () => {
    const root = document.createElement('div')
    anchor('nav-overview', root)
    anchor('nav-play', root, false)
    anchor('create-game', root)

    const steps = buildAppTourSteps(appTourCopy('en'), root, isMarkedVisible)

    expect(steps.map(step => step.popover?.title)).toEqual(['Welcome to DiceFrame', 'Overview', 'Start your first game'])
    expect(steps[0].element).toBeUndefined()
  })

  it('uses the visible copy of an anchor that exists in both desktop and mobile nav', () => {
    const root = document.createElement('div')
    anchor('nav-overview', root, false)
    const mobile = anchor('nav-overview', root, true)

    const steps = buildAppTourSteps(appTourCopy('zh-CN'), root, isMarkedVisible)

    expect(steps[1].element).toBe(mobile)
  })

  it('falls back to English copy for an unknown locale', () => {
    expect(appTourCopy('fr').welcomeTitle).toBe(appTourCopy('en').welcomeTitle)
  })

  it('starts driver.js, marks the tour seen, and refuses to overlap a running tour', async () => {
    const link = anchor('nav-overview', document.body)
    link.getClientRects = () => [{}] as unknown as DOMRectList

    expect(hasSeenAppTour()).toBe(false)
    await expect(startAppTour('en')).resolves.toBe(true)
    expect(driveMock).toHaveBeenCalledTimes(1)
    expect(hasSeenAppTour()).toBe(true)
    await expect(startAppTour('en')).resolves.toBe(false)

    driverMock.mock.calls[0][0].onDestroyed?.()
    await expect(startAppTour('en')).resolves.toBe(true)
  })

  it('does not start, nor mark seen, when the page has nothing to point at', async () => {
    await expect(startAppTour('en')).resolves.toBe(false)
    expect(driveMock).not.toHaveBeenCalled()
    expect(hasSeenAppTour()).toBe(false)
  })

  describe('auto start', () => {
    function mountAutoStart(ready: ReturnType<typeof ref<boolean>>) {
      const locale = ref('en')
      return mount(defineComponent({
        setup() {
          useAppTourAutoStart(ready as never, locale)
          return () => null
        },
      }))
    }

    function visibleAnchor() {
      const link = anchor('nav-overview', document.body)
      link.getClientRects = () => [{}] as unknown as DOMRectList
    }

    it('waits for the startup dialogs, then starts once after the settle delay', async () => {
      vi.useFakeTimers()
      visibleAnchor()
      const ready = ref(false)
      const wrapper = mountAutoStart(ready)

      await vi.advanceTimersByTimeAsync(1000)
      expect(driveMock).not.toHaveBeenCalled()

      ready.value = true
      await nextTick()
      await vi.advanceTimersByTimeAsync(1000)
      expect(driveMock).toHaveBeenCalledTimes(1)
      wrapper.unmount()
    })

    it('stays quiet when a dialog reopens during the settle delay', async () => {
      vi.useFakeTimers()
      visibleAnchor()
      const ready = ref(true)
      const wrapper = mountAutoStart(ready)

      await vi.advanceTimersByTimeAsync(100)
      ready.value = false
      await nextTick()
      await vi.advanceTimersByTimeAsync(1000)

      expect(driveMock).not.toHaveBeenCalled()
      wrapper.unmount()
    })

    it('never auto-starts again after the tour was seen', async () => {
      vi.useFakeTimers()
      visibleAnchor()
      localStorage.setItem('diceframe:onboarding:tour:v1', '1')
      const wrapper = mountAutoStart(ref(true))

      await vi.advanceTimersByTimeAsync(1000)

      expect(driveMock).not.toHaveBeenCalled()
      wrapper.unmount()
      resetAppTourSeen()
      expect(hasSeenAppTour()).toBe(false)
    })
  })
})
