# Playground for the figmint editing loop in a MyST document.
#
# Run from examples/myst. `make help` lists everything.

FIGURE   ?= figures/composite.drawio.svg
HANDOFF  := .build/composite.drawio
RUN      := uv run
FIGMINT  := $(RUN) figmint

.DEFAULT_GOAL := help

.PHONY: help
help:  ## Show available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

.PHONY: plot
plot:  ## Regenerate the component from data
	$(RUN) python scripts/plot_cp.py

.PHONY: status
status:  ## Is any panel stale, or the composite behind its inputs?
	$(FIGMINT) status $(FIGURE) -v

.PHONY: check
check:  ## Is every panel identified well enough to publish?
	$(FIGMINT) check $(FIGURE) -v

.PHONY: edit
edit:  ## Open the figure in draw.io
	@command -v drawio >/dev/null || { echo "drawio not installed"; exit 1; }
	drawio $(FIGURE)

.PHONY: place
place:  ## Embed a figure with provenance: make place FIGURE_IN=figures/x.svg
	@test -n "$(FIGURE_IN)" || { echo "usage: make place FIGURE_IN=figures/x.svg"; exit 1; }
	@mkdir -p .build
	$(FIGMINT) reimport $(FIGURE) -o $(HANDOFF)
	$(FIGMINT) place $(FIGURE_IN) --into $(HANDOFF)
	drawio -x -f svg --embed-diagram --yes -o $(FIGURE) $(HANDOFF)

.PHONY: refresh
refresh:  ## Re-embed changed panels, then re-render the single file
	@mkdir -p .build
	$(FIGMINT) reimport $(FIGURE) -o $(HANDOFF)
	drawio -x -f svg --embed-diagram --yes -o $(FIGURE) $(HANDOFF)
	@echo "refreshed $(FIGURE)"

.PHONY: break
break:  ## Change the data so the figure goes stale
	@python3 -c "import pathlib; p=pathlib.Path('data/performance.csv'); \
	    t=p.read_text().replace('2.80,0.401','2.80,0.455'); p.write_text(t)"
	@$(MAKE) --no-print-directory plot
	@echo "data changed and the plot regenerated — try 'make status'"

.PHONY: site
site:  ## Build the MyST site
	$(RUN) myst build --html

.PHONY: serve
serve:  ## Serve the MyST site with live reload
	$(RUN) myst start

.PHONY: clean
clean:  ## Remove build output
	rm -rf _build .build
