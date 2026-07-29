import { useCallback, useEffect, useRef, useState } from 'react'
import { useEditor } from '../state/store'
import type { FigNode, GroupNode, NodeId, Rect } from '../model/types'
import {
  HANDLES,
  HANDLE_ANCHOR,
  HANDLE_CURSOR,
  type Handle,
  boundsOf,
  rectsIntersect,
  resizeRect,
  roundTo,
  screenToDoc,
} from '../model/geometry'
import {
  cellIndexAt,
  governingGroup,
  selectionTargetFor,
  solveLayout,
  solveWithPending,
} from '../model/layout'
import { NodeView } from './NodeView'

/**
 * The interactive canvas.
 *
 * Interaction is a small explicit state machine driven by pointer events with
 * pointer capture, rather than per-node drag handlers. That keeps multi-select
 * drags, marquee selection and resize from fighting each other, and means a drag
 * that leaves the window still tracks correctly.
 */

type Interaction =
  | { kind: 'idle' }
  | {
      kind: 'move'
      origin: { x: number; y: number }
      startRects: Record<string, Rect>
    }
  | {
      kind: 'resize'
      handle: Handle
      origin: { x: number; y: number }
      startRects: Record<string, Rect>
      startBounds: Rect
    }
  | {
      kind: 'reorder'
      group: NodeId
      childId: NodeId
      /** Cell the pointer is currently over, previewed before committing. */
      overIndex: number
    }
  | { kind: 'marquee'; origin: { x: number; y: number }; current: { x: number; y: number } }
  | { kind: 'pan'; origin: { x: number; y: number }; startPan: { x: number; y: number } }

export function Canvas() {
  const doc = useEditor((s) => s.doc)
  const selection = useEditor((s) => s.selection)
  const viewport = useEditor((s) => s.viewport)
  const select = useEditor((s) => s.select)
  const toggleSelect = useEditor((s) => s.toggleSelect)
  const setNodeRects = useEditor((s) => s.setNodeRects)
  const pushHistory = useEditor((s) => s.pushHistory)
  const setViewport = useEditor((s) => s.setViewport)
  const zoomBy = useEditor((s) => s.zoomBy)
  const zoomToFit = useEditor((s) => s.zoomToFit)
  const insertAsset = useEditor((s) => s.insertAsset)
  const reorderGroupChild = useEditor((s) => s.reorderGroupChild)

  const svgRef = useRef<SVGSVGElement>(null)
  const wrapRef = useRef<HTMLDivElement>(null)
  const [interaction, setInteraction] = useState<Interaction>({ kind: 'idle' })
  const [spaceDown, setSpaceDown] = useState(false)

  const selectedNodes = doc.nodes.filter((n) => selection.includes(n.id))
  const bounds = boundsOf(selectedNodes)
  const grid = doc.canvas.grid

  // Fit the document in view on first mount and whenever the canvas resizes.
  useEffect(() => {
    const el = wrapRef.current
    if (!el) return
    const observer = new ResizeObserver(() => {
      if (el.clientWidth > 0) {
        zoomToFit({ width: el.clientWidth, height: el.clientHeight })
      }
    })
    observer.observe(el)
    return () => observer.disconnect()
    // Intentionally runs once: refitting on every doc change would fight the user.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (e.code === 'Space' && !isTypingTarget(e.target)) setSpaceDown(true)
    }
    const up = (e: KeyboardEvent) => {
      if (e.code === 'Space') setSpaceDown(false)
    }
    window.addEventListener('keydown', down)
    window.addEventListener('keyup', up)
    return () => {
      window.removeEventListener('keydown', down)
      window.removeEventListener('keyup', up)
    }
  }, [])

  const toDoc = useCallback(
    (e: { clientX: number; clientY: number }) => {
      const rect = svgRef.current?.getBoundingClientRect()
      if (!rect) return { x: 0, y: 0 }
      return screenToDoc(e.clientX, e.clientY, rect, viewport)
    },
    [viewport],
  )

  const snap = useCallback(
    (value: number, disable: boolean) =>
      grid?.snap && !disable ? roundTo(value, grid.size) : value,
    [grid],
  )

  // --- pointer handlers ---------------------------------------------------

  const onNodePointerDown = (e: React.PointerEvent, node: FigNode) => {
    if (spaceDown || e.button !== 0 || node.locked) return
    e.stopPropagation()

    // Clicking a panel inside a group grabs the group, not the panel — click
    // again (or shift-click) to drill in. Without this you cannot pick up an
    // arrangement without hunting for a gap between its panels.
    const targetId = selectionTargetFor(doc.nodes, node.id, selection)

    let next = selection
    if (e.shiftKey) {
      toggleSelect(targetId)
      next = selection.includes(targetId)
        ? selection.filter((x) => x !== targetId)
        : [...selection, targetId]
    } else if (!selection.includes(targetId)) {
      select([targetId])
      next = [targetId]
    }
    if (next.length === 0) return

    // A panel whose position a layout owns cannot be dragged freely — the
    // solver would overwrite it. Dragging it reorders it instead.
    const layoutGroup = next.length === 1 ? governingGroup(doc.nodes, next[0]) : undefined
    if (layoutGroup) {
      ;(e.target as Element).setPointerCapture?.(e.pointerId)
      setInteraction({
        kind: 'reorder',
        group: layoutGroup.id,
        childId: next[0],
        overIndex: layoutGroup.children.indexOf(next[0]),
      })
      return
    }

    pushHistory()
    // Moving a group moves what it contains; otherwise the frame would slide
    // out from under its own panels.
    const moving = withGroupMembers(doc.nodes, next)
    const startRects: Record<string, Rect> = {}
    for (const n of doc.nodes) {
      if (moving.has(n.id)) {
        startRects[n.id] = { x: n.x, y: n.y, width: n.width, height: n.height }
      }
    }
    ;(e.target as Element).setPointerCapture?.(e.pointerId)
    setInteraction({ kind: 'move', origin: toDoc(e), startRects })
  }

  const onHandlePointerDown = (e: React.PointerEvent, handle: Handle) => {
    if (e.button !== 0 || !bounds) return
    e.stopPropagation()
    pushHistory()
    const startRects: Record<string, Rect> = {}
    for (const n of selectedNodes) {
      startRects[n.id] = { x: n.x, y: n.y, width: n.width, height: n.height }
    }
    ;(e.target as Element).setPointerCapture?.(e.pointerId)
    setInteraction({
      kind: 'resize',
      handle,
      origin: toDoc(e),
      startRects,
      startBounds: bounds,
    })
  }

  const onBackgroundPointerDown = (e: React.PointerEvent) => {
    if (e.button === 1 || spaceDown) {
      ;(e.currentTarget as Element).setPointerCapture?.(e.pointerId)
      setInteraction({
        kind: 'pan',
        origin: { x: e.clientX, y: e.clientY },
        startPan: { x: viewport.panX, y: viewport.panY },
      })
      return
    }
    if (e.button !== 0) return
    if (!e.shiftKey) select([])
    const p = toDoc(e)
    ;(e.currentTarget as Element).setPointerCapture?.(e.pointerId)
    setInteraction({ kind: 'marquee', origin: p, current: p })
  }

  const onPointerMove = (e: React.PointerEvent) => {
    if (interaction.kind === 'idle') return

    if (interaction.kind === 'pan') {
      setViewport({
        panX: interaction.startPan.x + (e.clientX - interaction.origin.x),
        panY: interaction.startPan.y + (e.clientY - interaction.origin.y),
      })
      return
    }

    const p = toDoc(e)

    if (interaction.kind === 'marquee') {
      setInteraction({ ...interaction, current: p })
      return
    }

    if (interaction.kind === 'move') {
      const rawDx = p.x - interaction.origin.x
      const rawDy = p.y - interaction.origin.y
      // Snap the selection's own origin so a group keeps its internal spacing.
      const anchor = Object.values(interaction.startRects)[0]
      const dx = anchor
        ? snap(anchor.x + rawDx, e.altKey) - anchor.x
        : rawDx
      const dy = anchor
        ? snap(anchor.y + rawDy, e.altKey) - anchor.y
        : rawDy
      // Shift constrains to the dominant axis, as in every other editor.
      const lockX = e.shiftKey && Math.abs(rawDx) < Math.abs(rawDy)
      const lockY = e.shiftKey && Math.abs(rawDy) <= Math.abs(rawDx)
      const next: Record<string, Rect> = {}
      for (const [id, start] of Object.entries(interaction.startRects)) {
        next[id] = {
          ...start,
          x: start.x + (lockX ? 0 : dx),
          y: start.y + (lockY ? 0 : dy),
        }
      }
      setNodeRects(solveWithPending(doc.nodes, next))
      return
    }

    if (interaction.kind === 'reorder') {
      const group = doc.nodes.find(
        (n): n is GroupNode => n.id === interaction.group && n.type === 'group',
      )
      if (!group) return
      const index = cellIndexAt(group, group.children.length, p)
      if (index !== null && index !== interaction.overIndex) {
        setInteraction({ ...interaction, overIndex: index })
      }
      return
    }

    if (interaction.kind === 'resize') {
      const dx = p.x - interaction.origin.x
      const dy = p.y - interaction.origin.y
      const aspect =
        e.shiftKey && interaction.startBounds.height !== 0
          ? interaction.startBounds.width / interaction.startBounds.height
          : null
      let nextBounds = resizeRect(interaction.startBounds, interaction.handle, dx, dy, {
        aspect,
        fromCenter: e.altKey,
      })
      if (grid?.snap && !e.altKey) {
        nextBounds = {
          x: roundTo(nextBounds.x, grid.size),
          y: roundTo(nextBounds.y, grid.size),
          width: roundTo(nextBounds.width, grid.size),
          height: roundTo(nextBounds.height, grid.size),
        }
      }
      // Map the bounding-box transform onto each selected node proportionally,
      // so resizing a multi-selection scales the whole arrangement.
      const sx =
        interaction.startBounds.width === 0
          ? 1
          : nextBounds.width / interaction.startBounds.width
      const sy =
        interaction.startBounds.height === 0
          ? 1
          : nextBounds.height / interaction.startBounds.height
      const next: Record<string, Rect> = {}
      for (const [id, start] of Object.entries(interaction.startRects)) {
        next[id] = {
          x: nextBounds.x + (start.x - interaction.startBounds.x) * sx,
          y: nextBounds.y + (start.y - interaction.startBounds.y) * sy,
          width: start.width * sx,
          height: start.height * sy,
        }
      }
      setNodeRects(solveWithPending(doc.nodes, next))
    }
  }

  const onPointerUp = () => {
    if (interaction.kind === 'reorder') {
      const group = doc.nodes.find(
        (n): n is GroupNode => n.id === interaction.group && n.type === 'group',
      )
      const from = group?.children.indexOf(interaction.childId) ?? -1
      if (group && interaction.overIndex >= 0 && interaction.overIndex !== from) {
        reorderGroupChild(group.id, interaction.childId, interaction.overIndex)
      }
      setInteraction({ kind: 'idle' })
      return
    }
    if (interaction.kind === 'marquee') {
      const box = normalize(interaction.origin, interaction.current)
      if (box.width > 1 || box.height > 1) {
        const hits = doc.nodes
          .filter((n) => !n.hidden && !n.locked && rectsIntersect(box, n))
          .map((n) => n.id)
        select(hits)
      }
    }
    setInteraction({ kind: 'idle' })
  }

  const onWheel = (e: React.WheelEvent) => {
    if (!(e.ctrlKey || e.metaKey)) return
    const rect = svgRef.current?.getBoundingClientRect()
    if (!rect) return
    zoomBy(e.deltaY < 0 ? 1.1 : 1 / 1.1, {
      x: e.clientX - rect.left,
      y: e.clientY - rect.top,
    })
  }

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault()
    const raw = e.dataTransfer.getData('application/x-figmint-asset')
    if (!raw) return
    try {
      const asset = JSON.parse(raw)
      const p = toDoc(e)
      insertAsset(asset, { x: snap(p.x, e.altKey), y: snap(p.y, e.altKey) })
    } catch {
      /* malformed payload — ignore */
    }
  }

  // --- render -------------------------------------------------------------

  const { zoom, panX, panY } = viewport
  const marqueeBox =
    interaction.kind === 'marquee'
      ? normalize(interaction.origin, interaction.current)
      : null
  /** Handles and outlines are drawn at constant screen size. */
  const px = 1 / zoom

  return (
    <div
      ref={wrapRef}
      className="canvas-wrap"
      onDragOver={(e) => e.preventDefault()}
      onDrop={onDrop}
    >
      <svg
        ref={svgRef}
        className="canvas"
        onPointerDown={onBackgroundPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onWheel={onWheel}
        style={{
          cursor:
            interaction.kind === 'pan' || spaceDown ? 'grabbing' : 'default',
        }}
      >
        <defs>
          <marker
            id="fm-arrowhead"
            viewBox="0 0 10 10"
            refX="9"
            refY="5"
            markerWidth="6"
            markerHeight="6"
            orient="auto-start-reverse"
          >
            <path d="M 0 0 L 10 5 L 0 10 z" fill="context-stroke" />
          </marker>
          <pattern
            id="fm-grid"
            width={grid?.size ?? 6}
            height={grid?.size ?? 6}
            patternUnits="userSpaceOnUse"
          >
            <path
              d={`M ${grid?.size ?? 6} 0 L 0 0 0 ${grid?.size ?? 6}`}
              fill="none"
              stroke="rgba(0,0,0,0.08)"
              strokeWidth={px}
            />
          </pattern>
        </defs>

        <g transform={`translate(${panX} ${panY}) scale(${zoom})`}>
          {/* Page */}
          <rect
            x={0}
            y={0}
            width={doc.canvas.width}
            height={doc.canvas.height}
            fill={doc.canvas.background ?? '#ffffff'}
            className="canvas-page"
          />
          {grid && grid.size > 0 && (
            <rect
              x={0}
              y={0}
              width={doc.canvas.width}
              height={doc.canvas.height}
              fill="url(#fm-grid)"
              pointerEvents="none"
            />
          )}

          {doc.nodes.map((node) => (
            <NodeView
              key={node.id}
              node={node}
              doc={doc}
              selected={selection.includes(node.id)}
              onPointerDown={onNodePointerDown}
            />
          ))}

          {/* Per-node selection outlines */}
          {selectedNodes.map((n) => (
            <rect
              key={`sel-${n.id}`}
              x={n.x}
              y={n.y}
              width={n.width}
              height={n.height}
              fill="none"
              stroke="#2f6feb"
              strokeWidth={px}
              pointerEvents="none"
            />
          ))}

          {/* Bounding box + resize handles */}
          {bounds && interaction.kind !== 'marquee' && (
            <g>
              <rect
                x={bounds.x}
                y={bounds.y}
                width={bounds.width}
                height={bounds.height}
                fill="none"
                stroke="#2f6feb"
                strokeWidth={px}
                strokeDasharray={
                  selectedNodes.length > 1 ? `${4 * px} ${3 * px}` : undefined
                }
                pointerEvents="none"
              />
              {HANDLES.map((h) => {
                const a = HANDLE_ANCHOR[h]
                const size = 7 * px
                return (
                  <rect
                    key={h}
                    x={bounds.x + a.x * bounds.width - size / 2}
                    y={bounds.y + a.y * bounds.height - size / 2}
                    width={size}
                    height={size}
                    fill="#ffffff"
                    stroke="#2f6feb"
                    strokeWidth={px}
                    style={{ cursor: HANDLE_CURSOR[h] }}
                    onPointerDown={(e) => onHandlePointerDown(e, h)}
                  />
                )
              })}
            </g>
          )}

          {/* Where a reordered panel will land. */}
          {interaction.kind === 'reorder' &&
            (() => {
              const group = doc.nodes.find(
                (n): n is GroupNode =>
                  n.id === interaction.group && n.type === 'group',
              )
              if (!group?.layout || interaction.overIndex < 0) return null
              const cell = solveLayout(
                group,
                group.layout,
                group.children.length,
              )[interaction.overIndex]
              if (!cell) return null
              return (
                <rect
                  x={cell.x}
                  y={cell.y}
                  width={cell.width}
                  height={cell.height}
                  fill="rgba(139,92,246,0.12)"
                  stroke="#8b5cf6"
                  strokeWidth={px}
                  pointerEvents="none"
                />
              )
            })()}

          {marqueeBox && (
            <rect
              x={marqueeBox.x}
              y={marqueeBox.y}
              width={marqueeBox.width}
              height={marqueeBox.height}
              fill="rgba(47,111,235,0.08)"
              stroke="#2f6feb"
              strokeWidth={px}
              pointerEvents="none"
            />
          )}
        </g>
      </svg>

      <div className="canvas-hud">
        {Math.round(zoom * 100)}% · {doc.canvas.width}×{doc.canvas.height}
        {doc.canvas.units}
      </div>
    </div>
  )
}

function normalize(a: { x: number; y: number }, b: { x: number; y: number }): Rect {
  return {
    x: Math.min(a.x, b.x),
    y: Math.min(a.y, b.y),
    width: Math.abs(a.x - b.x),
    height: Math.abs(a.y - b.y),
  }
}

/**
 * Expand a selection to include everything a selected group governs.
 *
 * Applied transitively, so nesting a group inside a group still moves the whole
 * subtree rather than orphaning the inner one.
 */
function withGroupMembers(nodes: FigNode[], ids: NodeId[]): Set<NodeId> {
  const out = new Set(ids)
  let grew = true
  while (grew) {
    grew = false
    for (const node of nodes) {
      if (node.type !== 'group' || !out.has(node.id)) continue
      for (const child of node.children) {
        if (!out.has(child)) {
          out.add(child)
          grew = true
        }
      }
    }
  }
  return out
}

function isTypingTarget(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null
  if (!el) return false
  const tag = el.tagName
  return tag === 'INPUT' || tag === 'TEXTAREA' || el.isContentEditable
}
