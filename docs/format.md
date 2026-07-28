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

### Accepting a change

A `stale` component is cleared by `figmint accept`, never by `figmint build`.
The separation is the point: rebuilding is mechanical, but deciding that a
regenerated panel still says what the figure claims it says is a judgement. If
building silently re-recorded hashes, every rebuild would destroy the evidence
that a component had moved underneath the figure.

Accepting re-reads the component and updates `hash`, `size`, `modified`,
`intrinsic`, and `credentials`, and stamps `provenance.acceptedAt`. If the
component has *lost* a manifest it previously had, the recorded `credentials`
are removed rather than left behind asserting provenance the file no longer has.

The rewrite goes through a round-trip YAML parser, so comments, key order,
blank lines, and explicit `null`s survive. A format that claims to be
hand-editable cannot reformat itself behind the author's back.

### Content Credentials (C2PA)

The primary provenance source is the signed [C2PA](https://c2pa.org) manifest
embedded in the artifact itself, when the producing tool emits one. It beats
anything figmint could infer, because it is cryptographically bound to the
file's contents rather than asserted by a text file sitting next to it.

figmint reads it on every scan and records it on the source:

```yaml
credentials:
  validationState: Valid        # Trusted | Valid | Invalid
  signedBy: Stencila local signing identity
  claimGenerator: Stencila CLI 2.15.0
  softwareAgent: Stencila 2.15.0
  digitalSourceType: digitalCreation
  sourceTypeLabel: created in software by a human
  machineGenerated: false
  ingredients:
    - title: Experimental setup schematic
      format: text/smd
      relationship: inputTo
      hasManifest: true
  stencila:
    contentDigest: sha256:eab1f75f…
    edgeKinds: [ConvertedInto, Generated, ReadBy, UsedBy]
```

Three fields carry most of the weight:

- **`digitalSourceType`** — the IPTC vocabulary value from the signed creation
  action. `trainedAlgorithmicMedia` means generative AI;
  `compositeWithTrainedAlgorithmicMedia` means *some element* was. This is the
  direct answer to the "deep fake figure" concern in `editor-design.md`, and it
  is why `machineGenerated` is derived from this field rather than guessed.
  Note that `algorithmicMedia` (a matplotlib plot) is deliberately *not* flagged
  as AI — conflating the two would cry wolf on every scripted figure.
- **`validationState`** — `Invalid` means the file was altered after signing.
  `Valid` means the signature verifies but the signer is not in a trust list,
  which is what every locally-signed development artifact looks like, so the UI
  does not present it as a success.
- **`ingredients`** — how C2PA expresses composition. `componentOf` is "this was
  composited from that", `parentOf` is "edited from", `inputTo` is "fed into".
  When an ingredient carries its own manifest the chain nests, which is exactly
  the knowledge graph `editor-design.md` asks for.

#### Signing changes the file

Embedding a manifest rewrites the bytes, so a freshly-signed artifact no longer
matches the hash figmint recorded at import. Stencila's `org.stencila.provenance`
assertion carries `org.stencila.contentDigest` — the digest *before* signing —
and `sourceStatus` checks it before declaring a panel stale. Without this,
signing a component would spuriously mark every figure using it as out of date.

### Provenance sidecars (fallback)

Not every tool signs its output. For those, whatever builds an artifact may drop
a `<name>.prov.yaml` next to it:

```yaml
# figures/cp_curve.svg.prov.yaml
generatedBy: scripts/plot_cp.py
command: uv run python scripts/plot_cp.py --case baseline
commit: "3f9a1c47b28e5d06a4f1e0b39c7d2a58e6104fbb"
derivedFrom: [data/processed/performance.csv]
```

The two are complementary rather than redundant: credentials win where they
overlap, and the sidecar supplies what C2PA has no field for — the script path,
the exact command line, the upstream data files. This is the seam where Calkit,
DVC, or a plain Makefile can declare what produced a figure.

## Building

`figmint build` composes a document into one self-contained artifact:

```sh
figmint build fig.fig.yaml --to svg --to pdf
```

Vector panels are **inlined as SVG**, not embedded as images — a plot's text
stays selectable text and its lines stay lines all the way into the PDF. Raster
panels become data URIs. The result references nothing outside itself.

Two details that matter for correctness:

- **Panel ids are namespaced on inline.** Two matplotlib plots both define
  `#clip1` and `#DejaVuSans-glyph-*`. Without rewriting ids and their
  references, the second panel silently adopts the first panel's clip paths and
  glyphs — which shows up as subtly wrong output, not an error.
- **Geometry in points means true size.** A 468pt canvas produces a PDF with
  `MediaBox [0 0 468 210]`, i.e. exactly 6.5in wide, with no scaling step.

LaTeX is rendered to vector paths via matplotlib's mathtext when matplotlib is
importable. It is not a runtime dependency — it is simply very likely to be
present in a project that makes figures. Without it, math falls back to
`<foreignObject>` + MathML, which browsers render but most SVG-to-PDF converters
drop; `build` warns rather than letting an equation vanish from a PDF silently.

PDF and PNG output shell out to `rsvg-convert`, `inkscape`, or ImageMagick,
whichever is present. SVG needs nothing.

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

#### Verified against the real decoder

Round-tripped through `stencila 2.15.0`, confirming the bridge holds:

```sh
stencila convert two-panel.smd out.json   # inspect the parsed tree
stencila render  two-panel.smd out.html   # compile overlays and render
```

- The outer `Figure` keeps `id`, `layout: "2"`, and `overlay`; the caption is
  parsed as `caption`, not swept into content.
- Each panel becomes a nested `Figure` → `ImageObject` with the right
  `contentUrl`.
- The `figmint:` frontmatter survives intact — canvas, sources, and all six
  nodes with exact geometry, including `background: null` and nested maps.
- **Use `render`, not `convert`, to produce output.** `convert` leaves `s:`
  components uncompiled; `render` expands `<s:roi-rect>` into plain SVG with
  styling preserved.

### Annotations are already pixel-perfect

Absolute positioning for annotations needs no bridge — `Figure.overlay` is an
SVG layer positioned over the content area and scaled via its `viewBox`, which
is exactly the model figmint uses. Stencila's `s:` namespace supplies arrows,
callouts, ROI boxes, scale bars, and halos; plain SVG elements pass through
untouched.

### Generating a component with Stencila

`examples/components/schematic.smd` is a component whose source is executable
code. `make components` renders it to a signed PNG:

```sh
stencila render schematic.smd ../figures/schematic.png --credentials --yes
```

The resulting image carries the full chain — the source document and the
execution environment appear as `inputTo` ingredients, each with its own
manifest — so the panel arrives already knowing what produced it, with no
sidecar involved. Run `stencila credentials init` once first to create a local
signing identity.

## Known limitations

- **Round-tripping `.smd` back into figmint is not implemented** — export is
  one-way. (The export itself *is* validated against the real decoder; see
  below.)
- **figmint does not write Content Credentials yet.** It reads them. Signing the
  exported composite — with each panel as a `componentOf` ingredient and
  `compositeWithTrainedAlgorithmicMedia` when any panel is AI-generated — is the
  obvious next step, but it needs a decision about signing identity: a local
  self-signed key like Stencila's, or a real certificate.
- **`stencila credentials sign` output fails C2PA validation.** Stencila 2.15.0
  omits `digitalSourceType` from its `c2pa.created` action when signing a static
  asset, which the C2PA v2 spec requires, so c2pa-rs reports
  `assertion.action.malformed` and marks the manifest `Invalid`. This affects
  only that command — `stencila render --credentials` sets the field and
  validates cleanly. Worth reporting upstream.
- **Math in the overlay uses `<foreignObject>` + MathML.** That renders in
  browsers, so HTML output is fine, but many SVG→PDF paths drop `foreignObject`.
  The TeX travels verbatim in the frontmatter, so a future exporter can swap in
  real vector glyphs without any loss.
- **Stencila cannot render our export to PDF.** Two independent blockers:
  its PDF media embedder rejects `.svg` panels ("file extension `.svg` was not
  recognized as an image format"), and PDF output needs an external tool it
  declines to install automatically. HTML output works. Since the canvas is
  already SVG, figmint emitting its own print-ready SVG (and converting that) is
  probably the better path than going through Stencila for PDF.
- **Layout inference is an approximation.** Panels are banded into rows by
  vertical overlap. Overlapping or deliberately off-grid arrangements fall back
  to a placement map, and the note is surfaced in the export dialog.
- **Rotation is stored but not exported** to the Stencila overlay.
- **Nested composite figures** — a figmint document embedding another figmint
  document — are not implemented. That's the knowledge-graph direction in
  `editor-design.md` and needs a source type that points at a document rather
  than an image.
