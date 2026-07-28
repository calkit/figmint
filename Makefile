.DEFAULT_GOAL := help

# Where the editor is allowed to read and write, and which subdirectory it
# scans for figures. Override per-project, e.g.:
#   make dev ROOT=~/research/turbine-paper FIGURES=figures
ROOT ?= .
FIGURES ?=
API_PORT ?= 8420
WEB_PORT ?= 5273

FIGURES_ARG := $(if $(FIGURES),--figures $(FIGURES),)

.PHONY: help
help:  ## Show available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

.PHONY: install
install: web/node_modules  ## Install Python and Node dependencies
	uv sync

web/node_modules: web/package.json
	cd web && npm install
	@touch web/node_modules

.PHONY: dev
dev: web/node_modules  ## Run the API and the Vite dev server together
	@echo "figmint  api :$(API_PORT)  editor http://localhost:$(WEB_PORT)"
	@trap 'kill 0' EXIT INT TERM; \
	uv run figmint serve --root $(ROOT) $(FIGURES_ARG) --port $(API_PORT) & \
	cd web && npm run dev -- --port $(WEB_PORT) & \
	wait

.PHONY: api
api:  ## Run only the backend API
	uv run figmint serve --root $(ROOT) $(FIGURES_ARG) --port $(API_PORT) --reload

.PHONY: web
web: web/node_modules  ## Run only the Vite dev server
	cd web && npm run dev -- --port $(WEB_PORT)

.PHONY: build
build: web/node_modules  ## Build the editor into web/dist
	cd web && npm run build

.PHONY: serve
serve: build  ## Build, then serve the editor from the API process alone
	uv run figmint serve --root $(ROOT) $(FIGURES_ARG) --port $(API_PORT)

.PHONY: test
test: web/node_modules  ## Run the front-end test suite
	cd web && npm test

.PHONY: check
check: web/node_modules  ## Typecheck, lint, and test the front end
	cd web && npm run typecheck && npm run lint && npm test

.PHONY: clean
clean:  ## Remove build output and installed packages
	rm -rf web/dist web/node_modules
