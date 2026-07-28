/**
 * @vitest-environment jsdom
 *
 * Smoke tests: the editor mounts, loads a document, and reflects direct model
 * edits. These catch the class of breakage that takes the whole app down —
 * cheaper and more reliable than driving a real browser for the same signal.
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { render, screen, cleanup } from '@testing-library/react'
import { act } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import { useEditor } from './state/store'
import { fromYaml } from './io/serialize'
import { emptyDocument, makeTextNode } from './model/document'

const EXAMPLE = resolve(__dirname, '../../examples/two-panel.fig.yaml')

beforeEach(() => {
  // jsdom has no layout engine, so ResizeObserver and pointer capture — both
  // used by the canvas — need stubbing.
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  )
  Element.prototype.setPointerCapture ??= () => {}
  Element.prototype.releasePointerCapture ??= () => {}

  // The backend isn't running under test; the editor must degrade, not crash.
  vi.stubGlobal(
    'fetch',
    vi.fn(() => Promise.reject(new Error('offline'))),
  )

  useEditor.setState({
    doc: emptyDocument(),
    selection: [],
    past: [],
    future: [],
    assets: [],
    assetsByPath: new Map(),
    documentPath: null,
    dirty: false,
    status: null,
  })
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('App', () => {
  it('mounts without the backend running', async () => {
    await act(async () => {
      render(<App />)
    })
    expect(screen.getByText('figmint')).toBeTruthy()
    expect(screen.getByText('Layers')).toBeTruthy()
    expect(screen.getByText('Provenance')).toBeTruthy()
  })

  it('surfaces a clear message when the backend is unreachable', async () => {
    await act(async () => {
      render(<App />)
    })
    expect(screen.getByText(/Cannot reach the figmint backend/)).toBeTruthy()
  })

  it('renders every node of the example document on the canvas', async () => {
    const doc = fromYaml(readFileSync(EXAMPLE, 'utf-8'))
    await act(async () => {
      render(<App />)
    })
    await act(async () => {
      useEditor.setState({ doc })
    })

    for (const node of doc.nodes) {
      expect(
        document.querySelector(`[data-node-id="${node.id}"]`),
        `node ${node.id} should be on the canvas`,
      ).toBeTruthy()
    }
    // Panel names show up in the layer list.
    expect(screen.getByText('Power coefficient')).toBeTruthy()
  })

  it('renders LaTeX through KaTeX rather than showing raw source', async () => {
    const doc = fromYaml(readFileSync(EXAMPLE, 'utf-8'))
    await act(async () => {
      render(<App />)
    })
    await act(async () => {
      useEditor.setState({ doc })
    })
    expect(document.querySelector('.katex')).toBeTruthy()
    expect(document.querySelector('math')).toBeTruthy()
  })

  it('flags a placed source whose file is missing from the index', async () => {
    const doc = fromYaml(readFileSync(EXAMPLE, 'utf-8'))
    await act(async () => {
      render(<App />)
    })
    await act(async () => {
      useEditor.setState({ doc })
    })
    // No assets loaded, so every source reads as missing.
    expect(screen.getAllByText('file not found').length).toBeGreaterThan(0)
  })

  it('flags a source as stale when the file hash has moved on', async () => {
    const doc = fromYaml(readFileSync(EXAMPLE, 'utf-8'))
    const assets = Object.values(doc.sources).map((s) => ({
      path: s.path,
      name: s.path.split('/').pop()!,
      hash: 'sha256:something-else-entirely',
      size: 1,
      modified: '2026-07-28T00:00:00Z',
      mediaType: 'image/svg+xml',
    }))
    await act(async () => {
      render(<App />)
    })
    await act(async () => {
      useEditor.setState({
        doc,
        assets,
        assetsByPath: new Map(assets.map((a) => [a.path, a])),
      })
    })
    expect(screen.getAllByText('source changed').length).toBe(2)
  })
})

describe('editing', () => {
  it('undo restores the document from before an edit', async () => {
    await act(async () => {
      render(<App />)
    })
    await act(async () => {
      useEditor.getState().addNode(makeTextNode(10, 10, 'hello'))
    })
    expect(useEditor.getState().doc.nodes).toHaveLength(1)

    await act(async () => {
      useEditor.getState().undo()
    })
    expect(useEditor.getState().doc.nodes).toHaveLength(0)

    await act(async () => {
      useEditor.getState().redo()
    })
    expect(useEditor.getState().doc.nodes).toHaveLength(1)
  })

  it('a drag records exactly one undo step, not one per frame', async () => {
    await act(async () => {
      render(<App />)
    })
    await act(async () => {
      useEditor.getState().addNode(makeTextNode(10, 10))
    })
    const before = useEditor.getState().past.length

    // Simulate what the canvas does during a drag: snapshot once, then write
    // geometry repeatedly.
    const id = useEditor.getState().doc.nodes[0].id
    await act(async () => {
      useEditor.getState().pushHistory()
      for (let i = 0; i < 20; i += 1) {
        useEditor
          .getState()
          .setNodeRects({ [id]: { x: i, y: i, width: 60, height: 14 } })
      }
    })
    expect(useEditor.getState().past.length).toBe(before + 1)
    expect(useEditor.getState().doc.nodes[0].x).toBe(19)
  })

  it('deleting the selection clears it', async () => {
    await act(async () => {
      render(<App />)
    })
    await act(async () => {
      useEditor.getState().addNode(makeTextNode(0, 0))
    })
    expect(useEditor.getState().selection).toHaveLength(1)
    await act(async () => {
      useEditor.getState().deleteSelected()
    })
    expect(useEditor.getState().doc.nodes).toHaveLength(0)
    expect(useEditor.getState().selection).toHaveLength(0)
  })

  it('reorders the paint stack without dropping nodes', async () => {
    await act(async () => {
      render(<App />)
    })
    await act(async () => {
      useEditor.getState().addNode(makeTextNode(0, 0, 'bottom'))
      useEditor.getState().addNode(makeTextNode(0, 0, 'top'))
    })
    const topId = useEditor.getState().doc.nodes[1].id
    await act(async () => {
      useEditor.getState().select([topId])
      useEditor.getState().reorderSelected('back')
    })
    const ids = useEditor.getState().doc.nodes.map((n) => n.id)
    expect(ids).toHaveLength(2)
    expect(ids[0]).toBe(topId)
  })
})
