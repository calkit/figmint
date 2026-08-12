# Roadmap

Things that have been considered and either deferred or ruled out, with the
reasoning.
A feature that was rejected for a bad reason should be easy to reopen, and a
feature that was rejected for a good reason should be easy to stop
relitigating, which is what this file is for.

## Importing a path from another repository

[calkit/calkit#728](https://github.com/calkit/calkit/issues/728) asks for the
ability to import, link, or sync any single path between two repositories:
a config file that flows from a development repo to a deployment repo, a
`references.bib` shared across projects, a figure produced by a research
project and republished in a blog.
The issue sketches a generic `storage:` concept with several providers—git,
Overleaf, Google Drive—and per-path settings for the external path, the
external revision, and whether syncing is one-way or bidirectional.

Some of that is squarely `fromwhere`'s job and some of it is structurally not.
The dividing line is the same one [`docs/calkit.md`](calkit.md) draws for
pipelines: Calkit owns moving bytes around, `fromwhere` owns what those bytes
prove.

### What is in scope: fetching and checking a git origin

`fromwhere declare <path> --git host/owner/project/path/to/file@rev` already
exists, and it already records the location and the revision.
What it does not do is _go and look_.
It records a claim, in the same way `--doi` does.

`--url` is the exception—the one origin `fromwhere` checks while making it.
It downloads the file if it is absent, re-downloads and compares if it is
present, and refuses on a mismatch, naming both hashes.
The result is a record that says _this address served exactly these bytes at
this time_ rather than _somebody told me it did_.

Giving `--git` the same treatment is the highest-value piece of the issue and
the smallest.
It is parity with a code path that already works, and it upgrades the
origin kind that _looks_ most authoritative from verifiable-in-principle to
actually verified.
A pinned revision is better raw material for this than a URL is: a git object
at a given rev is immutable, so the check is exact rather than a claim about a
moment in time.

### What is in scope: noticing that upstream has moved

The other half is a freshness question, and freshness is what this tool does.
`status` already answers "the record says these bytes, the disk says those" for
every local input.
"Your copy is pinned at `rev-a`, and upstream's path is now at `rev-b`" is the
same question pointed outward, and it belongs in the same report.

One caveat worth designing around: everything `status` does today is offline.
Hashing local files needs no network, which is part of why the command is
usable in a pre-commit hook or on a plane.
An upstream check cannot be offline, so it has to be opt-in—a flag, or a
separate command—rather than something that silently makes `status` slow and
failure-prone.

### What is out of scope: merging

The issue's central use case is merging upstream changes into a local copy
that has diverged, using diff logic.
This is the part that does not fit, and the reason is the shape of the record
rather than the amount of work.

`fromwhere` is file-granular.
An artifact is a path, a SHA256 of its whole bytes, and a list of inputs.
Merging is line-granular: the honest provenance of a merged file is _these
lines came from upstream at that rev, those lines are local_, and a hash of
the whole file has no vocabulary for saying so.

It is tempting to model the merged file as a composite artifact with two
inputs, since composites are exactly what this tool is for.
That works right up until verification.
A composite figure is checkable because `rebuild` can re-run the recorded
command and get the same bytes back.
A merge that had conflicts is not reproducible—resolving it was a human
judgment, and re-running it does not reconstruct that judgment.
The record would then describe a chain that looks automatically checkable and
is not, which is the precise failure this project exists to prevent: every
check above the gap passes, and that is what makes the gap easy to miss.

There is a clean way out that needs no merge machinery.
A file you merged by hand is not derived, it is _authored_—from recorded
inputs, by a person, which is the `authored` kind a `.drawio` canvas already
uses.
The thing `fromwhere` is missing is not the ability to merge but the
vocabulary to say "this was forked from `upstream@rev` and has since
diverged, and a person owns the difference."
That is an origin refinement, and it composes with the fetching above: the
fork point is verifiable even when the current bytes are not.

### What is out of scope: bidirectional sync

Bidirectional syncing is not merely a later feature; the data model cannot
express it.
The record is a DAG, and `rebuild` depends on that—it repairs artifacts in
derivation order, dependencies first.
Bidirectional sync makes each copy an input to the other, which is a cycle.
There is no valid rebuild order for a cycle, and staleness would oscillate
rather than resolve.

One-way import has none of these problems, and it covers the use cases in the
issue that are about provenance.
The issue itself notes that publishing a research artifact into a blog "should
probably be one-way."

### What is out of scope: storage providers

Overleaf projects, Google Drive folders, and DVC remotes are all answers to
_where do the bytes live_.
That is a real problem and it is Calkit's, for the same reason DVC's caching
and remote storage are Calkit's.
Nothing about a storage backend is evidence about how an artifact was made.

### How the two would fit together

The composition is the one that already works for pipelines.
Calkit performs the sync; `fromwhere` records what came from where and
verifies it.
If Calkit grows the `storage:` and `paths:` configuration in the issue, the
work on this side is to record each imported path's upstream location and
revision as an origin and check the bytes against it—which is exactly the
`--git` fetching described above, with no new concept required.

A reasonable first step, independent of anything Calkit does:

1. Make `--git` and `--calkit` fetch and compare, matching `--url`.
2. Record the fork point so a later divergence can still name it.
3. Add an opt-in upstream freshness check to `status`.

None of that requires deciding anything about merging, and all of it is useful
on its own.

## Composite documents in Inkscape and GIMP

`fromwhere drawio import` gives a `.drawio` a workflow no other editor has
here: a panel produced by a plotting script is brought onto a canvas, the
canvas remembers where the panel came from, a stale panel is refreshed on the
way to export, and `status` can say so before anyone exports at all.
The question is whether the same workflow transfers to an Inkscape `.svg` and
a GIMP `.xcf`.

It does, in both cases, but not by porting the draw.io implementation.
The draw.io module is shaped almost entirely by one constraint—its Electron
sandbox refuses to load local files during export, so a panel has to be
embedded as a base64 data URI, which is what makes the embedded copy go stale
and what makes `Diagram.refresh` necessary.
Neither Inkscape nor GIMP has that constraint, and each is better served by
the mechanism it already has.

Everything below was verified directly, on Inkscape 1.4.4 and GIMP 3.2.4.

### What is in scope: Inkscape, by linking rather than embedding

Two properties carry the whole design.

**Foreign-namespace attributes survive Inkscape's writer.**
An `<image>` carrying `fw:src` and `fw:hash`, under an `xmlns:fw` of our own,
comes back from a round trip through Inkscape byte-for-byte, namespace
declaration intact.
This is worth stating next to the draw.io behavior it inverts: draw.io strips
a dotted prefix when it re-saves, which is why the attributes there are the
unprefixed `src` and `hash` and why they have to be filtered against a
reserved list.
Inkscape gives provenance a properly namespaced home that cannot collide with
a user's own data, and needs no such list.

**Linked images render on headless export, including SVG panels.**
A composite whose panels are relative `xlink:href`s exported correctly to PNG
with both a raster panel and a vector one drawn.

Linking rather than embedding is therefore the default, and it removes the
stale-copy problem instead of managing it.
Re-run the plotting script and the composite is already correct; there is no
copy inside the document to refresh before exporting.
An SVG panel also stays vector the whole way through, which the draw.io path
cannot offer at all.

`fw:src` and `fw:hash` are still written, but `hash` means something different
than it does in a `.drawio`, and the difference is the useful part.
There it answers "is the embedded copy stale".
Here the render is never stale, so it answers "has this panel moved since the
arrangement was blessed"—the composite will export differently than it did
when its author last looked at it, which is a thing worth being told and a
thing no rendering can reveal.

Three details to design around, none of them blocking:

- Inkscape can store an `xlink:href` as an absolute path depending on a user
  preference. Write relative and normalize on read.
- An embedded mode is still wanted for a document that has to travel outside
  the project, where a link resolves to nothing.
- Inkscape logs `WARNING: unknown type: c2pa:manifest` on a signed SVG panel.
  It passes the panel through and exports fine. Content Credentials on the
  output come from signing the export, exactly as `drawio export` already
  does, rather than from anything surviving the render.

### What is in scope: GIMP, through layer parasites

An `.xcf` layer can carry arbitrary named blobs—parasites—and one created
persistent is written into the file.
A parasite attached to a layer survives a save and reload of the `.xcf`
unchanged.
That is the exact analogue of a draw.io shape's attributes, and it is the
piece whose absence would have ruled the whole thing out.

Refreshing a layer in place works as well: load the new file as a layer,
insert it at the old layer's stack position, copy across offsets, opacity,
mode, name, and parasites, then remove the old layer.
Position, size, opacity, and the `src` parasite all survive a swap to
different artwork.

Two things learned the hard way, both of the kind that read as success if you
only check whether a file was written:

- **`layer.scale()` does nothing until the layer is inserted into the image.**
  Called before `insert_layer` it is silently ignored and the layer keeps the
  new file's natural size; called after, it works.
- **Use `--batch-interpreter=python-fu-eval`.** Parasites are reachable from
  Script-Fu but painful, and the existing `gimp.py` dialect-juggling exists
  only because flattening had to work on GIMP 2 as well. Composite support
  can require GIMP 3 without taking anything away: the flatten export keeps
  its fallback.

### What GIMP costs that draw.io and Inkscape do not

These are not objections, but they are real and they should be decided
deliberately rather than discovered during implementation.

1. **GIMP becomes a dependency of import, not just export.**
   `fromwhere drawio import` is pure Python and needs no draw.io installed.
   An `.xcf` cannot be written safely without GIMP, so every import and every
   refresh spawns it, and pays several seconds for the privilege.
2. **The copy inside is not byte-verifiable.**
   draw.io embeds verbatim bytes, so the embedded payload can be re-hashed and
   compared against the source—which is what lets a panel somebody added by
   hand through Edit Style still be checked. GIMP decodes an image into a
   pixel buffer, and the source file's SHA256 cannot be recovered from it. The
   `hash` parasite is an attestation rather than evidence.
3. **A refresh can destroy hand work, and must be able to notice.**
   A draw.io panel is an opaque image shape; the only things an author can do
   to it are move it and resize it, which is why `refresh` can overwrite it
   safely. A GIMP layer is editable pixels, and retouching one is a large part
   of why anyone opens GIMP. Storing a hash of the layer's pixel data at
   import restores a check—not the one lost in (2), but the one that matters
   here: it lets a refresh say "this layer has been painted on since import"
   and stop, instead of quietly discarding the work.
4. **Resizing has no free answer.**
   Keeping the old layer's box resamples a regenerated panel and costs
   quality; adopting the new artwork's natural size breaks the layout the
   author built. draw.io avoids the question by scaling at render time.
   Keeping the box and warning when the aspect ratio has changed is probably
   right, but it is a choice, not a default.

### What is out of scope: parsing and writing `.xcf` directly

Doing the layer work in Python without GIMP would remove cost (1) above, and
it is not worth attempting.
Reading the format is feasible; writing it is not meaningfully supported
anywhere, and a provenance tool that writes a subtly malformed `.xcf` damages
the artifact it exists to protect.

### What is out of scope: propagating edits back to a panel

Retouching an imported layer in GIMP and pushing the result back to the source
PNG is the cycle already ruled out under bidirectional sync above, for the
same reason: it makes each file an input to the other, and there is no valid
rebuild order for a cycle.

The related question—what a retouched layer's provenance actually is—has the
same answer given there.
It is not derived from its source any more; it is authored, from a recorded
input, by a person.
The vocabulary that entry asks for, naming a fork point that stays verifiable
after the bytes diverge, is the same vocabulary this needs, and neither
feature should invent its own.

### What is out of scope for now: splicing SVG panels

An SVG panel could be spliced into the composite as a `<g>` rather than
referenced by an `<image>`, which would let an author reach inside a panel and
edit it.
Linking already keeps a panel vector, which was the reason to want this, and
splicing brings id collisions and a much harder refresh.
Worth revisiting only if reaching inside a panel turns out to be something
people actually want.

### The shared shape, and when to extract it

Three implementations is the point at which the common shape is worth naming,
and `drawio.py` already contains it in unabstracted form: read the panels a
document declares, refresh what is stale, export, and record.
`rebuild.py` dispatches on the recorded command string, so an
`inkscape export` slots in with no new machinery.
What would need generalizing is the code that reasons about `.drawio` by file
extension, in `myst.py` and `status.py`, which should key off a document being
a composite rather than off its suffix.

### Order of work

Inkscape first.
It is the smaller piece, it needs no external binary to import, and linked
mode is an improvement on the draw.io workflow rather than a port of it.
GIMP second, where the substantive work is the pixel-hash guard in cost (3)
rather than the parasites themselves.
