# The figmint loop, in a MyST document. Run from examples/myst.

RUN     := uv run
FIGMINT := $(RUN) figmint
FIGURE  := figures/cp_curve.png

.DEFAULT_GOAL := help

.PHONY: help
help:  ## Show available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

.PHONY: plot
plot:  ## Produce the figure, recording its provenance
	$(FIGMINT) run -i data/performance.csv -o $(FIGURE) \
	    -- uv run python scripts/plot_cp.py

.PHONY: composite
composite:  ## Embed the figure into a draw.io diagram
	$(FIGMINT) drawio import $(FIGURE) figures/composite.drawio

.PHONY: status
status:  ## Is anything stale?
	$(FIGMINT) status -v

.PHONY: break
break:  ## Change the data so the figure goes stale
	@python3 -c "import pathlib; p=pathlib.Path('data/performance.csv'); \
	    t=p.read_text().replace('2.80,0.401','2.80,0.455'); p.write_text(t)"
	@echo "data changed — try 'make status'"

.PHONY: restore
restore:  ## Undo `make break`
	@python3 -c "import pathlib; p=pathlib.Path('data/performance.csv'); \
	    t=p.read_text().replace('2.80,0.455','2.80,0.401'); p.write_text(t)"

.PHONY: site
site:  ## Build the document
	$(RUN) myst build --html

.PHONY: serve
serve:  ## Serve the document with live reload
	$(RUN) myst start

.PHONY: clean
clean:  ## Remove build output
	rm -rf _build
