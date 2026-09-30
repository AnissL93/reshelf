# Reader smoke checklist — sub-project B

Run against a scratch library of real books at
`/tmp/claude-1000/-home-hy-Projects-book-org/8eaa9994-fbe1-426b-81a3-0d76a0f594d7/scratchpad/testlib`,
served at `http://127.0.0.1:8099`. Copies, not the live library — the
checklist writes annotations and those land in sidecars.

| sha | file | engine | notes |
|---|---|---|---|
| `b1befbb5` | text-layer.pdf | pdf | 33 pages, real text layer |
| `975f0e56` | scanned-280p.pdf | pdf | 280 pages, **no text layer** — the majority case |
| `5d7abdc7` | huge-707mb.pdf | pdf | 707 MB, symlinked, read-only |
| `9677468c` | tesla.epub | epub | |
| `c4ef56b7` | doctor-who.mobi | — → epub | convert-first path |
| `c7a56f98` | topology.djvu | — → pdf | convert-first path |

## Results

| # | Case | Result | Note |
|---|---|---|---|
| 1 | Text-layer PDF renders pages | PASS | Page 1 canvas in ~1.2 s; 33 placeholders, 1 canvas, 31 text-layer spans. 200 + two 206s. |
| 2 | Scanned PDF renders pages | **FAIL** | Only page 1 (a JPEG cover) renders. Pages 2-270 sampled: all-white canvas. Console: `JBig2 failed to initialize` / `Ensure that the wasmUrl API parameter is provided`. pdf.js is not given `wasmUrl`, so JBIG2 scans are blank. Shot: r2-page20.png |
| 3 | **707 MB PDF shows page 1 quickly**; network shows `206`s, not one 707 MB `200` | **FAIL** | Page 1 up in 1.7 s and range requests are `206`, BUT pdf.js also issues a plain no-Range request that returns `200` and the browser receives all 707,289,487 bytes (753 MB total over 317 requests in ~8 s). Looks like `disableAutoFetch`/`disableStream` are unset. Also ~316 range reads to walk the page tree for 327 placeholders. _(plan: lazy render + Range)_ |
| 4 | EPUB renders and paginates | PASS (defect) | Renders (cover) and paginates: `view.next()` 1 -> 45 pages; horizontal wheel turns pages. But no button, no arrow/PageDown/Space key, and vertical wheel does nothing - a mouse user cannot turn pages. Not on checklist. |
| 5 | Area tool draws a box on the scanned PDF and a highlight is created | PASS | Area drag on scan p.1 -> exactly one POST, overlay at [0.2,0.3,0.5,0.1], sidebar row. Drawn on the cover page only (later pages blank, see 2). _(plan: the majority interaction)_ |
| 6 | That highlight persists across a reload, same place | PASS (caveat) | Overlay identical after re-entry. A literal browser reload of `/read/<sha>` returns `{"detail":"Not Found"}` (no SPA fallback, see Extra findings), so reload was emulated by loading `/` then client-side navigating. |
| 7 | **Same highlight lands in the same place after zooming** | PASS | Box at [0.2,0.3,0.5,0.1] of the page at 140%, 200% and 80%; screenshots show it over the same glyphs (bottom of the large character + the author names). _(plan: why coordinates are normalised 0–1)_ |
| 8 | Text selection on the text-layer PDF creates a highlight storing its quote | PASS | Drag over text-layer PDF -> one `pdf-text` highlight with rects and quote in the sidebar. Note: ending a drag exactly on a line edge selected ~15 lines (pdf.js text-layer overshoot); ending mid-line selected 93 chars as expected. |
| 9 | One drag creates exactly ONE highlight, not a stream | PASS | One 30-step text drag -> 1 POST; one area drag -> 1 POST. _(plan: the debounce)_ |
| 10 | Note add / edit / delete on a highlight, surviving reload | PASS | Add "first note", reload, edit to "edited note", reload, delete via confirm + reload: all persisted server-side (PATCH/DELETE seen). |
| 11 | **Note typed while the create POST is still in flight survives** | PASS | Create POST delayed 4 s via route; clicked "Add a note" at once and typed ~6 s. Textarea kept text and focus across the id swap; PATCH went to the server id `aa405b...`; note persisted. _(plan: why `localId` exists)_ |
| 12 | Bookmark created and jumped to from the sidebar | PASS | Bookmark on p.6 shows as flag row; persists after re-entry; jump lands on p.6. |
| 13 | Sidebar lists in reading order; clicking jumps | PASS | Sidebar order p.1, p.1, p.3, p.6, p.10 although created 10, 3 order; each jump put its page at the top. |
| 14 | Reading position resumes on reopen | PASS (caveat) | Scrolled to p.15, waited 6.5 s: sidecar `page=15`; re-entry opened at p.15. Leaving at p.22 within ~1 s wrote `page=22` (beacon). Reload caveat as row 6. |
| 15 | **Opening the 707 MB PDF at a saved page and leaving within ~1 s does not overwrite it with page 1** | PASS (caveat) | Saved p.40 on the 707 MB book. Re-opened and left after 1000, 300, 0 ms and via an in-app link at 800 ms: sidecar stayed `page=40` each time; final open restored p.40. Reload caveat as row 6. _(plan: the readiness gate)_ |
| 16 | **Opening a book's other format does not overwrite the first format's saved position** | PASS | Seeded `page=5` (foreign locator) on the MOBI book: opening the derived EPUB idle 8 s and leaving left it unchanged. Read a few pages -> epubcfi saved; opening the MOBI card kept it; reopening the EPUB restored the same CFI. _(plan: the baseline gate)_ |
| 17 | MOBI offers "Convert to EPUB & read"; after the job, the EPUB opens | PASS | Card "Convert to EPUB & read"; job finished in ~2 s and the EPUB opened with a MOBI/EPUB selector. Minor: book was re-fetched 4x after done (onDone identity changes each render). |
| 18 | DJVU offers "Convert to PDF" | PASS | Card reads "Convert to PDF & read". Conversion not run. |
| 19 | **Annotations survive `rm db/books.sqlite3 && reshelf reindex`** | **FAIL** | Sidecars intact (all 6 dumped sidecars byte-identical before/after), no annotation data in SQLite. But after `rm db/books.sqlite3*` + `reshelf reindex` the `files` table is empty (reindex only rebuilds `book_index`): `GET /api/books/<sha>/annotations` -> 404 "no such book", reader shows 0 annotations, and reading/annotation PUT/POST would 404 too. Only `reshelf scan <books dir>` restored them (status also reset to SCANNED). _(plan: sidecar is the record)_ |
| 20 | Stopping the server mid-session keeps the mark on screen with a retry banner | PASS | Killed server, drew a box: mark stays, banner "1 unsaved: Failed to fetch" + Retry; restarted server, Retry saved it (5 annotations on server). _(plan: never lose a drawn mark)_ |
| 21 | Book page lists annotations grouped by file, with Read links | PASS | Book page groups by file ("PDF - original") with Read/open links; MOBI book lists both files with "Convert & read" / "Read". Highlights listed in creation order (p.10 before p.3). |
| 22 | No console errors during any of the above | **FAIL** | Real errors: 404 on deep links, JBIG2 warnings x3 (row 2). Expected: ERR_CONNECTION_REFUSED (row 20), ERR_ABORTED on beacon POSTs at navigation, iframe sandbox warning (foliate). See report. |

## Run notes

- Playwright MCP could not launch (`"chrome" executable not found`), so every row was driven with the
  `playwright-core` library and the cached Chromium 1234 (headless, 1400x900) via node scripts. Same engine,
  real browser; the MCP tool surface itself was not used.
- The server has no SPA fallback: `GET /read/<sha>`, `/book/<sha>`, `/jobs` ... return 404 JSON on a direct
  load or F5. Rows involving "reload" load `/` and navigate client-side instead.
- Rows 19 and 20 stopped/restarted the scratch server. Full narrative: `.superpowers/sdd/2026-09-30-web-app-b-reader/task-12-report.md`.

## Re-run of the failed rows (2026-09-30, after fixes)

| # | Result | Note |
|---|---|---|
| 2 | PASS | `wasmUrl` set; page 20 of the scanned PDF renders (8.7% dark pixels, was all-white). |
| 3 | PASS | `disableAutoFetch` + `disableStream`: 23.7 MB of 707 MB transferred after 10 s idle. |
| 19 | PASS | `rm db/books.sqlite3* && reshelf reindex` (files table: 0 rows): book page lists 6 annotations, reader shows them; endpoints fall back to the sidecar. |
| 22 | PASS | Deep links (`/read/<sha>`, `/jobs`) now 200 via SPA fallback; no JBIG2 errors. Only an expected ERR_ABORTED on navigation. |
