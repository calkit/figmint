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
- **Layouts are explicit, and derived geometry stays derived.** A group can
  carry a grid; when it does, the solver owns its children's x/y/w/h. This is
  not a reversal of "free-form canvas" — a figure with no group behaves exactly
  as before. It means the common case (a 2×2 panel grid) can be *stated* rather
  than approximated, which is also what lets the Stencila export emit
  `Figure.layout` directly instead of reverse-engineering it from coordinates.
- **Signing: local certificate now, cloud attestation later.** The near-term
  answer is a local self-signed identity, matching what Stencila does. The
  longer-term one is different in kind: a cloud certificate that signs the
  figmint output as an attestation that *the output truly reflects the inputs
  as described*. That is a claim about build integrity, not authorship, and it
  is only meaningful if the build is reproducible — which is exactly why the
  `reproducible` provenance level and the Calkit boundary below matter. Design
  for it now by keeping the built artifact a pure function of the document plus
  its components.

### Demo status

Against the walkthrough above:

| Step | State |
| --- | --- |
| 1–2. Insert components | done — drag from the figure panel, provenance recorded |
| 3. Agent resizes a panel | done — geometry is plain YAML an agent can edit |
| 4. Agent edits a script, UI updates on change | done — the backend watches and pushes over a websocket |
| 5. `figmint status` shows staleness | done — exits non-zero, so CI and agents can gate on it |
| 5. `figmint build` | done — composes to self-contained SVG/PDF/PNG |
| 6. Build from the UI, then status is clean | done — the Build button composes from the saved file |

`figmint build --sign` now signs the composite: each panel becomes a
`componentOf` ingredient (carrying its own manifest where it has one, so the
chain nests), and the figure discloses `compositeWithTrainedAlgorithmicMedia`
whenever any panel declares generative-AI origin. Signing refuses when a
component is below the provenance bar — a signature over a figure containing an
anonymous panel would assert much less than it appears to.

Building the demo surfaced a distinction the walkthrough glosses over: rebuilding
does not clear a *changed-component* warning, only a *stale-output* one. Deciding
that a regenerated panel still supports the claim it was placed to support is a
judgement, so it lives in a separate `figmint accept` step. Folding it into
`build` would mean every rebuild silently erased the evidence that an input had
moved — which would defeat the point of tracking staleness at all.

### Decisions taken

- **Signing is opt-in and gated.** `build --sign`, not every build. It refuses
  when `check` would fail, so the signature means "these are the components, and
  every one of them was identified" rather than merely "figmint wrote this file".
- **The provenance policy enforces by default**, with no `figmint.toml` needed.
  Blocking is the point, and "say where it came from" is a low bar; a project
  that wants it advisory sets `enforce = false`.
- **draw.io: build the adapter.** Keep the core format-agnostic and read both
  `.fig.yaml` and `.drawio`, rather than committing to either editor now.

### Still open

- **The draw.io adapter** — next up, per the decision above.
- **Round-tripping `.smd`** back into the editor — export is one-way, which
  still looks like the right call while `.fig.yaml` is canonical.
- **Composite-of-composite:** a source that points at another figmint document
  rather than an image. Closely related to the signing item above — C2PA
  ingredients and figmint sources want to become the same concept.

The Stencila export is validated against the real CLI (2.15.0), and PDF output
now goes through our own composed SVG rather than Stencila's PDF path — see
[format.md](format.md).

## The figmint / Calkit boundary

The question was whether figmint should embed a pipeline stage definition —
kind, script, environment — in its own YAML, with Calkit gaining a `figmint`
stage kind in return.

**Recommendation: figmint references stages; Calkit defines and runs them.**

Embedding a stage definition means duplicating Calkit's schema in a second file
that can drift from the first, and figmint would then need to resolve
environments, manage locks, and execute things for the definition to be worth
anything. Calkit already does all of that. Two sources of truth for one stage is
the bug, not the feature.

Referencing gets something strictly better anyway: a claim that can be
*checked*. "Produced by `scripts/plot.py`", written in a sidecar, is a
self-assertion that nothing verifies. "Stage `plot-cp` in `calkit.yaml` declares
this exact path as an output" is verifiable without running anything, and it
carries Calkit's environment locks and DVC hashes behind it. That is the
"unambiguous proof of work" an imported component needs.

So the only question figmint asks Calkit is: *which stage, if any, declares this
file as an output?* Implemented in `src/figmint/calkit.py`, ~150 lines, no
dependency on Calkit itself — it reads `calkit.yaml`. Everything else
(environments, locks, staleness of the stage) stays on Calkit's side and is
reachable through `calkit status`.

The other direction still makes sense and is not blocked by any of this: a
`figmint` stage kind in Calkit, where `figmint build` is the command, the
`.fig.yaml` and its components are inputs, and the composed SVG/PDF is the
output. That needs nothing from figmint beyond the CLI that already exists — it
is a Calkit-side change, which is the right place for it.

### figmint as a schema, Calkit as a manipulator/builder

Calkit could contain the tooling to build a figmint output, and the editor
could go into the vs code extension and calkit cloud.
Figmint could simply be a composite figure schema with provenance
retention, named like `my-figure.fig.yaml`.

Calkit stage:

```yaml
my-fig:
  kind: figmint
  target_path: my-figure.fig.yaml
  outputs:
    - my-figure.png
```

No environment needed since figmint processing will be within Calkit.
Calkit reads the inputs from the figmint yaml to compile the DVC stage.
Is that wasteful to do over and over?
Do we need another layer of caching for that, where we compute a hash
(also some work)?

### Consequence for provenance

Components now sort into five levels (`src/figmint/provenance.py`), and a
project declares its minimum in `figmint.toml`:

| Level | Meaning |
| --- | --- |
| `unidentified` | a file that simply appeared |
| `declared` | imported, with a stated origin (URL, DOI, citation) |
| `generated` | a sidecar names a producing script — unverified |
| `reproducible` | a Calkit stage declares it as an output — verifiable |
| `signed` | valid C2PA credentials naming the producer |

The editor refuses to place a component below the threshold, and
`figmint check` fails in CI. `figmint import` is the escape hatch that keeps
this from being obstructive: a genuinely external artifact stays usable, it just
has to say where it came from.

One wrinkle worth revisiting: `signed` and `reproducible` are not really
comparable — one answers *who made this*, the other *can I make it again*. They
are forced into a single order because a policy needs a comparison, which works
for the common case but will chafe for a project that wants both.

## Provenance issues for imported components

Imported components are not produced directly by a human or agent, i.e.,
they are not primary artifacts,
and they are not produced by reproducible local processes in a project.

Can we somehow prevent the user from inserting random images without
a solid identifier if they were "imported" into the project?

We could potentially allow imported assets or components so long as they
are Calkit pipeline stage outputs, or some other unambiguous
"proof of work" descriptor, but we need to think about the correct
module boundaries between the two.

Perhaps the figmint YAML and embed a pipeline stage definition in it,
with a kind, script, environment, etc.?
Calkit can then have a figmint stage kind, which reuses its way of
tracking locked environments, DVC locks for I/O hashing, etc.?
Calkit can compile a figmint figure into a DVC stage if all the inputs
are part of the figmint file.

## Inserting figures that are figmint outputs into another composite figmint figure

This produces a chain of provenance and the need to check staleness all the
way through.

## draw.io compatibility

Can/should we use the draw.io format so we don't need our own editor?
Imagine that we could allow users to use draw.io but still retain
provenance inside.

### It works, mechanically

draw.io has a first-class extension point for exactly this. A shape can be
wrapped in an `<object>` element carrying arbitrary XML attributes, editable in
the UI via **Edit Data** (Cmd/Ctrl+M) and preserved across round-trips:

```xml
<object label="" figmint.source="cp-curve"
        figmint.path="figures/cp_curve.svg"
        figmint.hash="sha256:ff1f3f…"
        figmint.level="reproducible">
  <mxCell style="shape=image;image=figures/cp_curve.svg" vertex="1">
    <mxGeometry x="12" y="36" width="216" height="162"/>
  </mxCell>
</object>
```

So "use draw.io but retain provenance inside" is not a hack — it is the
mechanism draw.io provides for this.

### The question is really "how much of figmint is the editor?"

Not much, as it turns out. Of the current code:

| | Lines | Editor-dependent? |
| --- | --- | --- |
| `src/figmint/` (hashing, credentials, provenance, Calkit, status, build, watch) | ~3000 | **No** |
| `web/src/model/`, `web/src/io/` (format, solver, export) | ~2500 | Format-dependent, not UI-dependent |
| `web/src/components/` (canvas) | ~2000 | Yes |

The differentiated part — content hashing, C2PA reading, the provenance ladder
and policy, the Calkit boundary, staleness, the build — is already independent
of how pixels get arranged. Only the canvas is not. So this is less "throw away
figmint" than "swap one of five layers".

### What we would actually give up

Worth being concrete, because these are not free:

- **Print units.** draw.io is pixel-based with no notion of points or physical
  size. The property that a 468pt canvas is exactly 6.5in in the PDF goes away
  unless we impose a 1px = 1pt convention and police it.
- **Prevention becomes detection.** The provenance policy currently blocks an
  unidentified component *at the moment you place it*. We cannot hook draw.io's
  insert, so the best available is flagging it afterwards in `figmint check`.
  That is a real weakening of the feature that motivated it.
- **Agent-editability.** `.drawio` defaults to base64+deflate-compressed XML;
  even uncompressed it is mxGraph attribute soup with geometry split between
  `<mxGeometry>` and a `style` string. An agent can edit it, but "open it in an
  editor and understand it" — a stated goal — is much weaker than the YAML.
- **Control of the build.** We would either shell out to the draw.io CLI
  (Electron, headless, another dependency) or keep composing ourselves from the
  parsed XML. The second is fine and probably right.

### What we would gain

Real things, not to be dismissed: no canvas to maintain, a tool scientists
already know, shape libraries, connectors, alignment guides, layers, and a VS
Code extension that keeps the file in the repo.

### Recommendation

**Don't choose yet — make the core format-agnostic and prototype the adapter.**

Introduce a narrow document adapter with two questions:

1. Which components does this document reference, with what recorded provenance?
2. Where is each one placed, and how big is the canvas?

`status`, `accept`, `check`, `watch`, and `build` need nothing else. Implement
the adapter for `.fig.yaml` (trivial — it is the current code) and for
`.drawio`. That is maybe a few hundred lines and it defers the bet entirely:
if scientists take to draw.io, the core already works there; if they do not, we
have lost nothing.

### Export fidelity: tested (draw.io 31.0.2)

The open question was whether draw.io's export preserves vector panels and true
page size. Both answers are yes, with one significant catch.

| | Result |
| --- | --- |
| Vector panel fidelity | **Preserved.** Plot text (`C_P`, `U/U∞`, axis labels) is selectable in the exported PDF — panels are not rasterised. |
| Page size | **Controllable.** draw.io units are 1/100in, so units × 0.72 = points. Authoring at 650×292 produced a PDF with `MediaBox [0 0 468 210]` — exactly 6.5in. |
| Custom `figmint.*` attributes | **Survive round-trips**, confirmed through the CLI's own XML export. |
| Local file references | **Blocked.** The Electron sandbox refuses `file://` outside its own roots: *"Blocked loading file from file:///tmp/…"*. Relative paths resolve against draw.io's app bundle, not the document. |

### The catch, and what it implies

Because local references are blocked, **panels must be embedded in the `.drawio`
as data URIs**. A draw.io figure is therefore a document containing *copies* of
its components, not references to them. That changes the provenance model in a
specific way:

- The `figmint.hash` attribute becomes the record of *which version was
  embedded*. Staleness is then "does the embedded copy still match the file on
  disk?" — the same comparison as today, just against a copy rather than a
  reference.
- Updating a changed component means rewriting the `.drawio` to re-embed it.
  That is exactly the "reimport" step anticipated in the framing above, and it
  is a real operation figmint would need to own — `figmint reimport` alongside
  `accept`.
- Files grow with their panels. A figure with several vector plots carries them
  all inline.

None of that is disqualifying, and it is arguably *more* robust for archival —
the figure travels with its components. But it means a draw.io-backed figure is
a different kind of object from a `.fig.yaml`: self-contained rather than
referential.

### Verdict

draw.io can own both the editing and the export. figmint's job in that world is
the provenance layer: embed and track `figmint.*` attributes, hash what was
embedded, report staleness, re-embed on change, and sign the result. That is
almost exactly the code that already exists.

Proceed with the adapter, with the shape now known: components are embedded, not
referenced, and recovering an origin joins `accept` as a first-class operation.

### The adapter, as built

`src/figmint/formats/` — a two-question interface (*which components, placed
where*) with readers for `.fig.yaml` and `.drawio`. Units are normalised to
points at the boundary, so nothing downstream knows draw.io measures in
hundredths of an inch.

Testing against a real file draw.io produced exposed the practical problem
immediately: importing an SVG through the UI leaves an **anonymous base64 blob**
with no `<object>` wrapper and no attributes. Provenance is not merely absent, it
is destroyed at import.

Two recovery routes, in priority order:

1. **Declared attributes** — `figmint.path` / `figmint.hash` on an `<object>`
   wrapper, which is what draw.io's own Edit Data panel writes and preserves.
2. **Content-hash matching** — hash the embedded bytes and look for a file in the
   project with the same hash. That identifies a blob nobody recorded, which is
   what lets `figmint adopt` label a diagram somebody already drew.

`figmint adopt <file>` does the matching and writes the answer back;
`figmint check <file>` reports what is still anonymous. Verified round-trip: after
adopting, draw.io's own CLI preserves the attributes and still exports to PDF.

One case worth its own error, found by testing rather than reasoning: a component
whose `figmint.path` points at a **file that has since been deleted**. The figure
still renders from the embedded copy, so nothing looks wrong — but the original
can never be re-derived or updated again. That is reported distinctly from a low
provenance level.

### Placing a figure without losing its origin

`figmint place figures/cp_curve.svg --into composite.drawio` embeds the figure
with `src` and `hash` already on the shape. It never creates the problem `adopt`
exists to clean up, and the result stays fully editable in draw.io — move it,
resize it, style it; the attributes survive, and `reimport` can refresh it
later.

It applies the same provenance bar the editor does on insert: an unidentified
figure is refused before it is in a diagram, rather than discovered in one
afterwards. `--force` overrides.

So there are two ways in, and only one of them is lossy:

| | Provenance |
| --- | --- |
| `figmint place …` | recorded up front |
| draw.io's own import | destroyed; recoverable by `adopt` only if the bytes still match a project file |

### Keeping embedded components fresh

draw.io embeds a *copy* with nothing pointing back at the original, so a
regenerated plot can never propagate on its own — there is no link for the app
to follow, and no version of draw.io could notice. `figmint reimport` closes
that gap: for each shape with a declared `src`, compare the file on disk against
the recorded `hash` and swap in the current bytes. Only the image payload
changes; geometry, styles, layers and every other shape are untouched, so a
diagram someone arranged by hand survives having its panels refreshed.

### As a Calkit pipeline

The natural shape is two stages, and the reason is a constraint rather than a
preference: **`reimport` reads and writes the same file, so making it one stage
would put `.drawio` in both `inputs` and `outputs` — a cycle DVC rejects.**

`reimport -o` exists for exactly this. The authored diagram stays a pure input,
the refreshed one is a pure output, and the DAG is acyclic:

```yaml
pipeline:
  stages:
    plot-cp:
      kind: python-script
      script_path: scripts/plot_cp.py
      environment: main
      inputs: [data/processed/performance.csv]
      outputs: [figures/cp_curve.svg]

    embed-figure:
      kind: command
      command: >-
        figmint reimport figures/composite.drawio
        -o figures/composite.built.drawio
      environment: main
      inputs:
        - figures/composite.drawio        # authored by hand, tracked in git
        - from_stage_outputs: plot-cp
      outputs:
        - path: figures/composite.built.drawio
          storage: git

    export-figure:
      kind: command
      command: >-
        drawio -x -f pdf -o figures/composite.pdf
        figures/composite.built.drawio
      environment: main
      inputs:
        - from_stage_outputs: embed-figure
      outputs: [figures/composite.pdf]
```

The alternative — refreshing in place and declaring `.drawio` as an output — is
tempting because it avoids a second file, but it hands the authored diagram to
DVC as generated content. `dvc checkout` could then restore a cached version
over someone's edits. Not worth the saved file.

`reimport` is idempotent, which the two-stage form depends on: a second run over
an already-fresh diagram changes nothing, so the pipeline settles instead of
oscillating. There is a test pinning that.

Worth noting the in-place form is still the right one for interactive use, and
`figmint reimport --check` is the CI guard — it exits non-zero when a diagram is
carrying stale copies, without modifying anything.

### Does draw.io preserve Content Credentials on import?

Tested against draw.io 31.0.2, by reading its own source and replaying its
algorithm on signed files. The short answer: **no, not by default, and the
failure mode is worse than losing them.**

draw.io calls `Editor.stripImageMetadata` on every imported raster, with
`Editor.removeImageMetadata = true` as the default. For PNG it uses a
*denylist*:

```js
new Set("eXIf tEXt iTXt zTXt iCCP tIME".split(" "))
```

The C2PA `caBX` chunk is **not** on that list, so the manifest itself survives.
But C2PA's hash binding covers the whole file, so removing *any* other chunk
invalidates it. Replaying the exact algorithm on a signed matplotlib PNG:

| | |
| --- | --- |
| chunks after signing | `IHDR caBX tEXt pHYs IDAT IEND` |
| draw.io removes | `tEXt` (matplotlib's Software tag) |
| validation before | `Valid` |
| validation after | **`Invalid`** |

`Invalid` means "altered after signing", which figmint's own ladder rates *below*
having no credentials at all — correctly, since a broken signature is evidence of
tampering. So an AI-generated image whose manifest says
`trainedAlgorithmicMedia` would arrive not as "AI-generated" but as
"unidentified, possibly tampered". The disclosure is not merely lost, it is
inverted.

A PNG with none of the denied chunks passes through intact — verified on a
Stencila-signed PNG (`IHDR caBX pHYs IDAT IEND`, zero bytes removed, still
`Valid`). But that is luck, not a guarantee: most tools write at least one.

SVG is worse and has no escape: C2PA lives in `<metadata><c2pa:manifest>`, a
namespaced element, and draw.io strips those wholesale when it re-serializes.

**There is a config switch, and it is not enough.** `Editor.removeImageMetadata`
is read from the desktop config and `stripImageMetadata` returns early when it is
false — but there is a second, worse path that ignores it entirely.

#### Large images are re-encoded, not merely stripped

draw.io resizes anything exceeding `maxImageSize = 1200` px (or
`maxImageBytes = 2 MB`) by drawing it to a canvas and re-encoding. A canvas
export produces a fresh PNG with no ancillary chunks at all, so every trace of
provenance is destroyed regardless of the metadata setting.

Measured on a real Gemini-generated PNG imported through the UI:

| | Source | After draw.io import |
| --- | --- | --- |
| dimensions | 1408 × 768 | **1200 × 655** |
| chunks | `IHDR caBX IDAT×185 IEND` | `IHDR IDAT×315 IEND` |
| C2PA | `Valid`, AI disclosed | **gone** |

This also defeats content-hash recovery: the bytes are not the original bytes, so
`adopt` cannot match them against the source file either. The component is
unrecoverably anonymous, and figmint correctly reports it as such — the safety
net holds, but only as a refusal, not a repair.

**The practical rule: never import a credentialed image through draw.io's UI.**
`figmint place` embeds verbatim; verified on the same file, the embedded copy is
byte-identical and still reports `Valid` with the AI disclosure intact. This is
the strongest argument yet for `place` being the supported path in rather than a
convenience.

One thing no setting fixes: draw.io's **export** rasterises the whole diagram
into a fresh PNG, so component-level credentials never reach the exported
artifact regardless. Provenance has to be re-asserted at the composite level —
which is what the `src`/`hash` attributes and `figmint build --sign` are for.

### What a real Gemini image actually carried

Tested with an image generated by Google Gemini and imported through draw.io's
UI. Two results, neither expected:

**The source file had no provenance at all.** Not a stripped or broken manifest —
nothing. The JPEG contains only `JFIF`, quantisation and Huffman tables, and
image data. No APP1 (EXIF/XMP), no APP11 (JUMBF/C2PA), no SynthID marker. So the
AI-generated image at the centre of the "deep fake figure" concern arrived
completely anonymous, before draw.io was involved at all.

Whether Gemini omits credentials or the download path strips them is not
established here. Either way, the practical lesson is that **credentials cannot
be assumed present even from a vendor that publishes them**, so the policy has
to be the backstop rather than the belt-and-braces.

**draw.io embedded it byte-for-byte** — the embedded copy hashes identical to the
source. Note this does *not* demonstrate that `removeImageMetadata: false`
works, since a file with no metadata has nothing to strip. That still needs a
test with a file carrying EXIF or a C2PA manifest.

#### A bug this found

`figmint check` passed the diagram. It should not have.

Content-hash matching had recovered the origin — it could see the embedded bytes
matched `diagram-from-gemini.jpeg` in the project — and `check` was treating that
recovered path as a *declared origin*. But locating a file answers "which file is
this?", not "where did it come from". The file itself had no sidecar and no
credentials; it was unidentified, and the diagram should have failed.

So an AI-generated image with zero provenance passed the check, purely because a
copy of it happened to sit in the project. Precisely the case the policy exists
to catch. Provenance is now assessed from the file's own sidecar, credentials and
pipeline stage — never from the fact that figmint managed to find it.

### Should draw.io record the source itself?

It would remove most of this. draw.io knows the filename at import time and
already has the `<object>` mechanism to hang it on; recording `src` there would
make `figmint adopt` unnecessary for the common case, and it is generically
useful — "where did this image come from" is not a figmint-specific question.
The change looks small and lands in the import path in the web app.

Two caveats before treating it as the plan. Older draw.io versions will not have
it, so content-hash matching stays as the fallback regardless. And a browser
drag-and-drop only exposes the filename, not a path — so it would identify the
file but not locate it, which still needs matching to resolve.

**Not yet wired for `.drawio`:** `status`, `accept`, `build`, and the watcher
still take the `.fig.yaml` path directly. Moving them onto the adapter is
mechanical; `check` and `adopt` went first because they are what a draw.io user
needs before anything else is meaningful.

### Calkit compatibility

We could have a `drawio` stage kind, and we fail to export if we see any
images in there without provenance information.

## Whose job is it to sign output artifacts?

You could make it the plotting script's job, or you could make it Calkit's
job to sign the PNG or PDF as part of the pipeline stage.
I personally prefer the latter, since the former requires discipline by
the user.

## MyST compatibility

How do we allow users to easily use these diagrams in MyST,
see that they are up-to-date, each imported element has sufficient
provenance, etc.?
draw.io can save the working file as svg with the XML embedded inside,
so there's only one file, and we could embed the svg directly in MyST?

### The `.drawio.svg` idea works, and figmint now reads it

draw.io's SVG export can carry the whole diagram in a `content` attribute on the
root `<svg>`. That single file is simultaneously a picture anything can display
and an editable draw.io source — and crucially, the `<object src= hash=>`
provenance attributes ride along inside the embedded XML.

Verified end to end:

- **MyST embeds it directly.** `:::{figure} figures/turbine.drawio.svg` builds
  with caption and cross-reference intact; the asset is copied to the site with
  the diagram XML still in it.
- **figmint reads it.** `.drawio.svg` dispatches to the draw.io reader, which
  unwraps the `content` attribute. `figmint check` and `figmint reimport --check`
  both work on the published file, so CI can answer "is every component
  identified, and is any of them stale?" against the artifact itself.
- **draw.io reads it back**, and re-exports it with the attributes preserved —
  which is what makes a single-file workflow possible rather than a dead end.

### One constraint, made explicit

figmint will **not** write to a `.drawio.svg` in place. Updating the embedded
diagram is trivial; regenerating the rendered picture around it requires draw.io.
An in-place write would leave a file whose image and whose metadata disagree —
worse than refusing, because it looks fine.

So refreshing a single-file diagram is a two-step handoff, and `reimport -o`
writes a plain `.drawio` for exactly this:

```sh
figmint reimport fig.drawio.svg --check          # CI guard, read-only
figmint reimport fig.drawio.svg -o .tmp.drawio   # figmint updates the diagram
drawio -x -f svg --embed-diagram \
       -o fig.drawio.svg .tmp.drawio             # draw.io re-renders the picture
```

Which is the same two-stage shape the Calkit pipeline already wants, so it
composes rather than adding a special case.

### The cost

Embedding doubles the file: components appear once rendered in the SVG body and
once inside the diagram XML. The example built for this test came out at 3.9 MB,
which is a lot to serve on a web page. Two other things worth knowing before
adopting it wholesale:

- The embedded XML **ships to the published site**, including the local `src`
  paths. That is arguably good for provenance and mildly leaky about directory
  structure.
- A plain SVG export is half the size but carries *no* provenance at all, since
  our attributes live on mxCells that do not survive into rendered output. So the
  choice is: publish the heavier self-describing file, or publish a light one and
  keep the `.drawio` beside it for figmint to check.

### Should compiling the document be the last pipeline stage?

Yes, but with **no declared outputs**.

The appeal is real: `calkit run` then goes from data to published document in one
command, and Calkit already treats document compilation as in-scope (it has a
`latex` stage kind). The problem is that a MyST build is not reproducible —
measured over two consecutive builds of the same input, 1255 files were
identical in name and all but two in content, and those two (`index.html`,
`index.json`) differ because **MyST assigns random node keys on every build**:

```
-"key":"qQYT5zYw4H"
+"key":"P2tBLUajG8"
```

So declaring `_build/` as an output would leave the stage permanently dirty:
DVC would hash it, see a different hash every time, and never consider it up to
date. Tracking buys nothing and costs a misleading signal.

A stage with `inputs` and `always_run: true` gets the useful half — it runs last,
after the figure is refreshed and provenance is checked — without pretending the
output is content-addressable. `examples/myst/calkit.yaml` does this.

Worth noting the stage ordering encodes something: `check-provenance` sits
*before* `build-site`, so a document containing an unidentified component fails
the pipeline rather than getting published.

### Where does figmint end and Calkit begin?

An open question, and the boundary has already been crossed once by accident.
While building the MyST example it became obvious that `data/performance.csv`
had no provenance — not a stage output, not a declared dataset. figmint grew a
check for it, which meant teaching figmint to read Calkit's `datasets:` and
`inputs:` schema: exactly the duplication the boundary note above argues
against. It was reverted; auditing whether a project's input data is declared is
Calkit's question about its own pipeline.

But the fact that it happened is evidence. The questions are adjacent enough
that the seam keeps wanting to move, which is an argument for figmint's
provenance machinery eventually living inside Calkit rather than beside it.
Parked for now — the prototype is easier to move fast on as a separate tool, and
nothing here forecloses merging later, since the format adapters and the
provenance ladder do not depend on being a separate process.

Signing the published SVG with C2PA would be a third option — provenance without
the embedded XML — and `figmint build --sign` already does this for `.fig.yaml`
figures. Extending it to draw.io exports is not yet done.
