import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import type { BookDetail as BookDetailData, BookMetadata, WriteBack } from "../api";
import {
  ApiError,
  chooseCandidate,
  convertBook,
  coverUrl,
  fileUrl,
  getBook,
  patchMetadata,
  rematchBook,
} from "../api";
import CandidateList from "../components/CandidateList";
import MetadataForm from "../components/MetadataForm";
import type { SaveOutcome } from "../components/MetadataForm";
import useCapabilities from "../hooks/useCapabilities";
import useJob from "../hooks/useJob";

const STATUS_CLASS: Record<string, string> = {
  MATCHED: "ok",
  COMMITTED: "ok",
  REVIEW: "warn",
  UNRESOLVED: "bad",
  DUPLICATE: "muted",
  ERROR: "bad",
};

// The metadata PATCH's only 422s all live inside the write-back "embed"
// branch (see patch_metadata in books.py), which runs after the sidecar
// mutate+sync - so any 422 shaped like this means tier 1 already saved.
// Deliberately not restricted to the three reasons this UI has a specific
// remedy for: a reason the backend adds later should still show its
// `message` via the generic branch in MetadataForm, not vanish.
function isSidecarSavedError(detail: unknown): detail is { reason: string; message: string } {
  if (typeof detail !== "object" || detail === null || Array.isArray(detail)) return false;
  const reason = (detail as Record<string, unknown>).reason;
  const message = (detail as Record<string, unknown>).message;
  return typeof reason === "string" && typeof message === "string";
}

type RematchQuery = { title: string; author: string; isbn: string };
const EMPTY_QUERY: RematchQuery = { title: "", author: "", isbn: "" };

function nonEmptyQuery(q: RematchQuery): Record<string, string> | undefined {
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(q)) {
    if (v.trim()) out[k] = v.trim();
  }
  return Object.keys(out).length ? out : undefined;
}

export default function BookDetail() {
  const { sha = "" } = useParams<{ sha: string }>();
  const caps = useCapabilities();

  const [book, setBook] = useState<BookDetailData | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const [query, setQuery] = useState<RematchQuery>(EMPTY_QUERY);
  const [rematchJobId, setRematchJobId] = useState<number | null>(null);
  const [convertJobId, setConvertJobId] = useState<number | null>(null);

  const { job: rematchJob, running: rematchRunning } = useJob(rematchJobId);
  const { job: convertJob, running: convertRunning } = useJob(convertJobId);

  const load = useCallback(() => {
    if (!sha) return;
    setLoading(true);
    getBook(sha)
      .then((b) => {
        setBook(b);
        setLoadError(null);
      })
      .catch((e: unknown) => {
        setLoadError(e instanceof Error ? e.message : "failed to load book");
      })
      .finally(() => setLoading(false));
  }, [sha]);

  useEffect(() => {
    load();
  }, [load]);

  // Refetch once a rematch or convert job finishes - new candidates, a new
  // derived/ file, or updated metadata otherwise wouldn't show up without a
  // manual reload.
  useEffect(() => {
    if (rematchJob && rematchJob.status === "done") load();
  }, [rematchJob, load]);
  useEffect(() => {
    if (convertJob && convertJob.status === "done") load();
  }, [convertJob, load]);

  const handleSave = useCallback(
    async (metadata: BookMetadata, writeBack: WriteBack): Promise<SaveOutcome> => {
      try {
        const result = await patchMetadata(sha, metadata, writeBack);
        setBook((b) => (b ? { ...b, sidecar: result.sidecar } : b));
        return { ok: true, warnings: result.warnings };
      } catch (e) {
        if (e instanceof ApiError && e.status === 422 && isSidecarSavedError(e.detail)) {
          // Tier 1 (the sidecar) already saved server-side before this tier
          // was attempted - refetch so the header/candidates reflect it.
          load();
          return {
            ok: false,
            sidecarSaved: true,
            reason: e.detail.reason,
            message: e.detail.message,
          };
        }
        // A network failure, a 404/500, or any other shape - nothing is
        // known to have persisted. Never rethrow: MetadataForm already
        // cleared its previous error state before calling this, so an
        // unhandled rejection here would leave the form looking exactly
        // like a successful save.
        return {
          ok: false,
          sidecarSaved: false,
          message: e instanceof Error ? e.message : "failed to save",
        };
      }
    },
    [sha, load],
  );

  const handleConvert = useCallback(() => {
    setActionError(null);
    convertBook(sha)
      .then((r) => setConvertJobId(r.job_id))
      .catch((e: unknown) =>
        setActionError(e instanceof Error ? e.message : "failed to start convert"),
      );
  }, [sha]);

  const startRematch = useCallback(
    (ai: boolean) => {
      setActionError(null);
      rematchBook(sha, { query: nonEmptyQuery(query), ai })
        .then((r) => setRematchJobId(r.job_id))
        .catch((e: unknown) =>
          setActionError(e instanceof Error ? e.message : "failed to start search"),
        );
    },
    [sha, query],
  );

  const handleChoose = useCallback(
    (editionId: number) => {
      setActionError(null);
      chooseCandidate(sha, editionId)
        .then(setBook)
        .catch((e: unknown) =>
          setActionError(e instanceof Error ? e.message : "failed to use candidate"),
        );
    },
    [sha],
  );

  if (loading && !book) return <p className="muted">Loading…</p>;
  if (loadError && !book) return <p className="error">{loadError}</p>;
  if (!book) return null;

  const sidecar = book.sidecar;
  const meta = sidecar.metadata;
  // Ranked server-side (books.get_book): only the backend knows the
  // library root, and only with it can a library copy be told from an
  // original under incoming/.
  const format = book.primary_format;
  const convertTarget = caps?.convert[format ?? ""];
  const converterUnavailable = format !== null && caps?.converters_available[format] === false;

  return (
    <div className="book-detail">
      <div className="book-cover-col">
        {book.has_cover ? (
          <img className="book-cover" src={coverUrl(sha)} alt="" />
        ) : (
          <div className="book-cover placeholder" aria-hidden="true">
            <span>{(format ?? "?").toUpperCase()}</span>
          </div>
        )}
      </div>

      <div className="book-main">
        <h1>{meta.title || `Untitled (${sha.slice(0, 10)})`}</h1>
        {meta.subtitle && <p className="muted">{meta.subtitle}</p>}
        <p>
          {book.status && (
            <span className={`badge ${STATUS_CLASS[book.status] ?? "muted"}`}>{book.status}</span>
          )}{" "}
          <span className="muted">
            resolver: {sidecar.source.resolver} · confidence {sidecar.source.confidence.toFixed(2)}
          </span>
        </p>

        <ul className="file-list">
          {sidecar.files.map((f) => (
            <li key={f.path}>
              <a href={fileUrl(sha, f.path)}>{f.path}</a>{" "}
              <span className="muted">
                ({f.role}, {f.format})
              </span>
            </li>
          ))}
        </ul>

        {actionError && <p className="error">{actionError}</p>}

        <MetadataForm
          metadata={meta}
          capabilities={caps}
          primaryFormat={format}
          onSave={handleSave}
          onConvert={handleConvert}
          convertRunning={convertRunning}
        />

        <section className="fix-metadata">
          <h3>Fix metadata</h3>
          <input
            placeholder="title"
            value={query.title}
            onChange={(e) => setQuery({ ...query, title: e.target.value })}
          />
          <input
            placeholder="author"
            value={query.author}
            onChange={(e) => setQuery({ ...query, author: e.target.value })}
          />
          <input
            placeholder="isbn"
            value={query.isbn}
            onChange={(e) => setQuery({ ...query, isbn: e.target.value })}
          />
          <button disabled={rematchRunning} onClick={() => startRematch(false)}>
            Search providers
          </button>
          <button
            disabled={rematchRunning || !caps?.ai}
            title={caps?.ai ? "" : "Configure an AI provider in Settings"}
            onClick={() => startRematch(true)}
          >
            Search + AI judge
          </button>
          {rematchRunning && <progress value={rematchJob?.progress ?? 0} max={rematchJob?.total ?? 1} />}
          {rematchJob?.status === "failed" && <p className="error">{rematchJob.error}</p>}
        </section>

        {format && convertTarget !== undefined && (
          <section className="convert-section">
            <h3>Convert</h3>
            <button
              disabled={convertRunning || converterUnavailable}
              title={
                converterUnavailable
                  ? `${format.toUpperCase()} converter is not installed on this machine.`
                  : ""
              }
              onClick={handleConvert}
            >
              Convert to {convertTarget.toUpperCase()}
            </button>
            {convertRunning && (
              <progress value={convertJob?.progress ?? 0} max={convertJob?.total ?? 1} />
            )}
            {convertJob?.status === "failed" && <p className="error">{convertJob.error}</p>}
          </section>
        )}

        <CandidateList candidates={book.candidates} onChoose={handleChoose} />
      </div>
    </div>
  );
}
