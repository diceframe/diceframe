# Built-in Lorebook resources

World template schema v3 keeps World metadata and Lorebook ownership separate.

Each Book resource is a JSON object with:

```json
{
  "lorebook_schema_version": 1,
  "id": "arkham-lore",
  "name": "Arkham Lore",
  "description": "…",
  "language": "en",
  "entries": []
}
```

The World file points to it with `primary_lorebook_ref` (for example,
`core:arkham-lore`). Locale overlays live under
`templates/lorebooks/locales/<locale>/<id>.json` and may change only Book/Entry
display fields. They cannot change ids, ownership, bindings, or retrieval
mechanics.

v1/v2 World files that still contain `starter_lorebook` are read only through
`LegacyWorldAdapter`; new canonical services must use the World definition and
Book resource separately.
