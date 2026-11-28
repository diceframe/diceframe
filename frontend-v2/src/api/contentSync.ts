import { api } from '@/api/client'

// DTOs mirror the backend contract by hand:
// - src/content_modules/sync.py (preview/commit responses, sync item shape)
// - src/content_modules/plan.py (PlanItem portable dict, decisions, PLAN_STALE)
// - src/content_modules/adapters/compendium.py (foundry-compendium rides the lorebook slot)
// - src/lorebook/sync.py + src/lorebook/import_plan.py (existing match details, receipt rows)
// - src/webui/routes/content.py (owner-only, body/item limits, transport error codes)
// Frozen contract: docs/internal/FVTT_COMPAT_WORKPLAN.md §6.17 (m3-01 / m3-04).

export type ContentSyncDecision = 'update' | 'duplicate' | 'skip'
export type ContentSyncAction = 'create' | 'update' | 'unchanged' | 'unsupported'

export interface ContentSyncSource {
  kind: 'device'
  id: string
}

export interface ContentSyncItem {
  client_ref: string
  kind: string
  format: string
  document: unknown
  canonical_hint?: string
}

export interface ContentSyncWarning {
  code: string
  message: string
}

// The lorebook importer's match details; other kinds may add their own keys.
export interface ContentSyncExisting {
  canonical_id: string
  state_token: string
  server_modified: boolean | null
  name?: string
  matched_by?: string
  entries_add?: number
  entries_update?: number
  entries_remove?: number
  other_matches?: string[]
}

export interface ContentSyncPlanItem {
  client_ref: string
  kind: string
  draft_digest: string
  action: ContentSyncAction
  existing: ContentSyncExisting | null
  allowed: ContentSyncDecision[]
  reason: string
  // Only the first entry of each pushed item carries the plan's warnings.
  warnings?: ContentSyncWarning[]
}

export interface ContentSyncPreviewResponse {
  ok: true
  plan_digest: string
  items: ContentSyncPlanItem[]
  server_instance_id?: string
}

export interface ContentSyncCommitItem {
  client_ref: string
  kind?: string
  // created | updated | duplicated | skipped | unchanged | error | not_attempted
  status: string
  canonical_id?: string
  state_token?: string
  entries_removed?: number
  detached_id?: string
  error_code?: string
  error?: string
}

export interface ContentSyncCommitResponse {
  ok: boolean
  items: ContentSyncCommitItem[]
  server_instance_id?: string
}

// Codes declared by the sync pipeline, the compendium adapter and the content
// route. User-facing copy lives in i18n `apiErrors.<code>` (errorMessage()).
export const CONTENT_SYNC_ERROR_CODES = [
  'REQUEST_INVALID',
  'IMPORT_SOURCE_INVALID',
  'PLAN_DIGEST_REQUIRED',
  'PLAN_STALE',
  'DECISION_REQUIRED',
  'DECISION_NOT_ALLOWED',
  'ITEM_UNSUPPORTED',
  'KIND_NOT_SUPPORTED',
  'FORMAT_UNSUPPORTED',
  'CLIENT_REF_INVALID',
  'DUPLICATE_CLIENT_REF',
  'TOO_MANY_ENTRIES',
  'ENTRY_TOO_LARGE',
  'BODY_TOO_LARGE',
  'TOO_MANY_ITEMS',
  'OWNER_REQUIRED',
  'SOURCE_NOT_THIS_DEVICE',
  'INSTALL_ID_IN_USE',
  'SERVER_IDENTITY_UNAVAILABLE',
  'FOUNDRY_COMPENDIUM_MALFORMED',
  'FOUNDRY_COMPENDIUM_FORMAT_UNSUPPORTED',
  'FOUNDRY_COMPENDIUM_NOTHING_IMPORTABLE',
] as const

export type ContentSyncErrorCode = (typeof CONTENT_SYNC_ERROR_CODES)[number]

export const COMPENDIUM_FORMAT = 'foundry-compendium'

// The sync route caps the whole JSON body at 8 MiB (MAX_BODY_BYTES); the
// compendium parser's own 16 MiB ceiling is unreachable through this route.
export const CONTENT_IMPORT_MAX_BYTES = 8 * 1024 * 1024

export function previewContentImport(
  source: ContentSyncSource,
  items: ContentSyncItem[],
): Promise<ContentSyncPreviewResponse> {
  return api<ContentSyncPreviewResponse>('/content/import/preview', {
    method: 'POST',
    body: JSON.stringify({ source, items }),
  })
}

export function commitContentImport(
  source: ContentSyncSource,
  items: ContentSyncItem[],
  planDigest: string,
  decisions: Record<string, ContentSyncDecision>,
): Promise<ContentSyncCommitResponse> {
  return api<ContentSyncCommitResponse>('/content/import/commit', {
    method: 'POST',
    body: JSON.stringify({ source, items, plan_digest: planDigest, decisions }),
  })
}

// A compendium pack is one sync item on the lorebook slot; the backend parses
// the raw source text (JSON / JSONL / YAML) and plans one Book per Journal plus
// one Book for Items/NPCs. Scene definitions are reported, never auto-created.
export function compendiumSyncItem(clientRef: string, documentText: string): ContentSyncItem {
  return { client_ref: clientRef, kind: 'lorebook', format: COMPENDIUM_FORMAT, document: documentText }
}

const INSTALL_ID_STORAGE_KEY = 'df_content_install_id'

// The owner's web install declares a stable device source. The master-password
// owner may declare any canonical id; a paired device is bound server-side.
export function contentInstallId(): string {
  let id = localStorage.getItem(INSTALL_ID_STORAGE_KEY)
  if (!id) {
    id = `web-${crypto.randomUUID()}`
    localStorage.setItem(INSTALL_ID_STORAGE_KEY, id)
  }
  return id
}

// The client_ref prefixes every Book identity this pack produces, so it must
// stay stable when the same pack is pushed again after edits. Derived from the
// file name by default; the GM can adjust it before previewing.
export function compendiumClientRef(fileName: string): string {
  const base = fileName.replace(/\.[A-Za-z0-9]+$/, '')
  const slug = base.replace(/[^A-Za-z0-9_.:-]+/g, '-').replace(/^[-_.:]+|[-_.:]+$/g, '').slice(0, 100)
  return slug || 'pack'
}

export const CLIENT_REF_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}$/