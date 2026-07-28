import { parse, stringify } from 'yaml'
import type { FigmintDocument, FigNode } from '../model/types'
import { FIGMINT_FORMAT_VERSION } from '../model/document'

/**
 * `.fig.yaml` serialization.
 *
 * The on-disk format is intentionally a near-direct dump of the document model:
 * an agent (or a human in vim) should be able to open it, see which file each
 * panel came from, nudge a coordinate, and save. Two things we do beyond a plain
 * dump:
 *
 *  - Round geometry, so dragging doesn't produce `x: 12.000000000000002`.
 *  - Emit keys in a fixed, meaningful order rather than insertion order.
 */

const GEOMETRY_PRECISION = 2

function round(n: number): number {
  const f = 10 ** GEOMETRY_PRECISION
  return Math.round(n * f) / f
}

/** Drop undefined/null-ish keys so the YAML stays uncluttered. */
function compact<T extends object>(obj: T): Partial<T> {
  const out: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(obj)) {
    if (v === undefined) continue
    if (v === null) {
      out[k] = null
      continue
    }
    if (typeof v === 'object' && !Array.isArray(v)) {
      const inner = compact(v as object)
      if (Object.keys(inner).length > 0) out[k] = inner
      continue
    }
    out[k] = v
  }
  return out as Partial<T>
}

const NODE_KEY_ORDER = [
  'id',
  'type',
  'name',
  'source',
  'text',
  'tex',
  'label',
  'x',
  'y',
  'width',
  'height',
  'rotation',
  'from',
  'to',
  'curve',
  'display',
  'fit',
  'opacity',
  'locked',
  'hidden',
  'fontSize',
  'color',
  'style',
]

function orderKeys(obj: Record<string, unknown>, order: string[]) {
  const out: Record<string, unknown> = {}
  for (const key of order) {
    if (key in obj) out[key] = obj[key]
  }
  for (const key of Object.keys(obj)) {
    if (!(key in out)) out[key] = obj[key]
  }
  return out
}

function serializeNode(node: FigNode): Record<string, unknown> {
  const rounded = {
    ...node,
    x: round(node.x),
    y: round(node.y),
    width: round(node.width),
    height: round(node.height),
  }
  return orderKeys(
    compact(rounded) as Record<string, unknown>,
    NODE_KEY_ORDER,
  )
}

export function toYaml(doc: FigmintDocument): string {
  const out = {
    figmint: doc.figmint,
    id: doc.id,
    ...(doc.title ? { title: doc.title } : {}),
    ...(doc.caption ? { caption: doc.caption } : {}),
    canvas: compact({
      ...doc.canvas,
      width: round(doc.canvas.width),
      height: round(doc.canvas.height),
    }),
    sources: doc.sources,
    nodes: doc.nodes.map(serializeNode),
  }
  return stringify(out, {
    lineWidth: 88,
    // Block scalars keep multi-line captions and TeX readable.
    defaultStringType: 'PLAIN',
    defaultKeyType: 'PLAIN',
  })
}

export class DocumentParseError extends Error {}

export function fromYaml(text: string): FigmintDocument {
  let raw: unknown
  try {
    raw = parse(text)
  } catch (err) {
    throw new DocumentParseError(
      `Not valid YAML: ${err instanceof Error ? err.message : String(err)}`,
    )
  }
  if (!raw || typeof raw !== 'object') {
    throw new DocumentParseError('Document must be a YAML mapping')
  }
  const doc = raw as Partial<FigmintDocument>
  if (!doc.figmint) {
    throw new DocumentParseError('Missing `figmint:` format version key')
  }
  if (doc.figmint !== FIGMINT_FORMAT_VERSION) {
    // Forward compatible for now — there is only one version.
    console.warn(
      `Document format ${doc.figmint}, editor expects ${FIGMINT_FORMAT_VERSION}`,
    )
  }
  if (!doc.canvas) throw new DocumentParseError('Missing `canvas:`')

  return {
    figmint: doc.figmint,
    id: doc.id ?? 'fig-untitled',
    title: doc.title,
    caption: doc.caption,
    canvas: {
      width: doc.canvas.width ?? 252,
      height: doc.canvas.height ?? 189,
      units: doc.canvas.units ?? 'pt',
      background: doc.canvas.background ?? null,
      grid: doc.canvas.grid ?? { size: 6, snap: true },
    },
    sources: doc.sources ?? {},
    nodes: Array.isArray(doc.nodes) ? (doc.nodes as FigNode[]) : [],
  }
}
