import { describe, expect, it } from 'vitest'
import type { PendingPayment } from '@/api/types'
import { isEconomyProposalActionable, isNonBlockingPersonalPurchase, nextEconomyProposal } from '@/features/play/economyPrompts'

const pending = (values: Partial<PendingPayment>): PendingPayment => ({
  id: 'proposal',
  status: 'pending',
  ...values,
})

describe('economy prompt authority', () => {
  it('only classifies a plain personal purchase as postponable', () => {
    const purchase = pending({ id: 'p', kind: 'purchase', payer_uid: 'player', recipient_uid: 'player', approval_policy: 'payer', rewards: [{ name: 'Potion' }] })
    expect(isNonBlockingPersonalPurchase(purchase)).toBe(true)
    expect(isNonBlockingPersonalPurchase({ ...purchase, effect_group_id: 'effect-1' })).toBe(false)
    expect(isNonBlockingPersonalPurchase({ ...purchase, approval_policy: 'gm' })).toBe(false)
  })
  it('routes a private charge only to its payer and a reward only to the GM', () => {
    const charge = pending({ id: 'charge', payer_uid: 'payer', approval_policy: 'payer' })
    const reward = pending({ id: 'reward', recipient_uid: 'player', approval_policy: 'gm' })

    expect(isEconomyProposalActionable(charge, 'payer', 'gm')).toBe(true)
    expect(isEconomyProposalActionable(charge, 'other', 'gm')).toBe(false)
    expect(isEconomyProposalActionable(reward, 'player', 'gm')).toBe(false)
    expect(isEconomyProposalActionable(reward, 'gm', 'gm')).toBe(true)
  })

  it('keeps a closed proposal pending but skips it until the player reopens it', () => {
    const first = pending({ id: 'first', payer_uid: 'player' })
    const second = pending({ id: 'second', payer_uid: 'player' })

    expect(nextEconomyProposal([first, second], 'player', 'gm', new Set(['first']))).toBe(second)
    expect(nextEconomyProposal([first], 'player', 'gm', new Set(['first']))).toBeUndefined()
    expect(nextEconomyProposal([first], 'player', 'gm', new Set())).toBe(first)
  })
})
