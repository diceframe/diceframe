# Story plans, intermissions and finales

## A plan for a free game

Open **Play → GM workspace → Scene → Story plan**. Write a theme, party goal,
scenes, exit conditions, successors and alternative clues, or consent to token
usage and request an AI original draft. Generation is cancellable; consumed
usage cannot be undone. Review and explicitly adopt the draft. Historical games
do not receive automatic plans, and adoption does not rewrite a world or publish
an Adventure package.

Players and AI companions receive only current public objectives. Future scenes
are not revealed merely because their visibility is public. GM diagnostics show
the actual scene block, source, budget, stall hints and rejected-NPC warnings.
Strict mode admits state only for established/adopted NPCs; explicitly create a
new important NPC in character management before adding it to the plan. Open mode
permits new NPC state. Prose still needs review; arbitrary models can invent facts.

To advance, select an adjacent scene, reference committed log rounds and confirm
its exit condition. AI proposals never execute themselves. Stale revisions,
cross-run requests, nonadjacent transitions and missing evidence are rejected.
Alternative routes/costs do not change failed dice. Bound Adventures retain their
own progress controls.

## Create a reusable Adventure from a plan

Expand **Story plan → Create reusable Adventure**, choose a fresh identity and
prepare the draft. Edit, validate and explicitly review it in the existing
Adventure dialog before publication. Preview/cancellation never publishes or
changes the source game.

The copy starts at the authored beginning; successor alternatives become
exclusive choices. It excludes live progress, characters, chat and world state.
Exit conditions/hints stay in descriptions, so copied scenes/objectives start
GM-only. Remove private hints before making descriptions public. Live NPC
references are omitted; create or map reusable resources explicitly.

New Adventures, prose imports and plan copies default to **Universal story** for
graph-capable CoC, custom and D&D rules. Specific mechanical gates, combat and
rewards still require their owning runtime. Old D&D packages retain their
requirements. See the [contract](../adventures/UNIVERSAL_ADVENTURES.md).

## Lite CoC skill development

Successful settled skill checks create one pending mark per actor/skill.
Attributes, failures, luck-spent successes, non-improvable and excluded skills do
not mark. Narration or AI tags cannot directly increase a skill.

Open **Intermission & finale → Skill development marks** in the same Scene tab.
Resolve outstanding actions, combat, dice, luck/Push, rest and rewards first.
The current policy rolls d100: above skill or at least 96 grants d10, capped at 99.
Receipts preserve before/roll/after values. Failed saves can retry using reserved
randomness without rerolling. A settled check cannot earn development again, and
rewind/swipe cannot cross a settled intermission. Historical increases remain;
old logs do not produce guessed marks.

## End the adventure

1. Resolve the listed blockers and choose **Prepare to end this adventure**.
2. Writes freeze. Preparation grants no rewards and can be cancelled to resume.
3. Write the public epilogue, whole-run recap and optional private GM notes, or
   consent to token usage for an AI public draft. AI reads the whole public run
   and current public scene, summarizing long runs in segments with visible,
   cancellable progress.
4. Review and save materials, check the final confirmation and commit settlement.

Another GM window changing materials requires a refresh and new review. Failed,
cancelled or disconnected generation never ends the run. Committed settlement
creates a permanent read-only archive, surviving server restart. Reset/restart
cannot overwrite it. Lite CoC applies supported intermission development; D&D
audits existing rewards, requires pending level choices to be completed and adds
no duplicate rewards. SAN and money are not inferred: resolve them through
existing rule/GM actions before preparing.

## Export and carry

Players export public recaps and their own character. GMs can export selected
characters and use the existing save export for a full ZIP. Settled ZIPs add
`finale/`: epilogue, whole-run recap, private GM notes, public source, receipt,
character JSON and a SHA-256 manifest. Full ZIPs are GM backups; share the public
recap/personal character exports with players. Room passwords and invitation
credentials are scrubbed. Export retries never settle again.

Choose settled characters and **Create next game (keep archive)** to create a
separate free game in the same world. This path retains full settled character
values and materializes the selected Lorebooks. Invite players again: old
credentials, NPCs, secrets and story plans do not carry. Failed delivery retries
reuse the same destination. This entry creates a free game; use the corresponding
creation flow to select another supported Adventure package.

Character JSON includes the full `character_sheet` and provenance receipt.
Ordinary character-library import still reads creation fields; it is not a
lossless restoration of every HP/resource/XP/runtime value. Use full-snapshot
game carry for that continuity.

## Upgrade and support boundary

The current writer uses instance schema 48; migration 46→47 adds the one-use
resource recovery authority and reader fence, while 47→48 fences affection
rollback snapshots without inferring historical affection scores. Existing values are retained;
historical plans and marks are not generated. Older programs cannot enforce the
new archive authority and reject new saves. Preserve a complete data backup
before upgrading and do not force new saves through an older reader. Cancel a
preparing finale before importing its save into a different run; ended imports
remain read-only.

Supported here: generic free plans, existing Adventure scene projection, Lite CoC
intermission growth and D&D finale audit. Full CoC Adventure runtime remains a
separate task; plans/finales do not automatically grant that capability.
