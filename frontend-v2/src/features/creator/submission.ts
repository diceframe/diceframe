// 投稿走插件索引仓库的 Issue 表单，作者自己维护源码仓库、官方只存指针。
// 没有"上传包"的表单，也没有官方代托管这一步。
export const SUBMISSION_REPOSITORY = 'https://github.com/diceframe/diceframe-plugins'
// 索引仓库的机器人只认这个标题前缀（scripts/submission.cjs）。
const SUBMISSION_TITLE_PREFIX = '[Plugin Submission]'
const SUBMISSION_TEMPLATE = 'add-plugin.yml'

/**
 * 投稿页 URL：预填标题和插件 ID，仓库地址可选（作者建好仓库后再填）。
 *
 * 只走 `add-plugin.yml`（作者自建仓库）。托管表单 `add-content-pack.yml` 已经废弃：
 * 官方不代为托管内容包，风险必须留在作者那边。
 */
export function contentPackSubmissionUrl(pluginId = '', repositoryUrl = ''): string {
  const query = new URLSearchParams({ template: SUBMISSION_TEMPLATE })
  const id = pluginId.trim()
  query.set('title', id ? `${SUBMISSION_TITLE_PREFIX} ${id}` : SUBMISSION_TITLE_PREFIX)
  if (id) query.set('plugin_id', id)
  const repository = repositoryUrl.trim()
  if (repository) query.set('repository', repository)
  return `${SUBMISSION_REPOSITORY}/issues/new?${query.toString()}`
}

// Same rule as the plugin host and the index (`_ID_RE` / `PLUGIN_ID_RE`).
export const CONTENT_PACK_ID_PATTERN = /^[a-z0-9]+(?:-[a-z0-9]+)*$/
