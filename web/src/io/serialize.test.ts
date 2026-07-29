import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { fromYaml, toYaml, DocumentParseError } from './serialize'
import { toStencilaMarkdown, inferLayout, buildOverlay } from './stencila'
import type { FigmintDocument, ImageNode } from '../model/types'

const EXAMPLE = resolve(__dirname, '../../../examples/two-panel.fig.yaml')

function loadExample(): FigmintDocument {
  return fromYaml(readFileSync(EXAMPLE, 'utf-8'))
}

describe('fig.yaml round trip', () => {
  it('parses the checked-in example', () => {
    const doc = loadExample()
    expect(doc.id).toBe('fig-turbine-performance')
    expect(doc.canvas.width).toBe(468)
    expect(doc.nodes).toHaveLength(6)
    expect(Object.keys(doc.sources)).toEqual(['cp-curve', 'wake-profile'])
  })

  it('survives a serialize/parse cycle unchanged', () => {
    const doc = loadExample()
    const again = fromYaml(toYaml(doc))
    expect(again).toEqual(doc)
  })

  it('is stable on a second pass, so saving twice produces no diff', () => {
    const doc = loadExample()
    const once = toYaml(doc)
    const twice = toYaml(fromYaml(once))
    expect(twice).toBe(once)
  })

  it('keeps LaTeX byte-for-byte through the round trip', () => {
    const doc = loadExample()
    const math = doc.nodes.find((x) => x.type === 'math')
    expect(math).toBeDefined()
    if (math?.type !== 'math') throw new Error('expected a math node')
    expect(math.tex).toBe('C_P = \\frac{P}{\\tfrac{1}{2}\\rho A U_\\infty^3}')

    const again = fromYaml(toYaml(doc)).nodes.find((x) => x.type === 'math')
    if (again?.type !== 'math') throw new Error('expected a math node')
    expect(again.tex).toBe(math.tex)
  })

  it('rounds geometry rather than emitting float noise', () => {
    const doc = loadExample()
    doc.nodes[0].x = 12.000000000000002
    doc.nodes[0].y = 36.987654321
    const yaml = toYaml(doc)
    expect(yaml).toContain('x: 12')
    expect(yaml).toContain('y: 36.99')
    expect(yaml).not.toContain('12.000000000000002')
  })

  it('rejects a document with no format version', () => {
    expect(() => fromYaml('id: nope\ncanvas: {}')).toThrow(DocumentParseError)
  })

  it('reports bad YAML as a parse error rather than crashing', () => {
    expect(() => fromYaml('figmint: "0.1"\n  bad: [indent')).toThrow(
      DocumentParseError,
    )
  })
})

describe('layout inference', () => {
  const panel = (x: number, y: number, w = 100, h = 80): ImageNode => ({
    id: `p-${x}-${y}`,
    type: 'image',
    source: 's',
    x,
    y,
    width: w,
    height: h,
  })

  const docWith = (nodes: ImageNode[]): FigmintDocument => ({
    figmint: '0.1',
    id: 'f',
    canvas: { width: 400, height: 300, units: 'pt' },
    sources: { s: { path: 'a.svg' } },
    nodes,
  })

  it('emits no layout for a single panel', () => {
    expect(inferLayout(docWith([panel(0, 0)])).layout).toBeNull()
  })

  it('detects a single row of equal panels', () => {
    const result = inferLayout(docWith([panel(0, 0), panel(120, 0)]))
    expect(result.layout).toBe('[2]')
    expect(result.order.map((x) => x.x)).toEqual([0, 120])
  })

  it('emits width ratios when a row is unevenly split', () => {
    const result = inferLayout(docWith([panel(0, 0, 90), panel(100, 0, 210)]))
    expect(result.layout).toBe('[30 70]')
  })

  it('orders panels left-to-right then top-to-bottom', () => {
    // Deliberately out of order in the document.
    const result = inferLayout(
      docWith([panel(120, 100), panel(0, 0), panel(120, 0), panel(0, 100)]),
    )
    expect(result.order.map((p) => [p.x, p.y])).toEqual([
      [0, 0],
      [120, 0],
      [0, 100],
      [120, 100],
    ])
    expect(result.layout).toBe('[2]')
  })

  it('falls back to a placement map for ragged rows', () => {
    const result = inferLayout(
      docWith([panel(0, 0), panel(120, 0), panel(0, 100, 220)]),
    )
    expect(result.layout).toBe('[a b | c c]')
    expect(result.note).toMatch(/placement map/)
  })

  it('ignores annotations when arranging panels', () => {
    const doc = docWith([panel(0, 0), panel(120, 0)])
    doc.nodes.push({
      id: 't',
      type: 'text',
      text: '(a)',
      x: 0,
      y: 0,
      width: 10,
      height: 10,
    })
    expect(inferLayout(doc).order).toHaveLength(2)
  })
})

describe('Stencila overlay', () => {
  it('emits s: components for labelled annotations and plain SVG otherwise', () => {
    const doc = loadExample()
    const overlay = buildOverlay(doc)
    expect(overlay).toContain('xmlns:s="https://stencila.io/svg"')
    expect(overlay).toContain(`viewBox="0 0 468 210"`)
    // The example's ROI box carries a label, so it becomes a Stencila component.
    expect(overlay).toContain('<s:roi-rect')
    expect(overlay).toContain('label="Peak"')
    // Panel labels are plain SVG text.
    expect(overlay).toContain('<text')
    expect(overlay).toContain('(a)')
    // Images belong to figure content, never the overlay.
    expect(overlay).not.toContain('<image')
  })

  it('escapes XML metacharacters in text', () => {
    const doc = loadExample()
    doc.nodes.push({
      id: 'x',
      type: 'text',
      text: 'a < b & "c"',
      x: 0,
      y: 0,
      width: 10,
      height: 10,
    })
    const overlay = buildOverlay(doc) ?? ''
    expect(overlay).toContain('a &lt; b &amp; &quot;c&quot;')
    expect(overlay).not.toContain('a < b & "c"')
  })

  it('renders LaTeX to MathML rather than dropping it', () => {
    const doc = loadExample()
    const overlay = buildOverlay(doc) ?? ''
    expect(overlay).toContain('<foreignObject')
    expect(overlay).toContain('<math')
    expect(overlay).toContain('data-tex=')
  })

  it('returns null when there is nothing to annotate', () => {
    const doc = loadExample()
    doc.nodes = doc.nodes.filter((x) => x.type === 'image')
    expect(buildOverlay(doc)).toBeNull()
  })
})

describe('Stencila Markdown export', () => {
  it('produces a figure fence with the document id and inferred layout', () => {
    const { smd } = toStencilaMarkdown(loadExample())
    expect(smd).toContain('::: figure #fig-turbine-performance [2]')
    expect(smd.trimEnd().endsWith(':::')).toBe(true)
  })

  it('references source paths, not internal source keys', () => {
    const { smd } = toStencilaMarkdown(loadExample())
    expect(smd).toContain('![](figures/cp_curve.svg)')
    expect(smd).toContain('![](figures/wake_profile.svg)')
    expect(smd).not.toContain('![](cp-curve)')
  })

  it('carries the caption into the figure body', () => {
    const { smd } = toStencilaMarkdown(loadExample())
    expect(smd).toContain('Power coefficient versus tip speed ratio')
  })

  it('round-trips exact geometry through the frontmatter', () => {
    const { smd } = toStencilaMarkdown(loadExample())
    const frontmatter = smd.split('---')[1]
    expect(frontmatter).toContain('figmint:')
    // The precise per-node geometry Stencila has no schema slot for.
    expect(frontmatter).toContain('id: panel-a')
    expect(frontmatter).toContain('x: 12')
    expect(frontmatter).toContain('cp-curve')
  })

  it('emits the overlay as a fenced svg overlay block', () => {
    const { smd } = toStencilaMarkdown(loadExample())
    expect(smd).toContain('```svg overlay')
  })
})

describe('explicit group layouts', () => {
  const withGroup = (layout: import('../model/types').Layout) => {
    const doc = loadExample()
    doc.nodes.push({
      id: 'panels',
      type: 'group',
      children: ['panel-a', 'panel-b'],
      layout,
      x: 12,
      y: 36,
      width: 444,
      height: 162,
    })
    return doc
  }

  it('uses the declared grid instead of inferring one', () => {
    const result = inferLayout(withGroup({ type: 'grid', columns: 2 }))
    expect(result.layout).toBe('[2]')
  })

  it('emits declared column weights as Stencila ratios', () => {
    const result = inferLayout(
      withGroup({ type: 'grid', columns: 2, columnWidths: [30, 70] }),
    )
    expect(result.layout).toBe('[30 70]')
  })

  it('treats weights as relative when converting', () => {
    const result = inferLayout(
      withGroup({ type: 'grid', columns: 2, columnWidths: [3, 7] }),
    )
    expect(result.layout).toBe('[30 70]')
  })

  it('emits a row layout', () => {
    expect(inferLayout(withGroup({ type: 'row' })).layout).toBe('[row]')
  })

  it('column layouts fall through to Stencila stacking', () => {
    expect(inferLayout(withGroup({ type: 'column' })).layout).toBeNull()
  })

  it('orders panels by the group, not by position', () => {
    const doc = loadExample()
    doc.nodes.push({
      id: 'panels',
      type: 'group',
      // Deliberately reversed relative to canvas order.
      children: ['panel-b', 'panel-a'],
      layout: { type: 'grid', columns: 2 },
      x: 12,
      y: 36,
      width: 444,
      height: 162,
    })
    expect(inferLayout(doc).order.map((p) => p.id)).toEqual([
      'panel-b',
      'panel-a',
    ])
  })

  it('notes panels left outside the group', () => {
    const doc = loadExample()
    doc.nodes.push({
      id: 'panels',
      type: 'group',
      children: ['panel-a'],
      layout: { type: 'grid', columns: 1 },
      x: 12,
      y: 36,
      width: 216,
      height: 162,
    })
    const result = inferLayout(doc)
    expect(result.order).toHaveLength(2)
    expect(result.note).toMatch(/outside the group/)
  })

  it('groups contribute nothing to the overlay', () => {
    const overlay = buildOverlay(withGroup({ type: 'grid', columns: 2 })) ?? ''
    expect(overlay).not.toContain('panels')
  })
})
