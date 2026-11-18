# AI and interface settings

English | [简体中文](../zh/ai-settings.md)

The sidebar is ordered **Models and AI, Connection and integrations,
Security and privacy, General**. The General page combines interface language,
speech playback and recognition, DF Agent and system maintenance in one view.
Voice, Agent and maintenance no longer have separate sidebar pages. Appearance
and About remain available under the General group, alongside the separate Game page. Previous `section=voice`,
`section=agent` and `section=system` links open General at the relevant area;
search results also locate settings within the combined page.
The interface language is shared with the header and saved on this browser.
Models and AI contains providers and model routing.
Search sits at the top of the sidebar on desktop and phone, with a short “Search” placeholder. It uses
setting names and synonyms, opens the relevant details and preserves
unsaved fields when switching sections. It never indexes credentials or prompt text.

Open **Manage → Settings**, then choose a page in the sidebar:

| Group | Pages and common tasks |
| --- | --- |
| Models and AI | **Model API**: save providers and credentials. **Model Routing**: assign providers/models to capabilities; expand Advanced generation settings for sampling and context. |
| Connection and integrations | Network Proxy, Share Address and Bot API; Backend Connection is available in the standalone frontend. |
| Security and privacy | Access Password; **Security** includes DiceFrame Hub and privacy. |
| General | **General**: interface language, speech, DF Agent and maintenance. **Game**: defaults for new games. **Appearance**: theme and backgrounds. **About**: version updates, project links and restart controls. |

General, Game, Appearance and About are sibling pages, not nested pages.
Only the active settings pane occupies the content area. Navigation scrolls
independently on desktop, bounded by the viewport rather than editor length.
Navigation and the editor have separate surfaces with space between them; on
phones the section list can fold beneath search. Manage's log menu follows
the same vertical layout, with processing logs and export reachable in short windows.

## OpenAI GPT-6.2

Select the OpenAI format and `https://api.openai.com/v1` in the shared provider catalog, then select `gpt-6.2-astra` or `gpt-6.2-sol`. Connection tests, narration and native function calls automatically use the Responses API. Leave Temperature empty or set it to 1. Other explicit sampling overrides raise an error. DF supplies the context for each request and sets `store: false`.

## Providers and check planning

Save providers in the shared directory, then assign each capability a provider
reference and model. There are no per-capability inline endpoint/key fallbacks.
Deleting a provider removes its credentials and clears dependent references.

**Model Routing → Dedicated check model** follows the main model when disabled.
When enabled, `check_provider_ref` and `check_model` select a separate client for
check/world-intent planning; dice and settlement remain server-authoritative.
An enabled but unconfigured route cannot silently use old credentials or the main
model. Configure it or disable the dedicated route. Ordinary offline/manual check
resolution does not require an AI model. Provider failure retains the existing
deterministic offline recovery policy.

## Context and sampling

Expand **Model Routing → Advanced generation settings** for sampling, context,
generation budgets and request timeout. This group follows the model card layout
and starts folded. One save applies the advanced generation fields together,
excluding the independent DF Agent budget. Searching a parameter opens the group
and focuses the field; existing deep links remain supported.

`context_recent_rounds` defaults to `0`, retaining automatic context budgeting.
Values `1`–`100` cap directly supplied recent GM/player log rounds. This does not
delete saved history, disable long-term memory, exceed the context budget or
expand a viewer's visibility. Regeneration applies the same limit to its historical
branch, excluding later rounds.

`llm_generation_options` is an optional object. Missing keys retain feature defaults:

| Key | Range |
| --- | --- |
| `temperature` | 0–2 |
| `top_p` | 0–1 |
| `frequency_penalty` | −2–2 |
| `presence_penalty` | −2–2 |

Overrides apply to the main and fallback model clients, including text and Agent
requests. The independent check client keeps check-specific defaults. Clear an
input to remove its override. Provider/model support varies; explicit unsupported
values produce an error rather than being dropped. Native Anthropic Messages has
no frequency/presence-penalty equivalent in this client. Consult the provider's
current API contract before setting overrides.

Chat parameter compatibility is negotiated only from explicit API errors: a
rejected `max_tokens` can become `max_completion_tokens` without changing its
number, and an unsupported feature-default temperature can be omitted. User
overrides are protected. Existing output-truncation retry policy is separate.

## DF Agent budget

**The DF Agent area on the General page** contains visibility, launcher-position recovery and its independent
per-request output budget (`assistant_agent_max_tokens`, default 4096). This is
not a total task spending limit. Generation tools retain their own output budgets.
Provider context and account limits still apply.

## What the model controls

The main model receives game instructions, visible game state, selected reference
material and recent history within the configured budget. The check planner
receives current rules, authoritative character skills and actions. Its output
proposes a check; the server validates the subject and calculates modifiers and
dice. A separate planner model changes that request's provider, not the rules.

Models differ in how often they propose scene and memory changes. Those are
structured proposals parsed by the application, not required prose headings.
Printing `SCENE:` or `MEMORY:` every round does not authorize a change, disclose
hidden material or override an adventure's active scene. Protocol and repair
behavior live in `prompts/check_planner_en.md`, `src/llm/protocol.py` and
`src/commands/protocol_repair.py`; installed code defines supported tags. Keep
campaign instructions in world style settings and use the dedicated check route
to change planner models.
