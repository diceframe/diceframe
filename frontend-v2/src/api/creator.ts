import { api, apiBlob } from '@/api/client'

export type CreatorProjectSummary = {
  id: string
  name: string
  revision: number
  source?: Record<string, string>
  updated_at?: string
  file_count: number
}

export type CreatorProject = CreatorProjectSummary & {
  files: Record<string, string>
  digest: string
}

export type CreatorProjectsResponse = {
  ok: boolean
  projects: CreatorProjectSummary[]
  error?: string
}

export const creatorApi = {
  list: () => api<CreatorProjectsResponse>('/creator/projects'),
  create: (name: string, source?: Record<string, string>) => api<{ ok: boolean; project: CreatorProjectSummary }>('/creator/projects', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, source }),
  }),
  read: (projectId: string) => api<{ ok: boolean; project: CreatorProject }>('/creator/projects/' + encodeURIComponent(projectId)),
  update: (projectId: string, body: { expected_revision: number; files?: Record<string, string>; remove?: string[]; name?: string }) =>
    api<{ ok: boolean; project: CreatorProject }>('/creator/projects/' + encodeURIComponent(projectId), {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  validate: (projectId: string) =>
    api<{ ok: boolean; errors?: string[]; revision?: number; digest?: string }>('/creator/projects/' + encodeURIComponent(projectId) + '/validate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
    }),
  preview: (projectId: string) =>
    api<{ ok: boolean; preview?: Record<string, unknown>; errors?: string[]; revision?: number; digest?: string }>('/creator/projects/' + encodeURIComponent(projectId) + '/preview', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
    }),
  build: (projectId: string, expectedRevision: number) =>
    apiBlob('/creator/projects/' + encodeURIComponent(projectId) + '/build', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ expected_revision: expectedRevision }),
    }),
  submission: (projectId: string, expectedRevision: number) =>
    api<{ ok: boolean; submission?: Record<string, unknown>; artifact_sha256?: string; filename?: string; error?: string }>('/creator/projects/' + encodeURIComponent(projectId) + '/submission', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ expected_revision: expectedRevision }),
    }),
  delete: (projectId: string) =>
    api<{ ok: boolean; deleted?: boolean }>('/creator/projects/' + encodeURIComponent(projectId), {
      method: 'DELETE',
    }),
}
