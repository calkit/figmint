import { describe, expect, it } from 'vitest'
import {
  applyLayouts,
  cellIndexAt,
  gapsOf,
  isLaidOut,
  placeInCell,
  reorderChild,
  solveLayout,
  solveTracks,
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
