export type ManualRollVisibility = 'party' | 'private'
export type ManualRollStatus = 'pending' | 'resolved' | 'cancelled'
export type ManualRollPurpose = 'free' | 'check' | 'contest'
export type ManualRollComparison = 'auto' | 'at_least' | 'at_most'

export interface ManualRollFace {
  sides: number
  value: number
  taken: boolean
  sign?: number
}

export interface ManualRollResult {
  formula: string
  rolls: number[]
  modifier: number
  total: number
  natural: number | null
  faces?: ManualRollFace[]
  target?: number
  comparison?: ManualRollComparison
  verdict?: 'success' | 'failure' | 'winner' | 'loss' | string
  rolled_by: string
  rolled_at: string
}

export interface ManualRollCheck {
  ability: string
  skill: string
  dc: number
}

export interface ManualRollRequest {
  id: string
  operation_id: string
  run_id: string
  round_number: number
  created_by: string
  created_at: string
  label: string
  formula: string
  /** G4：GM 指定的技能/属性检定；有它时结算由规则集负责。 */
  check?: ManualRollCheck | null
  purpose?: ManualRollPurpose
  source?: 'quick' | string
  target?: number | null
  comparison?: ManualRollComparison
  include_in_ai_context?: boolean
  visibility: ManualRollVisibility
  target_uids: string[]
  target_names: Record<string, string>
  status: ManualRollStatus
  results: Record<string, ManualRollResult>
  cancel_reason?: string
}

export interface ManualRollRequestsResponse {
  ok: true
  run_id: string
  requests: ManualRollRequest[]
}
