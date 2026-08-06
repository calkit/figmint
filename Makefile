.PHONY: help
help: ## Show this help.
	@uv run python -c "import re; \
	[[print(f'\033[36m{m[0]:<20}\033[0m {m[1]}') for m in re.findall(r'^([a-zA-Z_-]+):.*?## (.*)$$', open(makefile).read(), re.M)] for makefile in ('$(MAKEFILE_LIST)').strip().split()]"

.PHONY: install
install: ## Create the project's virtual environment.
	@echo "🚀 Creating virtual environment"
	@uv sync

.PHONY: format
format: ## Automatically format files.
	@echo "🚀 Linting code with pre-commit"
	@uv run pre-commit run -a

.PHONY: check
check: format ## Run code quality tools.
	@echo "🚀 Checking lock file consistency with 'pyproject.toml'"
	@uv lock --locked
	@echo "🚀 Static type checking with mypy"
	@uv run mypy

.PHONY: test
test: ## Test the code with pytest.
	@echo "🚀 Testing code with pytest"
	@uv run pytest

.PHONY: test-cov
test-cov: ## Test the code coverage with pytest.
	@echo "🚀 Testing code coverage with pytest"
	@uv run pytest --cov --cov-config=pyproject.toml

.PHONY: example
example: ## Build the MyST example end to end.
	@echo "🚀 Building the MyST example"
	@$(MAKE) -C examples/myst all

.PHONY: clean-example
clean-example: ## Remove the MyST example's build output.
	@$(MAKE) -C examples/myst clean

.PHONY: example-quarto
example-quarto: ## Build the Quarto example end to end.
	@echo "🚀 Building the Quarto example"
	@$(MAKE) -C examples/quarto extension declare all

.PHONY: clean-example-quarto
clean-example-quarto: ## Remove the Quarto example's build output.
	@$(MAKE) -C examples/quarto clean

.PHONY: example-calkit
example-calkit: ## Run the Calkit example's pipeline.
	@echo "🚀 Running the Calkit example"
	@cd examples/calkit && calkit run && figmint status

.PHONY: example-snakemake
example-snakemake: ## Run the Snakemake example's workflow.
	@echo "🚀 Running the Snakemake example"
	@cd examples/snakemake && uv lock && snakemake --cores 1 && figmint status

.PHONY: example-astra
example-astra: ## Run the ASTRA example's baseline universe.
	@echo "🚀 Running the ASTRA example"
	@cd examples/astra && uv lock && astra validate astra.yaml \
		&& uv run python src/run.py && figmint status
