import { useMemo, useState } from 'react'
import { useEditor } from '../state/store'
import { toYaml } from '../io/serialize'
import { toStencilaMarkdown } from '../io/stencila'
import { downloadText } from '../io/api'

type Tab = 'yaml' | 'smd'

/**
 * Shows both serializations side by side so you can see exactly what will be
 * written before anything touches disk. Both are meant to be readable and
 * hand-editable — if either looks like machine sludge, that's a bug.
 */
export function ExportDialog({ onClose }: { onClose: () => void }) {
  const doc = useEditor((s) => s.doc)
  const [tab, setTab] = useState<Tab>('yaml')

  const yaml = useMemo(() => toYaml(doc), [doc])
  const stencila = useMemo(() => toStencilaMarkdown(doc), [doc])

  const text = tab === 'yaml' ? yaml : stencila.smd
  const filename = tab === 'yaml' ? `${doc.id}.fig.yaml` : `${doc.id}.smd`
  const mime = tab === 'yaml' ? 'application/yaml' : 'text/markdown'

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <header className="modal-header">
          <div className="tabs">
            <button
              className={tab === 'yaml' ? 'tab active' : 'tab'}
              onClick={() => setTab('yaml')}
            >
              figmint YAML
            </button>
            <button
              className={tab === 'smd' ? 'tab active' : 'tab'}
              onClick={() => setTab('smd')}
            >
              Stencila Markdown
            </button>
          </div>
          <button className="btn-ghost" onClick={onClose}>
            ✕
          </button>
        </header>

        {tab === 'smd' && (
          <p className="notice">
            Panels are arranged with{' '}
            <code>{stencila.layout.layout ?? 'no grid layout'}</code>; annotations
            become an SVG overlay. Exact geometry round-trips through the{' '}
            <code>figmint:</code> frontmatter key.
            {stencila.layout.note && <> {stencila.layout.note}</>}
          </p>
        )}

        <pre className="code-view">{text}</pre>

        <footer className="modal-footer">
          <button
            className="btn"
            onClick={() => void navigator.clipboard.writeText(text)}
          >
            Copy
          </button>
          <button
            className="btn"
            onClick={() => downloadText(filename, text, mime)}
          >
            Download {filename}
          </button>
        </footer>
      </div>
    </div>
  )
}
