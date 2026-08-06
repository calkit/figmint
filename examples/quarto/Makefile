# The fromwhere loop, in a Quarto document. Run from examples/quarto.
#
# The targets are real files with real prerequisites rather than a list of
# phony verbs, so `make` works out what needs redoing and in what order. That
# is not just convenience: fromwhere's whole model is a derivation graph, and a
# Makefile that did not mirror it would let you rebuild the document from a
# figure nobody had redrawn — which `fromwhere status` would then, correctly,
# complain about.

RUN     := pixi run
FROMWHERE := $(RUN) fromwhere

DATA      := data/performance.csv
CP_SCRIPT := scripts/plot_cp.py
CT_SCRIPT := scripts/plot_ct.py
CP        := figures/cp_curve.svg
CP_HTML   := figures/cp_curve_interactive.html
CT        := figures/ct_curve.svg
DIAGRAM   := figures/composite.drawio
COMPOSITE := figures/composite.svg
HTML      := _site/index.html

.DEFAULT_GOAL := help

.PHONY: help
help:  ## Show available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

.PHONY: all
all: $(HTML)  ## Build everything that is out of date, in order

.PHONY: extension
extension: _extensions/fromwhere/fromwhere.lua  ## Install the Quarto filter

# Installed from the package rather than with `quarto add`, because the filter
# and `fromwhere-quarto` speak a protocol private to fromwhere and have to be the
# same version.
_extensions/fromwhere/fromwhere.lua: pixi.lock
	$(FROMWHERE) quarto install

# The declarations. Nothing produces a dataset or a plotting script, so without
# these the record has a hole at the bottom that every check above it passes
# straight over.
.PHONY: declare
declare:  ## Say where the primary artifacts came from
	$(FROMWHERE) declare $(DATA) --mine
	$(FROMWHERE) declare $(CP_SCRIPT) --mine --with-ai 'Claude Opus 5'
	$(FROMWHERE) declare $(CT_SCRIPT) --mine --with-ai 'Claude Opus 5'
	$(FROMWHERE) declare $(DIAGRAM) --mine
	$(FROMWHERE) declare index.qmd --mine
	$(FROMWHERE) declare _quarto.yml --mine

# The environment is an input to these, which is why `pixi.lock` is a
# prerequisite: a different Plotly draws a different figure, and fromwhere
# records the lock precisely so that is visible rather than assumed.
$(CP): $(DATA) $(CP_SCRIPT) pixi.lock
	$(FROMWHERE) run -i $(DATA) -o $@ -- pixi run python $(CP_SCRIPT)

$(CT): $(DATA) $(CT_SCRIPT) pixi.lock
	$(FROMWHERE) run -i $(DATA) -o $@ -- pixi run python $(CT_SCRIPT)

# Recorded but not signed, and not by choice: c2pa does not recognize HTML in
# either direction, so `provenance.toml` is the only provenance this figure has.
$(CP_HTML): $(DATA) scripts/plot_cp_interactive.py pixi.lock
	$(FROMWHERE) run -i $(DATA) -o $@ \
	    -- pixi run python scripts/plot_cp_interactive.py

# `drawio import` is an *authoring* step — it puts a new panel on the canvas —
# so it is not a build rule. Nothing here re-imports: each shape in the diagram
# records the `src` it came from, and the export re-embeds any panel that has
# been redrawn since, keeping the layout the author gave it. That is why the
# `.drawio` is the one thing under figures/ that is committed.
#
# The export is also what carries the provenance out of draw.io: `src` and
# `hash` live on mxCells that do not survive into a rendered SVG, so the SVG is
# signed with every panel as an ingredient instead.
$(COMPOSITE): $(DIAGRAM) $(CP) $(CT)
	$(FROMWHERE) drawio export $(DIAGRAM) $@

.PHONY: compose
compose:  ## Author the diagram from scratch (only needed if you delete it)
	$(FROMWHERE) drawio import $(CP) $(DIAGRAM) --width 460 --x 0 --y 0
	$(FROMWHERE) drawio import $(CT) $(DIAGRAM) --width 460 --x 500 --y 0

# The document is an artifact too, and the most useful one to check: it is what
# a reader actually receives. Its inputs are the sources it is built from —
# `fromwhere status` reaches everything behind them on its own, so the figure's
# data and script do not need repeating here.
#
# `provenance.toml` is deliberately *not* an input, even though the filter reads it
# to draw the panels. Recording the file that `fromwhere run` itself writes would
# make the document stale the instant it finished building.
#
# Recorded but not signed: c2pa does not recognize HTML, so `provenance.toml` is
# the only provenance this output has.
$(HTML): index.qmd _quarto.yml $(COMPOSITE) $(CP_HTML) pixi.lock \
		_extensions/fromwhere/fromwhere.lua
	$(FROMWHERE) run -i index.qmd -i _quarto.yml -i $(COMPOSITE) -i $(CP_HTML) \
	    -o $@ -- pixi run quarto render

pixi.lock: pixi.toml
	pixi install

.PHONY: plot
plot: $(CP) $(CT) $(CP_HTML)  ## Produce the figures, recording their provenance

.PHONY: composite
composite: $(COMPOSITE)  ## Export the multi-panel figure from the diagram

.PHONY: site
site: $(HTML)  ## Build the document, recording its provenance

.PHONY: status
status:  ## Is anything stale?
	$(FROMWHERE) status -v

.PHONY: break
break:  ## Change the data so the figure goes stale
	@python3 -c "import pathlib; p=pathlib.Path('data/performance.csv'); \
	    t=p.read_text().replace('2.80,0.401','2.80,0.455'); p.write_text(t)"
	@echo "data changed — try 'make status', then 'make all'"

.PHONY: restore
restore:  ## Undo `make break`
	@python3 -c "import pathlib; p=pathlib.Path('data/performance.csv'); \
	    t=p.read_text().replace('2.80,0.455','2.80,0.401'); p.write_text(t)"

# `quarto preview` re-renders when *its own* sources change — the .qmd,
# _quarto.yml, a linked image. A change to a script or a dataset is invisible
# to it, so the provenance panels would keep showing the previous render and a
# stale figure would look current for as long as you left the tab open. This
# watches everything fromwhere records as an input and touches index.qmd when any
# of it moves, which is the one thing Quarto does notice.
#
# It has to be *this* preview. VS Code's Quarto extension starts its own with
# `--no-watch-inputs`, which turns off re-rendering on file changes altogether
# — it re-renders when you save the document and never otherwise — so the touch
# below would do nothing there.
define WATCH_INPUTS
import pathlib, time, tomllib

record = pathlib.Path("provenance.toml")
target = pathlib.Path("index.qmd")


def stamps():
    try:
        data = tomllib.loads(record.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    paths = {
        item["path"]
        for artifact in (data.get("artifact") or {}).values()
        for item in (artifact.get("inputs") or [])
    }
    # Never the file being touched, or this chases its own tail forever.
    paths.discard(str(target))
    return {
        p: pathlib.Path(p).stat().st_mtime
        for p in paths
        if pathlib.Path(p).is_file()
    }


last = stamps()
while True:
    time.sleep(1)
    now = stamps()
    if now and now != last:
        last = now
        target.touch()
endef
export WATCH_INPUTS

.PHONY: preview
preview:  ## Serve the document with live reload
	@python3 -c "$$WATCH_INPUTS" & \
	trap 'kill %1 2>/dev/null' EXIT; \
	quarto preview

.PHONY: clean
clean:  ## Remove build output
	rm -rf _site .quarto
