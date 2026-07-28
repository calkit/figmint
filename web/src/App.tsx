import { useState } from 'react'
import { Canvas } from './components/Canvas'
import { Toolbar } from './components/Toolbar'
import { AssetPanel } from './components/AssetPanel'
import { Inspector } from './components/Inspector'
import { LayerPanel } from './components/LayerPanel'
import { ExportDialog } from './components/ExportDialog'
import { useKeyboard } from './hooks/useKeyboard'
import { useEditor } from './state/store'
import './app.css'

export default function App() {
  useKeyboard()
  const [exporting, setExporting] = useState(false)
  const status = useEditor((s) => s.status)

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
      <footer className="statusbar">
        <span>{status ?? 'Ready'}</span>
        <span className="muted small">
          drag to move · shift-click multi-select · space-drag to pan ·
          ⌘/ctrl-scroll to zoom · alt to bypass snap
        </span>
      </footer>
      {exporting && <ExportDialog onClose={() => setExporting(false)} />}
    </div>
  )
}
