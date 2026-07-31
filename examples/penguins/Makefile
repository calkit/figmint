# Live editing for the provenance probe page.
#
# This project has two document paths and they do not overlap:
#
#   article.myst   the paper — built by Stencila (`snakemake article.pdf`)
#   provenance.md  figmint's provenance panel over the artifacts — built by mystmd
#
# Only the second is served here. Nothing in this file touches the Snakemake
# workflow or the paper.

RUN       ?= uv run --project ../..
MYST      := .venv/bin/myst
FIGMINT   := $(RUN) figmint
OPEN_PORT ?= 8765

# The artifacts the probe page reports on. Watching these is what makes the
# panel react: figmint follows each one back to the Snakemake rule that declares
# it, and watches that rule's inputs too.
ARTIFACTS := figures/dimorphism.png figures/isotopes.png figures/sensitivity.png \
             penguins_normalized.csv penguins.csv schematic.png

.DEFAULT_GOAL := help

.PHONY: help
help:  ## Show available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

.PHONY: install
install:  ## Install the document toolchain (mystmd + figmint) into .venv
	uv venv --allow-existing
	uv pip install mystmd
	uv pip install -e ../..

.PHONY: serve
serve:  ## Serve provenance.md with live provenance (the one to use)
	@# Two processes, because one cannot do the job. `myst start` rebuilds when
	@# the markdown changes, but it caches the parsed page — so regenerating a
	@# figure leaves the provenance panel reporting state that is no longer
	@# true. The watcher notices the artifacts and touches provenance.md, which
	@# is what makes the panel react.
	@#
	@# The watcher also serves the endpoint behind the panel's edit button.
	@# FIGMINT_OPEN_URL is exported here rather than set by the watcher, because
	@# myst is its sibling and would inherit nothing from it.
	@test -x $(MYST) || { echo "run 'make install' first"; exit 1; }
	@FIGMINT_OPEN_URL=http://127.0.0.1:$(OPEN_PORT)/open; export FIGMINT_OPEN_URL; \
	  $(FIGMINT) watch $(ARTIFACTS) --touch provenance.md \
	    --open-server --open-port $(OPEN_PORT) & \
	  watcher=$$!; \
	  trap "kill $$watcher 2>/dev/null" EXIT INT TERM; \
	  $(MYST) start

.PHONY: site
site:  ## Build provenance.md to HTML
	@test -x $(MYST) || { echo "run 'make install' first"; exit 1; }
	$(MYST) build --html

.PHONY: check
check:  ## Is every artifact identified well enough to publish?
	@for f in $(ARTIFACTS); do $(FIGMINT) check $$f -v; done

.PHONY: status
status:  ## Report each artifact's provenance level
	@$(RUN) python -c "import sys; from pathlib import Path; \
	  from figmint.report import inspect_any; \
	  [print(f'  {c.level.slug:<14} {a:<28} {c.reason[:64]}') \
	   for a in sys.argv[1:] \
	   for c in inspect_any(Path(a)).components]" $(ARTIFACTS)

.PHONY: run
run:  ## Run the Snakemake workflow (the analysis, not the probe page)
	$(RUN) --group dev snakemake -c1

.PHONY: clean
clean:  ## Remove the probe page's build output
	rm -rf _build
