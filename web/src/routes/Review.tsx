import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import type { BookDetail as BookDetailData, BookListItem } from "../api";
import { chooseCandidate, getBook, listBooks } from "../api";
import CandidateList from "../components/CandidateList";

// The review CLI loop's whole backlog fits comfortably in one page - no
// pager needed for a queue meant to be worked through, not browsed. 200 is
// the backend's hard cap (list_books: page_size le=200); today's 83-book
// REVIEW backlog fits well inside it.
const QUEUE_PAGE_SIZE = 200;

const LEGEND: { key: string; action: string }[] = [
  { key: "1–9", action: "choose that candidate, then advance" },
  { key: "j / ↓", action: "next book, no decision" },
  { key: "k / ↑", action: "previous book" },
  { key: "s", action: "skip (advance without deciding)" },
  { key: "o", action: "open full detail page in a new tab" },
  { key: "?", action: "toggle this legend" },
];

export default function Review() {
  const [queue, setQueue] = useState<BookListItem[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [cursor, setCursor] = useState(0);
  const [showLegend, setShowLegend] = useState(true);

  const [book, setBook] = useState<BookDetailData | null>(null);
  const [bookLoading, setBookLoading] = useState(false);
  const [bookError, setBookError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  // Load the REVIEW queue once - the brief is explicit that this screen is
  // optimised for churning through a fixed backlog, not for a live view of
  // it, so no polling/refetch here.
  useEffect(() => {
    listBooks({ status: "REVIEW", sort: "title", page_size: QUEUE_PAGE_SIZE })
      .then((res) => setQueue(res.items))
      .catch((e: unknown) =>
        setLoadError(e instanceof Error ? e.message : "failed to load review queue"),
      );
  }, []);

  const current = queue && cursor >= 0 && cursor < queue.length ? queue[cursor] : null;

  // Fetch the current book's full detail (candidates, sidecar) whenever the
  // cursor lands on a new sha. Race-guarded the same way Library.tsx guards
  // its list fetch: a slow response for a book we've since navigated past
  // must never clobber what's on screen.
  useEffect(() => {
    if (!current) {
      setBook(null);
      return;
    }
    let cancelled = false;
    setBookLoading(true);
    setBookError(null);
    getBook(current.sha256)
      .then((b) => {
        if (cancelled) return;
        setBook(b);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setBookError(e instanceof Error ? e.message : "failed to load book");
      })
      .finally(() => {
        if (!cancelled) setBookLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // current is re-derived from queue/cursor every render but is
    // referentially the same array element until the cursor moves, so
    // keying on its sha is enough and avoids re-running on unrelated
    // re-renders.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [current?.sha256]);

  const advance = useCallback(() => {
    setActionError(null);
    setCursor((c) => c + 1);
  }, []);

  const retreat = useCallback(() => {
    setActionError(null);
    setCursor((c) => Math.max(0, c - 1));
  }, []);

  const handleChoose = useCallback(
    (editionId: number) => {
      if (!current) return;
      setActionError(null);
      chooseCandidate(current.sha256, editionId)
        .then(() => advance())
        .catch((e: unknown) => {
          // Do NOT advance: a failed choose that still moved the cursor
          // would silently skip this book, and the next one would take its
          // place as if nothing went wrong.
          setActionError(
            `Failed to use that candidate - book not advanced. ${
              e instanceof Error ? e.message : "unknown error"
            }`,
          );
        });
    },
    [current, advance],
  );

  // Bound on window, cleaned up on unmount so it never fires on another
  // route once this one is left.
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      const tag = document.activeElement?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA") return;

      if (e.key === "?") {
        setShowLegend((v) => !v);
        return;
      }
      if (!current) return;

      if (e.key >= "1" && e.key <= "9") {
        const candidate = book?.candidates[Number(e.key) - 1];
        if (candidate) handleChoose(candidate.edition_id);
        return;
      }

      switch (e.key) {
        case "j":
        case "ArrowDown":
          advance();
          break;
        case "k":
        case "ArrowUp":
          retreat();
          break;
        case "s":
          advance();
          break;
        case "o":
          window.open(`/book/${current.sha256}`, "_blank", "noopener");
          break;
        default:
          break;
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [current, book, handleChoose, advance, retreat]);

  if (loadError) return <p className="error">{loadError}</p>;
  if (!queue) return <p className="muted">Loading…</p>;

  const exhausted = queue.length === 0 || cursor >= queue.length;
  const remaining = Math.max(0, queue.length - cursor);

  return (
    <div className="review">
      <header className="review-header">
        <h1>Review queue</h1>
        {!exhausted && (
          <span className="review-counter">
            {remaining} of {queue.length}
          </span>
        )}
        <button type="button" onClick={() => setShowLegend((v) => !v)}>
          {showLegend ? "Hide" : "Show"} shortcuts
        </button>
      </header>

      {showLegend && (
        <ul className="review-legend muted">
          {LEGEND.map((l) => (
            <li key={l.key}>
              <kbd>{l.key}</kbd> {l.action}
            </li>
          ))}
        </ul>
      )}

      {exhausted ? (
        <p className="empty muted">
          Queue empty. <Link to="/">Back to library</Link>
        </p>
      ) : (
        <>
          {actionError && <p className="error">{actionError}</p>}
          {bookError && <p className="error">{bookError}</p>}

          {bookLoading && !book && <p className="muted">Loading…</p>}

          {book && current && (
            <div className="review-book">
              <h2>{book.sidecar.metadata.title || `Untitled (${current.sha256.slice(0, 10)})`}</h2>
              <p className="muted">
                {book.sidecar.metadata.authors.join(", ") || "unknown author"} · resolver:{" "}
                {book.sidecar.source.resolver} · confidence{" "}
                {book.sidecar.source.confidence.toFixed(2)}
              </p>
              <CandidateList candidates={book.candidates} onChoose={handleChoose} showIndex />
            </div>
          )}
        </>
      )}
    </div>
  );
}
