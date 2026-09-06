import type { PendingPayment } from '@/api/types'

/** Narrow client-side mirror of the server's fail-closed postpone policy. */
export function isNonBlockingPersonalPurchase(proposal: PendingPayment): boolean {
  const payer = String(proposal.payer_uid || proposal.uid || '')
  const recipient = String(proposal.recipient_uid || payer)
  return proposal.status === 'pending'
    && proposal.kind === 'purchase'
    && proposal.approval_policy === 'payer'
    && Boolean(payer)
    && recipient === payer
    && Boolean(proposal.rewards?.length)
    && !proposal.effect_group_id
}

export function isEconomyProposalActionable(
  proposal: PendingPayment,
  actorId: string,
  gmUid: string,
): boolean {
  if (proposal.status !== 'pending') return false
  return Boolean(
    proposal.payer_uid === actorId
    || proposal.uid === actorId
    || (proposal.approval_policy === 'gm' && gmUid === actorId),
  )
}

export function nextEconomyProposal(
  proposals: PendingPayment[],
  actorId: string,
  gmUid: string,
  dismissedIds: ReadonlySet<string>,
): PendingPayment | undefined {
  return proposals.find((proposal) => {
    const id = String(proposal.id || proposal.payment_id || '')
    return id
      && !dismissedIds.has(id)
      && isEconomyProposalActionable(proposal, actorId, gmUid)
  })
}

/**
 * Build the reward-policy save payload, or null when nothing should be sent.
 *
 * The game-settings dialog shares one save button with the room password and
 * luck timeout. Submitting the reward policy unconditionally would clear the
 * game's override whenever `economy_reward_policy` was missing from the
 * loaded detail (empty mode means "clear" on the server). The request is
 * therefore only built when the GM actually touched the reward fields;
 * explicitly selecting the follow-default mode still clears the override.
 */
export function buildRewardPolicySave(
  touched: boolean,
  mode: string,
  cap: string,
): { mode: string; auto_reward_cap: number | null } | null {
  if (!touched) return null
  const trimmed = String(cap || '').trim()
  return {
    mode,
    auto_reward_cap: trimmed !== '' ? Number(trimmed) : null,
  }
}
