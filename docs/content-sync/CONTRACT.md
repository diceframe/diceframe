# Content sync contract

This is the server contract that offline clients use to push portable content into a DiceFrame server and pull it back. The mobile content library is the first such client.

This document is the reference. Keep it current with the code; the server tests consume the fixtures under `fixtures/` and the schemas under `schemas/`.

- Code: `src/webui/routes/content.py`, `src/webui/services/content_sync.py`, `src/content_modules/sync.py`, `src/content_modules/plan.py`, `src/lorebook/import_plan.py`, `src/lorebook/sync.py`, `src/webui/services/character_card_import.py`.
- Tests: `tests/test_content_sync_api.py`, `tests/test_content_sync_fixtures.py`.

## Identity

- Each client install generates **one install id** on first launch and keeps it. It must be a canonical id matching `[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}` (a UUID works). It must not change across servers.
- Every pushed item has a **client_ref**: the device-local content id. It is canonical too, and stable for the life of that local item.
- On the server, an item's identity is `(source_kind = "device", source_id = install id, external_id = client_ref)`. A card's embedded book has the identity `client_ref + ".book"`.
- Server **canonical ids** are separate from client ids. Keep a per-server map `{client_ref → canonical_id, state_token}`, keyed by the server's **`server_instance_id`**.
  - `server_instance_id` is a stable random id that survives IP and tunnel changes.
  - `GET /api/config` returns it to the owner. Every `/api/content/*` response also carries it, so a client can check it is talking to the server it mapped.
  - If the server cannot produce the id, `/api/config` returns `server_identity_error: "SERVER_IDENTITY_UNAVAILABLE"` to the owner instead, and every `/api/content/*` request gets **503 `SERVER_IDENTITY_UNAVAILABLE`**. Show the owner a message about the server's data directory, and do not sync.

## Formats

Golden documents live in `fixtures/` and minimal JSON Schemas in `schemas/`. Clients should copy them as their own golden samples.

**Character card.** A Tavern `chara_card_v3` envelope (`schemas/chara-card-v3.schema.json`).

- The DiceFrame card body (schema 2, `schemas/card-body.schema.json`) is the authority. It lives in `data.extensions.diceframe`. Only `schema_version` (2) and `character_name` are required; every other field has a server default.
- Embedded lore goes in the standard `data.character_book` field.
- Server-only fields in the body are ignored on push: `id`, `card_id`, `provenance`, `source_plugin`, `plugin_content_id`, `raw_sillytavern`, `ruleset_*`.
- Only `builtin` portraits (`{"kind": "builtin", "id": "..."}`) are portable. `upload` / `generated` portraits refer to server-local assets; they are dropped with the warning `PORTRAIT_NOT_PORTABLE`.
- Rules-aware cards are `unsupported` for push in v1 (`RULESET_CARD_UNSUPPORTED`). These are cards with `rule_binding` / `ruleset_character`, such as D&D 2024. After a pull, keep them read-only on the device.
- Fixtures: `card-freeform`, `card-coc`, `card-dnd2024` (unsupported) and `card-with-book`.

**Lorebook.** `lorebook_v3` (`schemas/lorebook-v3.schema.json`, fixture `lorebook.lorebook_v3.json`).

- Every entry needs a stable `id`, plus `content` and `keys`. Entries are mirrored by `id`.
- Entries created on the server (not pushed) are exported under their canonical id. The first time such an entry is pushed back, it is re-created under an id derived from that one, with the content unchanged. This one-time id churn happens once per entry, and only for server-created entries.

World documents are not supported yet.

## Auth

**Owner only.** The request must carry the master password or a paired-device token (`Authorization: Bearer …`).

- In practice the access-control middleware answers before the route:
  - anonymous, wrong credentials and share/seat credentials get **401**;
  - bot and plugin tokens get **403**.
- The route's own **403 `OWNER_REQUIRED`** is a defensive fallback for any non-owner caller that gets past the middleware.
- Without an access password, the server has no authentication boundary, the same as for the rest of the owner API.

**Paired-device identity rule.** A request authenticated by a paired-device token may declare only that device's install id. Otherwise it gets **403 `SOURCE_NOT_THIS_DEVICE`**.

- **Registration at pairing.** Send `install_id` with `POST /api/pairing/claim`, next to `code` and `label`. A malformed id gets 400 `INSTALL_ID_INVALID`, and the pairing code is not consumed.
- **First-use binding.** A device paired without an install id is bound on first use, to the `source.id` of its first declared request.
- **One install id, one paired device.**
  - **Re-pairing takes over.** A valid pairing code is the owner's authorisation. If the claimed `install_id` is held by an older pairing, that is the same app install pairing again (for example after its token was lost). The older device is **revoked** and the new pairing takes the binding: the claim returns 200 with `replaced_device_id`, and the server logs the takeover. The old token stops working (401).
  - **A push never takes over.** A first-use binding (through a content request, without pairing) of an id that another paired device holds gets 403 `INSTALL_ID_IN_USE`.
- **Clearing a binding.**
  - The owner can clear a binding with `DELETE /api/devices/{device_id}/install-id`; the device binds again on its next push.
  - Revoking a device discards its binding.
  - `GET /api/devices` shows each device's `install_id`.

**Master password.** A master-password owner may declare any device. A phone may log in with the password instead of pairing.

**Confirm header.** Every `/api/content/*` request needs **`X-TRPG-Confirm: true`**; without it the server returns 403. Preview counts as a write because it can bind a device's install id. The header is the CSRF barrier.

**Rate limit.** Requests count against the write rate limit; previews and status checks count too. Over the limit, the server returns 429 with `retry_after`.

## Limits

Each limit returns **413** with an error code:

| Code | Limit | When it is checked |
|---|---|---|
| `BODY_TOO_LARGE` | body larger than 8 MB | while the body streams in, before parsing |
| `TOO_MANY_ITEMS` | more than 50 items (500 for `/status`) | before planning |
| `TOO_MANY_ENTRIES` | more than 2000 entries in one book, standalone or `character_book` | on the adapter's parsed draft, before planning |
| `ENTRY_TOO_LARGE` | an entry larger than 32 KB | on the adapter's parsed draft, before planning |

A malformed or pathologically nested body gets 400 `REQUEST_INVALID`.

## `POST /api/content/import/preview`

Request:

```json
{
  "source": {"kind": "device", "id": "<install id>"},
  "items": [
    {"client_ref": "card-7f3a", "kind": "character_template", "format": "chara_card_v3",
     "document": {"spec": "chara_card_v3", "...": "..."}, "canonical_hint": "<optional server card id>"},
    {"client_ref": "book-91c2", "kind": "lorebook", "format": "lorebook_v3",
     "document": {"spec": "lorebook_v3", "...": "..."}, "canonical_hint": "<optional server book id>"}
  ]
}
```

- `kind` is a content-kind registry id. Kinds without an importer get `KIND_NOT_SUPPORTED`.
- `format` is optional; when given, it must match the kind.
- **`canonical_hint`** is optional, for cards and lorebooks. It names a server object the client pulled earlier and that does not follow this install yet.
  - The hint is consulted only when nothing follows the declared identity; it never overrides an identity match.
  - A hinted object that belongs to someone else is offered only `duplicate` / `skip`, and `reason` says why:
    - `PLUGIN_CARD` / `PLUGIN_BOOK`: plugin content;
    - `TRACKED_BY_OTHER_SOURCE` / `DETACHED_FROM_OTHER_SOURCE`: another source's provenance, whatever its link;
    - `RULESET_CARD`: a rules-aware server card.
  - An `update` on a free hinted object adopts it: from then on it follows this install. A `duplicate` of a hinted object leaves that object untouched.
  - **Hinted books report their bindings.** For a lorebook matched by hint, `existing.bindings` lists where the server book is bound (`[{scope_kind, scope_id}]`, e.g. `game` or `character` scopes; `[]` when unbound). An `update` mirrors the push into that book, so it changes what those games and characters see; warn the user before sending it.

Response (200):

```json
{
  "ok": true,
  "server_instance_id": "srv-...",
  "plan_digest": "sha256:...",
  "items": [
    {"client_ref": "card-7f3a", "kind": "character_template", "action": "update",
     "draft_digest": "sha256:...", "allowed": ["update", "duplicate", "skip"], "reason": "",
     "existing": {"canonical_id": "card_...", "state_token": "sha256:...", "server_modified": false,
                  "name": "Mira", "matched_by": "identity"},
     "warnings": [{"code": "PORTRAIT_NOT_PORTABLE", "message": "..."}]},
    {"client_ref": "card-7f3a.book", "kind": "lorebook", "action": "create", "existing": null, "allowed": []},
    {"client_ref": "book-91c2", "kind": "lorebook", "action": "update",
     "existing": {"canonical_id": "import:device:...", "state_token": "12", "server_modified": null,
                  "matched_by": "identity", "entries_add": 1, "entries_update": 3, "entries_remove": 1,
                  "other_matches": []}}
  ]
}
```

| Action | Meaning |
|---|---|
| `create` | No server copy yet. Nothing to decide. |
| `update` | A server copy exists and differs. The client must choose an answer. |
| `unchanged` | The push equals the last import, or the server's own export of it, and the server copy has not been edited since. |
| `unsupported` | Cannot be imported. `reason` explains why. |

- `server_modified` is `true` when the server copy was edited after its last import. It is `null` when that is unknown, i.e. for hinted objects and for books imported before plans existed.
- `allowed` lists the answers the client may send. It is empty for `create` and `unsupported`.

## `POST /api/content/import/commit`

Request: the same `source` and `items` (the documents are sent again), plus:

```json
{"plan_digest": "<from preview>", "decisions": {"card-7f3a": "update", "book-91c2": "duplicate"}}
```

Every item that has `existing` needs a decision:

| Decision | Effect |
|---|---|
| `update` | Overwrite the server copy. Cards keep server-side fields. Lorebooks are a full mirror: metadata, settings and entries, including deletions and per-entry `enabled`. Bindings and the book's own `enabled` are kept. On an `unchanged` item, `update` is accepted and writes nothing. |
| `duplicate` | Keep both. A new server copy follows this install from now on. The old copy keeps its content and provenance; it is detached if it followed this install. |
| `skip` | Write nothing. The existing canonical id is returned. |

Errors:

- A missing or disallowed decision gets 400 `DECISION_REQUIRED` or `DECISION_NOT_ALLOWED`. An unsupported item gets its own reason code. Every decision is validated before anything is written.
- The server re-plans from the documents and its current state. If the plan differs from `plan_digest`, it returns **409 `PLAN_STALE`** with a fresh `preview` embedded.
- Retrying a successful commit never writes twice: the retry gets `PLAN_STALE`, and its fresh preview shows `unchanged`.

Response (200):

```json
{"ok": true, "server_instance_id": "srv-...", "items": [
  {"client_ref": "card-7f3a.book", "kind": "lorebook", "status": "created",
   "canonical_id": "import:device:...", "state_token": "3", "entries_removed": 0},
  {"client_ref": "card-7f3a", "kind": "character_template", "status": "updated",
   "canonical_id": "card_...", "state_token": "sha256:..."},
  {"client_ref": "book-91c2", "kind": "lorebook", "status": "duplicated",
   "canonical_id": "import:device:...:1a2b3c4d", "detached_id": "import:device:...", "state_token": "1"}
]}
```

- `status` is one of `created | updated | duplicated | skipped | unchanged`.
- Each item is atomic: a card with its embedded book (the book is written first and linked from the card's provenance), and a book with its entries.
- A failed item shows `status: "error"` with an `error_code`, and the response has `ok: false`.
  - An expected refusal, such as an identity conflict, affects only that item; later items still run, and the response is 409.
  - An unexpected failure is `IMPORT_FAILED`: later items are `not_attempted`, and the response is 500.
  - In both cases the rows show exactly what was written.

## `POST /api/content/status` (change detection)

Request: `{"items": [{"kind": "character_template", "canonical_id": "card_..."}, ...]}`, at most 500 items.

Response:

```json
{"ok": true, "server_instance_id": "srv-...", "items": [
  {"kind": "character_template", "canonical_id": "card_...", "exists": true,
   "state_token": "sha256:...",
   "provenance": {"source_kind": "device", "source_id": "<install id>", "external_id": "card-7f3a",
                  "link": "tracked", "...": "..."}},
  {"kind": "lorebook", "canonical_id": "gone", "exists": false, "state_token": "", "provenance": null}
]}
```

- Compare `state_token` with your mapped token to learn whether the server copy changed since your last push or pull, without exporting it.
- `exists: false` means the server copy was deleted.
- `provenance` is `null` for objects that never came from a source.
- To discover server objects you have not mapped yet, use the owner lists `GET /api/character-cards` and `GET /api/lorebooks` (unchanged legacy shapes), then ask `/status` or `/export` for the ids you care about.

## `POST /api/content/export` (pull)

Request: `{"items": [{"kind": "character_template", "canonical_id": "card_..."}, ...]}`, at most 50 items.

Response:

```json
{"ok": true, "server_instance_id": "srv-...", "items": [{
  "kind": "character_template", "format": "chara_card_v3", "document": {"spec": "chara_card_v3", "...": "..."},
  "warnings": [],
  "origin": {"server_instance_id": "srv-...", "canonical_id": "card_...", "state_token": "sha256:...",
             "provenance": {"source_kind": "device", "source_id": "<install id>", "external_id": "card-7f3a",
                            "link": "tracked", "...": "..."}}
}]}
```

- Cards include their linked book as `character_book`.
- Exported entries carry the ids they were pushed with. Importing an export unchanged gives `unchanged`.
- If `origin.provenance` names this install, map `origin.canonical_id` back to `provenance.external_id`. Otherwise keep the pulled item as a new local item and pass `canonical_hint` when pushing it.
- An unknown id gets 404 `NOT_FOUND`.

## Conflicts

There is no 3-way merge.

- **Push:** when `server_modified` is true, the user picks overwrite (`update`), keep both (`duplicate`) or `skip`.
- **Pull** onto a locally modified item: keep both locally.

## Other error codes

Codes not covered above that a client can receive from `/api/content/*`:

| Code | When |
|---|---|
| `IMPORT_SOURCE_INVALID` | the item's `source` / `external_id` is missing or not canonical (a card push must declare both), or `canonical_hint` is not a string |
| `CARD_V3_INVALID` | a card item's `document` is not a `chara_card_v3` envelope: wrong `spec`, no `data` object, or no `data.extensions.diceframe` body |
| `CARD_IDENTITY_CONFLICT` | defensive: another card already tracks this identity when the item is written. Returned per item with 409; re-run preview |
| `LOREBOOK_IDENTITY_CONFLICT` | the same, for a lorebook; per item with 409 |
| `BOOK_NOT_IMPORTED` | **warning**, not an error: the card's `character_book` was skipped because lorebooks are disabled on this server |
