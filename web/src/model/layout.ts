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

/**
 * Pick a grid that matches how the panels are already arranged.
 *
 * Defaulting to two columns ignores what the user has in front of them: three
 * panels in a row become two rows, and a vertical stack becomes a wide grid.
 * Reading the existing arrangement means "Grid" tidies what you have rather
 * than rearranging it into something you did not ask for.
 */
export function suggestColumns(rects: Rect[]): number {
  const count = rects.length
  if (count <= 1) return 1

  const bounds = { ...rects[0] }
  let maxX = bounds.x + bounds.width
  let maxY = bounds.y + bounds.height
  for (const r of rects) {
    bounds.x = Math.min(bounds.x, r.x)
    bounds.y = Math.min(bounds.y, r.y)
    maxX = Math.max(maxX, r.x + r.width)
    maxY = Math.max(maxY, r.y + r.height)
  }
  const spanX = maxX - bounds.x
  const spanY = maxY - bounds.y

  // Count distinct rows by vertical overlap — the same banding the Stencila
  // exporter uses, so the editor and the export agree about what a "row" is.
  const rows: Rect[][] = []
  for (const r of [...rects].sort((a, b) => a.y - b.y || a.x - b.x)) {
    const row = rows.find((existing) => {
      const top = Math.min(...existing.map((x) => x.y))
      const bottom = Math.max(...existing.map((x) => x.y + x.height))
      const overlap = Math.min(bottom, r.y + r.height) - Math.max(top, r.y)
      return overlap > Math.min(r.height, bottom - top) * 0.5
    })
    if (row) row.push(r)
    else rows.push([r])
  }

  if (rows.length === 1) return count // one row
  if (rows.length === count) return 1 // one column
  if (rows.every((r) => r.length === rows[0].length)) return rows[0].length

  // Ragged: fall back to the aspect of the whole arrangement.
  return spanY > 0 && spanX / spanY > 1.2
    ? Math.ceil(count / Math.max(1, rows.length))
    : Math.ceil(Math.sqrt(count))
}

/**
 * Bounds that fit `count` panels in a grid without dead space.
 *
 * A group created from the bounding box of scattered panels inherits all the
 * empty space between them, so the panels end up floating in oversized cells
 * and "Grid" looks like it did nothing. Sizing to the largest panel makes the
 * result tight and obviously arranged.
 */
export function boundsForGrid(
  rects: Rect[],
  layout: Layout,
  origin: { x: number; y: number },
): Rect {
  const count = rects.length
  if (count === 0) return { ...origin, width: 0, height: 0 }

  const columns =
    layout.type === 'column'
      ? 1
      : layout.type === 'row'
        ? count
        : Math.max(1, Math.floor(layout.columns ?? 1))
  const rows = Math.max(1, Math.ceil(count / columns))

  const cellWidth = Math.max(...rects.map((r) => r.width))
  const cellHeight = Math.max(...rects.map((r) => r.height))
  const { column: columnGap, row: rowGap } = gapsOf(layout)

  return {
    x: origin.x,
    y: origin.y,
    width: cellWidth * columns + columnGap * (columns - 1),
    height: cellHeight * rows + rowGap * (rows - 1),
  }
}

/**
 * Fold pending geometry into the layout solution.
 *
 * Used during a drag: the caller has computed where the dragged nodes are going,
 * and this returns that plus wherever the solver puts anything those nodes
 * govern. Without it, resizing a group grows the frame while its panels sit
 * still until the pointer is released, which reads as broken.
 */
export function solveWithPending(
  nodes: FigNode[],
  pending: Record<NodeId, Rect>,
): Record<NodeId, Rect> {
  const projected = nodes.map((n) =>
    pending[n.id] ? ({ ...n, ...pending[n.id] } as FigNode) : n,
  )
  // Solved geometry wins: a child inside a laid-out group does not get to keep
  // a position the layout disagrees with.
  return { ...pending, ...applyLayouts(projected) }
}

/** The group directly containing a node, laid out or not. */
export function parentGroup(
  nodes: FigNode[],
  id: NodeId,
): GroupNode | undefined {
  return nodes.find(
    (n): n is GroupNode => n.type === 'group' && n.children.includes(id),
  )
}

/**
 * The outermost group containing a node.
 *
 * Clicking a panel should grab the whole arrangement, the way every other
 * design tool behaves — you select the group, then drill in if you actually
 * meant the panel.
 */
export function outermostGroup(
  nodes: FigNode[],
  id: NodeId,
): GroupNode | undefined {
  let current = parentGroup(nodes, id)
  if (!current) return undefined
  // Guard against a cycle in hand-edited YAML rather than hanging the editor.
  const seen = new Set<NodeId>([id, current.id])
  for (;;) {
    const next = parentGroup(nodes, current.id)
    if (!next || seen.has(next.id)) return current
    seen.add(next.id)
    current = next
  }
}

/** Every ancestor group of a node, innermost first. */
export function ancestorGroups(nodes: FigNode[], id: NodeId): GroupNode[] {
  const out: GroupNode[] = []
  let current = parentGroup(nodes, id)
  const seen = new Set<NodeId>([id])
  while (current && !seen.has(current.id)) {
    out.push(current)
    seen.add(current.id)
    current = parentGroup(nodes, current.id)
  }
  return out
}

/**
 * What a click on `id` should actually select.
 *
 * Selects the outermost group, unless an ancestor is already selected — in
 * which case the user has clearly already grabbed the group and now means the
 * thing inside it. That is the standard click-then-drill-in behaviour.
 */
export function selectionTargetFor(
  nodes: FigNode[],
  id: NodeId,
  currentSelection: NodeId[],
): NodeId {
  const ancestors = ancestorGroups(nodes, id)
  if (ancestors.length === 0) return id
  const alreadyInside = ancestors.some((g) => currentSelection.includes(g.id))
  if (alreadyInside || currentSelection.includes(id)) return id
  return ancestors[ancestors.length - 1].id
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
