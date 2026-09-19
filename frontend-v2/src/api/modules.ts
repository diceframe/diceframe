import { api } from '@/api/client'

export type ModuleSummary = {
  id: string
  name: string
  version: string
  content_profile: string
  content_delivery_mode: string
  is_module: boolean
  status: string
  adventure_count: number
  content_counts: Record<string, number>
}

export type ModulesResponse = {
  ok: boolean
  modules: ModuleSummary[]
}

export const moduleApi = {
  list: () => api<ModulesResponse>('/modules'),
}
