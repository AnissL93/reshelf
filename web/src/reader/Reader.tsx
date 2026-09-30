// The reader shell. Owns the toolbar, the annotation sidebar and
// persistence; knows nothing about pdf.js or foliate-js beyond picking
// which one to construct.
import { useCallback, useEffect, useRef, useState } from "react";
import { useParams, useSearchParams, Link } from "react-router-dom";
import { beaconReading, fileUrl, getBook, putReading } from "../api";
import type { Annotation, AnnotationColor, BookDetail, ReadableFile } from "../api";
import { COLORS, SWATCH } from "./colors";
import ConvertToRead from "./ConvertToRead";
import Sidebar from "./Sidebar";
import useAnnotations from "./useAnnotations";
import { EpubEngine } from "./engines/epub";
import { PdfEngine } from "./engines/pdf";
import type { Anchor, Engine } from "./engines/types";

/** Progress is written at most this often. Not an optimization: an
 * unthrottled writer rewrites a multi-kilobyte sidecar on every scroll
 * tick. */
const PROGRESS_MS = 5000;
/** Quiet time after the last selectionchange before a highlight is made. */
const SELECT_MS = 500;

function pick(book: BookDetail, want: string | null): ReadableFile | null {
  if (want) return book.readable.find((f) => f.file_sha === want) ?? null;
  // Prefer a file we can actually render, then fall back to the first so
  // the convert-first card has something to describe.
  return book.readable.find((f) => f.engine) ?? book.readable[0] ?? null;
}

export default function Reader() {
  const { sha = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const [book, setBook] = useState<BookDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [color, setColor] = useState<AnnotationColor>("yellow");
  const [areaMode, setAreaMode] = useState(false);
  // One control, two meanings: a PDF zooms, reflowable EPUB text resizes.
  const [zoom, setZoom] = useState(1.4);
  const [fontSize, setFontSize] = useState(16);

  const hostRef = useRef<HTMLDivElement>(null);
  const engineRef = useRef<Engine | null>(null);
  const lastWrite = useRef(0);
  // Latest values for code that runs after an await or in a timer, where a
  // closed-over copy would be stale.
  const annRef = useRef<Annotation[]>([]);
  const areaRef = useRef(false);
  const zoomRef = useRef(zoom);
  const sizeRef = useRef(fontSize);

  const stored = book?.sidecar.reading?.locator ?? null;
  const file = book ? pick(book, params.get("file_sha")) : null;
  const fileEngine = file?.engine ?? null;
  const filePath = file?.path ?? null;
  const fileFileSha = file?.file_sha ?? null;
  const {
    annotations, create, update, remove, error: annError, unsaved, retry,
  } = useAnnotations(sha, file?.file_sha ?? null);

  const reload = useCallback(
    () => getBook(sha).then(setBook).catch((e: Error) => setError(e.message)),
    [sha],
  );
  useEffect(() => { void reload(); }, [reload]);

  // Declared BEFORE the mount effect on purpose: cleanups run in
  // declaration order, and the unmount write needs engineRef still set.
  // -- reading progress -------------------------------------------------
  useEffect(() => {
    if (!fileEngine) return;
    const tick = () => {
      const engine = engineRef.current;
      if (!engine) return;
      const loc = engine.locate();
      if (!loc.locator) return; // not mounted yet: never overwrite with blank
      const now = Date.now();
      if (now - lastWrite.current < PROGRESS_MS) return;
      lastWrite.current = now;
      void putReading(sha, loc).catch(() => {
        // Progress is not worth an error banner; the next tick retries.
      });
    };
    const id = window.setInterval(tick, PROGRESS_MS);
    const onLeave = () => {
      const engine = engineRef.current;
      // fetch() is cancelled on unload; sendBeacon is the only delivery
      // the browser guarantees.
      const loc = engine?.locate();
      if (loc?.locator) beaconReading(sha, loc);
    };
    window.addEventListener("pagehide", onLeave);
    return () => {
      window.clearInterval(id);
      window.removeEventListener("pagehide", onLeave);
      onLeave();
    };
  }, [sha, fileEngine]);

  // -- mount the engine -------------------------------------------------
  useEffect(() => {
    const host = hostRef.current;
    if (!host || !fileEngine || !filePath) return;
    const engine: Engine =
      fileEngine === "pdf" ? new PdfEngine(zoomRef.current) : new EpubEngine();
    if (engine instanceof EpubEngine) engine.setFontSize(sizeRef.current);
    engineRef.current = engine;
    let cancelled = false;

    engine
      .mount(host, fileUrl(sha, filePath))
      .then(async () => {
        if (cancelled) return;
        // After mount: the EPUB engine always opens at the start of the
        // text, so the saved position can only be applied once it resolves.
        if (stored) {
          await engine.goTo(
            fileEngine === "pdf"
              ? { kind: "pdf-page", page: Number(stored.replace("page=", "")) || 1 }
              : { kind: "epub", cfi: stored },
          );
        }
        if (cancelled) return;
        if (engine instanceof PdfEngine) engine.setAreaMode(areaRef.current);
        // Annotations may have loaded before the engine was ready.
        engine.paint(annRef.current);
      })
      .catch((e: Error) => {
        // A superseded mount (StrictMode, file switch) may reject; only a
        // live engine's failure is worth an error card.
        if (!cancelled) setError(e.message);
      });

    return () => {
      cancelled = true;
      engine.destroy();
      engineRef.current = null;
    };
    // Deliberately narrow: this effect loads the file, so it must re-run
    // only when the file changes. `create` and `color` are rebound by the
    // effect below instead - listing them here would re-download the book
    // every time the user picked a different highlight colour.
  }, [sha, fileFileSha, filePath, fileEngine, stored]);

  // Both engines fire onTextSelect from selectionchange, continuously while
  // the user drags. Wait for the selection to settle, then create once.
  // Rebound (not remounted) when the colour changes; declared after the
  // mount effect so the engine exists when this first runs.
  useEffect(() => {
    const engine = engineRef.current;
    if (!engine) return;
    let timer: number | undefined;
    engine.onTextSelect((anchor) => {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => create(anchor, "highlight", color), SELECT_MS);
    });
    engine.onAreaSelect((anchor) => create(anchor, "highlight", color));
    return () => window.clearTimeout(timer);
  }, [color, create, sha, fileFileSha, filePath, fileEngine, stored]);

  useEffect(() => {
    annRef.current = annotations;
    engineRef.current?.paint(annotations);
  }, [annotations]);

  useEffect(() => {
    areaRef.current = areaMode;
    if (engineRef.current instanceof PdfEngine) {
      engineRef.current.setAreaMode(areaMode);
    }
  }, [areaMode]);

  useEffect(() => {
    zoomRef.current = zoom;
    sizeRef.current = fontSize;
    const engine = engineRef.current;
    if (engine instanceof PdfEngine) {
      // Re-renders every page, so a change only: the constructor already
      // took the initial zoom. Repaint after, setScale drops the overlays.
      void engine.setScale(zoom).then(() => engine.paint(annRef.current)).catch(() => {});
    } else if (engine instanceof EpubEngine) {
      engine.setFontSize(fontSize);
    }
    // Deliberately excludes `annotations`: the effect above already
    // repaints when they change, and re-running this one would re-render
    // every page of the PDF each time a note was edited.
  }, [zoom, fontSize]);

  if (error) {
    return (
      <div className="reader-notice error">
        <p>{error}</p>
        <a href={fileUrl(sha, file?.path)}>Download instead</a>
      </div>
    );
  }
  if (!book || !file) return <p>Loading…</p>;

  const title = book.sidecar.metadata.title ?? sha.slice(0, 12);

  return (
    <div className="reader">
      <header className="reader-toolbar">
        <Link to={`/book/${sha}`}>&larr; {title}</Link>

        {book.readable.length > 1 && (
          <select
            value={file.file_sha}
            onChange={(e) => setParams({ file_sha: e.target.value })}
          >
            {book.readable.map((f) => (
              <option key={f.file_sha} value={f.file_sha}>
                {f.format.toUpperCase()} ({f.role})
              </option>
            ))}
          </select>
        )}

        {file.engine && (
          <>
            {COLORS.map((c) => (
              <button
                key={c}
                aria-label={`Highlight ${c}`}
                className={`swatch-button${color === c ? " on" : ""}`}
                style={{ background: SWATCH[c] }}
                onClick={() => setColor(c)}
              />
            ))}
            {/* Derived from the format, not from engineRef: the ref is
                null on the first render, so reading supportsArea off it
                would hide the tool until something else re-rendered. */}
            {file.engine === "pdf" && (
              <button
                className={areaMode ? "on" : ""}
                onClick={() => setAreaMode((v) => !v)}
              >
                Area
              </button>
            )}
            {file.engine === "pdf" ? (
              <span className="sizing">
                <button onClick={() => setZoom((z) => Math.max(0.5, z - 0.2))}>
                  &minus;
                </button>
                <span>{Math.round(zoom * 100)}%</span>
                <button onClick={() => setZoom((z) => Math.min(4, z + 0.2))}>
                  +
                </button>
              </span>
            ) : (
              <span className="sizing">
                <button onClick={() => setFontSize((f) => Math.max(10, f - 2))}>
                  A&minus;
                </button>
                <span>{fontSize}px</span>
                <button onClick={() => setFontSize((f) => Math.min(32, f + 2))}>
                  A+
                </button>
              </span>
            )}

            <button
              onClick={() => {
                const engine = engineRef.current;
                if (!engine) return;
                const loc = engine.locate();
                create(
                  file.engine === "pdf"
                    ? { kind: "pdf-page", page: Number(loc.locator.replace("page=", "")) || 1 }
                    : { kind: "epub", cfi: loc.locator },
                  "bookmark",
                  color,
                );
              }}
            >
              Bookmark
            </button>
          </>
        )}
      </header>

      <div className="reader-body">
        {file.engine ? (
          <div className="reader-host" ref={hostRef} />
        ) : (
          <ConvertToRead sha={sha} file={file} onDone={reload} />
        )}
        <Sidebar
          annotations={annotations}
          onJump={(a) => void engineRef.current?.goTo(a.anchor as Anchor)}
          onUpdate={update}
          onRemove={remove}
          unsaved={unsaved}
          error={annError}
          onRetry={retry}
        />
      </div>
    </div>
  );
}
