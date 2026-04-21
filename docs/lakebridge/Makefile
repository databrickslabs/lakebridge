all: dev build

clean:
	rm -fr node_modules build .docusaurus

dev:
	yarn install --frozen-lockfile

build:
	yarn build

serve-dev:
	yarn start

serve: build
	yarn serve

lock-dependencies:
	rm -f yarn.lock
	yarn install
	@perl -ni -e 'print unless /^\s*resolved\s+"https:/' yarn.lock
	@printf 'Stripped repository references from yarn.lock.\n'

.DEFAULT: all
.PHONY: all clean dev build serve-dev serve lock-dependencies
