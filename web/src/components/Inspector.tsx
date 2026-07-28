import { useEditor } from '../state/store'
import type { FigNode, ShapeStyle, TextStyle } from '../model/types'

/**
 * Right rail: properties of the current selection, and document settings when
 * nothing is selected.
 *
 * Every control writes straight to the document model — there is no separate
 * "style" layer — so what you set here is exactly what lands in the YAML.
 */

export function Inspector() {
  const doc = useEditor((s) => s.doc)
  const selection = useEditor((s) => s.selection)
  const updateNode = useEditor((s) => s.updateNode)
  const pushHistory = useEditor((s) => s.pushHistory)
  const patchDoc = useEditor((s) => s.patchDoc)

  const nodes = doc.nodes.filter((n) => selection.includes(n.id))

  if (nodes.length === 0) {
    return (
      <aside className="panel panel-right">
        <header className="panel-header">
          <h2>Document</h2>
        </header>
        <Field label="Title">
          <input
            className="input"
            value={doc.title ?? ''}
            onChange={(e) => patchDoc((d) => void (d.title = e.target.value), false)}
          />
        </Field>
        <Field label="Figure id">
          <input
            className="input"
            value={doc.id}
            onChange={(e) => patchDoc((d) => void (d.id = e.target.value), false)}
          />
        </Field>
        <Field label="Caption">
          <textarea
            className="input"
            rows={4}
            value={doc.caption ?? ''}
            onChange={(e) =>
              patchDoc((d) => void (d.caption = e.target.value), false)
            }
          />
        </Field>
        <div className="row">
          <Field label={`Width (${doc.canvas.units})`}>
            <NumberInput
              value={doc.canvas.width}
              onChange={(v) => patchDoc((d) => void (d.canvas.width = v))}
            />
          </Field>
          <Field label={`Height (${doc.canvas.units})`}>
            <NumberInput
              value={doc.canvas.height}
              onChange={(v) => patchDoc((d) => void (d.canvas.height = v))}
            />
          </Field>
        </div>
        <div className="row">
          <Field label="Grid">
            <NumberInput
              value={doc.canvas.grid?.size ?? 0}
              onChange={(v) =>
                patchDoc((d) => {
                  d.canvas.grid = { size: v, snap: d.canvas.grid?.snap ?? true }
                })
              }
            />
          </Field>
          <Field label="Snap">
            <input
              type="checkbox"
              checked={doc.canvas.grid?.snap ?? false}
              onChange={(e) =>
                patchDoc((d) => {
                  d.canvas.grid = {
                    size: d.canvas.grid?.size ?? 6,
                    snap: e.target.checked,
                  }
                })
              }
            />
          </Field>
        </div>
        <p className="muted small">
          Geometry is stored in points (1pt = 1/72in) so the figure lands at true
          size in a PDF.
        </p>
      </aside>
    )
  }

  const node = nodes[0]
  const multiple = nodes.length > 1
  const set = (patch: Partial<FigNode>) => {
    for (const n of nodes) updateNode(n.id, patch)
  }

  return (
    <aside className="panel panel-right">
      <header className="panel-header">
        <h2>{multiple ? `${nodes.length} selected` : node.type}</h2>
      </header>

      <div className="row">
        <Field label="X">
          <NumberInput
            value={node.x}
            onChange={(v) => {
              pushHistory()
              set({ x: v })
            }}
          />
        </Field>
        <Field label="Y">
          <NumberInput
            value={node.y}
            onChange={(v) => {
              pushHistory()
              set({ y: v })
            }}
          />
        </Field>
      </div>
      <div className="row">
        <Field label="W">
          <NumberInput
            value={node.width}
            onChange={(v) => {
              pushHistory()
              set({ width: v })
            }}
          />
        </Field>
        <Field label="H">
          <NumberInput
            value={node.height}
            onChange={(v) => {
              pushHistory()
              set({ height: v })
            }}
          />
        </Field>
      </div>
      <div className="row">
        <Field label="Rotation">
          <NumberInput
            value={node.rotation ?? 0}
            onChange={(v) => set({ rotation: v })}
          />
        </Field>
        <Field label="Opacity">
          <NumberInput
            value={node.opacity ?? 1}
            step={0.05}
            onChange={(v) => set({ opacity: Math.min(1, Math.max(0, v)) })}
          />
        </Field>
      </div>

      {!multiple && node.type === 'text' && (
        <>
          <Field label="Text">
            <textarea
              className="input"
              rows={3}
              value={node.text}
              onChange={(e) => updateNode(node.id, { text: e.target.value })}
            />
          </Field>
          <TextStyleFields
            style={node.style ?? {}}
            onChange={(style) => updateNode(node.id, { style })}
          />
        </>
      )}

      {!multiple && node.type === 'math' && (
        <>
          <Field label="LaTeX">
            <textarea
              className="input mono"
              rows={3}
              value={node.tex}
              onChange={(e) => updateNode(node.id, { tex: e.target.value })}
            />
          </Field>
          <div className="row">
            <Field label="Size">
              <NumberInput
                value={node.fontSize ?? 10}
                onChange={(v) => updateNode(node.id, { fontSize: v })}
              />
            </Field>
            <Field label="Color">
              <input
                type="color"
                value={node.color ?? '#111111'}
                onChange={(e) => updateNode(node.id, { color: e.target.value })}
              />
            </Field>
          </div>
          <Field label="Display mode">
            <input
              type="checkbox"
              checked={node.display ?? false}
              onChange={(e) =>
                updateNode(node.id, { display: e.target.checked })
              }
            />
          </Field>
        </>
      )}

      {!multiple && node.type === 'image' && (
        <>
          <Field label="Source">
            <code className="source-ref">{node.source}</code>
          </Field>
          <Field label="Path">
            <code className="source-ref small">
              {doc.sources[node.source]?.path ?? '—'}
            </code>
          </Field>
          <Field label="Fit">
            <select
              className="input"
              value={node.fit ?? 'contain'}
              onChange={(e) =>
                updateNode(node.id, {
                  fit: e.target.value as 'contain' | 'cover' | 'fill',
                })
              }
            >
              <option value="contain">contain</option>
              <option value="cover">cover</option>
              <option value="fill">fill</option>
            </select>
          </Field>
        </>
      )}

      {!multiple &&
        (node.type === 'rect' ||
          node.type === 'ellipse' ||
          node.type === 'arrow') && (
          <>
            <Field label="Label">
              <input
                className="input"
                value={node.label ?? ''}
                placeholder="e.g. Region A"
                onChange={(e) =>
                  updateNode(node.id, { label: e.target.value || undefined })
                }
              />
            </Field>
            <ShapeStyleFields
              style={node.style ?? {}}
              onChange={(style) => updateNode(node.id, { style })}
            />
            <p className="muted small">
              Labelled boxes and arrows export as Stencila overlay components
              (<code>s:roi-rect</code>, <code>s:arrow</code>).
            </p>
          </>
        )}

      <Field label="Name">
        <input
          className="input"
          value={node.name ?? ''}
          placeholder="Panel caption / layer name"
          onChange={(e) => set({ name: e.target.value || undefined })}
        />
      </Field>
    </aside>
  )
}

function TextStyleFields({
  style,
  onChange,
}: {
  style: TextStyle
  onChange: (s: TextStyle) => void
}) {
  return (
    <>
      <div className="row">
        <Field label="Size">
          <NumberInput
            value={style.fontSize ?? 9}
            onChange={(v) => onChange({ ...style, fontSize: v })}
          />
        </Field>
        <Field label="Color">
          <input
            type="color"
            value={style.color ?? '#111111'}
            onChange={(e) => onChange({ ...style, color: e.target.value })}
          />
        </Field>
      </div>
      <Field label="Font">
        <input
          className="input"
          value={style.fontFamily ?? ''}
          placeholder="Helvetica, Arial, sans-serif"
          onChange={(e) => onChange({ ...style, fontFamily: e.target.value })}
        />
      </Field>
      <div className="row">
        <Field label="Align">
          <select
            className="input"
            value={style.align ?? 'left'}
            onChange={(e) =>
              onChange({
                ...style,
                align: e.target.value as TextStyle['align'],
              })
            }
          >
            <option value="left">left</option>
            <option value="center">center</option>
            <option value="right">right</option>
          </select>
        </Field>
        <Field label="Bold">
          <input
            type="checkbox"
            checked={style.fontWeight === 'bold' || style.fontWeight === 700}
            onChange={(e) =>
              onChange({
                ...style,
                fontWeight: e.target.checked ? 'bold' : 'normal',
              })
            }
          />
        </Field>
      </div>
    </>
  )
}

function ShapeStyleFields({
  style,
  onChange,
}: {
  style: ShapeStyle
  onChange: (s: ShapeStyle) => void
}) {
  return (
    <>
      <div className="row">
        <Field label="Stroke">
          <input
            type="color"
            value={style.stroke ?? '#111111'}
            onChange={(e) => onChange({ ...style, stroke: e.target.value })}
          />
        </Field>
        <Field label="Width">
          <NumberInput
            value={style.strokeWidth ?? 1}
            step={0.25}
            onChange={(v) => onChange({ ...style, strokeWidth: v })}
          />
        </Field>
      </div>
      <div className="row">
        <Field label="Fill">
          <input
            type="color"
            value={style.fill ?? '#ffffff'}
            onChange={(e) => onChange({ ...style, fill: e.target.value })}
          />
        </Field>
        <Field label="No fill">
          <input
            type="checkbox"
            checked={!style.fill}
            onChange={(e) =>
              onChange({ ...style, fill: e.target.checked ? null : '#ffffff' })
            }
          />
        </Field>
      </div>
      <Field label="Dash">
        <input
          className="input"
          value={style.strokeDash ?? ''}
          placeholder="e.g. 4 2"
          onChange={(e) =>
            onChange({ ...style, strokeDash: e.target.value || null })
          }
        />
      </Field>
    </>
  )
}

function Field({
  label,
  children,
}: {
  label: string
  children: React.ReactNode
}) {
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      {children}
    </label>
  )
}

function NumberInput({
  value,
  onChange,
  step = 1,
}: {
  value: number
  onChange: (v: number) => void
  step?: number
}) {
  return (
    <input
      className="input"
      type="number"
      step={step}
      value={Number.isFinite(value) ? Math.round(value * 100) / 100 : 0}
      onChange={(e) => {
        const v = Number.parseFloat(e.target.value)
        if (Number.isFinite(v)) onChange(v)
      }}
    />
  )
}
