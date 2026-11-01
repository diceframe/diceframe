# Content, characters and play

English | [简体中文](../zh/content-and-play.md)

This guide covers the content and play workflows in the current application. See [application updates](updates.md) before upgrading an existing installation.

## Find the right workspace

| Navigation | What opens there |
| --- | --- |
| Overview | Create an adventure or resume a save. |
| Play | Current game, actions, party and GM workspace. |
| Characters | Reusable character card library and this game's characters. |
| Content | Content library, World notes, Content packs and Rules. |
| Manage | Memory, Logs, Plugins and Settings. |

In **Content → Content library**, use the toolbar for the world gallery, adventure workbench, script import and source management. Worlds and adventures are library tools, not additional top-level navigation tabs. The library's search and content-language filter help narrow its cards. Player invitation pages expose the player's permitted controls rather than all owner workspaces.

## Worlds, lorebooks, rules and adventures

| Content | Purpose |
|---|---|
| World | The setting and identity of a game, its background, default rules and associated lorebooks |
| Lorebook | Detailed NPC, place, item, faction and background knowledge retrieved for the GM as needed |
| Rules | The authoritative check, character and combat capabilities; translated names do not change them |
| Adventure | A versioned story graph with scenes, objectives, branches and optional encounters |
| Content pack | An installable source containing adventures and reusable resources |

A short world description is a setting summary, not its complete knowledge base. Lorebooks supply the detail. Installing a pack does not inject all its resources into every model request.

Use **Content library** to browse worlds, adventures and lorebooks. World cards offer starting a game and managing its lorebooks. The world gallery also offers preview and **Clone world**; cloning creates an editable personal world.

Lorebook cards keep the book's own title and identify its associated worlds. A primary book initialized with its world's title displays “Primary lorebook” to distinguish it from the world; this is a display label, not a rename of saved data. Independent books are marked separately.

In **World notes**, lorebook choices follow the current world/game scope and selected content language. A direct link can add its exact independent book, without adding the whole catalogue. Choosing another world, including through the language filter, clears the previous book and entry targets without changing the active game's saved state.

**Settings → General → Interface language** shares the header's language selection and stores it on this device. **Settings → General → DF Agent** independently sets the maximum output tokens per Agent model request (default 4096). The Agent uses the main model; generation tools keep their own budgets. This setting is not a total task token or spending limit. See [AI and interface settings](ai-settings.md) for context limits, sampling and the independent check model.

During game creation, choose a template, custom world or AI-generated draft. The compact view keeps the common choices visible; expandable settings retain language, seed, additional background and game-specific lorebook selection. Choosing books for this game changes only its bindings. Editing a shared source book can affect other games that reference it; use a personal copy when you need independent content.

Standard free play uses normal GM narration. Adventure mode follows the selected adventure and its supported rules. Compatibility is checked by the server; a D&D 2014 module is not silently converted to D&D 2024.

After switching to module mode, choose a package before checking its required rules and world. With no package selected, the page asks you to choose one. Source, rule and world corrections appear only for a selected package with a compatibility issue.

## Install or create an adventure

1. Open **Content → Content packs**, or source management from the library. Choose online content or a local `.dfplugin` file.
2. Preview installation effects and resolve blocking errors. Confirm installation and check the resulting status or error message.
3. Open a pack's details, choose an adventure and select **Use this adventure**.
4. Review the world, rules, lorebooks and characters, then start the game.

Open the **adventure workbench** from **Content → Content library** to create an original adventure, generate an AI draft, import a native package, import a script/external module, or organize existing text. Saved games pin the adventure's identity, version and digest. Updates, disable and uninstall are blocked when they would break those bindings; inspect the affected saves instead of deleting package files.

## Import a script, PDF or external module

The import page accepts UTF-8 Markdown/TXT, Foundry Adventure export JSON, unencrypted Fantasy Grounds XML/`.mod`, and PDFs with a text layer. It reads local data into a draft. It does not execute module scripts, install an entire Foundry system, read arbitrary LevelDB collections or fetch external attachments. Scanned PDFs need OCR before import.

1. Choose the file and parse it. Inspect extracted chapters, scenes, GM notes, resources and references.
2. Correct the draft and its visibility. Imported content starts GM-only; make material public deliberately.
3. Resolve errors. An unresolved NPC, location or encounter reference blocks publication unless you explicitly keep it as prose.
4. Validate again after editing. Review the publication checklist and confirmation; the page explains why **Publish as adventure** is unavailable.
5. Publish the validated draft, then use that adventure to create a game.

Numbers in external prose remain reference material. Import does not turn monster statistics, spells, rewards or house rules into executable mechanics.

**AI organize draft** is optional. It uses the configured main model, sends the draft text to that provider and consumes tokens. Confirm the usage notice before starting. The progress bar reports completed processing steps, not a guaranteed duration. Canceling or closing the import dialog stops pending work, but completed calls may still be billed. Review the generated result and explicitly adopt it; failed, canceled or unadopted results do not replace your edited draft. Validate and confirm publication afterward.

## Adventure branches and secrets

The GM sees the full adventure; players receive only its public projection. Native exclusive choices can collect player proposals and require GM confirmation before committing one branch. The server checks the current run and state, preventing stale proposals from advancing another run.

The text marker `@choice label -> scene_id` defines a graph connection. Several unconditional connections can open together; this marker alone does not declare a native exclusive-choice group. Configure the structured choice in the adventure editor when that behavior is needed.

## Character cards, invitations and AI seats

**Character card library** stores reusable cards; **This game's characters** manages participants in the active game. Applying a card to a character that has already acted requires GM authority. The reusable card and a character's current game state serve different purposes.

On the play page, use **Invite friends** beneath the party list. Share the actual invitation URL and let the player open it, or paste it into **Join friends** on the homepage. Players do not need to extract or type `web|room|host` from a link. The invitation dialog's **Multiplayer help** button opens the official guide. Invitations and seat credentials follow the server's access policy; a bare legacy player ID is not a credential. If an old link no longer authenticates, the GM should issue a new invitation.

The GM can open **⋯ Member actions** on the right of each member card for control links, editing, away/return, AI control and removal. Available actions follow the member's current state. Kick appears after a divider in a danger color and requires confirmation; the host and current actor cannot be kicked. The workbench player list retains status and rest notices, while member management uses this menu.

The menu's **Control link** resumes an existing character; **Invite friends** beneath the list is for new players. Reissuing an existing seat link requires confirmation; reopening a valid link in the same GM session reuses it without disconnecting its player. Players do not see these management controls.

AI-hosted seats and companions use the same server action and rules boundaries as human players. Their policy belongs to the game and survives saving/loading; narration cannot independently grant private information or bypass resource costs.

## Checks and combat

When your character has skills, **Action skill** appears inside the input frame,
above the action button on its right. Its choices open upward without resizing the conversation area.
Leave it automatic or choose a skill from the server's
character sheet. An explicit choice takes priority over the planner's guess;
modifiers still follow the selected rules. Skill descriptions cannot grant
arbitrary bonuses. A failed draft retains its choice on this browser for the
same game, run, actor and round. A skill choice applies to one submission: acceptance
resets it to automatic, so the next action does not reuse it. Refreshing a confirmed
post-commit failure also resets it; an unconfirmed draft retains its choice for review
or retry. The section folds after
selection to keep the conversation visible on phones.

New games default to automatic dice reveal. In Play, open **GM workspace → Rules →
Check flow → Dice reveal** to choose automatic or shared click-to-reveal.
Explicit choices survive reload and restart; upgrading preserves an existing
game's recorded choice. Revealing changes presentation, never the server's roll.

**Settings → Game**, directly below General, stores new-game defaults for dice reveal,
Push, quick action suggestions, Luck decision timeout, narrative perspective/presentation/style, and supported
advancement policies. Creation copies these defaults; explicit creation choices win.
Changing defaults never edits existing or imported saves. Reset, restart and seed
recreation retain the source table's preferences. Following the world keeps its style.

Each player can hide or show fixed and generated action suggestions using **Suggestions**
beside the action panel title. The browser remembers this device's choice per game and
character across reloads, rounds and new runs. It does not affect other players or stop
suggestion generation. Free-form action input and skill selection remain available.

**GM workspace → Settings → Action panel → Show quick action suggestions by default**
sets the table's initial visibility. **Settings → Game → Action panel** sets the new-game
default. Both start enabled, including old saves without the field. A player's explicit
choice takes priority over later GM changes. If the running backend has not loaded the
new field, the default switch requests a server restart instead of submitting an
unsupported field; personal hide/show controls remain available.

**GM workspace → Rules → Check flow → Allow Push** starts enabled for rulesets that
support it. Disabling prevents new Push choices and closes unfinished ones using the
original result, retaining an explanation. Completed rerolls remain unchanged, and
reenabling never reopens old choices. Luck remains independent. Finishing the last
blocking choice attempts to resume the existing round flow; a later generation failure
does not undo saved settings, and the UI reports that continuation needs attention.

Server owners can **save this game's preferences as defaults** or **apply current defaults**
under **GM workspace → Settings → Game preferences**. Only preferences are copied, never
passwords, characters, reward ledgers or immediate actions. Unsupported rule preferences
do not overwrite other rules' defaults when saving a table profile. Generation in flight
blocks changes; a pending judgment allows disabling Push but not changing story presentation.

The right side inside the input frame is one compact column: the optional skill
selector above the action button. Text and voice input occupy the left side.
Skill options open upwards without increasing the composer's height. Characters
without skills only show the action button.

Available check modes depend on the selected rules and GM policy. CoC Push is a linked follow-up to an eligible failed check, not an unrestricted reroll. Required GM consent and its consequences remain attached to that check.

In D&D 2024 authoritative combat, available spells depend on the character's prepared/granted spells, resources and current trigger. A level 2+ Paladin gains **Divine Smite** through Paladin's Smite. It becomes available after a qualifying melee hit while the bonus action and a spell slot or free use remain available. It is not an always-available attack button. Long Rest restores the class's free use. This follows the supported 2024 rules, not the 2014 version of Smite.

The Lite rules and custom rules expose only the actions their runtime declares. Missing spell mechanics are not inferred from a class name. See [rules and dice](rules-and-dice.md) and the [automation coverage](../rulesets/dnd2024/AUTOMATION_COVERAGE.md) for the implemented boundary.

Leaving combat does not resurrect a character. In D&D, healing an unconscious
living character outside combat updates canonical HP, clears unconscious/stable
state and resets death saves. A truly dead character requires explicit revival;
ordinary healing and Long Rest do not revive them. Rejected narrative healing
does not display a successful HP change; the GM receives a resurrection notice.
During authoritative combat, narrative HP tags cannot duplicate settlement.
Combat, rest, saving and reloading read the same ruleset health state.

## DF Agent and recovery

Story plans, deferred CoC development, whole-run finales and carrying settled
characters are described in [Story plans, intermissions and finales](run-story-and-finale.md).

The owner's DF Agent can search documentation, inspect game/content state and prepare drafts. Model-directed writes require the application's review and confirmation flow and remain subject to the owning service's permissions. Ordinary player pages do not expose the owner agent. Read the proposed changes before confirming; AI operations use the configured provider and may consume tokens.

Operations use the live tool catalog and current application state. Help is retrieved only when needed through `search_documentation`, using this installed version's bundled English or Simplified Chinese instructions. Development checkouts use their current local files. Moving remote manuals and older unversioned indexes do not describe the Agent's available actions or enter every conversation automatically.

On phones, **Game tools** groups character information, scene maps and the party; the GM workspace retains its own top entry. The compact character status strip also opens the character drawer. When the character panel is not docked or open, the left-edge **Character Status** button provides another way to reopen it; the party has its own edge control.

The DF Agent task workspace can fold to leave more room for conversation on phones. If its draggable launcher becomes hard to find, use **Manage → Settings → General → DF Agent** to reset the launcher position.
The party rail is ordered **Party members → Party actions → Rules recap**.
Each member shows their submitted, waiting-for-roll or away status. Rulesets
with a registered rest interface offer **Party rest**, opening the character
center directly at its rest controls. An active rest shows readiness and a
**Join rest** or **View party rest** action. Opening it does not resolve the rest;
the player must confirm elapsed game time and submit.
GMs **Request roll** here. Players see **Pending roll · count** for unfinished
requests and **Roll history** when records exist. Empty history and unsupported
rest controls stay hidden. The server retains dice, actor and current-run authority;
a player who already rolled is not prompted again while others are pending.
On phones, **Game tools → Party** opens the same controls. Targeted roll
confirmations still arrive with the party drawer closed; postponed requests can
be reopened from the pending entry. The GM workspace retains management tools
without a duplicate roll panel.

Lorebook operation history supports reviewing and recovering supported changes. Recovery is owner-private and validates its plan before applying it. It does not replace an upgrade backup: preserve the complete `data/` directory before changing program versions.
