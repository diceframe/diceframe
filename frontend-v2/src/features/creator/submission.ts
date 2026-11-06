// Content packs without their own repository are submitted through an issue
// form on the plugin index; the package is attached to it as a .zip.
export const CONTENT_PACK_SUBMISSION_REPOSITORY = 'https://github.com/diceframe/diceframe-plugins'

/** Issue form URL for a content pack submission; the ID is prefilled when known. */
export function contentPackSubmissionUrl(pluginId = ''): string {
  const query = new URLSearchParams({ template: 'add-content-pack.yml' })
  const id = pluginId.trim()
  if (id) {
    query.set('title', `[Content Pack] ${id}`)
    query.set('plugin_id', id)
  }
  return `${CONTENT_PACK_SUBMISSION_REPOSITORY}/issues/new?${query.toString()}`
}

// Same rule as the plugin host and the index (`_ID_RE` / `PLUGIN_ID_RE`).
export const CONTENT_PACK_ID_PATTERN = /^[a-z0-9]+(?:-[a-z0-9]+)*$/
