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
