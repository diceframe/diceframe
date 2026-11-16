# Remote Event Apply Receipts

The host persists `_host_remote_apply_v1` annotations in its ruleset event ledger. These annotations belong to the host, are restored with the existing save/rewind boundary, and are stripped before a ledger is sent to a child runtime.

A successful state bridge must append exactly one batch without rewriting child-owned history. The host receipt binds the batch payload digest to the runtime identity/version and authoritative game/run scope. Replaying an applied batch returns its public result with `applied: false, replayed: true`, without applying its old state snapshot or contacting the child again.

Transport request receipts are not proof that an aggregate mutation committed. A cached transport response may be reused after a failed save only with the same aggregate pre-image. Reusing a batch ID with a different payload or runtime identity is rejected. Historical ledger rows without host receipts cannot prove the original payload and therefore fail closed on apply replay; they remain visible to the child as historical rows.

This bridge accepts only the runtime's opaque state and append-only event ledger. It does not authorize arbitrary aggregate patches or character-sheet replacement.
