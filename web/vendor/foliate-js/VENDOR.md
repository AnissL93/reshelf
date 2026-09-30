# foliate-js (vendored)

Upstream: https://github.com/johnfactotum/foliate-js
Licence: MIT (see LICENSE in this directory)
Pinned commit: 78914aef4466eb960965702401634c2cb348e9b1
Vendored on: 2026-09-30

## Why vendored rather than installed

The `foliate-js` package on npm is not published by the author. It is a
third-party republish, one version, untouched since 2025-04, and it
ships only a subset of the modules — no zip reader, so its `epub.js`
cannot actually open a file. Upstream is plain ESM with no build step
and is meant to be vendored.

## Updating

Re-clone upstream, copy `*.js` and `vendor/` over this directory (then
delete `eslint.config.js`, `rollup.config.js` and `*.map`, see below),
update the pinned commit above, then run the reader smoke checklist in
`docs/superpowers/plans/2026-09-30-web-app-b-reader.md` (Task 12).

## Local modifications

None to any source file. Keep it that way — anything reshelf-specific
belongs in `web/src/reader/engines/epub.ts`.

Files not copied, because nothing imports them: upstream's `package.json`,
`reader.html`/`reader.js` demo, `ui/`, `tests/`, `rollup/`, and the
`eslint`/`rollup` configs. `*.map` files under `vendor/pdfjs` were dropped
(~7 MB). `vendor/pdfjs` itself is kept only because `pdf.js` imports it
and Vite must resolve the graph; reshelf never opens a PDF through it.
The tree is excluded from oxlint via `web/.oxlintrc.json`.
