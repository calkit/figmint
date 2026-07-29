import type { FigNode, GroupNode, Layout, NodeId, Rect } from './types'

/**
 * Layout solving.
 *
 * A group with a layout owns its children's geometry: you set the grid, and the
 * solver decides where each panel goes. Without one, a group is just a moveable
 * bundle and children keep whatever coordinates they were dragged to.
 *
 * Children stay *top-level nodes* referenced by id rather than being nested
 * inside the group. That keeps rendering, hit-testing, selection and export
 * working exactly as before — the solver's only job is to write x/y/w/h. Nesting
 * would have meant recursion through every one of those paths for no gain the
 * document format actually needs.
 *
 * The solver is pure: (group rect, layout, child count) -> rects. That makes it
 * testable without a canvas, and it means the same function can run in the
 * editor, in the exporter, and in a headless build.
 */

/** Resolve `gap` in either scalar or [column, row] form. */
export function gapsOf(layout: Layout): { column: number; row: number } {
  const gap = layout.gap ?? 0
  return Array.isArray(gap)
    ? { column: gap[0] ?? 0, row: gap[1] ?? gap[0] ?? 0 }
    : { column: gap, row: gap }
}

/**
 * Split a length into `count` tracks, honouring optional proportional weights.
 *
 * Weights are relative, not absolute: `[30, 70]` and `[3, 7]` mean the same
 * thing. Missing or short weight lists fall back to equal tracks so a
 * half-written layout still renders something sensible.
 */
export function solveTracks(
  total: number,
  count: number,
  gap: number,
  weights?: number[],
): number[] {
  if (count <= 0) return []
  const available = Math.max(0, total - gap * (count - 1))

  const usable =
    weights && weights.length === count && weights.every((w) => w > 0)
      ? weights
      : Array.from({ length: count }, () => 1)

  const sum = usable.reduce((a, b) => a + b, 0)
  if (sum <= 0) return Array.from({ length: count }, () => available / count)
  return usable.map((w) => (available * w) / sum)
}

/** Cumulative offsets for a list of track sizes separated by `gap`. */
function offsets(sizes: number[], gap: number): number[] {
  const out: number[] = []
  let cursor = 0
  for (const size of sizes) {
    out.push(cursor)
    cursor += size + gap
  }
  return out
}

/**
 * Positions for `count` children inside `bounds`.
 *
 * Returns exactly `count` rects, in child order. Cells beyond the grid's
 * capacity are placed in overflow rows rather than dropped — silently losing a
 * panel because the column count was wrong would be much worse than an ugly
 * layout you can see and fix.
 */
export function solveLayout(
  bounds: Rect,
  layout: Layout,
  count: number,
): Rect[] {
  if (count <= 0) return []
  const { column: columnGap, row: rowGap } = gapsOf(layout)

  const columns =
    layout.type === 'column'
      ? 1
      : layout.type === 'row'
        ? count
        : Math.max(1, Math.floor(layout.columns ?? 1))

  const rows = Math.max(1, Math.ceil(count / columns))

  const columnSizes = solveTracks(
    bounds.width,
    columns,
    columnGap,
    layout.columnWidths,
  )
  const rowSizes = solveTracks(bounds.height, rows, rowGap, layout.rowHeights)

  const columnOffsets = offsets(columnSizes, columnGap)
  const rowOffsets = offsets(rowSizes, rowGap)

  const out: Rect[] = []
  for (let i = 0; i < count; i += 1) {
    const columnIndex =
      layout.order === 'column-major'
        ? Math.floor(i / rows)
        : i % columns
    const rowIndex =
      layout.order === 'column-major' ? i % rows : Math.floor(i / columns)

    // Column-major with a ragged final column can index past the tracks.
    const safeColumn = Math.min(columnIndex, columns - 1)
    const safeRow = Math.min(rowIndex, rows - 1)

    out.push({
      x: bounds.x + columnOffsets[safeColumn],
      y: bounds.y + rowOffsets[safeRow],
      width: columnSizes[safeColumn],
      height: rowSizes[safeRow],
    })
  }
  return out
}

/**
 * Fit a child into its cell.
 *
 * `stretch` fills the cell. `preserve` keeps the child's current aspect ratio
 * and centres it, which is what you want for plots — a grid should arrange
 * panels, not silently distort them.
 */
export function placeInCell(cell: Rect, child: Rect, fit: Layout['fit']): Rect {
  if (fit === 'stretch' || child.width <= 0 || child.height <= 0) return cell
  const aspect = child.width / child.height
  let width = cell.width
  let height = width / aspect
  if (height > cell.height) {
    height = cell.height
    width = height * aspect
  }
  return {
    x: cell.x + (cell.width - width) / 2,
    y: cell.y + (cell.height - height) / 2,
    width,
    height,
  }
}

/**
 * Compute geometry for every node governed by a laid-out group.
 *
 * Returns only the nodes whose rects change, so callers can write them in one
 * pass. Groups without a layout contribute nothing.
 */
export function applyLayouts(nodes: FigNode[]): Record<NodeId, Rect> {
  const byId = new Map(nodes.map((n) => [n.id, n]))
  const updates: Record<NodeId, Rect> = {}

  for (const node of nodes) {
    if (node.type !== 'group' || !node.layout) continue
    const children = node.children
      .map((id) => byId.get(id))
      .filter((c): c is FigNode => c !== undefined && !c.hidden)
    if (children.length === 0) continue

    const cells = solveLayout(node, node.layout, children.length)
    children.forEach((child, index) => {
      const cell = cells[index]
      if (!cell) return
      const placed = placeInCell(cell, child, node.layout?.fit)
      if (
        child.x !== placed.x ||
        child.y !== placed.y ||
        child.width !== placed.width ||
        child.height !== placed.height
      ) {
        updates[child.id] = placed
      }
    })
  }

  return updates
}

/** The group governing a node's geometry, if any. */
export function governingGroup(
  nodes: FigNode[],
  id: NodeId,
): GroupNode | undefined {
  return nodes.find(
    (n): n is GroupNode =>
      n.type === 'group' && !!n.layout && n.children.includes(id),
  )
}

/** Whether a node's position is decided by a layout rather than by dragging. */
export function isLaidOut(nodes: FigNode[], id: NodeId): boolean {
  return governingGroup(nodes, id) !== undefined
}

/**
 * Move a child within its group's order.
 *
 * With a layout in force, dragging a panel cannot move it freely — the solver
 * would immediately overwrite the new coordinates. Reordering is the meaningful
 * operation instead, so a drag becomes "put this panel in that slot".
 */
export function reorderChild(
  group: GroupNode,
  id: NodeId,
  toIndex: number,
): NodeId[] {
  const current = group.children.indexOf(id)
  if (current < 0) return group.children
  const next = [...group.children]
  next.splice(current, 1)
  next.splice(Math.max(0, Math.min(next.length, toIndex)), 0, id)
  return next
}

/** Index of the cell containing a point, for drag-to-reorder. */
export function cellIndexAt(
  group: GroupNode,
  count: number,
  point: { x: number; y: number },
): number | null {
  if (!group.layout || count <= 0) return null
  const cells = solveLayout(group, group.layout, count)
  for (let i = 0; i < cells.length; i += 1) {
    const cell = cells[i]
    if (
      point.x >= cell.x &&
      point.x <= cell.x + cell.width &&
      point.y >= cell.y &&
      point.y <= cell.y + cell.height
    ) {
      return i
    }
  }
  return null
}
