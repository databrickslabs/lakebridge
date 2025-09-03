all: clean dev fmt lint test

clean:
	rm -rf .direnv .venv clean htmlcov .mypy_cache .pytest_cache .ruff_cache .coverage coverage.xml .python-version

setup_python:
	@echo "You have selected python setup with pyenv. It will install pyenv on your system."
	brew list pyenv &>/dev/null || brew install pyenv
	pyenv install -s 3.10
	pyenv local 3.10
	pyenv init - | grep PATH | tail -1
	@echo "Append the above line (export PATH...) to your profile i.e ~/.zshrc or ~/.bash_profile or ~/.profile and resource your profile for the changes in PATH variable to take effect."


dev:
	which hatch > /dev/null || pip3 install hatch
	hatch env create
	hatch run pip3 install --upgrade pip
	hatch run pip3 install -e '.[test]'
	hatch run which python
	@echo "Hatch has created the above virtual environment. Please activate it using 'source .venv/bin/activate' and also select the .venv/bin/python interpreter in your IDE."


lint:
	hatch run verify

fmt:
	hatch run fmt

setup_spark_remote:
	.github/scripts/setup_spark_remote.sh

test:
	hatch run test

test-install:
	hatch run test-install

integration: setup_spark_remote
	hatch run integration

coverage:
	hatch run coverage && open htmlcov/index.html

clean_coverage_dir:
	rm -fr ${OUTPUT_DIR}

python_coverage_report:
	hatch run python src/databricks/labs/lakebridge/coverage/lakebridge_snow_transpilation_coverage.py
	hatch run pip install --upgrade sqlglot
	hatch -e sqlglot-latest run python src/databricks/labs/lakebridge/coverage/sqlglot_snow_transpilation_coverage.py
	hatch -e sqlglot-latest run python src/databricks/labs/lakebridge/coverage/sqlglot_tsql_transpilation_coverage.py

dialect_coverage_report: clean_coverage_dir python_coverage_report
	hatch run python src/databricks/labs/lakebridge/coverage/local_report.py


docs-build:
	yarn --cwd docs/lakebridge build

docs-serve-dev:
	yarn --cwd docs/lakebridge start

docs-install:
	yarn --cwd docs/lakebridge install

docs-serve: docs-build
	yarn --cwd docs/lakebridge serve
