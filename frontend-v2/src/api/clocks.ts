import { submitRulesetIntent } from '@/api/rulesets'

// 命刻由 GM 手动维护：作为规则意图提交，由当前规则运行时校验身份并修改剧情追踪器。
// 只有声明了命刻编辑（registry 的 clockControls）的规则才会出现这些控件。

/** GM 新增命刻。 */
export function createClock(gameKey: string, title: string, segments: number) {
  return submitRulesetIntent(gameKey, { type: 'clock.create', title, segments })
}

/** GM 修改命刻标题。 */
export function renameClock(gameKey: string, clockId: string, title: string) {
  return submitRulesetIntent(gameKey, { type: 'clock.rename', clock_id: clockId, title })
}

/** GM 删除命刻。 */
export function deleteClock(gameKey: string, clockId: string) {
  return submitRulesetIntent(gameKey, { type: 'clock.delete', clock_id: clockId })
}

/** GM 手动推进/回退命刻（服务端按 ±12 与 0..总格钳制）。 */
export function advanceClock(gameKey: string, clockId: string, delta: number) {
  return submitRulesetIntent(gameKey, { type: 'clock.advance', clock_id: clockId, delta })
}
