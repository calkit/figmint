# Playground for the figmint editing loop in a MyST document.
#
# Run from examples/myst. `make help` lists everything.

# The authored diagram and the published picture are deliberately two files.
# SOURCE is what you edit; RENDERED is what the document embeds and what
# `figmint check` reads. Nothing writes SOURCE except you, and nothing but the
# pipeline writes RENDERED — see the note in calkit.yaml for why collapsing them
# into one file cannot work once a pipeline is involved.
SOURCE   ?= figures/composite.drawio
RENDERED ?= figures/composite.drawio.svg
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
	$(FIGMINT) status $(RENDERED) -v

.PHONY: check
check:  ## Is every panel identified well enough to publish?
	$(FIGMINT) check $(RENDERED) -v

.PHONY: edit
edit:  ## Open the authored diagram in draw.io
	@command -v drawio >/dev/null || { echo "drawio not installed"; exit 1; }
	drawio $(SOURCE)

.PHONY: place
place:  ## Embed a figure with provenance: make place FIGURE_IN=figures/x.svg
	@test -n "$(FIGURE_IN)" || { echo "usage: make place FIGURE_IN=figures/x.svg"; exit 1; }
	$(FIGMINT) place $(FIGURE_IN) --into $(SOURCE)
	@$(MAKE) --no-print-directory refresh

.PHONY: refresh
refresh:  ## Re-embed changed panels, then re-render the published picture
	@mkdir -p .build
	$(FIGMINT) reimport $(SOURCE) -o $(HANDOFF)
	drawio -x -f svg --embed-diagram --yes -o $(RENDERED) $(HANDOFF)
	@echo "refreshed $(RENDERED)"

.PHONY: break
break:  ## Change the data so the figure goes stale
	@python3 -c "import pathlib; p=pathlib.Path('data/performance.csv'); \
	    t=p.read_text().replace('2.80,0.401','2.80,0.455'); p.write_text(t)"
	@$(MAKE) --no-print-directory plot
	@echo "data changed and the plot regenerated — try 'make status'"

.PHONY: site
site:  ## Build the MyST site
	$(RUN) myst build --html --execute

OPEN_PORT ?= 8765

.PHONY: serve
serve:  ## Serve the document with live provenance (the one to use)
	@# Two processes, because one cannot do the job. `myst start` rebuilds when
	@# index.md changes, but it caches the parsed page — so editing a plotting
	@# script, or running the pipeline, leaves the provenance panel reporting
	@# state that is no longer true. The watcher notices those files and touches
	@# index.md, which is what makes the panel react.
	@#
	@# This used to be two targets, `serve` and `preview`, and `serve` was the
	@# obvious name for the one that silently lacked the watcher. There is now
	@# one command, because the version without it is never what anyone wants.
	@#
	@# The watcher also serves the endpoint behind the panel's edit button.
	@# FIGMINT_OPEN_URL is exported here rather than set by the watcher, because
	@# myst is its sibling and would inherit nothing from it.
	@# Killing the server kills the watcher.
	@FIGMINT_OPEN_URL=http://127.0.0.1:$(OPEN_PORT)/open; export FIGMINT_OPEN_URL; \
	  $(FIGMINT) watch $(RENDERED) --touch index.md \
	    --open-server --open-port $(OPEN_PORT) & \
	  watcher=$$!; \
	  trap "kill $$watcher 2>/dev/null" EXIT INT TERM; \
	  $(RUN) myst start --execute

.PHONY: preview
preview: serve  ## Alias for `serve`

.PHONY: watch
watch:  ## Just the watcher, without the document server
	$(FIGMINT) watch $(RENDERED) --touch index.md

.PHONY: clean
clean:  ## Remove build output
	rm -rf _build .build
