# The figmint document format

A figmint figure is a single `*.fig.yaml` file. It is the whole figure: which
artifacts it draws from, where each of them came from, and exactly where every
element sits on the page.

The format is designed to be edited by hand and by agents, not just by the GUI.
If you can't open it in an editor and understand it, that's a bug.

See `examples/two-panel.fig.yaml` for a complete, working example.

## Shape

```yaml
figmint: "0.1"           # format version
id: fig-turbine-performance
title: Cross-flow turbine performance
caption: >-
  (a) Power coefficient versus tip speed ratio. (b) Wake profile.

canvas:
  width: 468             # points; 1pt = 1/72in, so this is 6.5in
  height: 210
  units: pt
  background: null       # null = transparent, which is usually right for print
  grid: {size: 6, snap: true}

sources:                 # provenance ledger, keyed by a short readable slug
  cp-curve:
    path: figures/cp_curve.svg
    hash: sha256:ff1f3f…  # content hash when the panel was placed
    intrinsic: {width: 216, height: 162}
    provenance:
      generatedBy: scripts/plot_cp.py
      command: uv run python scripts/plot_cp.py --case baseline
      commit: "3f9a1c47…"
      importedAt: "2026-07-28T00:00:00Z"
      derivedFrom: [data/processed/performance.csv]

nodes:                   # paint order: later entries draw on top
  - {id: panel-a, type: image, source: cp-curve, x: 12, y: 36, width: 216, height: 162}
  - {id: label-a, type: text, text: "(a)", x: 12, y: 12, width: 24, height: 14}
  - {id: eq-cp, type: math, tex: 'C_P = \frac{P}{\tfrac{1}{2}\rho A U_\infty^3}', …}
  - {id: roi-peak, type: rect, label: Peak, x: 108, y: 54, width: 48, height: 42}
```

### Why points

Geometry is always stored in points. A figure authored at 468pt wide lands at
exactly 6.5in in a PDF with no scaling step, and a 9pt label really is 9pt —
the same number you'd put in a LaTeX preamble. Zoom is a view setting and never
touches the document.

### Why sources are keyed, not inlined

Nodes reference a source by key (`source: cp-curve`) rather than by path. So
re-pointing a source updates every panel using it at once, and the provenance
record — the expensive, interesting part — is written once rather than per panel.

## Staleness

`sources.<key>.hash` is the content hash of the file at the moment the panel was
placed. On every scan the backend re-hashes what is actually on disk and the
editor compares:

| State | Meaning |
| --- | --- |
| `ok` | hash matches — the panel reflects the current artifact |
| `stale` | file changed since the panel was placed — the figure may be out of date |
| `missing` | the file is gone |
| `unknown` | no hash recorded (hand-written YAML) — nothing can be said |

This deliberately mirrors how Stencila decides whether a node needs
re-execution: it compares a node's `compilationDigest` against the
`executionDigest` captured at the last run.

### Provenance sidecars

figmint doesn't try to understand every pipeline tool. Instead, whatever builds
an artifact may drop a `<name>.prov.yaml` next to it:

```yaml
# figures/cp_curve.svg.prov.yaml
generatedBy: scripts/plot_cp.py
command: uv run python scripts/plot_cp.py --case baseline
commit: "3f9a1c47b28e5d06a4f1e0b39c7d2a58e6104fbb"
derivedFrom: [data/processed/performance.csv]
```

The backend reads it during the scan and it is copied into the document when the
panel is placed. This is the seam where Calkit, DVC, or a plain Makefile can
declare what produced a figure.

## Relationship to Stencila

Stencila's schema (v2.15) covers more of this than expected, so the exporter
leans on native constructs rather than inventing new ones:

| figmint | Stencila |
| --- | --- |
| composite figure | `::: figure #id [layout]` |
| panel arrangement | `Figure.layout` grid language, e.g. `[30 70 : a b \| a c]` |
| image panel | nested `::: figure` containing an `ImageObject` |
| text, math, boxes, arrows | `Figure.overlay` — an absolutely-positioned SVG layer |
| labelled box / arrow | `<s:roi-rect>` / `<s:arrow>` in the `s:` component namespace |
| caption | trailing paragraph of the figure |

### The one gap, and how it's bridged

Stencila's `ImageObject` has **no** `x`/`y`/`width`/`height`, and there is no
per-node `extra` bag for custom properties. So free-form panel geometry has
nowhere native to live.

What *does* survive a round trip is unknown keys in an article's YAML
frontmatter — they land in `Article.extra`, which is preserved across
`md`/`smd`/`myst`/`qmd`/`docx`. So the exporter writes:

- a **grid approximation** in the body, inferred from the panel positions, which
  is what any Stencila consumer renders; and
- the **authoritative geometry** under a `figmint:` frontmatter key, which
  figmint reads back to restore exact positions.

Nothing is lost, and the `.smd` renders sensibly in tools that have never heard
of figmint.

### Annotations are already pixel-perfect

Absolute positioning for annotations needs no bridge — `Figure.overlay` is an
SVG layer positioned over the content area and scaled via its `viewBox`, which
is exactly the model figmint uses. Stencila's `s:` namespace supplies arrows,
callouts, ROI boxes, scale bars, and halos; plain SVG elements pass through
untouched.

## Known limitations

- **The SMD export has not been validated against the real Stencila decoder.**
  It is written to the documented syntax, but `stencila convert` was not
  available in this environment. Round-tripping a `.smd` back into figmint is
  not implemented yet either — export is currently one-way.
- **Math in the overlay uses `<foreignObject>` + MathML.** That renders in
  browsers, so HTML output is fine, but many SVG→PDF paths drop `foreignObject`.
  The TeX travels verbatim in the frontmatter, so a future exporter can swap in
  real vector glyphs without any loss.
- **Layout inference is an approximation.** Panels are banded into rows by
  vertical overlap. Overlapping or deliberately off-grid arrangements fall back
  to a placement map, and the note is surfaced in the export dialog.
- **Rotation is stored but not exported** to the Stencila overlay.
- **Nested composite figures** — a figmint document embedding another figmint
  document — are not implemented. That's the knowledge-graph direction in
  `editor-design.md` and needs a source type that points at a document rather
  than an image.
