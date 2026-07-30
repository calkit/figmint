# Using Stencila from figmint

Three things Stencila does that figmint wants: it builds a **provenance graph**
for an asset, it **verifies Content Credentials** into a structured report rather
than a single verdict, and it **signs** an asset with that graph embedded as a
C2PA assertion.

Everything below was run against the real SDK (`stencila` 2.15.0, from the local
checkout) and the real files in `examples/myst`. Where something is untested or
inferred it says so.

## Getting the SDK

Two packages, and they behave differently in editable mode:

```toml
[dependency-groups]
stencila = ["stencila", "stencila-types"]

[tool.uv.sources]
stencila = { path = "../stencila/python/stencila", editable = true }
stencila-types = { path = "../stencila/python/stencila_types", editable = true }
```

`stencila_types` is pure Python — edits in the checkout are live immediately.
`stencila` wraps Rust through pyo3, so editable covers only the Python source
under its `python-source` directory; the compiled `_stencila` extension is a
build artifact. After changing Rust, rebuild with `uv sync --reinstall-package
stencila` or `maturin develop`.

It is in its own dependency group, not in `dependencies`, because installing it
compiles a slice of Stencila's Rust workspace: **9 minutes** on first build here.
That is a steep price to put in front of everyone who installs figmint for an
integration not everyone needs. `uv sync --group stencila` opts in.

The public surface is small:

```python
from stencila import graph, credentials
# graph(subject, *, output, source, workspace, profile, provenance, render_options)
# credentials.sign / verify / inspect / init / register_renderer
```

## The provenance graph

`graph()` returns the exact graph that signing would embed — the same preparation
path, so a preview cannot disagree with what gets signed.

```python
from stencila import graph
g = graph("figures/cp_curve.svg", source="scripts/plot_cp.py", workspace=".")
```

On a bare file with no `source`, the result is thin: three nodes, all directory
containment. **`source` is what makes it interesting** — for a file subject the
SDK does not infer it (`infer_source=False`), so figmint has to supply it. That is
easy, because figmint already knows: `calkit.yaml` names the stage, and the stage
names its `script_path`.

With the source supplied, the same file yields 9 nodes and 10 edges:

| Node | Type | What it carries |
| --- | --- | --- |
| `asset:signed` | `ImageObject` | the subject, with asset-type identifiers |
| `code:scripts/plot_cp.py` | `SoftwareSourceCode` | **git authorship** (`Person`), `date_created` |
| `dir:.`, `dir:figures`, `dir:scripts` | `Directory` | workspace containment |
| `package:pypi/matplotlib` | `SoftwareSourceCode` | import, identified by **purl** |
| `symbol:…:HERE`, `symbol:…:out` | `Variable` | module-level symbols |

Edge kinds observed: `PartOf`, `Generated`, `ImportedBy`, `DerivedInto`. Each
edge carries `evidence` with a `kind`, and the kinds are the useful part:

- `Observed` — read off the filesystem.
- `Declared` — asserted by a caller (`details: {detector: stencila-python-runtime}`).
- `StaticAnalysis` — derived by parsing the source, with a `CodeLocation`
  pinning the line.

That distinction is the same one figmint's provenance ladder makes between
`generated` (a self-assertion) and `reproducible` (checkable), which is a good
sign the two models can be mapped onto each other rather than fought.

One live demonstration of what static analysis buys: while writing this, the
plotting script contained a typo, `import matplotlibbb`. The graph reported
`package:pypi/matplotlibbb` as an imported package. It is reading the source, not
the environment.

### Profiles and redaction

`profile` is `"public"` (default), `"private"`, or `"full"`, and it is a privacy
control, not a verbosity one. At `public` the author node carries
`Person(name='Pete Bachant')`; at `full` it carries `emails=[...]` too. Signing at
`public` reported `credential privacy policy applied 1 redaction(s)`.

A figure published from a repository should almost certainly stay at `public`.
Anything else leaks contributor email addresses into an artifact that travels.

### Rendering it

Stencila has **two** graph renderers, and they are not equally useful here.

**Graphviz DOT**, in Rust: `rust/graph/src/dot.rs` exposes `to_dot(&GraphView)`.

**Cytoscape**, in TypeScript, and this is the real one. `web/src/views/graph.ts`
defines a `<stencila-graph-view>` Lit element backed by cytoscape.js, and the
interesting part is not the drawing but `web/src/graphs/project.ts`:

| Concept | Values |
| --- | --- |
| preset | `auto`, `full`, `data-flow`, `software-dependencies`, `citations`, `reactivity` |
| detail | `low`, `medium`, `high` |
| layout | `breadthfirst`, `cose`, `grid`, `circle` |

Presets "describe the question a reader is asking of the graph"; `auto` picks the
first useful one from the relationships actually present. `PartOf` is singled out
as `STRUCTURE_EDGE_KIND` — containment, useful in some views and noise in most —
and `vocabulary.ts` supplies human labels per node and edge kind.

That is precisely the filtering problem worth worrying about, already solved and
already thought through. A 9-node graph for a one-script figure will not stay at
9 for a real composite, and the symbol-level nodes are too fine-grained for a
reader.

There is also `Format::Cytoscape` (`application/vnd.cytoscape.v3+json`), one of
Stencila's *visualization* formats alongside Mermaid, Plotly, and VegaLite — the
media type an `ImageObject` carries when it holds a spec rather than pixels. And
`rust/convert/src/html_to_png.rs` knows how to wait for a cytoscape view to
settle before screenshotting it, so a static raster path exists.

#### Why figmint cannot just use it

**The projection is TypeScript-only.** There is no Rust `Graph` → Cytoscape
conversion — `rust/graph/src/` contains `dot.rs` and nothing else for rendering.
The sole Python binding is `graph.prepare`. So the preset logic, the vocabulary,
and the Cytoscape adapter are all on the far side of a language boundary from
figmint.

**The bundle is not self-contained.** `web/dist/views/graph.js` is 20 KB but
imports sibling chunks — cytoscape itself, a decorators chunk, a theme chunk —
totalling roughly 680 KB. Vendoring that into a MyST site is possible but is a
real commitment, and it would have to be kept in step with the Stencila version
that produced the graph.

**MyST can host raw HTML, but script execution is unproven.** A `raw` node with
`lang: html` does reach `_build/html/index.html` with an inline `<script>` intact
— verified. Whether that script *executes* under MyST's React theme is not
verified; inline scripts inserted through React's `dangerouslySetInnerHTML` do
not run, and only a browser test would settle it. That check has not been done.

**Mermaid needs none of this.** MyST parses a `{mermaid}` block into a
first-class `mermaid` AST node that survives into the built HTML — verified. Zero
extra JavaScript, no vendoring, no version coupling.

#### Recommendation

Unchanged in destination, better informed in route:

1. **Render `Graph` → Mermaid in figmint**, but **port the projection's intent
   rather than inventing filtering**. `project.ts` and `vocabulary.ts` are the
   valuable artifacts here, not the Cytoscape adapter: which edge kinds belong in
   which view, that `PartOf` is structural, and what each kind should be called.
   Reimplementing ~200 lines of preset logic in Python is a smaller and more
   honest cost than vendoring 680 KB of JS to avoid it.
2. **Ask Stencila to expose the projection in Rust**, not just `to_dot`. If
   `GraphView` projection moved behind a pyo3 binding, figmint could ask for
   `preset="data-flow", detail="low"` and render the result — and the presets
   would stay Stencila's to define, which is where they belong. This is the
   upstream request worth making, and it is a larger one than a `to_dot` binding.
3. **Cytoscape web component** only if figmint ever wants an *interactive* graph
   in an HTML document, and only after testing whether MyST executes injected
   scripts. Interactivity is worthless in the PDF that most of these figures are
   ultimately for.
4. **`html_to_png`** is the interesting one for print: a rasterised Cytoscape view
   embedded as an image sidesteps both the bundling and the script-execution
   questions. Untested, and it implies a headless browser in the pipeline.

## Credentials validation

This is where Stencila is clearly better than what figmint has today.

figmint's `credentials.py` collapses everything into one `validationState` string
plus some fields it digs out of the manifest chain. Stencila's `verify()` keeps
the guarantees **independent**, which matters because they genuinely differ:

```python
credentials.verify("figures/turbine.png")
```

```
manifest      : present=True valid=True active=True from_sidecar=False
signature     : valid=True trusted=False signer='Google Media Processing Services'
asset_binding : valid=True
provenance    : assertion_present=False attested=False schema_known=False
reproducibility: not-checked
problems      : ()
```

The Gemini image has a **cryptographically valid signature from an untrusted
signer**. Collapsing that into one boolean loses the distinction that matters —
and figmint's current reader reports `validationState='Valid'` with
`warnings=['signingCredential.untrusted', …]`, which buries it in a list.

On a Stencila-signed asset the provenance axis lights up:

```
provenance    : assertion_present=True attested=True schema_known=True
schema_url    : https://stencila.org/v2.15.0/Graph.schema.json
```

Unsigned files give a usable message rather than a bare false:

```
problems: ('no embedded manifest found and no sidecar at figures/cp_curve.c2pa;
           credentials may have been lost',)
```

`require_trusted_signer` and `require_stencila_assertion` **add to `problems`
rather than raising**, so policy stays the caller's decision:

```
problems: ('required: signer trusted (--require trusted-signer)',)
```

That maps cleanly onto figmint's `figmint.toml` policy, and is a better shape
than figmint's current pass/fail.

### One thing figmint must keep

Stencila's typed `VerificationReport` has no field for `digitalSourceType`, and
its `summary` came back empty on every file tested. So it cannot answer *"was this
machine-generated?"* — the disclosure figmint surfaces in the MyST panel and the
reason the turbine image is labelled **AI-generated**.

The data is still reachable: `credentials.inspect()` returns the raw c2pa-rs
report, and `digitalSourceType` and `trainedAlgorithmicMedia` both appear in it.

So the split is:

- **`verify()`** for signature validity, trust, asset binding, and Stencila
  provenance — replacing the equivalent parts of figmint's reader.
- **figmint's own chain walk** (`_ai_in_chain`) for AI disclosure, fed by
  `inspect()` instead of by figmint's own C2PA parsing.

Worth checking before committing to that: `inspect()`'s raw shape is c2pa-rs's,
which is what figmint already parses, so the walk should port with little change.
Not yet attempted.

## Signing

```python
s = credentials.sign(
    "figures/cp_curve.svg", "out/cp_curve.svg",
    source="scripts/plot_cp.py", workspace=".",
)
```

```
manifest_kind : embedded   (or 'sidecar' when the format cannot carry one)
manifest_id   : urn:c2pa:f299eec0-…
media_type    : image/svg+xml
signing_mode  : local
source_digest : sha256:f04fd143…      ← the bytes as supplied
signed_digest : sha256:bb2b81d0…      ← the bytes after embedding
warnings      : ('no source-to-output generation edge was found',
                 'credential privacy policy applied 1 redaction(s)')
```

Points that matter for figmint:

- **Two digests, deliberately.** Embedding a manifest changes the file even when
  the content is unchanged. figmint already hit exactly this problem and worked
  around it by reading Stencila's recorded pre-signing digest during staleness
  checks (see `status.py`). `SignedAsset` now hands both over directly.
- **`sign()` can take a live plot**, not just a path — a Matplotlib `Figure`,
  `Axes`, or `pyplot`, rendered internally, with `register_renderer` as the
  extension point for other libraries. This is a different integration shape from
  figmint's: figmint composes files, and does not hold plot objects. Interesting
  for a future `figmint` kernel, not for the current pipeline.
- **`provenance="required"`** raises `ProvenanceNotFoundError` when no source can
  be established. That is the enforcement hook, and it corresponds to figmint's
  `enforce = true`.
- **`credentials.init()`** creates the local self-signed identity. figmint's
  `sign.py` currently mints its own CA + leaf chain (after a long fight with
  c2pa-rs over SubjectKeyIdentifier). Stencila's `init()` does that work, and
  `signing_mode` distinguishes `local` from `cloud` — which is the shape the
  eventual cloud-signing story wants.

The warning `no source-to-output generation edge was found` is worth noting: the
graph knows the script and the asset, but nothing *observed* the script writing
that file, so the `Generated` edge is `Declared` rather than proven. figmint has
better evidence here than the SDK can see — `calkit.yaml` declares the stage and
`dvc.lock` records that it ran — which is an argument for figmint passing what it
knows into the graph rather than accepting the weaker inference.

## Where this leaves the integration

Ordered by value-to-effort:

1. ~~**`Graph` → Mermaid**, rendered into the MyST panel behind an option
   (`:graph: true`).~~ **Done** — `src/figmint/graph.py` ports the projection
   policy and renders Mermaid; `:graph:` and `:graph-detail:` are directive
   options. Two things surfaced while building it, below.
2. **Adopt `verify()`** for the credential axes figmint currently flattens, and
   surface `trusted` separately in the panel. The turbine image is the motivating
   case: valid, untrusted, and currently shown as neither.
3. **Feed figmint's evidence into the graph** — supply `source` from the Calkit
   stage rather than letting inference fail, so the `Generated` edge is not
   merely declared.
4. **Signing** last. figmint's `sign.py` works; the gain is `signing_mode`,
   `init()`, and the two-digest contract, not new capability.

### What building it surfaced

**Every graph names its own subject `asset:signed`.** Merging per-component
graphs without renaming silently fuses two panels into one node — a composite
figure would come out claiming its panels were the same file. `graph.subject`
carries the distinguishing id (`asset:figures/cp_curve.svg`), so the merge
rewrites `asset:signed` to that.

**An optional dependency group cannot stay optional under `calkit run`.** `uv
run` re-syncs the environment to the *default* groups, so a group installed with
`uv sync --group stencila` is uninstalled the first time a pipeline stage runs
through `calkit xenv`. The published document then loses its graph with no error
anywhere — the panel's "SDK not installed" line is correct and completely
unexplanatory. The example lists the group in `[tool.uv] default-groups` for that
reason, which means the Rust build is not really optional once the graph is in
use.

**The graph misses the data dependency.** For the example figure, Stencila finds
the script, its packages, and the `Generated` edge — but not
`data/performance.csv`. Nothing in the static analysis links the CSV read to the
output. figmint knows that edge from `calkit.yaml`, so as it stands the graph is
*weaker* than the provenance table printed beside it. That makes item 3 below
more valuable than it first looked: figmint has evidence the SDK cannot see, and
the graph should carry it.

The open question underneath all of it: figmint currently reads C2PA itself and
would then read it two ways. That is tolerable while only the AI-disclosure walk
stays local, and not tolerable if it spreads. Making the SDK a hard dependency
would settle it — at the cost of a 9-minute Rust build for every install.
