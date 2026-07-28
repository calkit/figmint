import { useEditor } from '../state/store'
import { displayName } from '../model/document'

const TYPE_GLYPH: Record<string, string> = {
  image: '▣',
  text: 'T',
  math: '∑',
  rect: '▭',
  ellipse: '◯',
  arrow: '↗',
}

/** Layer list. Rendered top-of-stack first, matching what you see on canvas. */
export function LayerPanel() {
  const doc = useEditor((s) => s.doc)
  const selection = useEditor((s) => s.selection)
  const select = useEditor((s) => s.select)
  const toggleSelect = useEditor((s) => s.toggleSelect)
  const updateNode = useEditor((s) => s.updateNode)
  const reorderSelected = useEditor((s) => s.reorderSelected)

  const ordered = [...doc.nodes].reverse()

  return (
    <div className="layer-panel">
      <header className="panel-header">
        <h2>Layers</h2>
        <div className="btn-row">
          <button
            className="btn-ghost"
            onClick={() => reorderSelected('forward')}
            title="Bring forward"
          >
            ↑
          </button>
          <button
            className="btn-ghost"
            onClick={() => reorderSelected('backward')}
            title="Send backward"
          >
            ↓
          </button>
        </div>
      </header>
      <ul className="layer-list">
        {ordered.map((node) => (
          <li
            key={node.id}
            className={selection.includes(node.id) ? 'layer selected' : 'layer'}
            onClick={(e) =>
              e.shiftKey ? toggleSelect(node.id) : select([node.id])
            }
          >
            <span className="layer-glyph">{TYPE_GLYPH[node.type] ?? '•'}</span>
            <span className="layer-name">{displayName(node)}</span>
            <button
              className="btn-icon"
              title={node.hidden ? 'Show' : 'Hide'}
              onClick={(e) => {
                e.stopPropagation()
                updateNode(node.id, { hidden: !node.hidden })
              }}
            >
              {node.hidden ? '◌' : '◉'}
            </button>
            <button
              className="btn-icon"
              title={node.locked ? 'Unlock' : 'Lock'}
              onClick={(e) => {
                e.stopPropagation()
                updateNode(node.id, { locked: !node.locked })
              }}
            >
              {node.locked ? '🔒' : '🔓'}
            </button>
          </li>
        ))}
        {ordered.length === 0 && <li className="muted">Canvas is empty</li>}
      </ul>
    </div>
  )
}
