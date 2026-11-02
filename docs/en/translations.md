# Translation maintenance

English | [简体中文](../zh/translations.md)

DiceFrame maintains **English and Simplified Chinese** as its core interface and documentation languages. English is the source for the central UI message catalog. Both core catalogs must contain the same keys; new features update these two languages.

Japanese, German and Russian are existing **community translations**. They remain selectable, but coverage and continued translation work belong to volunteers for those languages. Adding a language does not transfer an ongoing translation obligation to the core project owner. Existing contributions do not automatically appoint their authors as permanent maintainers.

## Language maintenance overview

English and Simplified Chinese are the officially maintained interface and documentation languages. Japanese, German and Russian remain available as community translations; volunteers maintain them, and adding a language does not make the core project owner responsible for future translations.

Missing or unreviewed central UI translations fall back to English. Community catalogs can be paused without deleting locale IDs or changing saved games and mechanics. See the [translation policy](translations.md) for status, source review and contributor responsibilities.

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
