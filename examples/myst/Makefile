# The figmint loop, in a MyST document. Run from examples/myst.
#
# The targets are real files with real prerequisites rather than a list of
# phony verbs, so `make` works out what needs redoing and in what order. That
# is not just convenience: figmint's whole model is a derivation graph, and a
# Makefile that did not mirror it would let you rebuild the document from a
# figure nobody had redrawn — which `figmint status` would then, correctly,
# complain about.

RUN     := uv run
FIGMINT := $(RUN) figmint

DATA      := data/performance.csv
SCRIPT    := scripts/plot_cp.py
SCHEMATIC := figures/turbine.png
FIGURE    := figures/cp_curve.png
DIAGRAM   := figures/composite.drawio
COMPOSITE := figures/composite.svg
HTML      := _build/html/index.html

.DEFAULT_GOAL := help

.PHONY: help
help:  ## Show available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

.PHONY: all
all: $(HTML)  ## Build everything that is out of date, in order

# The environment is an input to every one of these, which is why `uv.lock`
# appears as a prerequisite: a different matplotlib draws a different figure,
# and figmint records the lock precisely so that is visible rather than
# assumed.
$(FIGURE): $(DATA) $(SCRIPT) uv.lock
	$(FIGMINT) run -i $(DATA) -o $@ -- uv run python $(SCRIPT)

# `drawio import` is an *authoring* step — it puts a new panel on the canvas —
# so it is not a build rule. Nothing here re-imports: each shape in the diagram
# records the `src` it came from, and the export re-embeds any panel that has
# been redrawn since, keeping the layout the author gave it.
#
# The export is also what carries the provenance out of draw.io: `src` and
# `hash` live on mxCells that do not survive into a rendered SVG, so the SVG is
# signed with every panel as an ingredient instead.
$(COMPOSITE): $(DIAGRAM) $(FIGURE) $(SCHEMATIC)
	$(FIGMINT) drawio export $(DIAGRAM) $@

# The document is an artifact too, and the most useful one to check: it is what
# a reader actually receives. Its inputs are the sources it is built from —
# `figmint status` reaches everything behind them on its own, so the figure's
# data and script do not need repeating here.
#
# `figmint.toml` is deliberately *not* an input, even though the plugin reads it
# to draw the panels. Recording the file that `figmint run` itself writes would
# make the document stale the instant it finished building.
#
# Recorded but not signed: c2pa does not recognise HTML, so `figmint.toml` is
# the only provenance this output has.
$(HTML): index.md myst.yml $(COMPOSITE) uv.lock
	$(FIGMINT) run -i index.md -i myst.yml -i $(COMPOSITE) \
	    -o $@ -- uv run myst build --html

.PHONY: plot
plot: $(FIGURE)  ## Produce the figure, recording its provenance

.PHONY: composite
composite: $(COMPOSITE)  ## Embed the figure into a diagram and export it

.PHONY: site
site: $(HTML)  ## Build the document, recording its provenance

.PHONY: status
status:  ## Is anything stale?
	$(FIGMINT) status -v

.PHONY: break
break:  ## Change the data so the figure goes stale
	@python3 -c "import pathlib; p=pathlib.Path('data/performance.csv'); \
	    t=p.read_text().replace('2.80,0.401','2.80,0.455'); p.write_text(t)"
	@echo "data changed — try 'make status', then 'make all'"

.PHONY: restore
restore:  ## Undo `make break`
	@python3 -c "import pathlib; p=pathlib.Path('data/performance.csv'); \
	    t=p.read_text().replace('2.80,0.455','2.80,0.401'); p.write_text(t)"

.PHONY: serve
serve:  ## Serve the document with live reload
	$(RUN) myst start

.PHONY: clean
clean:  ## Remove build output
	rm -rf _build
