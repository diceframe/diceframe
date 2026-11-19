# Attribution

This work includes material from the System Reference Document 5.2.1 (“SRD 5.2.1”) by Wizards of the Coast LLC, available at https://www.dndbeyond.com/srd. The SRD 5.2.1 is licensed under the Creative Commons Attribution 4.0 International License, available at https://creativecommons.org/licenses/by/4.0/legalcode.

DiceFrame's original code, original adventures, presets, tutorial copy, translations, and visual assets are governed by their respective repository or asset licenses.

## Spell description text

`content/spell_catalogs/srd_spell_descriptions.json` reproduces the spell
descriptions of SRD 5.2.1. The text was transcribed through the dnd5eapi
mirror (https://www.dnd5eapi.co, `/api/2024/spells`) and is redistributed here
under the same CC-BY-4.0 terms as the SRD itself; the file carries the
attribution and license identifiers in its own header, so a consumer that loads
only this catalog still knows where the prose came from. Regenerate it with
`scripts/rulesets/import_srd_spell_descriptions.py` — never hand-edit prose.

## Adventuring Gear text

`content/equipment_catalogs/srd_adventuring_gear.json` reproduces the
Adventuring Gear entries of SRD 5.2.1 -- the subsection that list, not the
equipment chapter as a whole. Membership, text, price, weight and the pack
contents all come from dnd5eapi's 2024 `adventuring-gear` category and are
redistributed under the same CC-BY-4.0 terms as the SRD. Regenerate with
`scripts/rulesets/import_srd_adventuring_gear.py`.

## Tools text

`content/equipment_catalogs/srd_tools.json` reproduces the Tools table of SRD
5.2.1 (artisan's tools, kits, musical instruments, gaming sets) with the
ability, utilize, craft and contents entries that table declares. Data comes
from dnd5eapi's 2024 `tools` category and is redistributed under the same
CC-BY-4.0 terms as the SRD. Regenerate with
`scripts/rulesets/import_srd_tools.py`.
