# Documentation map

Start here when developing or using DiceFrame. The source in the checked-out revision and its architecture documents describe current behavior. A plan or archived handoff does not override them.

## Current contracts

- Architecture: [English](ARCHITECTURE_EN.md) · [简体中文](ARCHITECTURE_CN.md)
- [Engineering rules](ENGINEERING_RULES.md): ownership, authority, data, extensions and contract testing
- [Accepted architecture decisions](adr/README.md): why durable boundaries were chosen
- Content: [Adventure bundle](adventures/ADVENTURE_BUNDLE_EN.md), [lorebook adapter](LOREBOOK_ADAPTER_GUIDE.md), [content synchronization](content-sync/CONTRACT.md)
- Plugin development: [English](plugins/EXTENDING_EN.md) · [简体中文](plugins/EXTENDING_CN.md)

## User guides

| Topic | English | 简体中文 |
| --- | --- | --- |
| AI and interface settings | [Guide](en/ai-settings.md) | [指南](zh/ai-settings.md) |
| Content and play, including DF Agent | [Guide](en/content-and-play.md) | [指南](zh/content-and-play.md) |
| Story plans, skill development and finales | [Guide](en/run-story-and-finale.md) | [指南](zh/run-story-and-finale.md) |
| Updates and recovery | [Guide](en/updates.md) | [指南](zh/updates.md) |
| Rules and dice | [Guide](en/rules-and-dice.md) | [指南](zh/rules-and-dice.md) |
| Direct connections | [Guide](en/direct-connect.md) | [指南](zh/direct-connect.md) |
| Translation maintenance | [Policy](en/translations.md) | [政策](zh/translations.md) |
| Lorebook management | [Guide](LOREBOOK_USER_GUIDE_EN.md) | [指南](LOREBOOK_USER_GUIDE_CN.md) |

## Documentation ownership and UI changes

This repository owns version-bound configuration, feature behavior and extension contracts. The public [documentation repository](https://github.com/diceframe/diceframe-content/tree/main/docs) owns the everyday user manual, deployment and voice instructions. The website displays that content; it does not maintain another copy of the manual.

When changing navigation, labels, defaults or an operation's prerequisites:

1. Check the relevant English and Simplified Chinese guides in both repositories against the implemented routes, translated labels and service behavior. Keep the two languages equivalent; community translations follow the [translation policy](en/translations.md).
2. Update entry paths and examples together. Link to the owning contract rather than copying configuration schemas into several manuals.
3. Check relative links and any affected screenshots. Capture screenshots only with demonstration data, without credentials, private saves or personal information.
4. Base current instructions on merged implementation. Mark planned behavior as a proposal; coordinate related documentation PRs without presenting an unmerged feature as available. Before merging a docs-only follow-up, recheck the target branch's UI for changes.

## Internal notes and history

[Internal maintainer notes](internal/README.md) (`internal/`) hold construction plans, the content-architecture vNext master plan and dated design drafts. They describe local, possibly unreleased work: branch names, PR sequencing and unfinished boundaries there are internal coordination details, not product commitments. Do not link them from user-facing guides, the website or the public documentation repository, and do not present an unmerged feature they describe as available.

[Archived plans and handoffs](archive/README.md) preserve historical decisions and unfinished inventories. Their branch names, PR ordering and test counts are historical. Temporary review reports, local paths, approval drafts and execution logs belong outside the published documentation; report their outcomes in the working conversation.

When a change lands, update its user guide and affected contract. Keep a durable decision in an ADR, keep a proposal explicitly marked as a proposal, and archive superseded execution plans. Do not maintain a second manual catalog of test names or copy session transcripts into `docs/`.
