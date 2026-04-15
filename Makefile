all: clean dev fmt test

# Ensure that all uv commands don't automatically update the lock file: instead they use the locked dependencies.
export UV_FROZEN := 1
# Ensure that hatchling is pinned when builds are needed.
export UV_BUILD_CONSTRAINT := .build-constraints.txt

UV_RUN := uv run --exact --all-extras
UV_TEST := $(UV_RUN) pytest --timeout 30 --durations 20 --cov=src

clean: docs-clean
	rm -fr .venv clean htmlcov .mypy_cache .pytest_cache .ruff_cache .coverage coverage.xml
	find . -name '__pycache__' -print0 | xargs -0 rm -fr

dev:
	uv sync --all-extras

lint:
	$(UV_RUN) black --check .
	$(UV_RUN) ruff check .
	$(UV_RUN) mypy --disable-error-code 'annotation-unchecked' .
	$(UV_RUN) pylint --output-format=colorized -j 0 src tests

fmt:
	$(UV_RUN) black .
	$(UV_RUN) ruff check . --fix
	$(UV_RUN) mypy --disable-error-code 'annotation-unchecked' .
	$(UV_RUN) pylint --output-format=colorized -j 0 src tests

setup_spark_remote:
	.github/scripts/setup_spark_remote.sh

test:
	$(UV_TEST) --cov-report=xml tests/unit

integration: setup_spark_remote
	$(UV_TEST) tests/integration

coverage:
	$(UV_TEST) --cov-report=html tests \
	           --ignore=tests/integration/install \
	           --ignore=tests/integration/connections \
	           --ignore=tests/integration/assessments
	open htmlcov/index.html

build:
	uv build --require-hashes --build-constraints=.build-constraints.txt

lock-dependencies: UV_FROZEN := 0
lock-dependencies:
	uv lock
	$(UV_RUN) --group yq tomlq -r '.["build-system"].requires[]' pyproject.toml | \
	    uv pip compile --generate-hashes --universal --no-header --quiet - > build-constraints-new.txt
	mv build-constraints-new.txt .build-constraints.txt
	perl -pi -e 's|registry = "https://[^"]*"|registry = "https://pypi.org/simple/"|g' uv.lock

clean_coverage_dir:
	@printf "Deleting: %s\n" "$${OUTPUT_DIR:?must be set}"
	@rm -fr "$${OUTPUT_DIR}"

python_coverage_report:
	$(UV_RUN) python src/databricks/labs/lakebridge/coverage/lakebridge_snow_transpilation_coverage.py
	$(UV_RUN) --group sqlglot python src/databricks/labs/lakebridge/coverage/sqlglot_snow_transpilation_coverage.py
	$(UV_RUN) --group sqlglot python src/databricks/labs/lakebridge/coverage/sqlglot_tsql_transpilation_coverage.py

dialect_coverage_report: clean_coverage_dir python_coverage_report
	$(UV_RUN) python src/databricks/labs/lakebridge/coverage/local_report.py

docs-clean docs-dev docs-build docs-serve-dev docs-serve docs-lock-dependencies:
	$(MAKE) -C docs/lakebridge $(@:docs-%=%)

.DEFAULT: all
.PHONY: all clean dev lint fmt test integration coverage build lock-dependencies \
	setup_spark_remote clean_coverage_dir python_coverage_report dialect_coverage_report \
	docs-clean docs-dev docs-build docs-serve-dev docs-serve docs-lock-dependencies
