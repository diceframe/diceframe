import { computed, reactive, ref, watch, type Ref } from 'vue'
import type { JsonObject, RulesetCombatAction, RulesetGameplayView } from '@/api/types'
import {
  battleDistance, gridCellKey, gridReachableCells,
  type BattleToken, type GridCell,
} from './battleView'

type CombatView = RulesetGameplayView['combat']

export interface GridCombatOptions {
  combat: Ref<CombatView | undefined>
  moveAction: Ref<RulesetCombatAction | undefined>
  tokens: Ref<BattleToken[]>
  currentToken: Ref<BattleToken | null>
  isOwnTurn: Ref<boolean>
  selectDockTab: (tab: string) => void
  stage: (payload: JsonObject) => void
  t: (key: string, params?: Record<string, unknown>) => string
  copy: Ref<{ move: string; feet: string }>
}

/**
 * Grid battlefield state for the combat HUD (workplan §6.14 / ROAD PR-5).
 *
 * Owns the staged target cell, the client-side reachability hint and the grid
 * move intent shape. The server stays authoritative: this only prepares a
 * `target_cell` payload and never writes coordinates. Theater logic lives in
 * the panel and is untouched here.
 */
export function useGridCombat(options: GridCombatOptions) {
  const { combat, moveAction, tokens, currentToken, isOwnTurn, selectDockTab, stage, t, copy } = options
  const selectedCell = ref<GridCell | null>(null)

  const board = computed(() => (
    combat.value?.position_mode === 'grid' ? combat.value?.board ?? null : null
  ))
  const isGrid = computed(() => Boolean(board.value))
  const cellSize = computed(() => Math.max(1, Number(board.value?.cell_size) || 5))
  const canMove = computed(() => isGrid.value && isOwnTurn.value && Boolean(moveAction.value))
  const reachable = computed<Set<string>>(() => (
    canMove.value && board.value
      ? gridReachableCells(
        board.value, tokens.value,
        String(combat.value?.current_actor_id || ''),
        Number(moveAction.value?.movement_remaining || 0),
      )
      : new Set<string>()
  ))
  const moveCost = computed(() => {
    const cell = selectedCell.value
    const mover = currentToken.value
    if (!cell || !mover || mover.coord.y === undefined) return 0
    return battleDistance(mover.coord, cell, cellSize.value)
  })
  const dockCost = computed(() => `${moveCost.value} ${copy.value.feet}`)
  const moveRangeText = computed(() => (
    `${moveCost.value} ${copy.value.feet} · ${Number(moveAction.value?.movement_remaining || 0)} ${copy.value.feet}`
  ))
  const moveHint = computed(() => t('dndGridMoveHint'))
  const targetCellLabel = computed(() => t('dndGridTargetCell'))

  function cellLabel(cell: GridCell): string {
    return t('dndGridCell', { x: cell.x, y: cell.y })
  }

  function chooseCell(cell: GridCell): void {
    if (!canMove.value || !reachable.value.has(gridCellKey(cell.x, cell.y))) return
    selectedCell.value = cell
    selectDockTab('move')
  }

  function stageMove(): void {
    const cell = selectedCell.value
    if (!cell) return
    stage({
      type: 'move', actor_id: String(moveAction.value?.actor_id || ''),
      target_cell: { x: cell.x, y: cell.y },
    })
  }

  /** Grid move summary, or '' when the payload is a theater distance move. */
  function moveSummary(payload: JsonObject): string {
    const cell = payload.target_cell as GridCell | undefined
    if (!cell) return ''
    return `${copy.value.move} · ${cellLabel(cell)} · ${moveCost.value} ${copy.value.feet}`
  }

  function reset(): void {
    selectedCell.value = null
  }

  /** Drop a staged cell once it is no longer reachable; a legal choice survives. */
  function pruneInvalid(): void {
    if (selectedCell.value && !reachable.value.has(gridCellKey(selectedCell.value.x, selectedCell.value.y))) {
      selectedCell.value = null
    }
  }

  // A new turn (or game) clears the staged cell; a spent budget / moved token
  // prunes it. Mirrors the panel's resetDock / resetSelections behavior.
  watch(() => combat.value?.current_actor_id, () => reset())
  watch(reachable, () => pruneInvalid())

  return reactive({
    selectedCell, board, isGrid, cellSize, canMove, reachable, moveCost,
    dockCost, moveRangeText, moveHint, targetCellLabel,
    cellLabel, chooseCell, stageMove, moveSummary, reset, pruneInvalid,
  })
}

export type GridCombat = ReturnType<typeof useGridCombat>