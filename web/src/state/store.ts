import { create } from 'zustand'
import { produce } from 'immer'
import type {
  Asset,
  FigmintDocument,
  FigNode,
  NodeId,
  Rect,
  SourceKey,
} from '../model/types'
import {
  emptyDocument,
  fitIntoBox,
  makeImageNode,
  makeSourceKey,
  sourceFromAsset,
} from '../model/document'
import type { Viewport } from '../model/geometry'
import { fetchAssets, fetchDocument, saveDocument } from '../io/api'
import { fromYaml, toYaml } from '../io/serialize'

const HISTORY_LIMIT = 100

interface EditorState {
  doc: FigmintDocument
  documentPath: string | null
  dirty: boolean

  selection: NodeId[]
  viewport: Viewport

  assets: Asset[]
  assetsByPath: Map<string, Asset>
  assetError: string | null
  status: string | null

  past: FigmintDocument[]
  future: FigmintDocument[]

  // --- history -----------------------------------------------------------
  /**
   * Snapshot the document before a mutation. Call once at the start of an
   * interaction (pointerdown), not on every frame of a drag — otherwise a single
   * drag fills the undo stack with hundreds of intermediate states.
   */
  pushHistory: () => void
  undo: () => void
  redo: () => void

  // --- document ----------------------------------------------------------
  setDoc: (doc: FigmintDocument) => void
  patchDoc: (fn: (draft: FigmintDocument) => void, history?: boolean) => void
  newDocument: () => void
  openDocument: (path: string) => Promise<void>
  save: (path?: string) => Promise<void>

  // --- nodes -------------------------------------------------------------
  addNode: (node: FigNode) => void
  updateNode: (id: NodeId, patch: Partial<FigNode>) => void
  setNodeRects: (rects: Record<NodeId, Rect>) => void
  deleteSelected: () => void
  duplicateSelected: () => void
  nudgeSelected: (dx: number, dy: number) => void
  reorderSelected: (direction: 'front' | 'forward' | 'backward' | 'back') => void

  // --- selection ---------------------------------------------------------
  select: (ids: NodeId[]) => void
  toggleSelect: (id: NodeId) => void
  selectAll: () => void
  clearSelection: () => void

  // --- viewport ----------------------------------------------------------
  setViewport: (vp: Partial<Viewport>) => void
  zoomBy: (factor: number, center?: { x: number; y: number }) => void
  zoomToFit: (size: { width: number; height: number }) => void

  // --- assets ------------------------------------------------------------
  loadAssets: (dir?: string) => Promise<void>
  insertAsset: (asset: Asset, at?: { x: number; y: number }) => void
  relinkSource: (key: SourceKey, asset: Asset) => void

  setStatus: (message: string | null) => void
}

export const useEditor = create<EditorState>((set, get) => ({
  doc: emptyDocument(),
  documentPath: null,
  dirty: false,

  selection: [],
  viewport: { zoom: 2, panX: 40, panY: 40 },

  assets: [],
  assetsByPath: new Map(),
  assetError: null,
  status: null,

  past: [],
  future: [],

  // --- history -----------------------------------------------------------

  pushHistory: () =>
    set((s) => ({
      past: [...s.past, s.doc].slice(-HISTORY_LIMIT),
      future: [],
    })),

  undo: () =>
    set((s) => {
      const previous = s.past.at(-1)
      if (!previous) return s
      return {
        doc: previous,
        past: s.past.slice(0, -1),
        future: [s.doc, ...s.future].slice(0, HISTORY_LIMIT),
        dirty: true,
        selection: s.selection.filter((id) =>
          previous.nodes.some((x) => x.id === id),
        ),
      }
    }),

  redo: () =>
    set((s) => {
      const next = s.future[0]
      if (!next) return s
      return {
        doc: next,
        past: [...s.past, s.doc].slice(-HISTORY_LIMIT),
        future: s.future.slice(1),
        dirty: true,
        selection: s.selection.filter((id) =>
          next.nodes.some((x) => x.id === id),
        ),
      }
    }),

  // --- document ----------------------------------------------------------

  setDoc: (doc) => set({ doc, dirty: true }),

  patchDoc: (fn, history = true) => {
    if (history) get().pushHistory()
    set((s) => ({ doc: produce(s.doc, fn), dirty: true }))
  },

  newDocument: () =>
    set({
      doc: emptyDocument(),
      documentPath: null,
      selection: [],
      past: [],
      future: [],
      dirty: false,
      status: 'New document',
    }),

  openDocument: async (path) => {
    const payload = await fetchDocument(path)
    set({
      doc: fromYaml(payload.text),
      documentPath: payload.path,
      selection: [],
      past: [],
      future: [],
      dirty: false,
      status: `Opened ${payload.path}`,
    })
  },

  save: async (path) => {
    const target = path ?? get().documentPath
    if (!target) throw new Error('No document path — use Save As')
    const text = toYaml(get().doc)
    const result = await saveDocument(target, text)
    set({
      documentPath: result.path,
      dirty: false,
      status: `Saved ${result.path} (${result.bytes} bytes)`,
    })
  },

  // --- nodes -------------------------------------------------------------

  addNode: (node) => {
    get().pushHistory()
    set((s) => ({
      doc: produce(s.doc, (d) => {
        d.nodes.push(node)
      }),
      selection: [node.id],
      dirty: true,
    }))
  },

  updateNode: (id, patch) =>
    set((s) => ({
      doc: produce(s.doc, (d) => {
        const node = d.nodes.find((x) => x.id === id)
        if (node) Object.assign(node, patch)
      }),
      dirty: true,
    })),

  /** Bulk geometry write used by drag/resize; deliberately skips history. */
  setNodeRects: (rects) =>
    set((s) => ({
      doc: produce(s.doc, (d) => {
        for (const node of d.nodes) {
          const rect = rects[node.id]
          if (rect) Object.assign(node, rect)
        }
      }),
      dirty: true,
    })),

  deleteSelected: () => {
    const { selection } = get()
    if (selection.length === 0) return
    get().pushHistory()
    set((s) => ({
      doc: produce(s.doc, (d) => {
        d.nodes = d.nodes.filter((x) => !selection.includes(x.id))
      }),
      selection: [],
      dirty: true,
    }))
  },

  duplicateSelected: () => {
    const { selection, doc } = get()
    if (selection.length === 0) return
    get().pushHistory()
    const copies: FigNode[] = []
    set((s) => ({
      doc: produce(s.doc, (d) => {
        for (const id of selection) {
          const original = doc.nodes.find((x) => x.id === id)
          if (!original) continue
          const copy = {
            ...structuredClone(original),
            id: `${original.type}-${Math.random().toString(36).slice(2, 7)}`,
            x: original.x + 8,
            y: original.y + 8,
          } as FigNode
          copies.push(copy)
          d.nodes.push(copy)
        }
      }),
      selection: copies.map((c) => c.id),
      dirty: true,
    }))
  },

  nudgeSelected: (dx, dy) => {
    const { selection } = get()
    if (selection.length === 0) return
    set((s) => ({
      doc: produce(s.doc, (d) => {
        for (const node of d.nodes) {
          if (selection.includes(node.id) && !node.locked) {
            node.x += dx
            node.y += dy
          }
        }
      }),
      dirty: true,
    }))
  },

  reorderSelected: (direction) => {
    const { selection } = get()
    if (selection.length === 0) return
    get().pushHistory()
    set((s) => ({
      doc: produce(s.doc, (d) => {
        const picked = d.nodes.filter((x) => selection.includes(x.id))
        const rest = d.nodes.filter((x) => !selection.includes(x.id))
        if (direction === 'front') d.nodes = [...rest, ...picked]
        else if (direction === 'back') d.nodes = [...picked, ...rest]
        else {
          // Step one position, preserving relative order within the selection.
          const step = direction === 'forward' ? 1 : -1
          const indices = d.nodes
            .map((x, i) => (selection.includes(x.id) ? i : -1))
            .filter((i) => i >= 0)
          const ordered = step > 0 ? indices.reverse() : indices
          for (const i of ordered) {
            const j = i + step
            if (j < 0 || j >= d.nodes.length) continue
            if (selection.includes(d.nodes[j].id)) continue
            ;[d.nodes[i], d.nodes[j]] = [d.nodes[j], d.nodes[i]]
          }
        }
      }),
      dirty: true,
    }))
  },

  // --- selection ---------------------------------------------------------

  select: (ids) => set({ selection: ids }),
  toggleSelect: (id) =>
    set((s) => ({
      selection: s.selection.includes(id)
        ? s.selection.filter((x) => x !== id)
        : [...s.selection, id],
    })),
  selectAll: () => set((s) => ({ selection: s.doc.nodes.map((x) => x.id) })),
  clearSelection: () => set({ selection: [] }),

  // --- viewport ----------------------------------------------------------

  setViewport: (vp) => set((s) => ({ viewport: { ...s.viewport, ...vp } })),

  zoomBy: (factor, center) =>
    set((s) => {
      const zoom = Math.min(16, Math.max(0.1, s.viewport.zoom * factor))
      if (!center) return { viewport: { ...s.viewport, zoom } }
      // Keep the document point under the cursor fixed while zooming.
      const scale = zoom / s.viewport.zoom
      return {
        viewport: {
          zoom,
          panX: center.x - (center.x - s.viewport.panX) * scale,
          panY: center.y - (center.y - s.viewport.panY) * scale,
        },
      }
    }),

  zoomToFit: (size) =>
    set((s) => {
      const margin = 48
      const zoom = Math.min(
        (size.width - margin * 2) / s.doc.canvas.width,
        (size.height - margin * 2) / s.doc.canvas.height,
      )
      const clamped = Math.min(16, Math.max(0.1, zoom))
      return {
        viewport: {
          zoom: clamped,
          panX: (size.width - s.doc.canvas.width * clamped) / 2,
          panY: (size.height - s.doc.canvas.height * clamped) / 2,
        },
      }
    }),

  // --- assets ------------------------------------------------------------

  loadAssets: async (dir) => {
    try {
      const index = await fetchAssets(dir)
      set({
        assets: index.assets,
        assetsByPath: new Map(index.assets.map((a) => [a.path, a])),
        assetError: null,
      })
    } catch (err) {
      set({
        assetError: err instanceof Error ? err.message : String(err),
        assets: [],
        assetsByPath: new Map(),
      })
    }
  },

  insertAsset: (asset, at) => {
    const { doc } = get()
    get().pushHistory()

    // Reuse an existing source entry when the same file is already in the
    // document, so provenance is recorded once and re-links stay coherent.
    let key = Object.keys(doc.sources).find(
      (k) => doc.sources[k].path === asset.path,
    )
    const size = fitIntoBox(asset.intrinsic, {
      width: doc.canvas.width * 0.6,
      height: doc.canvas.height * 0.6,
    })
    const origin = at ?? {
      x: (doc.canvas.width - size.width) / 2,
      y: (doc.canvas.height - size.height) / 2,
    }
    const node = makeImageNode(key ?? '', { ...origin, ...size })

    set((s) => ({
      doc: produce(s.doc, (d) => {
        if (!key) {
          key = makeSourceKey(asset.path, d.sources)
          d.sources[key] = sourceFromAsset(asset)
        }
        node.source = key
        d.nodes.push(node)
      }),
      selection: [node.id],
      dirty: true,
      status: `Inserted ${asset.name}`,
    }))
  },

  /** Point a source at a different file, or re-record its hash after a rebuild. */
  relinkSource: (key, asset) => {
    get().pushHistory()
    set((s) => ({
      doc: produce(s.doc, (d) => {
        const previous = d.sources[key]
        d.sources[key] = {
          ...sourceFromAsset(asset),
          provenance: {
            ...previous?.provenance,
            ...asset.provenance,
            importedAt: previous?.provenance?.importedAt,
          },
        }
      }),
      dirty: true,
      status: `Re-linked ${key} → ${asset.path}`,
    }))
  },

  setStatus: (message) => set({ status: message }),
}))
