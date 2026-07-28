import { useMemo } from 'react'
import katex from 'katex'
import type {
  ArrowNode,
  EllipseNode,
  FigmintDocument,
  FigNode,
  ImageNode,
  MathNode,
  RectNode,
  TextNode,
} from '../model/types'
import { assetUrl } from '../io/api'

/**
 * Renders one document node as SVG.
 *
 * Everything on the canvas is real SVG in document coordinates — the same
 * elements the exporter emits. That keeps what you see and what you publish in
 * agreement, and means the canvas can be serialized to a print-ready vector file
 * without a separate rendering path.
 */

interface Props {
  node: FigNode
  doc: FigmintDocument
  selected: boolean
  onPointerDown: (event: React.PointerEvent, node: FigNode) => void
}

function transformFor(node: FigNode): string | undefined {
  if (!node.rotation) return undefined
  const cx = node.x + node.width / 2
  const cy = node.y + node.height / 2
  return `rotate(${node.rotation} ${cx} ${cy})`
}

function ImageView({ node, doc }: { node: ImageNode; doc: FigmintDocument }) {
  const source = doc.sources[node.source]
  if (!source) {
    return (
      <g>
        <rect
          x={node.x}
          y={node.y}
          width={node.width}
          height={node.height}
          fill="#fff0f0"
          stroke="#d1495b"
          strokeWidth={0.5}
          strokeDasharray="3 2"
        />
        <text
          x={node.x + node.width / 2}
          y={node.y + node.height / 2}
          textAnchor="middle"
          fontSize={7}
          fill="#d1495b"
        >
          missing source: {node.source}
        </text>
      </g>
    )
  }
  const preserve =
    node.fit === 'fill'
      ? 'none'
      : node.fit === 'cover'
        ? 'xMidYMid slice'
        : 'xMidYMid meet'
  return (
    <image
      href={assetUrl(source.path)}
      x={node.x}
      y={node.y}
      width={node.width}
      height={node.height}
      preserveAspectRatio={preserve}
    />
  )
}

function TextView({ node }: { node: TextNode }) {
  const st = node.style ?? {}
  const size = st.fontSize ?? 9
  const lineHeight = size * (st.lineHeight ?? 1.2)
  const anchor =
    st.align === 'center' ? 'middle' : st.align === 'right' ? 'end' : 'start'
  const x =
    st.align === 'center'
      ? node.x + node.width / 2
      : st.align === 'right'
        ? node.x + node.width
        : node.x
  return (
    <text
      x={x}
      y={node.y + size}
      textAnchor={anchor}
      fontFamily={st.fontFamily ?? 'Helvetica, Arial, sans-serif'}
      fontSize={size}
      fontWeight={st.fontWeight}
      fontStyle={st.fontStyle}
      fill={st.color ?? '#111111'}
      style={{ whiteSpace: 'pre' }}
    >
      {node.text.split('\n').map((line, i) => (
        <tspan key={i} x={x} dy={i === 0 ? 0 : lineHeight}>
          {line}
        </tspan>
      ))}
    </text>
  )
}

function MathView({ node }: { node: MathNode }) {
  const html = useMemo(() => {
    try {
      return katex.renderToString(node.tex, {
        throwOnError: false,
        displayMode: node.display ?? false,
        output: 'htmlAndMathml',
      })
    } catch (err) {
      return `<span style="color:#d1495b">${
        err instanceof Error ? err.message : 'TeX error'
      }</span>`
    }
  }, [node.tex, node.display])

  return (
    <foreignObject
      x={node.x}
      y={node.y}
      width={node.width}
      height={node.height}
      style={{ overflow: 'visible' }}
    >
      <div
        // KaTeX needs a real DOM subtree; foreignObject gives us one inside SVG.
        // React applies the XHTML namespace to foreignObject children for us.
        style={{
          fontSize: `${node.fontSize ?? 10}px`,
          color: node.color ?? '#111111',
          lineHeight: 1.2,
        }}
        dangerouslySetInnerHTML={{ __html: html }}
      />
    </foreignObject>
  )
}

function RectView({ node }: { node: RectNode }) {
  const st = node.style ?? {}
  return (
    <g>
      <rect
        x={node.x}
        y={node.y}
        width={node.width}
        height={node.height}
        rx={st.cornerRadius ?? 0}
        fill={st.fill ?? 'none'}
        stroke={st.stroke ?? '#111111'}
        strokeWidth={st.strokeWidth ?? 1}
        strokeDasharray={st.strokeDash ?? undefined}
      />
      {node.label && (
        <text
          x={node.x}
          y={node.y - 2}
          fontSize={7}
          fill={st.stroke ?? '#111111'}
          fontFamily="Helvetica, Arial, sans-serif"
        >
          {node.label}
        </text>
      )}
    </g>
  )
}

function EllipseView({ node }: { node: EllipseNode }) {
  const st = node.style ?? {}
  return (
    <ellipse
      cx={node.x + node.width / 2}
      cy={node.y + node.height / 2}
      rx={node.width / 2}
      ry={node.height / 2}
      fill={st.fill ?? 'none'}
      stroke={st.stroke ?? '#111111'}
      strokeWidth={st.strokeWidth ?? 1}
      strokeDasharray={st.strokeDash ?? undefined}
    />
  )
}

function ArrowView({ node }: { node: ArrowNode }) {
  const st = node.style ?? {}
  const x1 = node.x + node.from.x * node.width
  const y1 = node.y + node.from.y * node.height
  const x2 = node.x + node.to.x * node.width
  const y2 = node.y + node.to.y * node.height
  const stroke = st.stroke ?? '#111111'

  let d = `M ${x1} ${y1} L ${x2} ${y2}`
  if (node.curve === 'quad') {
    // Bow the line perpendicular to its own direction by a fixed fraction.
    const mx = (x1 + x2) / 2
    const my = (y1 + y2) / 2
    const dx = x2 - x1
    const dy = y2 - y1
    d = `M ${x1} ${y1} Q ${mx - dy * 0.2} ${my + dx * 0.2} ${x2} ${y2}`
  } else if (node.curve === 'elbow') {
    d = `M ${x1} ${y1} L ${x2} ${y1} L ${x2} ${y2}`
  }

  return (
    <g>
      <path
        d={d}
        fill="none"
        stroke={stroke}
        strokeWidth={st.strokeWidth ?? 1}
        strokeDasharray={st.strokeDash ?? undefined}
        markerEnd="url(#fm-arrowhead)"
      />
      {node.label && (
        <text
          x={(x1 + x2) / 2}
          y={(y1 + y2) / 2 - 3}
          fontSize={7}
          textAnchor="middle"
          fill={stroke}
          fontFamily="Helvetica, Arial, sans-serif"
        >
          {node.label}
        </text>
      )}
    </g>
  )
}

export function NodeView({ node, doc, selected, onPointerDown }: Props) {
  if (node.hidden) return null

  const body = (() => {
    switch (node.type) {
      case 'image':
        return <ImageView node={node} doc={doc} />
      case 'text':
        return <TextView node={node} />
      case 'math':
        return <MathView node={node} />
      case 'rect':
        return <RectView node={node} />
      case 'ellipse':
        return <EllipseView node={node} />
      case 'arrow':
        return <ArrowView node={node} />
    }
  })()

  return (
    <g
      transform={transformFor(node)}
      opacity={node.opacity ?? 1}
      onPointerDown={(e) => onPointerDown(e, node)}
      style={{ cursor: node.locked ? 'not-allowed' : 'move' }}
      data-node-id={node.id}
      data-selected={selected || undefined}
    >
      {body}
      {/*
        Thin strokes and text are hard to grab. An invisible hit rect over the
        node's box makes every node equally clickable at any zoom level.
      */}
      <rect
        x={node.x}
        y={node.y}
        width={node.width}
        height={node.height}
        fill="transparent"
        stroke="none"
        pointerEvents={node.locked ? 'none' : 'all'}
      />
    </g>
  )
}
