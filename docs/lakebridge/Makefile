all: dev build

clean:
	rm -fr node_modules build .docusaurus

dev:
	bun install

build:
	bun run build

serve-dev:
	bun run start

serve: build
	bun run serve

lock-dependencies:
	@rm -f bun.lock
	bun install --lockfile-only
	@perl -pi -e 's|"https://[^/]+/([^"]*\.tgz)"|""|g' bun.lock
	@printf 'Stripped repository references from bun.lock.\n'

.DEFAULT: all
.PHONY: all clean dev build serve-dev serve lock-dependencies
