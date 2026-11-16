# Remote Ruleset Event Bridge

This describes the current host adapter boundary, not a new stable child SDK or
an implementation of the proposed ADR-0014 in its entirety.

## Application And Replay

`apply_event_batch` requires a non-empty, whitespace-free-at-the-edges string
`batch_id` (at most 200 characters), the server game/run identity, and the saved
runtime binding. A successful response may hand back `character_sheets` only, or
`state` plus an append-only `event_ledger`, optionally with sheets. The host
validates the entire handoff before changing state, ledger or any sheet.

A state/ledger handoff for a new batch must preserve the child history exactly
and append exactly one row with that batch ID. Returning the same ledger with a
new sheet/state post-image is rejected. An unsuccessful, unapplied or replayed
child response cannot carry a new bridge write. Sheet-only handoffs receive a
host ledger row even though there is no child state/ledger handoff.

Every accepted bridge application stores a host-owned receipt under the reserved
row key `_host_remote_apply_v1` inside the existing
`modules.ruleset_runtime.event_ledger`. For a state/ledger handoff this annotates
the newly appended child row; for sheet-only it is a host-only row. Receipt
contents are the runtime ID/version, server game/run scope, SHA-256 of canonical
finite JSON for the entire input batch, the non-bridge response, and `child_row`
to distinguish these two cases. No new module slot or module/save schema version
is introduced: event rows already have opaque ruleset-owned contents; this is an
explicit host-owned namespace at the remote event-ledger boundary only.

Before sending an instance DTO to the child for **any operation**, the adapter
omits host-only rows and strips receipt annotations from child rows. Children
must not return the reserved key. Child prefix validation uses this projection,
while the host retains its original interleaved history and receipts. Thus a
child does not need to understand, copy or fabricate host metadata.

Before RPC/cache access, an already-applied batch is checked against the saved
receipt. Identical payload and runtime/scope identity return the stored public
result with `applied: false, replayed: true`, without writing any state/sheet or
calling the child. Different payload or identity is rejected. A pre-existing
child event with that ID but no host receipt is rejected: the host cannot infer
its original batch payload. Historical rows otherwise remain valid prefixes for
new batches; they are not automatically migrated or assigned guessed receipts.

The application ledger is bounded at 4096 rows, with no receipt eviction. It is
not the separate, evicting RPC response cache. Reconstructing the adapter or
losing/evicting that cache does not remove the save's replay fence. Reaching the
ledger bound rejects new writes instead of forgetting old identities.

## Transactions, Save And Rollback

The receipt, child state and sheets are committed under the existing gameplay
transaction/locking boundary. They persist through the normal GameInstance
codec and GameRegistry save/load. Transaction rollback restores the existing
ledger and player before-images together, which also removes any new receipt.

The RPC response cache may outlive a rolled-back application. Its apply request
identity therefore also seals the projected aggregate pre-image digest. A retry
at the identical base may reuse that response; reusing that batch's cached
post-image at a different base is explicitly rejected. No child call or partial
host write occurs on this rejection. Other RPC identities are unchanged. Old
apply RPC receipts without this base seal are rejected rather than silently
upgraded. Host receipt lookup still precedes RPC lookup, so ordinary successful
replays do not depend on matching the old pre-image.

Round rewind already truncates the event ledger at its saved length/last-ID
marker, and restores character before-images through the round owner. Host
receipts follow that same truncation; they are not an independent monotonic
history across an intentional rollback. The existing ambiguous empty-ledger
marker behavior remains: it retains the ledger and returns `false` rather than
clearing possibly unrelated history. Retained receipts continue to prevent old
post-images from being reapplied, even if the round owner restores sheets.

The host does not infer numeric versions from opaque child state, implement
child mechanics, or repair a missing/damaged application ledger from RPC
responses. Removing the authoritative save/ledger, running an older writer
which ignores this boundary, or independently mutating child-owned external
state is outside this fence's guarantees. Approved native plugins remain native
code, not an OS sandbox.
