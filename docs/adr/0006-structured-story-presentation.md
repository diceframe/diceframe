# ADR 0006: Shared structured story presentation

- Status: Accepted
- Date: 2026-10-15

## Context

Public GM narration used to be a single `gm_response` string. Splitting quoted
prose in a browser cannot reliably determine who is speaking, distinguish two
NPCs with the same name, or restore the same presentation after a swipe. Chat
bubbles and a future visual-novel stage need the same ordered story data.

## Decision

The optional schema-1 `storytelling` module owns per-game `prose` / `dialogue`
generation settings and public speaker portrait overrides. Creation exposes the
same setting as the GM workbench; a mode change affects subsequent generation.
Missing settings preserve prose behavior. Unsupported future module schemas
fail closed. This is independent of Adventure `play_mode` and of game mechanics.

Each generated dialogue-mode log entry may carry a version-1 `story` sequence.
Its ordered segments contain narration or NPC dialogue, text, and for dialogue
an opaque speaker ID, the publicly revealed name at that moment, and an optional
portrait reference. Display names are never used to merge identities. No player
actions, rule effects, private messages or internal canonical NPC IDs belong in
this public sequence. Projection whitelists nested fields before sending it to
any viewer. Concealed personas do not inherit the known persona's ID or picture.

The model emits one JSON object per public beat (NDJSON), then the existing
`---` state-tag block. The presentation adapter validates and resolves these
beats; rules and state tags continue through their existing authoritative path.
Invalid format receives one bounded repair attempt, then usable public prose
becomes un-attributed narration. An unusable response fails the round. No extra
model call is needed for a correctly formatted response. Oversized stories are
bounded without losing speaker attribution, with stale quick actions discarded.

The validated sequence generates the ordinary `gm_response` text used by Bot,
history, memory and exports. A streaming adapter sends completed, sanitized
beats and ordinary text. SSE may additionally receive `story_update` snapshots;
retry and failure resets clear both presentations. Raw model metadata is not
forwarded to clients.

Every swipe keeps its own story alongside its own text. Legacy prose branches
remain valid and do not acquire guessed speakers. Active history supplies a
bounded roster, with discarded branches excluded. Known NPC IDs stay stable
across a new run of the same game using the module's identity namespace; guest
IDs and their avatar choices belong to their original run.

## Consequences

- Chat renders individual narrator cards and character bubbles. A future stage
  renderer can consume the same versioned sequence without generating dialogue
  again or introducing a second game state.
- Existing explicit portraits are reused. Missing pictures use stable initials
  and colors; entering a crowded scene does not trigger image generation.
- The GM can search the complete active cast and assign portrait references,
  independent of the timeline page currently loaded.
- Backgrounds, expression variants, voice bindings, stage placement and romance
  branch mechanics are separate features. They are not inferred from this schema.
- There is no historical text migration and no global save-schema/release bump.

## Explicit identity repair

A guest may be given a registered NPC handle by a later model response. Identical
public names alone do not prove identity. The GM may explicitly merge an active
guest into another active speaker with the same public persona. The schema-1
storytelling slot optionally stores `speaker_aliases` (guest ID → target ID).
Projection and future rosters resolve these links, preserving original log beats;
target portrait overrides take priority. Invalid or cyclic links fail closed.
The operation is GM-only, rejects generation/rewrite overlap, and rolls back its
owner slot if saving fails. Guest links are dropped across run transitions.

## Oversized story bounding

The bound is a runaway guard, not a length target. A story is shortened only when
its `story_text` exceeds a per-language ceiling (1500 characters for Chinese and
Japanese, 3600 for English, German and Russian), well above the 3–8 beats the
prompt asks for, so a regular reply is never cut. Whole beats are kept in order
and trailing beats are dropped; the first beat is cut at a sentence end only
when it alone is oversized. Rounds, swipes and the opening share one ceiling
(`story_output.story_limit`) and log a warning whenever it applies.

An earlier bound cut every reply longer than 500 characters down to 400. Because
the prompt allows up to eight beats, regular replies ended mid-sentence and the
trailing beats, often the narration of a check result, were discarded silently.
