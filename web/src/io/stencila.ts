import katex from 'katex'
import { stringify } from 'yaml'
import type {
  ArrowNode,
  EllipseNode,
  FigmintDocument,
  FigNode,
  ImageNode,
  MathNode,
  RectNode,
  TextNode,
} from '../model/types'

/**
 * Export a figmint document to Stencila Markdown (`.smd`).
 *
 * Mapping strategy
 * ----------------
 * Stencila's schema (v2.15) already covers most of what we need, so we lean on
 * native constructs rather than inventing our own:
 *
 *   composite figure  ->  `::: figure #id [layout]` with one nested `::: figure`
 *                         per image panel
 *   panel arrangement ->  `Figure.layout` grid mini-language, inferred from the
 *                         canvas positions (e.g. `[50 50 : a b | c c]`)
 *   annotations       ->  `Figure.overlay`, a ```svg overlay fenced block using
 *                         Stencila's `s:` component namespace for arrows,
 *                         callouts and ROI boxes, and plain SVG for the rest
 *   caption           ->  the trailing paragraph(s) of the figure
 *
 * The one thing Stencila has no home for is free-form per-panel geometry:
 * `ImageObject` has no x/y/width/height and there is no per-node `extra` bag.
 * So the exact figmint document is round-tripped through the article's YAML
 * frontmatter under a `figmint:` key — `Article.extra` preserves unknown
 * frontmatter keys across md/smd/myst/qmd/docx. Stencila renders the grid
 * approximation; figmint reads the frontmatter and restores pixel positions.
 */

// ---------------------------------------------------------------------------
// XML helpers
// ---------------------------------------------------------------------------

function esc(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

function n(value: number): string {
  return String(Math.round(value * 100) / 100)
}

function attrs(pairs: Record<string, string | number | null | undefined>) {
  return Object.entries(pairs)
    .filter(([, v]) => v !== null && v !== undefined && v !== '')
    .map(([k, v]) => `${k}="${typeof v === 'number' ? n(v) : esc(String(v))}"`)
    .join(' ')
}

// ---------------------------------------------------------------------------
// Layout inference
// ---------------------------------------------------------------------------

export interface InferredLayout {
  /** The `[...]` string, or null when a grid is a poor fit. */
  layout: string | null
  /** Image nodes in reading order — the order panels must be emitted in. */
  order: ImageNode[]
  /** Why we fell back, for surfacing in the UI. */
  note?: string
}

/**
 * Infer a Stencila grid layout from absolute panel positions.
 *
 * Panels are banded into rows by vertical overlap, then ordered left-to-right
 * within each row. If every row has the same number of panels we emit column
 * width ratios; otherwise we emit a placement map (`a b | c c`) so uneven rows
 * still render sensibly.
 *
 * This is a lossy approximation by design. The authoritative geometry travels in
 * the frontmatter; the grid is what non-figmint Stencila consumers see.
 */
export function inferLayout(doc: FigmintDocument): InferredLayout {
  const images = doc.nodes.filter((x): x is ImageNode => x.type === 'image')
  if (images.length === 0) return { layout: null, order: [] }
  if (images.length === 1) return { layout: null, order: images }

  const sorted = [...images].sort((a, b) => a.y - b.y || a.x - b.x)
  const rows: ImageNode[][] = []
  for (const node of sorted) {
    const row = rows.find((r) => {
      const top = Math.min(...r.map((x) => x.y))
      const bottom = Math.max(...r.map((x) => x.y + x.height))
      const overlap =
        Math.min(bottom, node.y + node.height) - Math.max(top, node.y)
      // Count as the same row when they share more than half of the shorter height.
      return overlap > Math.min(node.height, bottom - top) * 0.5
    })
    if (row) row.push(node)
    else rows.push([node])
  }
  for (const row of rows) row.sort((a, b) => a.x - b.x)

  const order = rows.flat()
  const widths = rows.map((r) => r.length)
  const cols = Math.max(...widths)

  if (widths.every((w) => w === cols)) {
    // Uniform grid — express the column proportions from the first row.
    const first = rows[0]
    const total = first.reduce((sum, x) => sum + x.width, 0)
    if (total <= 0) return { layout: `[${cols}]`, order }
    const ratios = first.map((x) => Math.round((x.width / total) * 100))
    // Fix rounding drift so the ratios sum to 100.
    const drift = 100 - ratios.reduce((a, b) => a + b, 0)
    ratios[ratios.length - 1] += drift
    const uniform = ratios.every((r) => Math.abs(r - 100 / cols) <= 2)
    return { layout: uniform ? `[${cols}]` : `[${ratios.join(' ')}]`, order }
  }

  // Ragged rows: build a placement map, spanning short rows across the grid.
  const letters = 'abcdefghijklmnopqrstuvwxyz'
  let i = 0
  const mapRows = rows.map((row) => {
    const cells: string[] = []
    const span = Math.floor(cols / row.length)
    for (const _ of row) {
      const letter = letters[i] ?? '.'
      i += 1
      for (let s = 0; s < span; s += 1) cells.push(letter)
    }
    while (cells.length < cols) cells.push('.')
    return cells.join(' ')
  })
  return {
    layout: `[${mapRows.join(' | ')}]`,
    order,
    note: 'Rows have differing panel counts; emitted a placement map.',
  }
}

// ---------------------------------------------------------------------------
// Overlay SVG
// ---------------------------------------------------------------------------

function textSvg(node: TextNode): string {
  const st = node.style ?? {}
  const size = st.fontSize ?? 9
  const anchor =
    st.align === 'center' ? 'middle' : st.align === 'right' ? 'end' : 'start'
  const x =
    st.align === 'center'
      ? node.x + node.width / 2
      : st.align === 'right'
        ? node.x + node.width
        : node.x
  const lines = node.text.split('\n')
  const lineHeight = size * (st.lineHeight ?? 1.2)
  // Baseline of the first line sits one ascent below the box top.
  const body = lines
    .map(
      (line, idx) =>
        `<tspan ${attrs({ x, dy: idx === 0 ? 0 : lineHeight })}>${esc(line)}</tspan>`,
    )
    .join('')
  return `<text ${attrs({
    x,
    y: node.y + size,
    'text-anchor': anchor,
    'font-family': st.fontFamily ?? 'Helvetica, Arial, sans-serif',
    'font-size': size,
    'font-weight': st.fontWeight ?? null,
    'font-style': st.fontStyle === 'italic' ? 'italic' : null,
    fill: st.color ?? '#111111',
    opacity: node.opacity ?? null,
  })}>${body}</text>`
}

/**
 * Math is rendered to MathML at export time and wrapped in a `<foreignObject>`.
 *
 * Caveat worth knowing: `foreignObject` renders in browsers (so HTML output is
 * fine) but many SVG->PDF paths drop it. The TeX source is preserved verbatim in
 * the frontmatter, so a future exporter can swap this for real vector glyphs
 * without any loss.
 */
function mathSvg(node: MathNode): string {
  let mathml: string
  try {
    mathml = katex.renderToString(node.tex, {
      output: 'mathml',
      throwOnError: false,
      displayMode: node.display ?? false,
    })
  } catch {
    mathml = `<span>${esc(node.tex)}</span>`
  }
  return `<foreignObject ${attrs({
    x: node.x,
    y: node.y,
    width: node.width,
    height: node.height,
  })}><div xmlns="http://www.w3.org/1999/xhtml" ${attrs({
    style: `font-size:${n(node.fontSize ?? 10)}pt;color:${node.color ?? '#111111'}`,
    'data-tex': node.tex,
  })}>${mathml}</div></foreignObject>`
}

function rectSvg(node: RectNode): string {
  const st = node.style ?? {}
  // Labelled boxes become Stencila ROI components, which draw their own
  // label chrome; unlabelled ones are plain SVG and pass through untouched.
  if (node.label) {
    return `<s:roi-rect ${attrs({
      x: node.x,
      y: node.y,
      width: node.width,
      height: node.height,
      label: node.label,
      stroke: st.stroke ?? '#d1495b',
      'stroke-style': st.strokeDash ? 'dashed' : null,
      'stroke-width': st.strokeWidth ?? null,
    })}/>`
  }
  return `<rect ${attrs({
    x: node.x,
    y: node.y,
    width: node.width,
    height: node.height,
    rx: st.cornerRadius ?? null,
    fill: st.fill ?? 'none',
    stroke: st.stroke ?? '#111111',
    'stroke-width': st.strokeWidth ?? 1,
    'stroke-dasharray': st.strokeDash ?? null,
    opacity: node.opacity ?? null,
  })}/>`
}

function ellipseSvg(node: EllipseNode): string {
  const st = node.style ?? {}
  if (node.label) {
    return `<s:roi-ellipse ${attrs({
      cx: node.x + node.width / 2,
      cy: node.y + node.height / 2,
      rx: node.width / 2,
      ry: node.height / 2,
      label: node.label,
      stroke: st.stroke ?? '#d1495b',
    })}/>`
  }
  return `<ellipse ${attrs({
    cx: node.x + node.width / 2,
    cy: node.y + node.height / 2,
    rx: node.width / 2,
    ry: node.height / 2,
    fill: st.fill ?? 'none',
    stroke: st.stroke ?? '#111111',
    'stroke-width': st.strokeWidth ?? 1,
    'stroke-dasharray': st.strokeDash ?? null,
    opacity: node.opacity ?? null,
  })}/>`
}

function arrowSvg(node: ArrowNode): string {
  const st = node.style ?? {}
  return `<s:arrow ${attrs({
    x: node.x + node.from.x * node.width,
    y: node.y + node.from.y * node.height,
    'to-x': node.x + node.to.x * node.width,
    'to-y': node.y + node.to.y * node.height,
    curve: node.curve && node.curve !== 'straight' ? node.curve : null,
    label: node.label ?? null,
    stroke: st.stroke ?? '#111111',
    'stroke-width': st.strokeWidth ?? null,
  })}/>`
}

function overlayElement(node: FigNode): string | null {
  if (node.hidden) return null
  switch (node.type) {
    case 'text':
      return textSvg(node)
    case 'math':
      return mathSvg(node)
    case 'rect':
      return rectSvg(node)
    case 'ellipse':
      return ellipseSvg(node)
    case 'arrow':
      return arrowSvg(node)
    case 'image':
      return null // images are figure content, not overlay
  }
}

export function buildOverlay(doc: FigmintDocument): string | null {
  const elements = doc.nodes
    .map(overlayElement)
    .filter((x): x is string => x !== null)
  if (elements.length === 0) return null
  const open = `<svg viewBox="0 0 ${n(doc.canvas.width)} ${n(doc.canvas.height)}" xmlns="http://www.w3.org/2000/svg" xmlns:s="https://stencila.io/svg">`
  return [open, ...elements.map((e) => `  ${e}`), '</svg>'].join('\n')
}

// ---------------------------------------------------------------------------
// Document export
// ---------------------------------------------------------------------------

function indent(block: string, by = '  '): string {
  return block
    .split('\n')
    .map((line) => (line.length > 0 ? by + line : line))
    .join('\n')
}

function panelBlock(node: ImageNode, doc: FigmintDocument): string {
  const source = doc.sources[node.source]
  const path = source?.path ?? node.source
  const caption = node.name ? `\n${node.name}\n` : ''
  return [`::: figure`, ``, `![](${path})`, caption, `:::`].join('\n')
}

export interface StencilaExport {
  smd: string
  layout: InferredLayout
}

export function toStencilaMarkdown(doc: FigmintDocument): StencilaExport {
  const layout = inferLayout(doc)
  const overlay = buildOverlay(doc)

  // Frontmatter: standard article metadata plus the figmint round-trip payload.
  // Unknown keys land in `Article.extra` and survive md/smd/myst/qmd round-trips.
  const frontmatter = stringify(
    {
      title: doc.title ?? 'Untitled figure',
      figmint: {
        format: doc.figmint,
        note: 'Authoritative geometry for the figmint editor. Stencila renders the grid layout above; figmint restores exact positions from here.',
        canvas: doc.canvas,
        sources: doc.sources,
        nodes: doc.nodes,
      },
    },
    { lineWidth: 88 },
  ).trimEnd()

  const parts: string[] = ['---', frontmatter, '---', '']

  const fenceHeader = ['::: figure', `#${doc.id}`, layout.layout]
    .filter(Boolean)
    .join(' ')
  parts.push(fenceHeader, '')

  if (layout.order.length === 1) {
    const only = layout.order[0]
    const source = doc.sources[only.source]
    parts.push(`![](${source?.path ?? only.source})`, '')
  } else {
    for (const panel of layout.order) {
      parts.push(indent(panelBlock(panel, doc)), '')
    }
  }

  if (overlay) {
    parts.push('```svg overlay', overlay, '```', '')
  }

  if (doc.caption?.trim()) {
    parts.push(doc.caption.trim(), '')
  }

  parts.push(':::', '')
  return { smd: parts.join('\n'), layout }
}
