import type { Asset, ProvenancePolicy } from '../model/types'

/**
 * Client for the figmint backend (see `src/figmint/server.py`).
 *
 * The backend owns everything that needs the filesystem: scanning the figure
 * directory, hashing files for staleness detection, and reading/writing
 * documents. The editor degrades to a read-only demo when it is not running, so
 * `npm run dev` on its own still gives you a usable canvas.
 */

const BASE = '/api'

export class ApiError extends Error {
  status?: number

  constructor(message: string, status?: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(`${BASE}${path}`, init)
  } catch (err) {
    throw new ApiError(
      `Cannot reach the figmint backend — is \`make dev\` running? (${
        err instanceof Error ? err.message : String(err)
      })`,
    )
  }
  if (!res.ok) {
    const detail = await res.text().catch(() => '')
    throw new ApiError(detail || `${res.status} ${res.statusText}`, res.status)
  }
  return (await res.json()) as T
}

export interface AssetIndex {
  root: string
  assets: Asset[]
  /** The project's provenance policy, from `figmint.toml`. */
  policy?: ProvenancePolicy
}

export function fetchAssets(dir?: string): Promise<AssetIndex> {
  const q = dir ? `?dir=${encodeURIComponent(dir)}` : ''
  return request<AssetIndex>(`/assets${q}`)
}

export interface DocumentPayload {
  path: string
  text: string
}

export function fetchDocument(path: string): Promise<DocumentPayload> {
  return request<DocumentPayload>(`/document?path=${encodeURIComponent(path)}`)
}

export function saveDocument(
  path: string,
  text: string,
): Promise<{ path: string; bytes: number }> {
  return request(`/document`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path, text }),
  })
}

export function listDocuments(): Promise<{ documents: string[] }> {
  return request<{ documents: string[] }>('/documents')
}

export interface BuildOutput {
  path: string
  bytes: number
}

/**
 * Build a *saved* document into artifacts. The backend composes from what is on
 * disk, so save before calling — the editor does this for you.
 */
export function buildDocument(
  path: string,
  formats: string[] = ['svg'],
): Promise<{ outputs: BuildOutput[]; warnings: string[] }> {
  return request('/build', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path, formats }),
  })
}

/** URL that serves the raw bytes of a project-relative path. */
export function assetUrl(path: string): string {
  return `${BASE}/file?path=${encodeURIComponent(path)}`
}

export function downloadText(filename: string, text: string, mime: string) {
  const blob = new Blob([text], { type: mime })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}
