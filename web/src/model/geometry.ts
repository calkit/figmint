import type { FigNode, Rect } from './types'

/** The eight resize handles, named by compass direction. */
export const HANDLES = ['nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w'] as const
export type Handle = (typeof HANDLES)[number]

export const HANDLE_CURSOR: Record<Handle, string> = {
  nw: 'nwse-resize',
  n: 'ns-resize',
  ne: 'nesw-resize',
  e: 'ew-resize',
  se: 'nwse-resize',
  s: 'ns-resize',
  sw: 'nesw-resize',
  w: 'ew-resize',
}

/** Where a handle sits within a unit box, as (0|0.5|1) fractions. */
export const HANDLE_ANCHOR: Record<Handle, { x: number; y: number }> = {
  nw: { x: 0, y: 0 },
  n: { x: 0.5, y: 0 },
  ne: { x: 1, y: 0 },
  e: { x: 1, y: 0.5 },
  se: { x: 1, y: 1 },
  s: { x: 0.5, y: 1 },
  sw: { x: 0, y: 1 },
  w: { x: 0, y: 0.5 },
}

export function roundTo(value: number, step: number): number {
  return step > 0 ? Math.round(value / step) * step : value
}

export function snapRect(rect: Rect, step: number): Rect {
  if (step <= 0) return rect
  return {
    x: roundTo(rect.x, step),
    y: roundTo(rect.y, step),
    width: roundTo(rect.width, step),
    height: roundTo(rect.height, step),
  }
}

/** Smallest axis-aligned box containing all the given boxes. */
export function boundsOf(rects: Rect[]): Rect | null {
  if (rects.length === 0) return null
  let minX = Infinity
  let minY = Infinity
  let maxX = -Infinity
  let maxY = -Infinity
  for (const r of rects) {
    minX = Math.min(minX, r.x)
    minY = Math.min(minY, r.y)
    maxX = Math.max(maxX, r.x + r.width)
    maxY = Math.max(maxY, r.y + r.height)
  }
  return { x: minX, y: minY, width: maxX - minX, height: maxY - minY }
}

export function rectsIntersect(a: Rect, b: Rect): boolean {
  return (
    a.x < b.x + b.width &&
    a.x + a.width > b.x &&
    a.y < b.y + b.height &&
    a.y + a.height > b.y
  )
}

export function pointInRect(px: number, py: number, r: Rect): boolean {
  return px >= r.x && px <= r.x + r.width && py >= r.y && py <= r.y + r.height
}

/** Minimum size in pt, so a node can never be resized into un-grabbable nothing. */
export const MIN_SIZE = 4

/**
 * Apply a resize drag to a box.
 *
 * `dx`/`dy` are the pointer delta in document units. Dragging a side past the
 * opposite side flips the box rather than clamping at zero, which is what every
 * other editor does and what people expect.
 */
export function resizeRect(
  start: Rect,
  handle: Handle,
  dx: number,
  dy: number,
  opts: { aspect?: number | null; fromCenter?: boolean } = {},
): Rect {
  const anchor = HANDLE_ANCHOR[handle]
  const movesX = anchor.x !== 0.5
  const movesY = anchor.y !== 0.5

  let left = start.x
  let top = start.y
  let right = start.x + start.width
  let bottom = start.y + start.height

  if (movesX) {
    if (anchor.x === 0) left += dx
    else right += dx
    if (opts.fromCenter) {
      if (anchor.x === 0) right -= dx
      else left -= dx
    }
  }
  if (movesY) {
    if (anchor.y === 0) top += dy
    else bottom += dy
    if (opts.fromCenter) {
      if (anchor.y === 0) bottom -= dy
      else top -= dy
    }
  }

  let width = right - left
  let height = bottom - top

  // Lock aspect ratio by growing the smaller axis to match, keeping the
  // dragged corner under the pointer as closely as possible.
  if (opts.aspect && opts.aspect > 0 && movesX && movesY) {
    const signW = Math.sign(width) || 1
    const signH = Math.sign(height) || 1
    const absW = Math.abs(width)
    const absH = Math.abs(height)
    if (absW / opts.aspect > absH) {
      height = signH * (absW / opts.aspect)
    } else {
      width = signW * absH * opts.aspect
    }
    if (anchor.x === 0) left = right - width
    else right = left + width
    if (anchor.y === 0) top = bottom - height
    else bottom = top + height
  }

  // Normalize a flipped box back to positive width/height.
  const x = Math.min(left, right)
  const y = Math.min(top, bottom)
  const w = Math.max(Math.abs(width), MIN_SIZE)
  const h = Math.max(Math.abs(height), MIN_SIZE)

  return { x, y, width: w, height: h }
}

/** Aspect ratio (w/h) of a node, used when shift-resizing images. */
export function aspectOf(node: FigNode): number | null {
  if (node.height === 0) return null
  return node.width / node.height
}

export interface Viewport {
  /** Zoom factor: document pt -> screen px. */
  zoom: number
  /** Pan offset in screen px. */
  panX: number
  panY: number
}

export function screenToDoc(
  clientX: number,
  clientY: number,
  rect: DOMRect,
  vp: Viewport,
): { x: number; y: number } {
  return {
    x: (clientX - rect.left - vp.panX) / vp.zoom,
    y: (clientY - rect.top - vp.panY) / vp.zoom,
  }
}
