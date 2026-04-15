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
	perl -pi -e 's|resolved "https://[^/]+/|resolved "https://registry.yarnpkg.com/|' yarn.lock

.DEFAULT: all
.PHONY: all clean dev build serve-dev serve lock-dependencies
