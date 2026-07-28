import { describe, expect, it } from 'vitest'
import {
  MIN_SIZE,
  boundsOf,
  rectsIntersect,
  resizeRect,
  roundTo,
  screenToDoc,
} from './geometry'

const box = { x: 10, y: 20, width: 100, height: 50 }

describe('resizeRect', () => {
  it('moves only the dragged edge', () => {
    expect(resizeRect(box, 'e', 20, 999)).toEqual({
      x: 10,
      y: 20,
      width: 120,
      height: 50,
    })
    expect(resizeRect(box, 's', 999, 10)).toEqual({
      x: 10,
      y: 20,
      width: 100,
      height: 60,
    })
  })

  it('moves the origin when dragging a top-left handle', () => {
    expect(resizeRect(box, 'nw', 10, 5)).toEqual({
      x: 20,
      y: 25,
      width: 90,
      height: 45,
    })
  })

  it('flips rather than collapsing when dragged past the opposite edge', () => {
    const result = resizeRect(box, 'e', -150, 0)
    expect(result.width).toBe(50)
    expect(result.x).toBe(-40)
  })

  it('never produces a box smaller than the minimum', () => {
    const result = resizeRect(box, 'e', -100, 0)
    expect(result.width).toBeGreaterThanOrEqual(MIN_SIZE)
    expect(result.height).toBeGreaterThanOrEqual(MIN_SIZE)
  })

  it('preserves aspect ratio on corner drags when asked', () => {
    const result = resizeRect(box, 'se', 100, 0, { aspect: 2 })
    expect(result.width / result.height).toBeCloseTo(2, 5)
    // The anchored corner stays put.
    expect(result.x).toBe(10)
    expect(result.y).toBe(20)
  })

  it('ignores aspect lock on edge drags, which have only one free axis', () => {
    const result = resizeRect(box, 'e', 100, 0, { aspect: 2 })
    expect(result.height).toBe(50)
    expect(result.width).toBe(200)
  })

  it('grows symmetrically from the centre when fromCenter is set', () => {
    const result = resizeRect(box, 'e', 20, 0, { fromCenter: true })
    expect(result.width).toBe(140)
    expect(result.x).toBe(-10)
    // Centre is unchanged.
    expect(result.x + result.width / 2).toBeCloseTo(box.x + box.width / 2, 5)
  })
})

describe('boundsOf', () => {
  it('returns null for an empty selection', () => {
    expect(boundsOf([])).toBeNull()
  })

  it('wraps every box in the selection', () => {
    expect(
      boundsOf([
        { x: 0, y: 0, width: 10, height: 10 },
        { x: 50, y: 20, width: 10, height: 40 },
      ]),
    ).toEqual({ x: 0, y: 0, width: 60, height: 60 })
  })
})

describe('rectsIntersect', () => {
  const a = { x: 0, y: 0, width: 10, height: 10 }
  it('detects overlap', () => {
    expect(rectsIntersect(a, { x: 5, y: 5, width: 10, height: 10 })).toBe(true)
  })
  it('rejects disjoint boxes', () => {
    expect(rectsIntersect(a, { x: 20, y: 0, width: 5, height: 5 })).toBe(false)
  })
  it('treats edge-touching as non-overlapping', () => {
    expect(rectsIntersect(a, { x: 10, y: 0, width: 5, height: 5 })).toBe(false)
  })
})

describe('roundTo', () => {
  it('snaps to the nearest multiple', () => {
    expect(roundTo(13, 6)).toBe(12)
    expect(roundTo(15, 6)).toBe(18)
  })
  it('passes values through when snapping is off', () => {
    expect(roundTo(13.7, 0)).toBe(13.7)
  })
})

describe('screenToDoc', () => {
  const rect = { left: 100, top: 50 } as DOMRect

  it('inverts pan and zoom', () => {
    expect(screenToDoc(300, 250, rect, { zoom: 2, panX: 40, panY: 20 })).toEqual(
      { x: 80, y: 90 },
    )
  })

  it('is the identity at zoom 1 with no pan', () => {
    expect(screenToDoc(150, 90, rect, { zoom: 1, panX: 0, panY: 0 })).toEqual({
      x: 50,
      y: 40,
    })
  })
})
