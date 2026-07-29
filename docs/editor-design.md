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
