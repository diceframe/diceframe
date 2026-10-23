# Universal Adventures

Adventure graph v2 is a reusable story layer for graph-capable CoC, custom and
D&D rules. The built-in legacy adapter and D&D runtime support it. This does not
convert one game's combat, SAN, spells, items or rewards into another's mechanics.

## Author and play

New Adventures and prose/external imports default to **Universal story**. Open
**Adventures**, create or import a package, edit the graph, check visibility and
references, validate and publish. The library shows compatible rules; fixed-world
requirements still apply. Specific-rule packages retain their existing requirements.

An adopted free-game plan can become reusable content through **Play → GM
workspace → Scene → Story plan → Create reusable Adventure**. Choose a fresh
identity and prepare the draft. Edit and explicitly review/publish it in the
existing script dialog. Preparation does not publish, bind or mutate the game.
Editing invalidates validation and requires review again.

The copy starts from the authored beginning; successor alternatives become
exclusive choices. Descriptions, objectives, exit conditions and hints are kept.
Every copied node/objective starts GM-only. Before making a description public,
remove its exit conditions and GM hints. Live NPC references are omitted with a
warning; create or map reusable resources explicitly. Chat, credentials, character
values, world state, progress and source run identifiers are excluded.

## Package contract

Use the existing manifest with this explicit requirement:

```json
{
  "format": "diceframe:adventure-graph-v2",
  "required_runtime": { "id": "core:adventure", "minimum_version": 1 }
}
```

This is an Adventure profile, not a character ruleset or plugin ruleset dependency.
Unknown profile versions and graph v1 fail closed. The writable user owner is
retained even before its directory exists; authoring must not fall through to
bundled templates.

New package starters declare only the locales they actually ship (`en` and
`zh-CN`). Other requested locales resolve to the manifest's default locale;
contributors can add a locale by supplying and validating its translations,
without making it a core-maintained language.

The profile supports scenes, objectives, decisions, milestones, references,
endings and world/progress gates/outcomes. An `ending` node has no transitions
and cannot complete other nodes; completing one ends the adventure, closes every
open node and freezes progress (`ending_node_id`). It rejects encounter nodes/references, `rules.*`
gates, `item_reward` outcomes and mechanical item/reward/encounter resources.
Imported source resources can remain inert records with `automation_level:
"reference"` and only `schema_version`, `kind`, `id`, `source_ref`,
`automation_level`, `visibility`, `name`, `description`, `source_record` fields.
A reference label cannot conceal extra mechanical fields. Source numbers remain
evidence, not executable effects. Data packages still forbid execution entry points.

Specific mechanics require their registered ruleset runtime and validator.
Extensions advertise graph formats through the capability contract and supply
rules evaluators/reward adapters as needed. Generic code does not branch on rule
names or locales. Marketplace/local module installation shares validation. A
plugin's `requires` must reference an actual ruleset, not this narrative profile.
The existing implicit module-catalogue owner remains unchanged.

## Authority and compatibility

GM completion and player choice requests reuse Adventure application commands
without requiring combat-intent support. Membership, actor/GM permission, phase,
immutable binding/source/digest, locks and save rollback remain enforced,
including cancellation during persistence. Other legacy ruleset intents remain
unavailable.

Old D&D packages/saves keep their identities, mechanics and bindings. Nothing is
silently relabelled. Instance schema and historical migrations are unchanged.
Older runtimes must reject this unknown profile. Missing/changed packages never
fall back to an unrelated free-game plan. See [ADR-0009](../adr/0009-universal-adventures.md).
