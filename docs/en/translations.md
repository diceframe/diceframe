# Translation maintenance

English | [简体中文](../zh/translations.md)

DiceFrame maintains **English and Simplified Chinese** as its core interface and documentation languages. English is the source for the central UI message catalog. Both core catalogs must contain the same keys; new features update these two languages.

Japanese, German and Russian are existing **community translations**. They remain selectable, but coverage and continued translation work belong to volunteers for those languages. Adding a language does not transfer an ongoing translation obligation to the core project owner. Existing contributions do not automatically appoint their authors as permanent maintainers.

## Language maintenance overview

English and Simplified Chinese are the officially maintained interface and documentation languages. Japanese, German and Russian remain available as community translations; volunteers maintain them, and adding a language does not make the core project owner responsible for future translations.

Missing or unreviewed central UI translations fall back to English. Community catalogs can be paused without deleting locale IDs or changing saved games and mechanics. See the [translation policy](translations.md) for status, source review and contributor responsibilities.

## Backend intent resources

Natural-language recognition for purchases, price inquiries, declined purchases, free grants and deferred payment uses stable intent IDs. The resources live in `src/engine/intent/locales/<locale>/intents.json`. A new language adds a resource file declaring the stable keys currently used by the parser:

```text
purchase_intent
purchase_decline
purchase_offer
free_purchase
deferred_payment
currency_labels
purchase_target_prefix
purchase_target_connector
purchase_target_suffix
purchase_quantity_classifier
purchase_generic_target
purchase_quantity_word
purchase_balance
```

Resources describe language cues only. They must not change canonical currencies, amount conversion, budgets, permissions, transaction states or rules mechanics. The economy engine consumes stable intent IDs; translated display names are never canonical identity. When a locale resource is missing, parsing falls back locale → English → Simplified Chinese. Missing resources must never cause price guessing or free item delivery.

Every new language must pass the intent-resource contract tests, UTF-8 validation and the relevant economy regressions. Adding a language should not modify `src/engine/economy.py` or settlement logic. If business code must change, add a stable intent ID or resource field first and document why the existing resource contract is insufficient.

Economy boundary notices and the economy planner repair prompt live in `messages.json` beside the intent resource. The stable message keys are `unbacked_payment_notice`, `unbacked_purchase_notice`, `unpriced_purchase_notice`, `pending_decision_notice` and `economy_repair_prompt`. A new language must provide them; missing messages fall back locale → English → Simplified Chinese.

Quote semantics do not treat translated keywords as authority. A structured quote may carry verbatim `evidence`; the server verifies that it came from trusted player action or GM narration, and downgrades unverifiable evidence to an unpriced intent without creating an order.

## Status and fallback

`src/localization/registry.json` declares each language's `core`/`community` tier, `active`/`paused` status and consenting maintainers. Empty community maintainer lists mean volunteers are welcome; existing catalogs remain active as an accepted baseline. A maintainer is listed only with their consent. New community languages need a volunteer, fallback verification and an integration PR.

Missing central UI keys fall back to English. Community translations are checked against the English source fingerprints in `frontend-v2/src/i18n/source-review.json`. If English changes, the old translation stops overriding it until a volunteer reviews and updates that key. A release does not wait for complete community coverage.

Set a community language to `paused` when its continued maintenance or correctness cannot be established. It disappears from UI-language pickers; an existing saved UI preference displays English. Its locale ID remains known for games, content, filters and save loading. Reactivating the catalog restores reviewed translations. Pausing does not rename content, convert mechanics or change the language requested for AI GM narration.

The source fingerprint mechanism covers the central frontend catalog, including nested API errors. Shared backend text/field helpers and content overlays honor paused status and fall back to English. Other feature-local/backend translations are not automatically checked for semantic staleness; a pause is the explicit fallback control for a discontinued catalog. This is a deliberate display policy, not a promise of complete translated rule content.

## Contributor workflow

1. Modify the relevant `frontend-v2/src/i18n/messages/<locale>.ts` strings. Preserve parameter names, protocol tags and canonical identities.
2. If an English source changed, review the new translation and record only those reviewed keys:

   ```bash
   cd frontend-v2
   npm run locales:review -- --locale de --keys scriptImportHint,apiErrors.WORLD_NOT_FOUND
   ```

3. Run `npm run locales:check`, typecheck and relevant tests. Core missing/extra keys fail the check. Missing or stale community keys are reported and use English; they do not block unrelated feature work.
4. Submit the translation and review record together. Never regenerate the accepted baseline merely to silence stale-key reports.

Official user documentation is published in English and Simplified Chinese. Other documentation translations may be maintained separately by community volunteers, without a core release requirement. Updating display language must never silently rewrite a game's language, rule ID, character IDs or numerical mechanics.
