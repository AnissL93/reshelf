# Sub-project B — Reader and Annotations

Date: 2026-09-30
Parent: `2026-09-26-web-app-decomposition.md`
Depends on: `2026-09-26-web-app-a-library.md` (shipped)
Status: approved (design)

## 1. Scope

**In:** an in-browser reader at `/read/:sha` for PDF and EPUB,
text-selection highlights, area (rectangle) highlights, notes attached
to highlights, bookmarks, automatic reading progress, a per-book
annotation sidebar, and the annotation API that persists all of it
into the existing JSON sidecar.

**Out (sub-project C):** Obsidian export, Markdown rendering of
annotations, Docker.

**Out entirely:** OCR, a cross-book highlights page, annotations
mirrored into SQLite, writing highlights back into the PDF or EPUB
file, auth, multi-user, Calibre.

### 1.1 Measured facts driving this design

Sampled 80 of 1,502 PDFs in the live library with PyMuPDF, counting a
page as text-bearing above 200 characters:

| | Files | Share |
|---|---|---|
| Scanned PDFs (no text layer) | ~51/80 | **64%** |
| Text-layer PDFs | ~29/80 | 36% |

Roughly 960 PDF files cannot support text-selection highlighting at
all. **Area highlights are therefore the primary PDF interaction, not
a fallback.** Text selection is offered in addition wherever a text
layer exists, because it yields quotable text for sub-project C.

Format counts from the decomposition spec: PDF 1129, EPUB 619, MOBI
67, AZW3 18, TXT 5, DJVU 3. PDF and EPUB are 95% of the library and
get reader engines; the remaining 93 files are read through A's
existing converters.

## 2. Engines

| Format | Engine | Source |
|---|---|---|
| PDF | `pdfjs-dist` | npm, official, current (6.x) |
| EPUB | foliate-js | **vendored** from `johnfactotum/foliate-js` at a pinned commit |

The `foliate-js` npm package is an unofficial republish by a third
party, one version, unmaintained since 2025-04. Upstream is MIT, plain
ESM with no build step, and intended to be vendored. It lands in
`web/vendor/foliate-js/` with `VENDOR.md` recording the upstream URL,
the pinned commit, the licence and the update procedure.

`epubjs` was considered and rejected: its built-in
`rendition.annotations` API would save some EPUB overlay code, but the
package has been stale since 2023 and a PDF overlay must be written by
hand regardless, so the saving does not pay for the dependency.

### 2.1 The engine interface

The reader shell never learns which format it is showing. Both engines
implement:

```ts
interface Locator { locator: string; percent: number }  // -> Reading

interface Engine {
  readonly supportsArea: boolean                 // true for PDF only
  mount(host: HTMLElement, fileUrl: string): Promise<void>
  goTo(anchor: Anchor): Promise<void>
  locate(): Locator                              // for progress
  paint(annotations: Annotation[]): void         // draw the overlay
  onTextSelect(cb: (a: Anchor, text: string) => void): void
  onAreaSelect(cb: (a: Anchor) => void): void    // EPUB: never fires
  destroy(): void
}
```

`Locator` is the pair already stored on the sidecar's `Reading` model:
an engine-defined opaque `locator` string (a page number for PDF, a CFI
for EPUB) and a `percent` for display.

`onAreaSelect` is part of the interface but the EPUB engine never
fires it — reflowable text has no stable page geometry to box. The
shell hides the area-highlight tool when the active engine reports
`supportsArea === false` rather than offering a control that does
nothing.

## 3. Anchors

An anchor is a discriminated union, stored verbatim in the sidecar and
never interpreted by the backend:

```json
{"kind":"pdf-text","page":42,"rects":[[0.1,0.2,0.3,0.02]],"text":"…"}
{"kind":"pdf-area","page":42,"rect":[0.1,0.2,0.4,0.15]}
{"kind":"epub","cfi":"epubcfi(/6/14!/4/2,/1:0,/1:22)","text":"…"}
{"kind":"pdf-page","page":88}
```

`pdf-page` carries a position and no region: it is what a PDF bookmark
anchors to. An EPUB bookmark uses an `epub` anchor whose CFI is a point
rather than a range, with no `text`.

**Coordinates are normalized 0–1 against the page's own size**, so a
highlight survives zoom, window resize, device pixel ratio, and a
different screen. Storing CSS pixels would bind an annotation to the
viewport that made it.

`page` is 1-based to match what the reader displays and what an
Obsidian export will cite.

`text` is stored alongside the anchor whenever the selection has text.
It is redundant for rendering and load-bearing for everything else:
sub-project C exports it, and it is the re-anchoring fallback if a
file is replaced. `pdf-area` has no text by construction.

### 3.1 Durability

**Amended 2026-09-30, while planning.** The original text promised that
an anchor whose CFI or rects no longer resolve would be re-found by
searching its stored `text`. Planning showed that fallback is
unreachable in this design, so it is not built and the promise is
withdrawn here rather than left as an untested claim.

An annotation binds to `file_sha`, which is a content hash (§4.1).
Different bytes give a different hash, so the reader never loads an old
annotation against a changed file — it loads nothing for that file and
there is no stale anchor to re-find. A text-search fallback would be
code that cannot run.

What the reader does instead:

- **`epub` anchors** resolve by CFI. One that does not resolve is listed
  in the sidebar and simply not painted.
- **`pdf-text` anchors** resolve by page and rects, and are likewise
  listed but not painted if the page is absent.
- **`pdf-area` anchors** are bound to a page number and nothing else.

An anchor kind this build does not know — a sidecar written by a future
version — is treated the same way: listed, not painted, never dropped
on write (§7).

`text` is still stored on every anchor that has any. Its justification
is sub-project C, which exports the quoted passage, not re-anchoring.

The one path that can still put an annotation next to different bytes is
sub-project A's offer to carry a sidecar across when a re-downloaded
copy hashes differently. Annotations carried that way keep a `file_sha`
that matches nothing and are listed but never painted — visible, not
silently lost. Recorded as a ceiling in §9.

## 4. The annotation record

Appended to the `annotations: list[dict]` field that A already
reserved on the sidecar. Promoted from `dict` to a typed model:

```json
{
  "id": "0f3c…",
  "type": "highlight",
  "file_sha": "a1b2…",
  "color": "yellow",
  "note": "核心論點",
  "anchor": { "kind": "pdf-area", "page": 42, "rect": [0.1,0.2,0.4,0.15] },
  "created_at": "2026-09-30T…Z",
  "updated_at": "2026-09-30T…Z"
}
```

| Field | Rule |
|---|---|
| `id` | `uuid4().hex`, server-assigned, never reused |
| `type` | `highlight` or `bookmark` |
| `file_sha` | which file this was read from (§4.1) |
| `color` | one of `yellow`, `green`, `blue`, `pink`; ignored for bookmarks |
| `note` | free text, may be empty; a bookmark's note is its name |
| `anchor` | §3; a bookmark's anchor carries only a position |

`Annotation` uses the same `extra="allow"` config as every other
sidecar model, so an annotation written by a future version survives a
rewrite by this one.

### 4.1 `file_sha` — why annotations are per-file, not per-book

A book can hold several files: a scanned PDF original and, after A's
convert step, a derived EPUB. Page 42 of the PDF and a CFI into the
EPUB name different places in different coordinate systems. An
annotation is therefore bound to the file it was made on:

```
file_sha = entry.sha256 or book.sha256
```

`FileEntry.sha256` is set on derived files and `None` on originals,
where the book's own hash is the original's — so the fallback is exact,
not a guess. The reader loads only the annotations whose `file_sha`
matches the file being read. Annotations on other files of the same
book remain in the sidecar, listed on the book detail page under the
file they belong to, and are never silently re-pointed.

## 5. HTTP API

```
GET    /api/books/{sha}/annotations            → [Annotation]
POST   /api/books/{sha}/annotations            → 201 Annotation
PATCH  /api/books/{sha}/annotations/{id}       → 200 Annotation
DELETE /api/books/{sha}/annotations/{id}       → 204
PUT    /api/books/{sha}/reading                → 200 Reading
```

- `GET` takes an optional `?file_sha=` to return one file's
  annotations; without it, all of the book's.
- `POST` assigns `id`, `created_at` and `updated_at` server-side. A
  client-supplied `id` is ignored, not honoured.
- `PATCH` accepts `note` and `color` only. An anchor is immutable: a
  mark in a different place is a different mark. A body naming any
  other field is `422`.
- `DELETE` on an unknown id is `404`, not a silent success — the client
  needs to know its optimistic state was wrong.
- `PUT /reading` writes the existing `Reading` model
  (`locator`, `percent`, `updated_at`).

Every write goes through `SidecarStore.update()`, which already holds a
per-sha lock and writes atomically via temp + `os.replace`. **No SQLite
change, no migration, no index write.** The user chose no cross-book
view, so annotations never enter the index.

### 5.1 Concurrency

`SidecarStore.update()` serializes writes per book, so two tabs
annotating the same book cannot interleave a lost update *within* the
store. What it does not prevent is a stale read-modify-write from a
client that loaded the list earlier. Each endpoint therefore mutates
the list inside the `update()` callback — appending, patching by id,
or removing by id — rather than writing back a list the client sent.
The client never PUTs a whole annotation array.

## 6. The reader

Route `/read/:sha`, with `?file_sha=` selecting which file when a
book has more than one readable file.

```
web/src/reader/
  Reader.tsx          shell: toolbar, engine host, sidebar
  engines/types.ts    the Engine interface and Anchor union
  engines/pdf.ts      pdfjs-dist adapter
  engines/epub.ts     foliate-js adapter
  anchors.ts          pure coordinate and CFI helpers — the tested part
  Sidebar.tsx         highlights, notes, bookmarks; click to jump
  useAnnotations.ts   CRUD with optimistic state and retry
```

**Toolbar:** page/location, zoom (PDF) or font size (EPUB), the
highlight-colour picker, the area-highlight toggle (PDF only), add
bookmark, sidebar toggle.

**Sidebar:** the active file's annotations in reading order —
colour swatch, page or chapter, quoted text where there is any, the
note. Clicking one calls `engine.goTo(anchor)`. Editing a note or
colour is in place; deleting asks first.

**Book detail page:** the same list, read-only, grouped by file, with
a "Read" button per readable file.

### 6.1 Reading progress

The engine reports a `Locator` on page or section change. The client
writes at most once per 5 seconds, and once more on unload via
`navigator.sendBeacon`. Opening a book with a stored locator resumes
there; a "start from the beginning" control overrides it.

Throttling is not an optimization — an unthrottled writer would rewrite
a multi-kilobyte sidecar on every scroll tick.

### 6.2 Formats with no engine

`/read/:sha` on a MOBI, AZW3 or TXT renders a card offering "Convert to
EPUB & read"; DJVU offers "Convert to PDF". The button posts A's
existing per-book convert job and, when it completes, navigates to the
derived file. No new converter code, no new dependency.

A format with neither an engine nor a converter renders "This format
cannot be read in the browser" with a download link.

## 7. Error handling

| Failure | Behaviour |
|---|---|
| Book or file 404 | Error card naming the recorded path, plus a download link |
| Engine fails to parse | Error card plus "Download instead" — a corrupt EPUB must not blank the page |
| Annotation save fails | **The mark stays on screen** with a retry banner. An optimistic mark is never rolled back into nothing; losing a highlight the user just made is worse than showing one that is not yet saved. |
| `DELETE` returns 404 | The list refetches — the client's state was stale |
| Unknown `anchor.kind` | Listed in the sidebar as "unsupported in this version", not rendered and **not dropped on the next write** |
| Convert job fails | The job's own error surfaces in the existing job UI |

## 8. Testing

**Python (pytest, as A):**
- annotation CRUD round-trips through a real sidecar file
- `POST` ignores a client-supplied `id`; `PATCH` rejects an `anchor`
- an unknown `anchor.kind` survives a `PATCH` to a sibling annotation
- an annotation on a derived file is not returned for the original's
  `file_sha`
- two interleaved writes to one book both land (no lost update)
- `PUT /reading` persists and is returned by `GET /books/{sha}`

**JavaScript (vitest — a deviation from A, agreed):** A ruled out a JS
test framework, correctly, when the SPA was forms over an API. This
sub-project puts real arithmetic in the browser: normalizing a
selection rectangle to 0–1 page space and back, merging selection
rects into highlight boxes, and CFI round-tripping. `anchors.ts` is
pure functions over plain data with no DOM, and it is exactly where a
silent off-by-one produces highlights that drift. vitest covers
`anchors.ts` and nothing else.

**Manual smoke checklist** against real library files, because engine
integration cannot be unit-tested honestly: a text-layer PDF, a
scanned PDF, a large (>50MB) scanned PDF, an EPUB, a converted-from-MOBI
EPUB, and a book with annotations on two different files.

## 9. Known ceilings

- `pdf-area` anchors are bound to a page number; replacing the PDF with
  a differently paginated one silently misplaces them (§3.1). OCR plus
  text anchors is the upgrade path, deliberately not taken.
- An annotation carried across by A's changed-hash sidecar rescue keeps
  a `file_sha` that matches no file, so it is listed on the book page
  but never painted in the reader. Re-pointing it would need a
  re-anchoring pass that §3.1 explains this design cannot reach.
- No cross-book annotation search. Annotations live only in sidecars,
  so finding a half-remembered highlight means knowing the book. Adding
  an `annotations` table to the index is the upgrade path.
- Highlights are not written into the PDF or EPUB file, so they are
  invisible to any other reader application. The sidecar is the record.
- A single reading position per book, not per file — a book read partly
  as PDF and partly as converted EPUB keeps one locator, last write
  wins.

## 10. Definition of done

1. `/read/:sha` opens a text-layer PDF, a scanned PDF and an EPUB from
   the live library, resuming at the stored position.
2. Dragging a box on a scanned PDF page creates a highlight that
   persists across a reload and lands in the same place at a different
   zoom level and window size.
3. Selecting text on a text-layer PDF or an EPUB creates a highlight
   that stores its quoted text.
4. A note can be attached to, edited on, and removed from any
   highlight; a bookmark can be added and jumped to.
5. The sidebar lists the active file's annotations and clicking one
   jumps to it.
6. Killing the server mid-session loses at most the last 5 seconds of
   reading position and no annotations.
7. Opening a MOBI offers "Convert to EPUB & read" and, after the job,
   reads the derived EPUB.
8. Deleting `db/books.sqlite3` and running `reshelf reindex` leaves
   every annotation intact — they were never in the index.
9. A's CLI, API and test suite still pass unchanged.
