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
  /** Provenance level as assessed when the panel was placed. */
  assessment?: Assessment
  /**
   * Credentials as read at import time. Recorded so the document preserves what
   * was claimed when the panel was placed, even if the file later loses or
   * changes its manifest; the live state is re-read on every scan.
   */
  credentials?: ContentCredentials
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
  /**
   * Tool named in the artifact's signed C2PA creation action. Stronger than
   * `generatedBy`, which is only ever an unsigned assertion in a sidecar.
   */
  signedAgent?: string
}

/**
 * A component asset recorded inside another asset's C2PA manifest.
 *
 * `componentOf` is the relationship that matters here — it is how the standard
 * expresses "this asset was composited from these sources", which is exactly
 * what a figmint composite figure is.
 */
export interface Ingredient {
  title?: string
  format?: string
  relationship?: 'parentOf' | 'componentOf' | 'inputTo' | string
  instanceId?: string
  /** The ingredient carries its own manifest, so the provenance chain nests. */
  hasManifest?: boolean
}

/**
 * How well we know where a component came from.
 *
 * The ordering matters: it is what a project's policy compares against. The
 * step from `generated` to `reproducible` is the meaningful one — a sidecar
 * saying "produced by plot.py" is a self-assertion, whereas a Calkit stage
 * declaring the file as an output can actually be checked.
 */
export type ProvenanceLevel =
  | 'unidentified'
  | 'declared'
  | 'generated'
  | 'reproducible'
  | 'signed'

export const PROVENANCE_ORDER: ProvenanceLevel[] = [
  'unidentified',
  'declared',
  'generated',
  'reproducible',
  'signed',
]

export interface Assessment {
  level: ProvenanceLevel
  label: string
  /** Why this level was assigned — shown so the verdict is auditable. */
  reason: string
}

/** What the project will accept, from `figmint.toml`. */
export interface ProvenancePolicy {
  require: ProvenanceLevel
  /** When false, violations are reported but placement is still allowed. */
  enforce: boolean
}

/**
 * The `org.stencila.provenance` assertion Stencila writes when it signs an
 * asset: a JSON-LD graph of nodes (asset, source document, producing software)
 * and edges recording how one became another.
 */
export interface StencilaProvenance {
  /**
   * Hash of the asset *before* the manifest was embedded. Signing rewrites the
   * file, so this is what lets figmint recognise a signed artifact as the same
   * content it originally placed.
   */
  contentDigest?: string
  producer?: string
  assetType?: string
  /** Stencila's AI-disclosure slot; null when nothing was declared. */
  aiDisclosure?: unknown
  /** Edge kinds in the graph, e.g. `ConvertedInto`, `Generated`. */
  edgeKinds?: string[]
  redactionCount?: number
}

/**
 * C2PA Content Credentials read from an artifact.
 *
 * Unlike the hash comparison — which answers "has this file changed since I
 * placed it?" — credentials answer "who made this, with what, and was any of it
 * AI-generated?", and they are cryptographically bound to the file's contents.
 */
export interface ContentCredentials {
  /**
   * `Trusted` — signer is in a recognised trust list.
   * `Valid` — signature verifies, but the signer is not trusted (the normal
   * state for locally-signed development artifacts).
   * `Invalid` — the file has been altered since signing, or the signature fails.
   */
  validationState?: 'Trusted' | 'Valid' | 'Invalid' | string
  signedBy?: string
  issuer?: string
  signedAt?: string
  /** Tool that wrote the claim, e.g. `Stencila 2.15.0`. */
  claimGenerator?: string
  /** Tool named in the creation action, e.g. `matplotlib 3.9.0`. */
  softwareAgent?: string
  /** Last segment of the IPTC digital source type URI. */
  digitalSourceType?: string
  sourceTypeLabel?: string
  /** True when the source type indicates generative-AI involvement. */
  machineGenerated?: boolean
  actions?: string[]
  ingredients?: Ingredient[]
  /** Non-fatal validation codes, e.g. `signingCredential.untrusted`. */
  warnings?: string[]
  /** Present when the asset was signed by Stencila. */
  stencila?: StencilaProvenance
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

/**
 * How a group arranges its children.
 *
 * `grid` is the general case; `row` and `column` are the one-dimensional
 * shorthands people actually reach for. Track weights are relative, so
 * `[30, 70]` and `[3, 7]` mean the same split — and `[30, 70]` happens to be
 * exactly what Stencila's `Figure.layout` wants, which keeps the export honest.
 */
export interface Layout {
  type: 'grid' | 'row' | 'column'
  /** Columns in a `grid`; ignored for `row` and `column`. */
  columns?: number
  /** Uniform gap, or `[column, row]` in points. */
  gap?: number | [number, number]
  /** Relative track widths. Length must match `columns` to take effect. */
  columnWidths?: number[]
  rowHeights?: number[]
  /** `stretch` fills each cell; `preserve` keeps the panel's aspect ratio. */
  fit?: 'stretch' | 'preserve'
  /** Fill order. Row-major (default) reads like text. */
  order?: 'row-major' | 'column-major'
}

/**
 * A container that positions other nodes.
 *
 * Children are referenced by id and remain top-level nodes, so everything that
 * already walks `doc.nodes` keeps working. When `layout` is set the group owns
 * its children's geometry; when it is null the group is just a way to move
 * several things together.
 */
export interface GroupNode extends BaseNode {
  type: 'group'
  children: NodeId[]
  layout?: Layout | null
  /** Optional frame, mostly useful while arranging. */
  style?: ShapeStyle
}

export type FigNode =
  | ImageNode
  | TextNode
  | MathNode
  | RectNode
  | EllipseNode
  | ArrowNode
  | GroupNode

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
  credentials?: ContentCredentials
  assessment?: Assessment
}
