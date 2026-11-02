import { onBeforeUnmount, ref, watch, type Ref } from 'vue'
import type { DriveStep } from 'driver.js'
import { appTourCopy, type AppTourCopy } from './tourCopy'

const seenKey = 'diceframe:onboarding:tour:v1'
/** Gives startup dialogs that settle in the same tick time to open before the tour checks again. */
const AUTO_START_DELAY_MS = 400

const running = ref(false)

export function hasSeenAppTour(): boolean {
  try {
    return localStorage.getItem(seenKey) === '1'
  } catch {
    // With storage blocked we cannot remember the choice; never auto-start in that case.
    return true
  }
}

function markAppTourSeen(): void {
  try {
    localStorage.setItem(seenKey, '1')
  } catch {
    // Replay stays available from the overview page.
  }
}

/** Reset the first-run flag; used by tests and kept for support/debugging. */
export function resetAppTourSeen(): void {
  try {
    localStorage.removeItem(seenKey)
  } catch {
    // Nothing to reset when storage is unavailable.
  }
}

function isVisible(element: Element): boolean {
  return element.getClientRects().length > 0
}

/**
 * The same anchor can exist twice (desktop nav and mobile bottom nav); only one is
 * laid out at a time, so pick whichever is actually visible.
 */
function findAnchor(anchor: string, root: ParentNode, visible: (element: Element) => boolean): Element | null {
  for (const element of root.querySelectorAll(`[data-tour="${anchor}"]`)) {
    if (visible(element)) return element
  }
  return null
}

interface TourStepSpec {
  /** `data-tour` value; omitted for the centered welcome step. */
  anchor?: string
  title: keyof AppTourCopy
  body: keyof AppTourCopy
  side?: 'top' | 'bottom' | 'left' | 'right'
}

const stepSpecs: readonly TourStepSpec[] = [
  { title: 'welcomeTitle', body: 'welcomeBody' },
  { anchor: 'nav-overview', title: 'overviewTitle', body: 'overviewBody', side: 'bottom' },
  { anchor: 'nav-play', title: 'playTitle', body: 'playBody', side: 'bottom' },
  { anchor: 'nav-characters', title: 'charactersTitle', body: 'charactersBody', side: 'bottom' },
  { anchor: 'nav-content', title: 'contentTitle', body: 'contentBody', side: 'bottom' },
  { anchor: 'nav-management', title: 'managementTitle', body: 'managementBody', side: 'bottom' },
  { anchor: 'assistant', title: 'assistantTitle', body: 'assistantBody', side: 'top' },
  { anchor: 'create-game', title: 'createTitle', body: 'createBody', side: 'bottom' },
]

/**
 * Steps whose anchor is not on screen (other route, assistant disabled, narrow
 * layout) are dropped instead of pointing at nothing.
 */
export function buildAppTourSteps(
  copy: AppTourCopy,
  root: ParentNode = document,
  visible: (element: Element) => boolean = isVisible,
): DriveStep[] {
  const steps: DriveStep[] = []
  for (const spec of stepSpecs) {
    const popover = { title: copy[spec.title], description: copy[spec.body], side: spec.side, align: 'center' as const }
    if (!spec.anchor) {
      steps.push({ popover })
      continue
    }
    const element = findAnchor(spec.anchor, root, visible)
    if (element) steps.push({ element, popover })
  }
  return steps
}

export async function startAppTour(locale: string): Promise<boolean> {
  if (running.value) return false
  running.value = true
  try {
    // Loaded on demand so first paint never pays for the tour library.
    const [{ driver }] = await Promise.all([import('driver.js'), import('./tour.css')])
    const copy = appTourCopy(locale)
    const steps = buildAppTourSteps(copy)
    // Only the welcome step left means the page has none of the shell to point at.
    if (steps.length <= 1) {
      running.value = false
      return false
    }
    markAppTourSeen()
    const tour = driver({
      steps,
      showProgress: true,
      progressText: copy.progress,
      nextBtnText: copy.next,
      prevBtnText: copy.prev,
      doneBtnText: copy.done,
      popoverClass: 'df-tour-popover',
      allowClose: true,
      disableActiveInteraction: true,
      animate: !window.matchMedia?.('(prefers-reduced-motion: reduce)').matches,
      onDestroyed: () => { running.value = false },
    })
    tour.drive()
    return true
  } catch {
    running.value = false
    return false
  }
}

/**
 * Starts the tour once per browser profile, after `ready` turns true and stays
 * true for a short settle window. `ready` must already account for the startup
 * dialogs (privacy, update, announcement) so the tour never competes with them.
 */
export function useAppTourAutoStart(ready: Ref<boolean>, locale: Ref<string>): void {
  let timer: ReturnType<typeof setTimeout> | undefined
  const stop = watch(ready, (isReady) => {
    clearTimeout(timer)
    if (!isReady || hasSeenAppTour()) return
    timer = setTimeout(() => {
      if (ready.value && !hasSeenAppTour()) void startAppTour(locale.value)
    }, AUTO_START_DELAY_MS)
  }, { immediate: true })
  onBeforeUnmount(() => {
    clearTimeout(timer)
    stop()
  })
}
