import { useState } from 'react'
import { Canvas } from './components/Canvas'
import { Toolbar } from './components/Toolbar'
import { AssetPanel } from './components/AssetPanel'
import { Inspector } from './components/Inspector'
import { LayerPanel } from './components/LayerPanel'
import { ExportDialog } from './components/ExportDialog'
import { useKeyboard } from './hooks/useKeyboard'
import { useWatch } from './hooks/useWatch'
import { useEditor } from './state/store'
import './app.css'

export default function App() {
  useKeyboard()
  const watch = useWatch()
  const [exporting, setExporting] = useState(false)
  const status = useEditor((s) => s.status)
  const externalEdit = useEditor((s) => s.externalEdit)
  const reloadFromDisk = useEditor((s) => s.reloadFromDisk)

  return (
    <div className="app">
      <Toolbar onExport={() => setExporting(true)} />
      <div className="workspace">
        <AssetPanel />
        <main className="stage">
          <Canvas />
          <LayerPanel />
        </main>
        <Inspector />
      </div>
      {externalEdit && (
        <div className="conflict-bar">
          <span>
            This document changed on disk while you have unsaved edits. Reloading
            discards yours; saving overwrites theirs.
          </span>
          <button className="btn-small" onClick={() => void reloadFromDisk()}>
            Reload from disk
          </button>
        </div>
      )}
      <footer className="statusbar">
        <span>
          <span className={`watch-dot watch-${watch}`} title={`Watcher ${watch}`} />
          {status ?? 'Ready'}
        </span>
        <span className="muted small">
          drag to move · shift-click multi-select · space-drag to pan ·
          ⌘/ctrl-scroll to zoom · alt to bypass snap
        </span>
      </footer>
      {exporting && <ExportDialog onClose={() => setExporting(false)} />}
    </div>
  )
}
