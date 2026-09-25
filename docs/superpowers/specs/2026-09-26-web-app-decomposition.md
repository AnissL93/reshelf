# Reshelf Web App — Decomposition

Date: 2026-09-26
Status: approved (design), specs pending

## Goal

Turn `reshelf` from a CLI pipeline into a self-hosted personal library
app: browse and correct metadata, read books in the browser, take
highlights and notes, export those notes to Obsidian, run the whole
thing from one Docker container.

Calibre-like in function, much smaller in scope, and with no Calibre
dependency.

## Constraints (decided during brainstorming)

| Decision | Value |
|---|---|
| Deployment | Local, single user, Docker. No auth, no user model. |
| Frontend | FastAPI JSON API + React/Vite SPA. |
| Calibre | **Not a dependency.** No `ebook-convert`, no `calibredb` in the web app or image. Kindle/TXT conversion uses the `mobi` package; DjVu uses `ddjvu`. |
| Pipeline control | Driven from the UI via an in-process, single-slot job runner. |
| Source of truth | Per-book JSON sidecar. SQLite is a deletable, rebuildable index. |
| AI | Optional. Off unless a provider is configured. |
| Library scope | Every scanned file is browsable and readable, matched or not. |

## Library composition (measured 2026-09-26)

| Format | Files |
|---|---|
| PDF | 1129 |
| EPUB | 619 |
| MOBI | 67 |
| AZW3 | 18 |
| TXT | 5 |
| DJVU | 3 |

Total 1841 files, collapsing by content hash to fewer book records.

PDF is the dominant reading format. This drives the reader design: a
first-class pdf.js path, not an EPUB-first one.

## Sub-projects

Each gets its own spec, plan, and implementation cycle.

### A — Library web app

Prerequisite refactor (pipeline logic out of typer command bodies),
JSON sidecar store, SQLite index, FastAPI + SPA, browse/search,
manual metadata editing, per-book "Fix metadata", job runner and
pipeline UI.

Alone, this replaces the `review` CLI loop and makes the collection
usable. Largest of the three.

Spec: `2026-09-26-web-app-a-library.md`

### B — Reader and annotations

pdf.js for PDF, foliate-js for EPUB/MOBI/AZW3, plain text for TXT.
Highlights, notes, bookmarks, reading progress — all persisted into
the sidecar. Books converted in A are read via their derived EPUB.

Depends on A's file-serving and sidecar layer.

### C — Obsidian export and Docker

Markdown rendering of annotations, export to a configured vault path
and/or download, multi-stage Docker image and compose file.

Depends on B for annotation data.

## Spec amendments required

`spec.md` §3.3 "Originals must be preserved" becomes "preserved by
default; `library.commit_mode: move` is an explicit opt-in". §18
(SQLite schema) is demoted from system of record to derived index.
Both amendments land with sub-project A.
