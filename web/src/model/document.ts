import type {
  Asset,
  FigmintDocument,
  FigNode,
  ImageNode,
  MathNode,
  NodeId,
  Source,
  SourceKey,
  SourceStatus,
  TextNode,
} from './types'

export const FIGMINT_FORMAT_VERSION = '0.1'

/** A4-ish single-column figure default: 3.5in wide, 4:3. */
const DEFAULT_CANVAS = { width: 252, height: 189 }

let counter = 0

/**
 * Short, readable, stable-per-session ids. Deliberately not UUIDs — these end up
 * in the YAML and in Stencila `#id` anchors, so people have to read them.
 */
export function makeId(prefix: string): NodeId {
  counter += 1
  return `${prefix}-${counter.toString(36)}${Math.random().toString(36).slice(2, 5)}`
}

/** Turn a file path into a readable, unique source key like `cp-curve`. */
export function makeSourceKey(
  path: string,
  existing: Record<SourceKey, unknown>,
): SourceKey {
  const base = (path.split('/').pop() ?? path)
    .replace(/\.[^.]+$/, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
  const stem = base || 'source'
  if (!(stem in existing)) return stem
  let n = 2
  while (`${stem}-${n}` in existing) n += 1
  return `${stem}-${n}`
}

export function emptyDocument(): FigmintDocument {
  return {
    figmint: FIGMINT_FORMAT_VERSION,
    id: 'fig-untitled',
    title: 'Untitled figure',
    caption: '',
    canvas: {
      ...DEFAULT_CANVAS,
      units: 'pt',
      background: null,
      grid: { size: 6, snap: true },
    },
    sources: {},
    nodes: [],
  }
}

export function findNode(
  doc: FigmintDocument,
  id: NodeId,
): FigNode | undefined {
  return doc.nodes.find((n) => n.id === id)
}

export function displayName(node: FigNode): string {
  if (node.name) return node.name
  switch (node.type) {
    case 'image':
      return node.source
    case 'text':
      return node.text.slice(0, 24) || 'Text'
    case 'math':
      return node.tex.slice(0, 24) || 'Math'
    default:
      return node.type[0].toUpperCase() + node.type.slice(1)
  }
}

// ---------------------------------------------------------------------------
// Insertion helpers
// ---------------------------------------------------------------------------

/**
 * Size a newly-inserted image so it fits inside `max` while keeping its
 * intrinsic aspect ratio. Assets with no known intrinsic size get a square.
 */
export function fitIntoBox(
  intrinsic: { width: number; height: number } | undefined,
  max: { width: number; height: number },
): { width: number; height: number } {
  if (!intrinsic || intrinsic.width <= 0 || intrinsic.height <= 0) {
    const side = Math.min(max.width, max.height)
    return { width: side, height: side }
  }
  const scale = Math.min(
    max.width / intrinsic.width,
    max.height / intrinsic.height,
    1,
  )
  return {
    width: intrinsic.width * scale,
    height: intrinsic.height * scale,
  }
}

export function sourceFromAsset(asset: Asset): Source {
  return {
    path: asset.path,
    hash: asset.hash,
    size: asset.size,
    modified: asset.modified,
    mediaType: asset.mediaType,
    intrinsic: asset.intrinsic,
    provenance: {
      ...asset.provenance,
      importedAt: new Date().toISOString(),
    },
  }
}

export function makeImageNode(
  sourceKey: SourceKey,
  rect: { x: number; y: number; width: number; height: number },
): ImageNode {
  return {
    id: makeId('img'),
    type: 'image',
    source: sourceKey,
    fit: 'contain',
    ...rect,
  }
}

export function makeTextNode(x: number, y: number, text = 'Label'): TextNode {
  return {
    id: makeId('txt'),
    type: 'text',
    text,
    x,
    y,
    width: 60,
    height: 14,
    style: {
      fontFamily: 'Helvetica, Arial, sans-serif',
      fontSize: 9,
      color: '#111111',
      align: 'left',
      lineHeight: 1.2,
    },
  }
}

export function makeMathNode(x: number, y: number, tex = 'E = mc^2'): MathNode {
  return {
    id: makeId('math'),
    type: 'math',
    tex,
    display: false,
    x,
    y,
    width: 80,
    height: 24,
    fontSize: 10,
    color: '#111111',
  }
}

// ---------------------------------------------------------------------------
// Staleness
// ---------------------------------------------------------------------------

/**
 * Compare a recorded source against the live asset index.
 *
 * `unknown` means we never recorded a hash (hand-written YAML), which is not an
 * error — we just can't say anything about freshness.
 */
export function sourceStatus(
  source: Source,
  assets: Map<string, Asset>,
): SourceStatus {
  const asset = assets.get(source.path)
  if (!asset) return 'missing'
  if (!source.hash) return 'unknown'
  return asset.hash === source.hash ? 'ok' : 'stale'
}

/** Sources referenced by at least one node, in document order. */
export function usedSourceKeys(doc: FigmintDocument): SourceKey[] {
  const keys: SourceKey[] = []
  for (const node of doc.nodes) {
    if (node.type === 'image' && !keys.includes(node.source)) {
      keys.push(node.source)
    }
  }
  return keys
}
