/**
 * Figmint document model.
 *
 * Design notes
 * ------------
 * The in-memory model is the *authoring* model: a free-form canvas of nodes with
 * absolute geometry, which is what you need to drag and resize things. It is
 * deliberately a flat, serializable, YAML-friendly shape — the on-disk `.fig.yaml`
 * is very close to a direct dump of this structure so it stays human readable and
 * agent-editable.
 *
 * Geometry is stored in **points** (1pt = 1/72in), the natural unit for print
 * figures. Zoom is a view concern and never touches the document.
 *
 * Provenance lives in `sources`, keyed by a short slug. Nodes reference a source
 * by key rather than by path so that re-pointing a source updates every panel that
 * uses it, and so the (larger) provenance record is written once.
 *
 * See `src/io/stencila.ts` for how this maps onto the Stencila Schema.
 */

export type NodeId = string
export type SourceKey = string

/** Document units. Stored geometry is always `pt`; this records author intent. */
export type Unit = 'pt' | 'px' | 'mm' | 'in'

// ---------------------------------------------------------------------------
// Sources & provenance
// ---------------------------------------------------------------------------

/**
 * Where a piece of content came from, and enough information to tell whether it
 * has changed since we last looked.
 *
 * `hash` is the content hash recorded at import time. The backend re-hashes the
 * file on scan; a mismatch means the composite figure is stale. This mirrors
 * Stencila's `CompilationDigest.stateDigest` vs `executionDigest` comparison.
 */
export interface Source {
  /** Path relative to the document file. */
  path: string
  /** `sha256:...` of the file contents when it was imported. */
  hash?: string
  /** Bytes at import time. */
  size?: number
  /** File mtime at import time, ISO 8601. */
  modified?: string
  mediaType?: string
  /** Natural dimensions in pt, used to seed the aspect ratio on insert. */
  intrinsic?: { width: number; height: number }
  provenance?: SourceProvenance
}

export interface SourceProvenance {
  /** Script/notebook that produced the file, relative to the document. */
  generatedBy?: string
  /** Command line used, if known. */
  command?: string
  /** VCS commit the artifact was built from. */
  commit?: string
  /** When figmint first pulled this into the document, ISO 8601. */
  importedAt?: string
  /** Free-form upstream links — DOIs, dataset URLs, ticket refs. */
  derivedFrom?: string[]
}

/** Result of comparing a recorded `Source` against what is on disk right now. */
export type SourceStatus = 'ok' | 'stale' | 'missing' | 'unknown'

// ---------------------------------------------------------------------------
// Nodes
// ---------------------------------------------------------------------------

export interface Rect {
  x: number
  y: number
  width: number
  height: number
}

export interface BaseNode extends Rect {
  id: NodeId
  /** Author-facing name, shown in the layer list. Falls back to a type label. */
  name?: string
  /** Degrees, clockwise, about the box centre. */
  rotation?: number
  opacity?: number
  locked?: boolean
  hidden?: boolean
}

/** A panel: an image/vector artifact pulled in from the local directory. */
export interface ImageNode extends BaseNode {
  type: 'image'
  source: SourceKey
  /** How the artifact fills its box when the box aspect differs from intrinsic. */
  fit?: 'contain' | 'cover' | 'fill'
}

export interface TextStyle {
  fontFamily?: string
  fontSize?: number
  fontWeight?: number | 'normal' | 'bold'
  fontStyle?: 'normal' | 'italic'
  color?: string
  align?: 'left' | 'center' | 'right'
  /** Multiplier on fontSize. */
  lineHeight?: number
}

/** Panel labels, callouts, axis annotations. */
export interface TextNode extends BaseNode {
  type: 'text'
  text: string
  style?: TextStyle
}

/** LaTeX, rendered with KaTeX. `tex` is stored verbatim so it stays editable. */
export interface MathNode extends BaseNode {
  type: 'math'
  tex: string
  /** Block math is centred and displayed; inline is baseline-ish. */
  display?: boolean
  color?: string
  fontSize?: number
}

export interface ShapeStyle {
  fill?: string | null
  stroke?: string | null
  strokeWidth?: number
  /** SVG dash array, e.g. "4 2". */
  strokeDash?: string | null
  cornerRadius?: number
}

export interface RectNode extends BaseNode {
  type: 'rect'
  style?: ShapeStyle
  /** Optional label drawn with the box — maps to `<s:roi-rect label=...>`. */
  label?: string
}

export interface EllipseNode extends BaseNode {
  type: 'ellipse'
  style?: ShapeStyle
  label?: string
}

/**
 * An arrow/leader line. The box is the bounding box; `from`/`to` are normalized
 * (0..1) within it so that resizing the box scales the arrow predictably.
 */
export interface ArrowNode extends BaseNode {
  type: 'arrow'
  from: { x: number; y: number }
  to: { x: number; y: number }
  style?: ShapeStyle
  label?: string
  curve?: 'straight' | 'quad' | 'elbow'
}

export type FigNode =
  | ImageNode
  | TextNode
  | MathNode
  | RectNode
  | EllipseNode
  | ArrowNode

export type NodeType = FigNode['type']

// ---------------------------------------------------------------------------
// Document
// ---------------------------------------------------------------------------

export interface Canvas {
  width: number
  height: number
  units: Unit
  /** `null` means transparent, which is usually what you want for print. */
  background?: string | null
  grid?: { size: number; snap: boolean }
}

export interface FigmintDocument {
  /** Format version, so we can migrate later. */
  figmint: string
  /** Stable id, used as the Stencila figure `#id` and cross-reference target. */
  id: string
  title?: string
  /** Figure caption. Becomes the caption paragraph(s) of the Stencila figure. */
  caption?: string
  canvas: Canvas
  sources: Record<SourceKey, Source>
  /** Paint order: last element is on top. */
  nodes: FigNode[]
}

// ---------------------------------------------------------------------------
// Asset browser (backend-provided view of the local figure directory)
// ---------------------------------------------------------------------------

export interface Asset {
  /** Path relative to the project root. */
  path: string
  name: string
  hash: string
  size: number
  modified: string
  mediaType: string
  intrinsic?: { width: number; height: number }
  provenance?: SourceProvenance
}
