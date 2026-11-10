/**
 * 创建游戏时的逐卡控制方式。
 *
 * 控制语义由后端 Player Control Contract 定义（human / ai / unclaimed），这里只
 * 负责把它映射成「角色」步骤上的一个紧凑选择，并保证任何进入 characters[] 的角色
 * 都带着控制方式——手动创建、角色卡选择器、导入、专业建卡都走同一条补齐逻辑，不会
 * 出现「导入的角色没有控制方式」。
 *
 * 前端不新增 AI 状态字段，也不修改 control revision：选出来的值只是创建 payload
 * 里的 `control`，由服务端写成 `players[uid].control.mode`。
 */

export const CARD_CONTROL_MODES = ['human', 'ai', 'unclaimed'] as const

export type CardControlMode = (typeof CARD_CONTROL_MODES)[number]

/**
 * 第一张默认「玩家」、其余默认「等待认领」：与既有产品默认一致，但以一个明确的值
 * 呈现，用户不需要再理解一层「跟随默认」。
 *
 * 其余角色默认 `unclaimed` 而不是 `ai`：`unclaimed` 是「席位存在但暂时无人负责」，
 * 角色不会自动行动；`ai` 是「服务端 AI 真的替这个角色行动」。开房时常见的做法是先
 * 建好几张卡等朋友认领，把默认值改成 `ai` 会直接改变游戏行为。
 */
export function defaultCardControl(index: number): CardControlMode {
  return index === 0 ? 'human' : 'unclaimed'
}

/**
 * 第一张卡就是 GM 席位：它绑定房主自己、永远拿不到分享链接，没有人能认领它。
 * 「等待认领」在这里只会建出一个连房主都提交不了行动的席位（服务端会拒绝创建），
 * 所以第一张只能是玩家或 AI 托管。
 */
export const GM_SEAT_CONTROL_MODES: readonly CardControlMode[] = ['human', 'ai']

/** 某个位置允许的控制方式：第一张（GM 席位）不能等待认领，其余三态都可以。 */
export function cardControlModesAt(index: number): readonly CardControlMode[] {
  return index === 0 ? GM_SEAT_CONTROL_MODES : CARD_CONTROL_MODES
}

/**
 * 把一个（可能来自旧状态或用户输入的）值收敛为该位置合法的控制方式。
 *
 * 删掉第一张角色后，原来第二张的「等待认领」会顶到 GM 席位上；这里按位置收敛，
 * 它会变回默认的「玩家」，而不是带着一个房主无法使用的状态进入本局。
 */
export function normalizeCardControl(value: unknown, index: number): CardControlMode {
  const text = String(value ?? '')
  return (cardControlModesAt(index) as readonly string[]).includes(text)
    ? (text as CardControlMode)
    : defaultCardControl(index)
}

/**
 * 把控制方式数组补齐／裁剪到角色数量。
 *
 * 已有选择保持不变（导入或增删角色不该重置用户已做的选择），新出现的角色拿到默认值。
 */
export function syncCardControls(
  count: number,
  current: readonly unknown[],
): CardControlMode[] {
  return Array.from(
    { length: Math.max(0, count) },
    (_, index) => normalizeCardControl(current[index], index),
  )
}

/** 创建 payload 里每张角色应带的 control 值。 */
export function cardControlPayload(
  count: number,
  current: readonly unknown[],
): CardControlMode[] {
  return syncCardControls(count, current)
}

/**
 * 读某张角色当前生效的控制方式：越界或缺失时给出该位置应有的默认值。
 *
 * 「角色步骤显示什么」与「确认页摘要什么」必须用同一个答案，否则用户会在确认页
 * 看到与刚才选择不同的值。默认规则也只有这里一份（{@link defaultCardControl}）。
 */
export function cardControlAt(current: readonly unknown[], index: number): CardControlMode {
  return normalizeCardControl(current[index], index)
}

/**
 * 三态循环按钮：返回把某张卡切到**下一个**控制方式后的新数组。
 *
 * 顺序就是契约顺序 human → ai → unclaimed → human；第一张（GM 席位）只在
 * human ↔ ai 之间切换（{@link cardControlModesAt}）。循环长度取自该位置允许的模式，
 * 以后契约变化不需要改这里的魔法数。先归一化再取下一个，因此坏数据被点到时也会先
 * 落回合法模式而不是被跳过。越界 index 不写入。
 */
export function cycleCardControl(
  current: readonly unknown[],
  index: number,
): CardControlMode[] {
  const next = syncCardControls(current.length, current)
  if (index < 0 || index >= next.length) return next
  const modes = cardControlModesAt(index)
  const position = modes.indexOf(next[index])
  next[index] = modes[(position + 1) % modes.length]
  return next
}

/**
 * 删除某一张角色时，同步删除它对应的控制方式。
 *
 * 角色与控制方式是按 index 平行保存的（`characters[i]` ↔ `cardControl[i]`）。只删
 * 角色不删控制方式，后面的角色就会**继承前一个角色的 control**：删掉 A(human) 之后
 * B 会拿到 human，而不是它原本的 ai。所以两者必须一起删。
 *
 * 越界 index 返回归一化后的原数组（不抛错、不误删）。归一化顺带把非法值收敛成合法
 * 模式，避免把坏值带进创建 payload；删掉第一张后顶上来的角色如果是「等待认领」，
 * 也会按 GM 席位收敛成「玩家」。
 */
export function removeCardControl(
  current: readonly unknown[],
  index: number,
): CardControlMode[] {
  const next = syncCardControls(current.length, current)
  if (index < 0 || index >= next.length) return next
  next.splice(index, 1)
  // 删除后各张卡的位置变了，按新位置再收敛一次（只影响顶到第一张的那张卡）。
  return syncCardControls(next.length, next)
}
