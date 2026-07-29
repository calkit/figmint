import { describe, expect, it } from 'vitest'
import {
  applyLayouts,
  boundsForGrid,
  cellIndexAt,
  gapsOf,
  isLaidOut,
  placeInCell,
  reorderChild,
  selectionTargetFor,
  solveLayout,
  solveTracks,
  solveWithPending,
  suggestColumns,
} from './layout'
import type { FigNode, GroupNode, ImageNode, Layout } from './types'

const BOUNDS = { x: 0, y: 0, width: 100, height: 100 }

const panel = (id: string, w = 40, h = 40): ImageNode => ({
  id,
  type: 'image',
  source: 's',
  x: 0,
  y: 0,
  width: w,
  height: h,
})

const group = (children: string[], layout: Layout | null): GroupNode => ({
  id: 'g',
  type: 'group',
  children,
  layout,
  ...BOUNDS,
})

describe('solveTracks', () => {
  it('splits evenly with no weights', () => {
    expect(solveTracks(100, 4, 0)).toEqual([25, 25, 25, 25])
  })

  it('subtracts gaps from the available space', () => {
    // 100 - 2 gaps of 10 = 80, split three ways.
    const tracks = solveTracks(100, 3, 10)
    expect(tracks.every((t) => Math.abs(t - 80 / 3) < 1e-9)).toBe(true)
  })

  it('honours proportional weights', () => {
    expect(solveTracks(100, 2, 0, [30, 70])).toEqual([30, 70])
  })

  it('treats weights as relative, not absolute', () => {
    // [3, 7] must mean the same as [30, 70].
    expect(solveTracks(100, 2, 0, [3, 7])).toEqual(solveTracks(100, 2, 0, [30, 70]))
  })

  it('falls back to equal tracks when weights do not match the count', () => {
    expect(solveTracks(100, 3, 0, [50, 50])).toEqual([100 / 3, 100 / 3, 100 / 3])
  })

  it('ignores non-positive weights rather than producing negative tracks', () => {
    expect(solveTracks(100, 2, 0, [0, 100])).toEqual([50, 50])
  })

  it('never returns negative sizes when gaps exceed the space', () => {
    expect(solveTracks(10, 3, 100).every((t) => t >= 0)).toBe(true)
  })

  it('returns nothing for a zero count', () => {
    expect(solveTracks(100, 0, 0)).toEqual([])
  })
})

describe('gapsOf', () => {
  it('reads a scalar gap as both axes', () => {
    expect(gapsOf({ type: 'grid', gap: 8 })).toEqual({ column: 8, row: 8 })
  })
  it('reads a pair as [column, row]', () => {
    expect(gapsOf({ type: 'grid', gap: [4, 12] })).toEqual({
      column: 4,
      row: 12,
    })
  })
  it('defaults to no gap', () => {
    expect(gapsOf({ type: 'grid' })).toEqual({ column: 0, row: 0 })
  })
})

describe('solveLayout', () => {
  it('lays a 2x2 grid out in reading order', () => {
    const cells = solveLayout(BOUNDS, { type: 'grid', columns: 2 }, 4)
    expect(cells.map((c) => [c.x, c.y])).toEqual([
      [0, 0],
      [50, 0],
      [0, 50],
      [50, 50],
    ])
  })

  it('puts a row on one line', () => {
    const cells = solveLayout(BOUNDS, { type: 'row' }, 4)
    expect(cells.every((c) => c.y === 0)).toBe(true)
    expect(cells.map((c) => c.width)).toEqual([25, 25, 25, 25])
  })

  it('stacks a column', () => {
    const cells = solveLayout(BOUNDS, { type: 'column' }, 4)
    expect(cells.every((c) => c.x === 0)).toBe(true)
    expect(cells.map((c) => c.height)).toEqual([25, 25, 25, 25])
  })

  it('applies column weights', () => {
    const cells = solveLayout(
      BOUNDS,
      { type: 'grid', columns: 2, columnWidths: [30, 70] },
      2,
    )
    expect(cells[0].width).toBe(30)
    expect(cells[1].width).toBe(70)
    expect(cells[1].x).toBe(30)
  })

  it('fills column-major when asked', () => {
    const cells = solveLayout(
      BOUNDS,
      { type: 'grid', columns: 2, order: 'column-major' },
      4,
    )
    // Down the first column, then down the second.
    expect(cells.map((c) => [c.x, c.y])).toEqual([
      [0, 0],
      [0, 50],
      [50, 0],
      [50, 50],
    ])
  })

  it('adds rows rather than dropping panels that overflow', () => {
    // Three panels in a 2-column grid needs two rows, not a lost panel.
    const cells = solveLayout(BOUNDS, { type: 'grid', columns: 2 }, 3)
    expect(cells).toHaveLength(3)
    expect(cells[2].y).toBeGreaterThan(0)
  })

  it('offsets by the group origin', () => {
    const cells = solveLayout(
      { x: 10, y: 20, width: 100, height: 100 },
      { type: 'grid', columns: 2 },
      2,
    )
    expect(cells[0].x).toBe(10)
    expect(cells[0].y).toBe(20)
  })

  it('returns nothing for no children', () => {
    expect(solveLayout(BOUNDS, { type: 'grid', columns: 2 }, 0)).toEqual([])
  })
})

describe('placeInCell', () => {
  const cell = { x: 0, y: 0, width: 100, height: 50 }

  it('stretch fills the cell exactly', () => {
    expect(placeInCell(cell, { x: 0, y: 0, width: 10, height: 10 }, 'stretch'))
      .toEqual(cell)
  })

  it('preserve keeps the aspect ratio', () => {
    const placed = placeInCell(
      cell,
      { x: 0, y: 0, width: 40, height: 40 },
      'preserve',
    )
    expect(placed.width).toBe(placed.height)
  })

  it('preserve centres within the cell', () => {
    const placed = placeInCell(
      cell,
      { x: 0, y: 0, width: 40, height: 40 },
      'preserve',
    )
    // A square in a 100x50 cell is 50x50, centred horizontally.
    expect(placed.width).toBe(50)
    expect(placed.x).toBe(25)
    expect(placed.y).toBe(0)
  })

  it('never overflows the cell', () => {
    const placed = placeInCell(
      cell,
      { x: 0, y: 0, width: 400, height: 10 },
      'preserve',
    )
    expect(placed.width).toBeLessThanOrEqual(cell.width)
    expect(placed.height).toBeLessThanOrEqual(cell.height)
  })

  it('degenerate children fall back to the cell', () => {
    expect(placeInCell(cell, { x: 0, y: 0, width: 0, height: 0 }, 'preserve'))
      .toEqual(cell)
  })
})

describe('applyLayouts', () => {
  it('positions the children of a laid-out group', () => {
    const nodes: FigNode[] = [
      group(['a', 'b'], { type: 'grid', columns: 2, fit: 'stretch' }),
      panel('a'),
      panel('b'),
    ]
    const updates = applyLayouts(nodes)
    expect(updates.a).toEqual({ x: 0, y: 0, width: 50, height: 100 })
    expect(updates.b).toEqual({ x: 50, y: 0, width: 50, height: 100 })
  })

  it('leaves a group without a layout alone', () => {
    const nodes: FigNode[] = [group(['a', 'b'], null), panel('a'), panel('b')]
    expect(applyLayouts(nodes)).toEqual({})
  })

  it('hiding a panel leaves a hole rather than reflowing the grid', () => {
    // The author asked for two columns. Silently widening the survivor to fill
    // the row would change a layout they explicitly specified — a visible gap
    // is more predictable, and undoing the hide restores exactly what was there.
    const hidden = { ...panel('b'), hidden: true }
    const nodes: FigNode[] = [
      group(['a', 'b'], { type: 'grid', columns: 2, fit: 'stretch' }),
      panel('a'),
      hidden,
    ]
    const updates = applyLayouts(nodes)
    expect(updates.a.width).toBe(50)
    expect(updates.b).toBeUndefined()
  })

  it('tolerates children that no longer exist', () => {
    const nodes: FigNode[] = [
      group(['a', 'ghost'], { type: 'grid', columns: 2, fit: 'stretch' }),
      panel('a'),
    ]
    expect(() => applyLayouts(nodes)).not.toThrow()
    // Same reasoning: the declared column count survives a missing child.
    expect(applyLayouts(nodes).a.width).toBe(50)
  })

  it('reports nothing when children are already where they belong', () => {
    const nodes: FigNode[] = [
      group(['a'], { type: 'grid', columns: 1, fit: 'stretch' }),
      { ...panel('a'), x: 0, y: 0, width: 100, height: 100 },
    ]
    expect(applyLayouts(nodes)).toEqual({})
  })

  it('is idempotent — solving twice changes nothing the second time', () => {
    const nodes: FigNode[] = [
      group(['a', 'b'], { type: 'grid', columns: 2, fit: 'stretch' }),
      panel('a'),
      panel('b'),
    ]
    const first = applyLayouts(nodes)
    const settled = nodes.map((n) =>
      first[n.id] ? { ...n, ...first[n.id] } : n,
    ) as FigNode[]
    expect(applyLayouts(settled)).toEqual({})
  })
})

describe('reordering', () => {
  it('moves a child to a new slot', () => {
    const g = group(['a', 'b', 'c'], { type: 'row' })
    expect(reorderChild(g, 'c', 0)).toEqual(['c', 'a', 'b'])
  })

  it('clamps an out-of-range index', () => {
    const g = group(['a', 'b'], { type: 'row' })
    expect(reorderChild(g, 'a', 99)).toEqual(['b', 'a'])
  })

  it('leaves the order alone for an unknown child', () => {
    const g = group(['a', 'b'], { type: 'row' })
    expect(reorderChild(g, 'zzz', 0)).toEqual(['a', 'b'])
  })

  it('finds the cell under a point', () => {
    const g = group(['a', 'b'], { type: 'grid', columns: 2 })
    expect(cellIndexAt(g, 2, { x: 75, y: 10 })).toBe(1)
    expect(cellIndexAt(g, 2, { x: 10, y: 10 })).toBe(0)
  })

  it('returns null outside every cell', () => {
    const g = group(['a'], { type: 'grid', columns: 1 })
    expect(cellIndexAt(g, 1, { x: 500, y: 500 })).toBeNull()
  })
})

describe('isLaidOut', () => {
  it('is true for a child of a laid-out group', () => {
    const nodes: FigNode[] = [group(['a'], { type: 'row' }), panel('a')]
    expect(isLaidOut(nodes, 'a')).toBe(true)
  })

  it('is false when the group has no layout', () => {
    const nodes: FigNode[] = [group(['a'], null), panel('a')]
    expect(isLaidOut(nodes, 'a')).toBe(false)
  })

  it('is false for an ungrouped node', () => {
    expect(isLaidOut([panel('a')], 'a')).toBe(false)
  })
})

describe('suggestColumns', () => {
  const at = (x: number, y: number) => ({ x, y, width: 100, height: 80 })

  it('a single row becomes one column per panel', () => {
    expect(suggestColumns([at(0, 0), at(150, 0), at(300, 0)])).toBe(3)
  })

  it('a single column stays one column', () => {
    expect(suggestColumns([at(0, 0), at(0, 150), at(0, 300)])).toBe(1)
  })

  it('a 2x2 arrangement gives two columns', () => {
    expect(suggestColumns([at(0, 0), at(150, 0), at(0, 150), at(150, 150)])).toBe(2)
  })

  it('bands rows by overlap, not exact equality', () => {
    // Slightly misaligned panels are still one row.
    expect(suggestColumns([at(0, 0), at(150, 4)])).toBe(2)
  })

  it('one panel needs no grid', () => {
    expect(suggestColumns([at(0, 0)])).toBe(1)
  })
})

describe('boundsForGrid', () => {
  const rects = [
    { x: 0, y: 0, width: 100, height: 80 },
    { x: 500, y: 0, width: 100, height: 80 },
  ]

  it('sizes to the panels, not to the space between them', () => {
    // The scattered bounding box is 600 wide; a tight 2-column grid is not.
    const bounds = boundsForGrid(
      rects,
      { type: 'grid', columns: 2, gap: 6 },
      { x: 0, y: 0 },
    )
    expect(bounds.width).toBe(206)
    expect(bounds.height).toBe(80)
  })

  it('accounts for wrapped rows', () => {
    const bounds = boundsForGrid(
      [...rects, { x: 0, y: 0, width: 100, height: 80 }],
      { type: 'grid', columns: 2, gap: 6 },
      { x: 0, y: 0 },
    )
    expect(bounds.height).toBe(166) // two rows of 80 plus one 6pt gap
  })

  it('uses the largest panel as the cell size', () => {
    const bounds = boundsForGrid(
      [
        { x: 0, y: 0, width: 100, height: 80 },
        { x: 0, y: 0, width: 250, height: 40 },
      ],
      { type: 'grid', columns: 2, gap: 0 },
      { x: 0, y: 0 },
    )
    expect(bounds.width).toBe(500)
  })
})

describe('solveWithPending', () => {
  it('moves children while a group is being resized', () => {
    // The bug: the frame grew during the drag while panels sat frozen until
    // the pointer was released.
    const nodes: FigNode[] = [
      group(['a', 'b'], { type: 'grid', columns: 2, fit: 'stretch' }),
      panel('a'),
      panel('b'),
    ]
    const solved = solveWithPending(nodes, {
      g: { x: 0, y: 0, width: 200, height: 100 },
    })
    expect(solved.a.width).toBe(100)
    expect(solved.b.x).toBe(100)
  })

  it('the solver overrides a pending position it disagrees with', () => {
    const nodes: FigNode[] = [
      group(['a'], { type: 'grid', columns: 1, fit: 'stretch' }),
      panel('a'),
    ]
    const solved = solveWithPending(nodes, {
      a: { x: 999, y: 999, width: 5, height: 5 },
    })
    expect(solved.a).toEqual({ x: 0, y: 0, width: 100, height: 100 })
  })

  it('leaves ungoverned nodes exactly where the drag put them', () => {
    const nodes: FigNode[] = [panel('a')]
    const pending = { a: { x: 7, y: 9, width: 40, height: 40 } }
    expect(solveWithPending(nodes, pending).a).toEqual(pending.a)
  })
})

describe('selectionTargetFor', () => {
  const nodes: FigNode[] = [
    group(['a', 'b'], { type: 'row' }),
    panel('a'),
    panel('b'),
  ]

  it('clicking a panel grabs its group', () => {
    // Otherwise you cannot pick up an arrangement without finding a gap.
    expect(selectionTargetFor(nodes, 'a', [])).toBe('g')
  })

  it('clicking again drills in to the panel', () => {
    expect(selectionTargetFor(nodes, 'a', ['g'])).toBe('a')
  })

  it('a panel already selected stays selected', () => {
    expect(selectionTargetFor(nodes, 'a', ['a'])).toBe('a')
  })

  it('an ungrouped node selects itself', () => {
    expect(selectionTargetFor([panel('z')], 'z', [])).toBe('z')
  })

  it('picks the outermost of nested groups', () => {
    const nested: FigNode[] = [
      { ...group(['inner'], null), id: 'outer', children: ['inner'] },
      { ...group(['a'], { type: 'row' }), id: 'inner', children: ['a'] },
      panel('a'),
    ]
    expect(selectionTargetFor(nested, 'a', [])).toBe('outer')
  })

  it('survives a cycle in hand-edited YAML', () => {
    const cyclic: FigNode[] = [
      { ...group(['g2'], null), id: 'g1', children: ['g2'] },
      { ...group(['g1'], null), id: 'g2', children: ['g1'] },
    ]
    expect(() => selectionTargetFor(cyclic, 'g1', [])).not.toThrow()
  })
})
