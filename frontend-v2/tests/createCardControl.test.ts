import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

import {
  CARD_CONTROL_MODES,
  cardControlAt,
  cardControlModesAt,
  cardControlPayload,
  cycleCardControl,
  defaultCardControl,
  normalizeCardControl,
  removeCardControl,
  syncCardControls,
} from '../src/features/create/cardControl'

const createView = readFileSync(
  join(process.cwd(), 'src/features/create/CreateView.vue'),
  'utf8',
)

describe('create-game per-card control', () => {
  it('Case A — speaks the same vocabulary as the backend Player Control Contract', () => {
    // human / ai / unclaimed 是后端权威的三态契约：unclaimed 表示「席位存在但暂时
    // 无人负责」，是正式产品状态而不是坏数据，创建阶段不能把它收窄掉。
    expect([...CARD_CONTROL_MODES]).toEqual(['human', 'ai', 'unclaimed'])
  })

  it('Case B — defaults the first character to human and the rest to unclaimed', () => {
    // 开房常见做法是先建好几张卡等朋友认领：默认 unclaimed 时角色不会自动行动，
    // 默认 ai 则会让服务端 AI 真的替这些角色行动——两者语义完全不同。
    expect(defaultCardControl(0)).toBe('human')
    expect(defaultCardControl(1)).toBe('unclaimed')
    expect(defaultCardControl(2)).toBe('unclaimed')
    expect(syncCardControls(3, [])).toEqual(['human', 'unclaimed', 'unclaimed'])
  })

  it('Case C — keeps the user choices and fills newly added characters', () => {
    // 导入 / 选择器新增角色时，已做的选择不能被重置。
    const chosen = ['ai', 'human']
    expect(syncCardControls(4, chosen)).toEqual(['ai', 'human', 'unclaimed', 'unclaimed'])
    // 删掉角色时数组跟着收缩。
    expect(syncCardControls(1, chosen)).toEqual(['ai'])
  })

  it('Case D — converges junk values by position, but never treats unclaimed as junk', () => {
    expect(normalizeCardControl('', 0)).toBe('human')
    expect(normalizeCardControl('script', 2)).toBe('unclaimed')
    expect(normalizeCardControl(undefined, 0)).toBe('human')
    // 三个契约值在普通席位上都原样通过。
    expect(normalizeCardControl('human', 1)).toBe('human')
    expect(normalizeCardControl('ai', 1)).toBe('ai')
    expect(normalizeCardControl('unclaimed', 1)).toBe('unclaimed')
  })

  it('Case D2 — the first card is the GM seat and can never wait for a claim', () => {
    // GM 席位绑定房主自己、拿不到分享链接：「等待认领」会让房主自己也无法行动，
    // 服务端会拒绝创建，所以第一张只允许玩家或 AI 托管。
    expect([...cardControlModesAt(0)]).toEqual(['human', 'ai'])
    expect([...cardControlModesAt(1)]).toEqual(['human', 'ai', 'unclaimed'])
    expect(normalizeCardControl('unclaimed', 0)).toBe('human')
    expect(normalizeCardControl('ai', 0)).toBe('ai')
    expect(cardControlAt(['unclaimed', 'unclaimed'], 0)).toBe('human')
    expect(cardControlPayload(2, ['unclaimed', 'unclaimed'])).toEqual(['human', 'unclaimed'])
  })

  it('Case E — produces the payload control values for all three modes', () => {
    // 每张角色都带明确的 control，创建 payload 直接使用这些值。
    expect(cardControlPayload(3, ['human', 'ai', 'unclaimed'])).toEqual([
      'human',
      'ai',
      'unclaimed',
    ])
    expect(cardControlPayload(3, ['human', 'ai', 'human'])).toEqual(['human', 'ai', 'human'])
  })
})

/**
 * Case F — 角色与控制方式按 index 平行保存，删除角色必须同步删除对应控制方式。
 * 只删角色会让后面的角色继承被删角色的 control（删 A(human) 后 B 变成 human）。
 */
describe('deleting a character keeps control aligned', () => {
  it('deleting the first character shifts the rest correctly', () => {
    // A human / B ai / C unclaimed → 删 A → B 仍是 ai、C 仍是 unclaimed
    expect(removeCardControl(['human', 'ai', 'unclaimed'], 0)).toEqual(['ai', 'unclaimed'])
  })

  it('deleting the first character never leaves an unclaimed GM seat', () => {
    // A human / B unclaimed → 删 A → B 成为 GM 席位：等待认领没人能认领，收敛成玩家；
    // 后面的席位不受影响。
    expect(removeCardControl(['human', 'unclaimed'], 0)).toEqual(['human'])
    expect(removeCardControl(['human', 'unclaimed', 'unclaimed'], 0)).toEqual(['human', 'unclaimed'])
  })

  it('deleting a middle character keeps its neighbours intact', () => {
    // A human / B ai / C unclaimed → 删 B → A 仍是 human、C 仍是 unclaimed
    expect(removeCardControl(['human', 'ai', 'unclaimed'], 1)).toEqual(['human', 'unclaimed'])
  })

  it('deleting the last character only drops the last control value', () => {
    expect(removeCardControl(['human', 'ai', 'unclaimed'], 2)).toEqual(['human', 'ai'])
  })

  it('an out-of-range index never removes anything', () => {
    expect(removeCardControl(['human', 'ai'], 5)).toEqual(['human', 'ai'])
    expect(removeCardControl(['human', 'ai'], -1)).toEqual(['human', 'ai'])
  })

  it('converges junk values while removing, so bad data cannot reach the payload', () => {
    // 'script' 先按原位置收敛成 unclaimed；删掉第一张后它顶到 GM 席位，再收敛成 human。
    expect(removeCardControl(['human', 'script', 'ai'], 0)).toEqual(['human', 'ai'])
    expect(removeCardControl(['human', 'ai', 'script'], 0)).toEqual(['ai', 'unclaimed'])
  })
})

/**
 * Case H — 单个循环按钮的唯一写入口。
 *
 * 角色卡上只有一个按钮显示当前控制方式，点一下切到下一个：循环顺序、坏数据收敛与
 * 越界保护都必须发生在这个模块内，不能让创建 payload 拿到契约之外的控制方式。
 */
describe('cycling a card control from the single button', () => {
  it('walks the contract order human → ai → unclaimed and wraps around', () => {
    expect(cycleCardControl(['human', 'human'], 1)).toEqual(['human', 'ai'])
    expect(cycleCardControl(['human', 'ai'], 1)).toEqual(['human', 'unclaimed'])
    expect(cycleCardControl(['human', 'unclaimed'], 1)).toEqual(['human', 'human'])
  })

  it('cycles the GM seat between human and ai only', () => {
    expect(cycleCardControl(['human'], 0)).toEqual(['ai'])
    expect(cycleCardControl(['ai'], 0)).toEqual(['human'])
    // 旧草稿里残留的 unclaimed 先按 GM 席位收敛成 human，再切到下一个。
    expect(cycleCardControl(['unclaimed'], 0)).toEqual(['ai'])
  })

  it('cycles only the clicked card and leaves its neighbours alone', () => {
    // 第 1 张是 unclaimed，按契约顺序切到 human；左右两张保持原值。
    expect(cycleCardControl(['human', 'unclaimed', 'ai'], 1)).toEqual(['human', 'human', 'ai'])
  })

  it('does not mutate the array it was given', () => {
    const current = ['human', 'unclaimed'] as const
    cycleCardControl(current, 0)
    expect([...current]).toEqual(['human', 'unclaimed'])
  })

  it('normalizes a junk value first, then cycles from the normalized mode', () => {
    // 'script' 在第 1 位按位置收敛成 unclaimed，再切到下一个 = human（不是跳过一格）。
    expect(cycleCardControl(['human', 'script'], 1)).toEqual(['human', 'human'])
  })

  it('an out-of-range index changes nothing', () => {
    expect(cycleCardControl(['human', 'ai'], 5)).toEqual(['human', 'ai'])
    expect(cycleCardControl(['human', 'ai'], -1)).toEqual(['human', 'ai'])
  })

  it('reads the same answer for the character step and the confirm summary', () => {
    expect(cardControlAt(['human', 'ai'], 0)).toBe('human')
    expect(cardControlAt(['human', 'ai'], 1)).toBe('ai')
    // 缺失值按位置给默认，与 defaultCardControl 同源。
    expect(cardControlAt([], 0)).toBe(defaultCardControl(0))
    expect(cardControlAt([], 2)).toBe(defaultCardControl(2))
  })
})

describe('CreateView control placement contract', () => {
  it('does not show the experimental profile template launcher in ordinary game creation', () => {
    expect(createView).not.toContain('ProfileCreateLauncher')
    expect(createView).not.toContain('profile-launcher')
  })

  it('offers a single cycling control button in the character step', () => {
    // 角色步骤：每张角色卡一个按钮显示当前控制方式，点一下切下一个；不再有需要展开的
    // 下拉，也不是铺开成整块的 radio 表单。
    expect(createView).toMatch(
      /create-character-card[\s\S]*?class="create-character-control-button"[\s\S]*?@click="cycleControl\(i\)"/,
    )
    expect(createView).toContain('cycleCardControl(cardControl.value, index)')
    // 按钮显示的是当前状态，因此必须说明点它会切换（title + aria-label）。
    expect(createView).toContain(':title="controlSwitchHint(i)"')
    expect(createView).toContain(':aria-label="controlSwitchHint(i)"')
    expect(createView).not.toContain('<select v-model="cardControl')
    expect(createView).not.toContain('<option v-for="mode in CARD_CONTROL_MODES"')
  })

  it('keeps the control button in the same action row as 编辑 / 删除', () => {
    // 布局契约：控制方式按钮与「编辑 / 删除」并排，不再单独占信息列里的一行。
    const card = createView.slice(createView.indexOf('class="create-character-card"'))
    const actions = card.slice(card.indexOf('class="actions"'), card.indexOf('</article>'))
    expect(actions).toContain('create-character-control-button')
    expect(actions).toContain('create-character-control-button"')
    // 信息列（名字 / 出身 / 技能数）里不再渲染控制方式。
    const info = card.slice(card.indexOf('<div>'), card.indexOf('class="actions"'))
    expect(info).not.toContain('create-character-control')
  })

  it('removes the matching control when a character is removed', () => {
    // 结构性锁定：removeCharacter 必须同时更新 cardControl，否则会再次错位。
    const body = createView.slice(createView.indexOf('function removeCharacter'))
    const fn = body.slice(0, body.indexOf('\n}'))
    expect(fn).toContain('characters.value.splice(idx, 1)')
    expect(fn).toContain('removeCardControl(cardControl.value, idx)')
  })

  it('Case G — the confirm step summarizes saved per-character control without a second editor', () => {
    const confirmSection = createView.slice(createView.indexOf('create-confirm-stage'))
    const summary = confirmSection.slice(0, confirmSection.indexOf('</section>'))
    // 确认页展示角色与已保存的控制方式，避免用户无法核对将要进入本局的席位状态；
    // 角色步骤的按钮仍是唯一配置入口。
    expect(summary).toContain('create-confirm-characters')
    expect(summary).toContain('controlLabel(controlOf(i))')
    expect(summary).toContain('Seat control is saved in the Characters step')
    expect(summary).not.toContain('create-confirm-controls')
    // 确认页始终只有只读摘要，不提供第二套配置入口。
    expect(summary).not.toContain('<select')
    expect(summary).not.toContain('type="radio"')
    expect(summary).not.toContain('v-model="cardControl')
  })

  it('does not expose the internal follow-default concept in the UI', () => {
    expect(createView).not.toContain('controlFollowDefault')
    expect(createView).not.toContain('unclaimedControlDefault')
  })
})
