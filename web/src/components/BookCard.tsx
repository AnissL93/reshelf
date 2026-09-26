import { Link } from "react-router-dom";
import type { BookListItem } from "../api";
import { coverUrl } from "../api";

// Status coloring only; unknown/future statuses fall back to "muted" rather
// than an undefined class name.
const STATUS_CLASS: Record<string, string> = {
  MATCHED: "ok",
  COMMITTED: "ok",
  REVIEW: "warn",
  UNRESOLVED: "bad",
  DUPLICATE: "muted",
  ERROR: "bad",
};

export default function BookCard({ book }: { book: BookListItem }) {
  // The list API has no filename - sha256 is the only thing every book has,
  // so an untitled book (1,574 of them) still shows something identifying
  // instead of a blank line. `||`, not `??`: an unresolved book's `authors`
  // comes back as "" (book_index joins an empty author list), not null.
  const title = book.title || "";
  const authors = book.authors || "";
  const heading = title || `Untitled (${book.sha256.slice(0, 10)})`;

  return (
    <Link className="card" to={`/book/${book.sha256}`}>
      <div className="card-cover">
        {book.has_cover ? (
          <img src={coverUrl(book.sha256, "thumb")} alt="" loading="lazy" />
        ) : (
          <div className="card-placeholder" aria-hidden="true">
            <span>{(book.primary_format ?? "?").toUpperCase()}</span>
          </div>
        )}
      </div>
      <div className="card-body">
        <div className={title ? "card-title" : "card-title placeholder"}>
          {heading}
        </div>
        <div className="card-authors">{authors || "Unknown author"}</div>
        {book.status && (
          <span className={`badge ${STATUS_CLASS[book.status] ?? "muted"}`}>
            {book.status}
          </span>
        )}
      </div>
    </Link>
  );
}
