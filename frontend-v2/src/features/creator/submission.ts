// Content packs without their own repository are submitted through an issue
// form on the plugin index; the package is attached to it as a .zip.
export const CONTENT_PACK_SUBMISSION_REPOSITORY = 'https://github.com/diceframe/diceframe-plugins'

function issueFormUrl(template: string, titlePrefix: string, pluginId: string): string {
  const query = new URLSearchParams({ template })
  const id = pluginId.trim()
  if (id) {
    query.set('title', `${titlePrefix} ${id}`)
    query.set('plugin_id', id)
  }
  return `${CONTENT_PACK_SUBMISSION_REPOSITORY}/issues/new?${query.toString()}`
}

/** Issue form URL for a content pack submission; the ID is prefilled when known. */
export function contentPackSubmissionUrl(pluginId = ''): string {
  return issueFormUrl('add-content-pack.yml', '[Content Pack]', pluginId)
}

/**
 * Issue form URL for registering a pack the author publishes from their own
 * public repository (the flat "repository source" export).
 */
export function repositorySubmissionUrl(pluginId = ''): string {
  return issueFormUrl('add-plugin.yml', '[Plugin Submission]', pluginId)
}

// Same rule as the plugin host and the index (`_ID_RE` / `PLUGIN_ID_RE`).
export const CONTENT_PACK_ID_PATTERN = /^[a-z0-9]+(?:-[a-z0-9]+)*$/
