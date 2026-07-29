import { useState } from 'react'
import { useEditor } from '../state/store'
import { makeId, makeMathNode, makeTextNode } from '../model/document'
import { buildDocument } from '../io/api'
import type { FigNode } from '../model/types'

export function Toolbar({ onExport }: { onExport: () => void }) {
  const doc = useEditor((s) => s.doc)
  const documentPath = useEditor((s) => s.documentPath)
  const dirty = useEditor((s) => s.dirty)
  const past = useEditor((s) => s.past)
  const future = useEditor((s) => s.future)
  const undo = useEditor((s) => s.undo)
  const redo = useEditor((s) => s.redo)
  const addNode = useEditor((s) => s.addNode)
  const newDocument = useEditor((s) => s.newDocument)
  const save = useEditor((s) => s.save)
  const openDocument = useEditor((s) => s.openDocument)
  const setStatus = useEditor((s) => s.setStatus)
  const zoomBy = useEditor((s) => s.zoomBy)
  const selection = useEditor((s) => s.selection)
  const groupSelection = useEditor((s) => s.groupSelection)

  const [busy, setBusy] = useState(false)

  /** Drop new nodes near the middle of the page so they're always visible. */
  const center = () => ({
    x: doc.canvas.width / 2 - 30,
    y: doc.canvas.height / 2 - 10,
  })

  const addShape = (type: 'rect' | 'ellipse' | 'arrow') => {
    const c = center()
    const base = {
      id: makeId(type),
      x: c.x,
      y: c.y,
      width: 60,
      height: 40,
      style: { stroke: '#d1495b', strokeWidth: 1, fill: null },
    }
    const node: FigNode =
      type === 'arrow'
        ? {
            ...base,
            type: 'arrow',
            from: { x: 0, y: 1 },
            to: { x: 1, y: 0 },
            curve: 'straight',
          }
        : { ...base, type }
    addNode(node)
  }

  const doSave = async () => {
    setBusy(true)
    try {
      let target = documentPath
      if (!target) {
        const suggested = `figures/${doc.id}.fig.yaml`
        const entered = window.prompt('Save document as:', suggested)
        if (!entered) return
        target = entered
      }
      await save(target)
    } catch (err) {
      setStatus(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  /**
   * Build composes from the saved file, not from memory, so the artifact always
   * matches a document someone else could open. Save first when dirty.
   */
  const doBuild = async () => {
    setBusy(true)
    try {
      let target = documentPath
      if (!target || dirty) {
        await doSave()
        target = useEditor.getState().documentPath
        if (!target) return
      }
      const result = await buildDocument(target, ['svg'])
      const names = result.outputs.map((o) => o.path).join(', ')
      setStatus(
        result.warnings.length > 0
          ? `Built ${names} — ${result.warnings[0]}`
          : `Built ${names}`,
      )
    } catch (err) {
      setStatus(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const doOpen = async () => {
    const path = window.prompt('Open document path:', 'figures/example.fig.yaml')
    if (!path) return
    setBusy(true)
    try {
      await openDocument(path)
    } catch (err) {
      setStatus(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="toolbar">
      <div className="toolbar-group">
        <strong className="brand">figmint</strong>
      </div>

      <div className="toolbar-group">
        <button className="btn" onClick={newDocument}>
          New
        </button>
        <button className="btn" onClick={() => void doOpen()} disabled={busy}>
          Open…
        </button>
        <button className="btn" onClick={() => void doSave()} disabled={busy}>
          Save{dirty ? ' •' : ''}
        </button>
        <button
          className="btn"
          onClick={() => void doBuild()}
          disabled={busy}
          title="Compose into a self-contained SVG next to the document"
        >
          Build
        </button>
        <button className="btn" onClick={onExport}>
          Export…
        </button>
      </div>

      <div className="toolbar-group">
        <button className="btn" onClick={undo} disabled={past.length === 0}>
          Undo
        </button>
        <button className="btn" onClick={redo} disabled={future.length === 0}>
          Redo
        </button>
      </div>

      <div className="toolbar-group">
        <button
          className="btn"
          onClick={() => {
            const c = center()
            addNode(makeTextNode(c.x, c.y))
          }}
          title="Add a text label"
        >
          Text
        </button>
        <button
          className="btn"
          onClick={() => {
            const c = center()
            addNode(makeMathNode(c.x, c.y))
          }}
          title="Add LaTeX"
        >
          Math
        </button>
        <button className="btn" onClick={() => addShape('rect')}>
          Box
        </button>
        <button className="btn" onClick={() => addShape('ellipse')}>
          Ellipse
        </button>
        <button className="btn" onClick={() => addShape('arrow')}>
          Arrow
        </button>
      </div>

      <div className="toolbar-group">
        <button
          className="btn"
          onClick={() => groupSelection({ type: 'grid', gap: 6, fit: 'preserve' })}
          disabled={selection.length < 2}
          title="Arrange the selection in a grid (⌘G)"
        >
          Grid
        </button>
        <button
          className="btn"
          onClick={() => groupSelection(null)}
          disabled={selection.length < 2}
          title="Group without a layout — moves together, keeps positions"
        >
          Group
        </button>
      </div>

      <div className="toolbar-group toolbar-right">
        <button className="btn-ghost" onClick={() => zoomBy(1 / 1.2)}>
          −
        </button>
        <button className="btn-ghost" onClick={() => zoomBy(1.2)}>
          +
        </button>
        <span className="doc-path" title={documentPath ?? 'unsaved'}>
          {documentPath ?? 'unsaved'}
        </span>
      </div>
    </div>
  )
}
