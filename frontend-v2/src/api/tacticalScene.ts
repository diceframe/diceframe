import { api } from '@/api/client'

// DTOs mirror the backend contract by hand:
// - src/tactical_scene/contract.py (typed documents)
// - src/tactical_scene/projection.py (seat whitelist)
// - src/webui/services/tactical_scene_read.py (read responses)
// - src/webui/services/tactical_scene_ops.py (SSE broadcast)
// Frozen contract: docs/internal/FVTT_COMPAT_WORKPLAN.md §6.5 / §6.6.

export interface TacticalSceneAssetRef {
  asset_id: string
  digest: string
  source_path: string
  mime: string
}

export interface TacticalSceneContentRef {
  source_kind: string
  source_id: string
  kind: string
  id: string
  digest: string
}

export interface TacticalScenePoint {
  x: number
  y: number
}

export interface TacticalSceneInitialView {
  x: number
  y: number
  zoom: number
}

export interface TacticalSceneDimensions {
  width: number
  height: number
  padding: number
  offset_x: number
  offset_y: number
  initial_view: TacticalSceneInitialView
}

export const TACTICAL_SCENE_GRID_TYPES = ['square', 'gridless', 'hex'] as const

export interface TacticalSceneGrid {
  // Unknown grid types are preserved by the backend and reported as degraded;
  // renderers must not guess mechanics from them.
  type: string
  size: number
  scale: number
  units: string
  color: string
  alpha: number
  distance: number
}

export interface TacticalSceneFog {
  exploration: boolean
  reset_policy: string
}

export interface TacticalSceneEnvironment {
  darkness: number
  global_illumination: boolean
  fog: TacticalSceneFog
  weather: string
}

export interface TacticalSceneTokenPosition {
  x: number
  y: number
  elevation: number
  level: number
}

export interface TacticalSceneTokenSize {
  width: number
  height: number
}

export interface TacticalSceneTokenDocument {
  schema_version: number
  kind: 'token'
  canonical_id: string
  actor_ref: TacticalSceneContentRef | null
  position: TacticalSceneTokenPosition
  size: TacticalSceneTokenSize
  rotation: number
  texture: TacticalSceneAssetRef | null
  ring: Record<string, unknown>
  alpha: number
  display: Record<string, unknown>
  disposition: number
  hidden: boolean
  locked: boolean
  sight: Record<string, unknown>
  detection_modes: unknown[]
  light: Record<string, unknown>
  bars: Record<string, unknown>
  delta: Record<string, unknown>
  external_ids: Record<string, string>
  flags: Record<string, Record<string, unknown>>
  extensions: Record<string, unknown>
  provenance: Record<string, unknown>
}

export interface TacticalSceneWallEndpoints {
  x0: number
  y0: number
  x1: number
  y1: number
}

export interface TacticalSceneWallRestrictions {
  move: number
  sight: number
  sound: number
  light: number
}

export interface TacticalSceneWallDocument {
  schema_version: number
  kind: 'wall'
  canonical_id: string
  endpoints: TacticalSceneWallEndpoints
  direction: number
  door: number
  door_state: number
  restrictions: TacticalSceneWallRestrictions
  external_ids: Record<string, string>
  flags: Record<string, Record<string, unknown>>
  extensions: Record<string, unknown>
  provenance: Record<string, unknown>
}

export interface TacticalSceneLightRadius {
  dim: number
  bright: number
}

export interface TacticalSceneLightDocument {
  schema_version: number
  kind: 'light'
  canonical_id: string
  position: TacticalScenePoint
  rotation: number
  elevation: number
  radius: TacticalSceneLightRadius
  color: string
  intensity: number
  angle: number
  hidden: boolean
  provides_vision: boolean
  blocked_by_walls: boolean
  external_ids: Record<string, string>
  flags: Record<string, Record<string, unknown>>
  extensions: Record<string, unknown>
  provenance: Record<string, unknown>
}

export interface TacticalSceneNoteJournalRef {
  entry_id: string | null
  page_id: string | null
}

export interface TacticalSceneNoteDocument {
  schema_version: number
  kind: 'note'
  canonical_id: string
  position: TacticalScenePoint
  journal: TacticalSceneNoteJournalRef
  text: string
  text_color: string
  font_size: number
  icon_size: number
  texture: TacticalSceneAssetRef | null
  elevation: number
  visible_globally: boolean
  external_ids: Record<string, string>
  flags: Record<string, Record<string, unknown>>
  extensions: Record<string, unknown>
  provenance: Record<string, unknown>
}

// tiles / sounds / drawings / templates / regions keep only the shared
// envelope; their type-specific fields stay opaque to generic code.
export interface TacticalSceneOpaquePlaceable {
  canonical_id: string
  external_ids: Record<string, string>
  flags: Record<string, Record<string, unknown>>
  extensions: Record<string, unknown>
  provenance: Record<string, unknown>
  [key: string]: unknown
}

export interface TacticalScenePlaceables {
  tokens: Record<string, TacticalSceneTokenDocument>
  tiles: Record<string, TacticalSceneOpaquePlaceable>
  walls: Record<string, TacticalSceneWallDocument>
  lights: Record<string, TacticalSceneLightDocument>
  sounds: Record<string, TacticalSceneOpaquePlaceable>
  notes: Record<string, TacticalSceneNoteDocument>
  drawings: Record<string, TacticalSceneOpaquePlaceable>
  templates: Record<string, TacticalSceneOpaquePlaceable>
  regions: Record<string, TacticalSceneOpaquePlaceable>
}

// Top-level fields a seat may receive (TACTICAL_SCENE_PLAYER_FIELDS). The GM
// document extends this with the private fields below.
export interface TacticalSceneSummary {
  schema_version: number
  kind: 'scene'
  canonical_id: string
  display_name: string
  navigation_name: string
  sort: number
  background: TacticalSceneAssetRef | null
  foreground: TacticalSceneAssetRef | null
  thumbnail: TacticalSceneAssetRef | null
  dimensions: TacticalSceneDimensions
  grid: TacticalSceneGrid
  environment: TacticalSceneEnvironment
  levels: Record<string, unknown>[]
  revision: number
}

export interface TacticalSceneDocument extends TacticalSceneSummary {
  source_ref: TacticalSceneContentRef | null
  external_ids: Record<string, string>
  ownership: Record<string, number>
  placeables: TacticalScenePlaceables
  flags: Record<string, Record<string, unknown>>
  extensions: Record<string, unknown>
  provenance: Record<string, unknown>
}

// Seat placeable projections: hidden tokens/lights are removed server-side and
// actor_ref is null unless the viewer owns the actor or the disposition is
// public. The frontend renders these as-is and never re-filters.
export interface TacticalSceneTokenProjection {
  canonical_id: string
  actor_ref: TacticalSceneContentRef | null
  position: TacticalSceneTokenPosition
  size: TacticalSceneTokenSize
  rotation: number
  texture: TacticalSceneAssetRef | null
  alpha: number
  display: Record<string, unknown>
  disposition: number
  bars: Record<string, unknown>
  locked: boolean
}

export interface TacticalSceneWallProjection {
  canonical_id: string
  endpoints: TacticalSceneWallEndpoints
  direction: number
  door: number
  door_state: number
  restrictions: TacticalSceneWallRestrictions
}

export interface TacticalSceneLightProjection {
  canonical_id: string
  position: TacticalScenePoint
  rotation: number
  elevation: number
  radius: TacticalSceneLightRadius
  color: string
  intensity: number
  angle: number
}

export interface TacticalSceneSeatProjection extends TacticalSceneSummary {
  placeables: {
    tokens: Record<string, TacticalSceneTokenProjection>
    walls: Record<string, TacticalSceneWallProjection>
    lights: Record<string, TacticalSceneLightProjection>
  }
}

export type TacticalSceneProjection = TacticalSceneDocument | TacticalSceneSeatProjection

export interface TacticalSceneReadResponse {
  ok: true
  scene_id: string
  scene: TacticalSceneProjection
  active_scene_ref: string | null
  revision: number
}

export interface TacticalSceneListResponse {
  ok: true
  scenes: TacticalSceneProjection[]
  active_scene_ref: string | null
  revision: number
}

export interface TacticalSceneErrorPayload {
  ok: false
  error_code: string
  error: string
}

// Codes actually declared by the tactical scene read/write services and the
// domain contract. User-facing copy lives in i18n `apiErrors.<code>` and is
// rendered through errorMessage() from api/client.ts.
export const TACTICAL_SCENE_ERROR_CODES = [
  'tactical_scene_not_found',
  'tactical_scene_placeable_not_found',
  'tactical_scene_forbidden',
  'tactical_scene_invalid_payload',
  'tactical_scene_unsupported_schema',
  'tactical_scene_stale_revision',
  'rewrite_in_progress',
  'stale_run',
  'save_failed',
] as const

export type TacticalSceneErrorCode = (typeof TACTICAL_SCENE_ERROR_CODES)[number]

export type TacticalSceneOperationKind =
  | 'tactical_scene_create'
  | 'tactical_scene_update'
  | 'tactical_scene_delete'
  | 'tactical_scene_set_active'
  | 'tactical_scene_placeable_create'
  | 'tactical_scene_placeable_update'
  | 'tactical_scene_placeable_delete'

// Broadcast after a committed write; projection is null when the scene was
// deleted and clients must re-read. The seat broadcast carries no owned actor
// refs (fail-closed), so its actor_ref stays null.
export interface TacticalSceneUpdatedEvent {
  event_type: 'tactical_scene_updated'
  run_id: string
  scene_id: string
  revision: number
  visibility: 'gm' | 'seat'
  source_operation: TacticalSceneOperationKind
  projection: TacticalSceneProjection | null
}

// The SSE pipeline delivers every game event as a default `message` whose data
// is the raw broadcast dict; tactical scene writes are identified by
// `event_type`, not the generic `type` field used by narration events.
export function isTacticalSceneUpdatedEvent(payload: unknown): payload is TacticalSceneUpdatedEvent {
  if (!payload || typeof payload !== 'object') return false
  const event = payload as Record<string, unknown>
  return event.event_type === 'tactical_scene_updated'
    && typeof event.scene_id === 'string'
    && typeof event.revision === 'number'
    && (event.visibility === 'gm' || event.visibility === 'seat')
    && (event.projection === null || typeof event.projection === 'object')
}

export function fetchTacticalScenes(gameKey: string): Promise<TacticalSceneListResponse> {
  return api<TacticalSceneListResponse>(`/games/${encodeURIComponent(gameKey)}/tactical-scenes`)
}

export function fetchTacticalScene(gameKey: string, sceneId: string): Promise<TacticalSceneReadResponse> {
  return api<TacticalSceneReadResponse>(
    `/games/${encodeURIComponent(gameKey)}/tactical-scenes/${encodeURIComponent(sceneId)}`,
  )
}

export function fetchActiveTacticalScene(gameKey: string): Promise<TacticalSceneReadResponse> {
  return api<TacticalSceneReadResponse>(`/games/${encodeURIComponent(gameKey)}/tactical-scene`)
}

// ---- GM write operations (scene-03) ---------------------------------------
// DTOs mirror the backend by hand:
// - src/webui/routes/tactical_scene.py (write routes, expected_revision)
// - src/webui/services/tactical_scene_ops.py (write response, GM projection)
// - src/tactical_scene/ops.py (op kinds, slot revision summary)
// Frozen contract: docs/internal/FVTT_COMPAT_WORKPLAN.md §6.6 / §6.8.

export type TacticalSceneCollection = keyof TacticalScenePlaceables

export type TacticalScenePlaceableDocument =
  | TacticalSceneTokenDocument
  | TacticalSceneWallDocument
  | TacticalSceneLightDocument
  | TacticalSceneNoteDocument
  | TacticalSceneOpaquePlaceable

// `revision` is the committed slot revision, not the scene document revision.
// Every write must carry the revision observed by the last read or write as
// expected_revision; a mismatch is rejected as tactical_scene_stale_revision.
// `scene` is the full GM projection of the changed scene, null after a delete.
export interface TacticalSceneWriteResponse {
  ok: true
  operation: TacticalSceneOperationKind
  scene_id: string
  revision: number
  active_scene_ref: string | null
  scene: TacticalSceneDocument | null
}

export function createTacticalScene(
  gameKey: string,
  scene: TacticalSceneDocument,
  expectedRevision: number,
): Promise<TacticalSceneWriteResponse> {
  return api<TacticalSceneWriteResponse>(`/games/${encodeURIComponent(gameKey)}/tactical-scenes`, {
    method: 'POST',
    body: JSON.stringify({ scene, expected_revision: expectedRevision }),
  })
}

export function updateTacticalScene(
  gameKey: string,
  sceneId: string,
  scene: TacticalSceneDocument,
  expectedRevision: number,
): Promise<TacticalSceneWriteResponse> {
  return api<TacticalSceneWriteResponse>(
    `/games/${encodeURIComponent(gameKey)}/tactical-scenes/${encodeURIComponent(sceneId)}`,
    { method: 'PATCH', body: JSON.stringify({ scene, expected_revision: expectedRevision }) },
  )
}

export function deleteTacticalScene(
  gameKey: string,
  sceneId: string,
  expectedRevision: number,
): Promise<TacticalSceneWriteResponse> {
  return api<TacticalSceneWriteResponse>(
    `/games/${encodeURIComponent(gameKey)}/tactical-scenes/${encodeURIComponent(sceneId)}`,
    { method: 'DELETE', body: JSON.stringify({ expected_revision: expectedRevision }) },
  )
}

export function activateTacticalScene(
  gameKey: string,
  sceneId: string,
  expectedRevision: number,
): Promise<TacticalSceneWriteResponse> {
  return api<TacticalSceneWriteResponse>(
    `/games/${encodeURIComponent(gameKey)}/tactical-scenes/${encodeURIComponent(sceneId)}/activate`,
    { method: 'POST', body: JSON.stringify({ expected_revision: expectedRevision }) },
  )
}

export function createTacticalScenePlaceable(
  gameKey: string,
  sceneId: string,
  collection: TacticalSceneCollection,
  placeable: TacticalScenePlaceableDocument,
  expectedRevision: number,
): Promise<TacticalSceneWriteResponse> {
  return api<TacticalSceneWriteResponse>(
    `/games/${encodeURIComponent(gameKey)}/tactical-scenes/${encodeURIComponent(sceneId)}/placeables/${collection}`,
    { method: 'POST', body: JSON.stringify({ placeable, expected_revision: expectedRevision }) },
  )
}

export function updateTacticalScenePlaceable(
  gameKey: string,
  sceneId: string,
  collection: TacticalSceneCollection,
  placeableId: string,
  placeable: TacticalScenePlaceableDocument,
  expectedRevision: number,
): Promise<TacticalSceneWriteResponse> {
  return api<TacticalSceneWriteResponse>(
    `/games/${encodeURIComponent(gameKey)}/tactical-scenes/${encodeURIComponent(sceneId)}/placeables/${collection}/${encodeURIComponent(placeableId)}`,
    { method: 'PATCH', body: JSON.stringify({ placeable, expected_revision: expectedRevision }) },
  )
}

export function deleteTacticalScenePlaceable(
  gameKey: string,
  sceneId: string,
  collection: TacticalSceneCollection,
  placeableId: string,
  expectedRevision: number,
): Promise<TacticalSceneWriteResponse> {
  return api<TacticalSceneWriteResponse>(
    `/games/${encodeURIComponent(gameKey)}/tactical-scenes/${encodeURIComponent(sceneId)}/placeables/${collection}/${encodeURIComponent(placeableId)}`,
    { method: 'DELETE', body: JSON.stringify({ expected_revision: expectedRevision }) },
  )
}

// ---- FVTT scene import (foundry-02) ---------------------------------------
// DTOs mirror the backend by hand:
// - src/compat/foundry/report.py (ConversionLevel / ConversionReport)
// - src/compat/foundry/import_plan.py (plan, item, policy, operation_id)
// - src/webui/services/foundry_import.py (preview / commit responses, receipt)
// Frozen contract: docs/internal/FVTT_COMPAT_WORKPLAN.md §6.6 / §6.9 / §8.2.

// The eight degradation classes, ordered from mildest to most severe.
export const FOUNDRY_CONVERSION_LEVELS = [
  'convertible',
  'saved_not_runnable',
  'degraded_display',
  'missing_external_ref',
  'missing_module_dependency',
  'unsupported_ruleset',
  'unknown_future_structure',
  'contains_script',
] as const

export type FoundryConversionLevel = (typeof FOUNDRY_CONVERSION_LEVELS)[number]

export interface FoundryConversionIssue {
  level: FoundryConversionLevel
  path: string
  detail: string
}

export interface FoundryMissingReference {
  kind: string
  path: string
  external_id: string
}

export interface FoundryConversionReport {
  source_version: string
  level: FoundryConversionLevel
  issues: FoundryConversionIssue[]
  preserved_fields: string[]
  unknown_fields: string[]
  missing_references: FoundryMissingReference[]
  warnings: string[]
  contains_script: boolean
}

export const FOUNDRY_IMPORT_ACTIONS = ['create', 'update', 'duplicate', 'skip'] as const
export type FoundryImportAction = (typeof FOUNDRY_IMPORT_ACTIONS)[number]

export type FoundryImportConflict = 'existing' | 'new'

export interface FoundryImportPlanItem {
  canonical_id: string
  source_canonical_id: string
  display_name: string
  external_ids: Record<string, string>
  conflict: FoundryImportConflict
  existing_revision: number | null
  level: FoundryConversionLevel
  contains_script: boolean
  report: FoundryConversionReport
  document: TacticalSceneDocument
}

export interface FoundryImportPlan {
  schema_version: number
  adapter: string
  source_version: string
  base_revision: number
  items: FoundryImportPlanItem[]
  scrubbed_fields: string[]
  plan_digest: string
}

export interface FoundryPreviewResponse {
  ok: true
  plan_digest: string
  plan: FoundryImportPlan
  contains_script: boolean
  level: FoundryConversionLevel
}

// One explicit policy per plan item. `update` must carry the existing_revision
// observed at preview time; the backend rejects any other value as stale.
export interface FoundryImportPolicy {
  action: FoundryImportAction
  expected_revision?: number
}

export interface FoundryReceiptItem {
  canonical_id: string
  action: FoundryImportAction
  status: string
}

export interface FoundryImportReceipt {
  operation_id: string
  plan_digest: string
  source_version: string
  revision: number
  items: FoundryReceiptItem[]
}

export interface FoundryCommitResponse {
  ok: true
  idempotent: boolean
  operation_id: string
  plan_digest: string
  // Present only on a fresh commit; an idempotent replay carries the receipt.
  revision?: number
  receipt: FoundryImportReceipt
}

// Codes declared by src/webui/services/foundry_import.py and the plan module.
export const FOUNDRY_IMPORT_ERROR_CODES = [
  'foundry_version_unsupported',
  'foundry_import_plan_invalid',
  'foundry_import_stale',
] as const

export type FoundryImportErrorCode = (typeof FOUNDRY_IMPORT_ERROR_CODES)[number]

export const FOUNDRY_IMPORT_MAX_BYTES = 16 * 1024 * 1024

export function previewFoundrySceneImport(
  gameKey: string,
  scene: unknown,
  sourceVersion: string,
): Promise<FoundryPreviewResponse> {
  return api<FoundryPreviewResponse>(`/games/${encodeURIComponent(gameKey)}/foundry/scenes/preview`, {
    method: 'POST',
    body: JSON.stringify({ scene, source_version: sourceVersion }),
  })
}

export function commitFoundrySceneImport(
  gameKey: string,
  plan: FoundryImportPlan,
  planDigest: string,
  policies: Record<string, FoundryImportPolicy>,
  operationId: string,
): Promise<FoundryCommitResponse> {
  return api<FoundryCommitResponse>(`/games/${encodeURIComponent(gameKey)}/foundry/scenes/commit`, {
    method: 'POST',
    body: JSON.stringify({ plan, plan_digest: planDigest, policies, operation_id: operationId }),
  })
}

// ---- Map background conversion (map-01) ------------------------------------
// DTOs mirror the backend by hand:
// - src/webui/services/map_scene_conversion.py (conversion response, error codes)
// - src/webui/routes/maps.py (from-map-background route)
// Frozen contract: docs/internal/FVTT_COMPAT_WORKPLAN.md §12.2 / §12.3.

// `created` is false when the background already has a scene (deterministic
// scene id); the stored map selection is never modified and the scene is not
// auto-activated.
export interface MapBackgroundConversionResponse {
  ok: true
  created: boolean
  scene_id: string
  revision: number
  active_scene_ref: string | null
  scene: TacticalSceneDocument
}

export const MAP_BACKGROUND_CONVERSION_ERROR_CODES = [
  'map_background_forbidden',
  'map_background_not_found',
  'map_background_unconfirmed',
  'map_background_unresolvable',
  'map_background_conversion_conflict',
] as const

export type MapBackgroundConversionErrorCode = (typeof MAP_BACKGROUND_CONVERSION_ERROR_CODES)[number]

// GM-only explicit conversion; confirm:true is the confirmation carrier.
// expected_revision is omitted so the backend binds the current slot revision.
export function convertMapBackgroundToTacticalScene(
  gameKey: string,
): Promise<MapBackgroundConversionResponse> {
  return api<MapBackgroundConversionResponse>(
    `/games/${encodeURIComponent(gameKey)}/tactical-scenes/from-map-background`,
    { method: 'POST', body: JSON.stringify({ confirm: true }) },
  )
}

// ---- Portable scene definition import (m3-03 / m3-04) ----------------------
// DTOs mirror the backend by hand:
// - src/webui/services/portable_scene_import.py (preview / import responses)
// - src/webui/routes/tactical_scene.py (import routes, status by error code)
// Frozen contract: docs/internal/FVTT_COMPAT_WORKPLAN.md §6.17 / §5.3.

// `noop` means the same identity with identical content already exists; the
// import then writes nothing. A different local scene with the same identity
// is refused (scene_import_conflict), never overwritten.
export type PortableSceneImportAction = 'create' | 'noop'

export interface PortableScenePreviewResponse {
  ok: true
  action: PortableSceneImportAction
  scene_id: string
  revision: number
  name_collision: boolean
  active_scene_ref?: string | null
  scene?: TacticalSceneDocument
}

// The create path returns the tactical_scene_create write response plus
// `created: true`; the noop path has no `operation` and `created: false`.
export interface PortableSceneImportResponse {
  ok: true
  created: boolean
  scene_id: string
  revision: number
  active_scene_ref: string | null
  scene: TacticalSceneDocument
  operation?: TacticalSceneOperationKind
}

export const PORTABLE_SCENE_IMPORT_ERROR_CODES = [
  'scene_import_forbidden',
  'scene_import_not_found',
  'scene_import_unconfirmed',
  'scene_import_conflict',
  'scene_import_name_conflict',
] as const

export type PortableSceneImportErrorCode = (typeof PORTABLE_SCENE_IMPORT_ERROR_CODES)[number]

// `definition` is the parsed scene JSON: either a bare TacticalSceneDocument
// or the portable `{spec: 'tactical-scene-v1', tactical_scene: …}` envelope.
export function previewPortableSceneImport(
  gameKey: string,
  definition: unknown,
): Promise<PortableScenePreviewResponse> {
  return api<PortableScenePreviewResponse>(
    `/games/${encodeURIComponent(gameKey)}/tactical-scenes/import/preview`,
    { method: 'POST', body: JSON.stringify({ definition }) },
  )
}

// GM-only explicit creation. `confirm: true` is always sent; a same-named local
// scene additionally needs confirm_name_collision. expected_revision carries
// the slot revision observed at preview so a concurrent change is refused.
export function importPortableScene(
  gameKey: string,
  definition: unknown,
  options: { confirmNameCollision?: boolean; expectedRevision?: number } = {},
): Promise<PortableSceneImportResponse> {
  return api<PortableSceneImportResponse>(
    `/games/${encodeURIComponent(gameKey)}/tactical-scenes/import`,
    {
      method: 'POST',
      body: JSON.stringify({
        definition,
        confirm: true,
        ...(options.confirmNameCollision ? { confirm_name_collision: true } : {}),
        ...(options.expectedRevision !== undefined ? { expected_revision: options.expectedRevision } : {}),
      }),
    },
  )
}