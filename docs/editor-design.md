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
- **Provenance sidecars** (`<artifact>.prov.yaml`) are the seam for pipeline
  tools, so figmint doesn't need to understand Calkit, DVC, and Make separately.

Staleness is already computed and surfaced in the editor's provenance panel
(`ok` / `stale` / `missing` / `unknown`), which covers step 5 of the demo from
the GUI side.

### Still open

- **`figmint status` and `figmint build` as CLI commands.** The demo above needs
  them so an agent can check staleness without the GUI. The hashing and
  comparison logic already exists in `src/figmint/assets.py`; what's missing is
  a command that loads a `.fig.yaml`, re-hashes its sources, and exits non-zero
  when anything is stale.
- **Watching for changes.** Step 4 wants the UI to update automatically when a
  script rewrites an artifact. Today you press Rescan.
- **Round-tripping `.smd`** back into the editor — export is one-way.
- **Validating the Stencila export** against the real `stencila` CLI.
- **Composite-of-composite:** a source that points at another figmint document
  rather than an image. This is the knowledge-graph direction above, and it is
  the main thing the current source model would need to grow.
