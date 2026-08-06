# The figmint loop, in a Quarto document. Run from examples/quarto.
#
# The targets are real files with real prerequisites rather than a list of
# phony verbs, so `make` works out what needs redoing and in what order. That
# is not just convenience: figmint's whole model is a derivation graph, and a
# Makefile that did not mirror it would let you rebuild the document from a
# figure nobody had redrawn — which `figmint status` would then, correctly,
# complain about.

RUN     := uv run
FIGMINT := $(RUN) figmint

DATA   := data/performance.csv
SCRIPT := scripts/plot_cp.py
FIGURE := figures/cp_curve.png
HTML   := _site/index.html

.DEFAULT_GOAL := help

.PHONY: help
help:  ## Show available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

.PHONY: all
all: $(HTML)  ## Build everything that is out of date, in order

.PHONY: extension
extension: _extensions/figmint/figmint.lua  ## Install the Quarto filter

# Installed from the package rather than with `quarto add`, because the filter
# and `figmint-quarto` speak a protocol private to figmint and have to be the
# same version.
_extensions/figmint/figmint.lua: uv.lock
	$(FIGMINT) quarto install

# The declarations. Nothing produces a dataset or a plotting script, so without
# these the record has a hole at the bottom that every check above it passes
# straight over.
.PHONY: declare
declare:  ## Say where the primary artifacts came from
	$(FIGMINT) declare $(DATA) --mine
	$(FIGMINT) declare $(SCRIPT) --mine --with-ai 'Claude Opus 5'
	$(FIGMINT) declare index.qmd --mine
	$(FIGMINT) declare _quarto.yml --mine

# The environment is an input to this, which is why `uv.lock` is a prerequisite:
# a different matplotlib draws a different figure, and figmint records the lock
# precisely so that is visible rather than assumed.
$(FIGURE): $(DATA) $(SCRIPT) uv.lock
	$(FIGMINT) run -i $(DATA) -o $@ -- uv run python $(SCRIPT)

# The document is an artifact too, and the most useful one to check: it is what
# a reader actually receives. Its inputs are the sources it is built from —
# `figmint status` reaches everything behind them on its own, so the figure's
# data and script do not need repeating here.
#
# `figmint.toml` is deliberately *not* an input, even though the filter reads it
# to draw the panels. Recording the file that `figmint run` itself writes would
# make the document stale the instant it finished building.
#
# Recorded but not signed: c2pa does not recognize HTML, so `figmint.toml` is
# the only provenance this output has.
$(HTML): index.qmd _quarto.yml $(FIGURE) uv.lock _extensions/figmint/figmint.lua
	$(FIGMINT) run -i index.qmd -i _quarto.yml -i $(FIGURE) \
	    -o $@ -- uv run quarto render

uv.lock: pyproject.toml
	uv lock

.PHONY: plot
plot: $(FIGURE)  ## Produce the figure, recording its provenance

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

# `quarto preview` re-renders when *its own* sources change — the .qmd,
# _quarto.yml, a linked image. A change to a script or a dataset is invisible
# to it, so the provenance panels would keep showing the previous render and a
# stale figure would look current for as long as you left the tab open. This
# watches everything figmint records as an input and touches index.qmd when any
# of it moves, which is the one thing Quarto does notice.
#
# It has to be *this* preview. VS Code's Quarto extension starts its own with
# `--no-watch-inputs`, which turns off re-rendering on file changes altogether
# — it re-renders when you save the document and never otherwise — so the touch
# below would do nothing there.
define WATCH_INPUTS
import pathlib, time, tomllib

record = pathlib.Path("figmint.toml")
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
