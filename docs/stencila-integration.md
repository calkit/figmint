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

### Rendering it — the gap

The user-facing goal is to put the graph *into* the figure panel that the MyST
plugin renders. Stencila can render a graph: `rust/graph/src/dot.rs` has
`to_dot(&GraphView)`, emitting Graphviz DOT with a projected view (`preset`,
`detail`).

**It is not reachable from Python.** The native surface is exactly:

```
_stencila.graph        → prepare
_stencila.credentials  → init, inspect, sign_prepared, verify
```

So there are three options, in rough order of preference:

1. **Render to Mermaid in figmint.** MyST renders ```` ```mermaid ```` blocks
   natively (see `docs/myst-plugin.md`), so a `Graph` → Mermaid function needs no
   new dependency, no Graphviz binary, and no build step. The `Graph` is plain
   typed data — `nodes`, `edges`, `kind`, `evidence` — so this is a
   straightforward projection. Filtering matters more than drawing: 9 nodes for a
   one-script figure will not stay at 9 for a real composite, and the symbol-level
   nodes are almost certainly too fine-grained for a reader.
2. **Ask Stencila to expose `to_dot`.** One pyo3 binding on a function that
   already exists. Better long-term — the projection presets are theirs to
   define, and it keeps the rendering consistent with Stencila's own tooling —
   but it needs an upstream change and a rebuild.
3. **Render DOT ourselves and shell out to Graphviz.** Adds a system dependency
   for a picture; hard to justify against option 1.

Recommendation: option 1 now, option 2 as an upstream request. They are not
exclusive — a `to_dot` binding would slot in behind the same figmint-side
interface.

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

Ordered by value-to-effort, all of it unstarted:

1. **`Graph` → Mermaid**, rendered into the MyST panel behind an option
   (`:graph: true`). Needs no new dependency at render time beyond the SDK, and
   MyST draws it natively.
2. **Adopt `verify()`** for the credential axes figmint currently flattens, and
   surface `trusted` separately in the panel. The turbine image is the motivating
   case: valid, untrusted, and currently shown as neither.
3. **Feed figmint's evidence into the graph** — supply `source` from the Calkit
   stage rather than letting inference fail, so the `Generated` edge is not
   merely declared.
4. **Signing** last. figmint's `sign.py` works; the gain is `signing_mode`,
   `init()`, and the two-digest contract, not new capability.

The open question underneath all of it: figmint currently reads C2PA itself and
would then read it two ways. That is tolerable while only the AI-disclosure walk
stays local, and not tolerable if it spreads. Making the SDK a hard dependency
would settle it — at the cost of a 9-minute Rust build for every install.
