# Figmint editor design

We want a web GUI that allows us to insert figures from a local directory
and keeps track of their provenance.
It will eventually save to a text-based format so AI agents can edit,
but we'll be able to see exactly where each component came from,
and know if one has changed in a way that makes our composite figure
stale.

We can use React Typescript with Vite for the front end.

Some inspiration we could take:

- tldraw
- draw.io
- Figrecipe

We want to be compatible with Stencila for rendering and provenance tracking.

A larger and more general problem to handle is the idea of embedding
components in others and not losing track of where they came from,
so they form a bit of a knowledge graph.

## Motivating use cases

AI agents can easily "deep fake" a multi-panel scientific figure,
with illustrations, charts that look like they were produced from
data, raw images from microscopes, etc.

Researchers can forget to reexport a composite figure after, e.g.,
updating data processing, leaving the published version out of date.
At the very least the friction and cognitive overhead associated with
keeping track of staleness should go away.
At the same time, some don't have the patience to script every figure,
and so need a WYSIWYG interface.
However, interactive editing can cause us to lose track of important
subcomponents with their own provenance concerns.

## Demo

1. Insert one figure, which is an illustration created by AI.
2. Insert another figure, which is created by a Python script from data.
3. User tells AI agent to make one of the figures bigger.
4. User tells AI agent to change color of markers, so it edits Python script
   and web UI updates automatically on change.
5. `figmint status` run by AI agent shows that the composite output is stale,
   so the agent runs `figmint build`.
6. Just as easily, the figmint UI can build/export the composite image and
   `figmint status` shows it's up-to-date, since none of the input components
   have changed.

## Decisions so far

Recorded as the scaffold gets built; the format itself is documented in
[format.md](format.md).

- **Free-form canvas, not a grid editor.** Panels have absolute `x`/`y`/`w`/`h`
  so they can be dragged and resized directly. Stencila's `Figure.layout` grid
  is derived from those positions on export rather than driving the editor.
- **Points as the storage unit.** A figure authored at 468pt is 6.5in in the PDF
  with no scaling step.
- **SVG all the way down.** The canvas renders the same SVG the exporter emits,
  so there's no separate print path to keep in sync, and vector panels stay
  vector.
- **No canvas framework.** tldraw and friends bring their own document model,
  which is the one thing here that has to stay ours — the text format is the
  point of the project.
- **The backend owns the filesystem.** Content hashes come from the actual bytes
  on disk, not from the browser, so provenance answers can't be spoofed by the
  UI. This matters for the "deep fake figure" concern above.
- **C2PA Content Credentials are the primary provenance source.** Where a
  generating tool signs its output, that signed manifest beats anything figmint
  could infer, and its `digitalSourceType` is a direct, cryptographically bound
  answer to "was any of this AI-generated?". Stencila already signs with
  `stencila render --credentials`. figmint reads credentials today; writing them
  for the exported composite is the next step.
- **Provenance sidecars** (`<artifact>.prov.yaml`) are the fallback for tools
  that don't sign, and carry what C2PA has no field for (script path, command
  line, upstream data). Credentials win where the two overlap.

### Demo status

Against the walkthrough above:

| Step | State |
| --- | --- |
| 1–2. Insert components | done — drag from the figure panel, provenance recorded |
| 3. Agent resizes a panel | done — geometry is plain YAML an agent can edit |
| 4. Agent edits a script, UI updates on change | partial — press Rescan; no watcher yet |
| 5. `figmint status` shows staleness | done — exits non-zero, so CI and agents can gate on it |
| 5. `figmint build` | done — composes to self-contained SVG/PDF/PNG |
| 6. Build from the UI, then status is clean | done — the Build button composes from the saved file |

Building the demo surfaced a distinction the walkthrough glosses over: rebuilding
does not clear a *changed-component* warning, only a *stale-output* one. Deciding
that a regenerated panel still supports the claim it was placed to support is a
judgement, so it lives in a separate `figmint accept` step. Folding it into
`build` would mean every rebuild silently erased the evidence that an input had
moved — which would defeat the point of tracking staleness at all.

### Still open

- **Watching for changes.** Step 4 wants the UI to update automatically when a
  script rewrites an artifact. Today you press Rescan. The backend already
  re-hashes on scan, so this is a watcher plus a websocket, not new provenance
  logic.
- **Signing the exported composite.** figmint reads Content Credentials but does
  not write them. Signing the output with each panel as a `componentOf`
  ingredient would make the knowledge graph real and machine-checkable — C2PA
  already has the vocabulary for it. Blocked on a decision: sign with a local
  self-signed identity like Stencila's, or a real certificate?
- **Round-tripping `.smd`** back into the editor — export is one-way.
- **Composite-of-composite:** a source that points at another figmint document
  rather than an image. Closely related to the signing item above — C2PA
  ingredients and figmint sources want to become the same concept.

The Stencila export is validated against the real CLI (2.15.0), and PDF output
now goes through our own composed SVG rather than Stencila's PDF path — see
[format.md](format.md).
