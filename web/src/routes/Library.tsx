import { useEffect, useState } from "react";
import type { BookListItem, ListBooksParams, Stats } from "../api";
import { getStats, listBooks, listTags } from "../api";
import BookCard from "../components/BookCard";
import Filters from "../components/Filters";
import type { FormatFilter, SortKey, StatusFilter } from "../components/Filters";

const PAGE_SIZE = 60;

type FilterState = {
  status: StatusFilter;
  format: FormatFilter;
  tag: string;
};

const EMPTY_FILTERS: FilterState = { status: "", format: "", tag: "" };

export default function Library() {
  const [q, setQ] = useState("");
  const [debouncedQ, setDebouncedQ] = useState("");
  const [filters, setFilters] = useState<FilterState>(EMPTY_FILTERS);
  const [sort, setSort] = useState<SortKey>("title");
  const [page, setPage] = useState(1);

  const [items, setItems] = useState<BookListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [tags, setTags] = useState<string[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);

  // Debounce free-text search - the backend runs an FTS query per request,
  // and this is a 1,800+ book library, so firing one per keystroke is wasteful.
  useEffect(() => {
    const t = setTimeout(() => setDebouncedQ(q), 250);
    return () => clearTimeout(t);
  }, [q]);

  // Any filter change lands the user back on page 1. Otherwise switching to
  // a narrower filter can leave `page` past the end of the new result set,
  // rendering an empty grid that looks like the filter is broken.
  useEffect(() => {
    setPage(1);
  }, [debouncedQ, filters.status, filters.format, filters.tag, sort]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    const params: ListBooksParams = {
      q: debouncedQ || undefined,
      status: filters.status || undefined,
      format: filters.format || undefined,
      tag: filters.tag || undefined,
      sort,
      page,
      page_size: PAGE_SIZE,
    };
    listBooks(params)
      .then((res) => {
        if (cancelled) return;
        setItems(res.items);
        setTotal(res.total);
        setError(null);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setItems([]);
        setTotal(0);
        setError(e instanceof Error ? e.message : "failed to load books");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [debouncedQ, filters.status, filters.format, filters.tag, sort, page]);

  useEffect(() => {
    listTags()
      .then(setTags)
      .catch(() => setTags([]));
    getStats()
      .then(setStats)
      .catch(() => setStats(null));
  }, []);

  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="library">
      <Filters
        q={q}
        onQ={setQ}
        status={filters.status}
        format={filters.format}
        tag={filters.tag}
        onFilterChange={(patch) => setFilters((f) => ({ ...f, ...patch }))}
        sort={sort}
        onSort={setSort}
        tags={tags}
        stats={stats}
      />

      {error && <p className="error">{error}</p>}

      <p className="result-count muted">
        {loading ? "Loading…" : `${total} book${total === 1 ? "" : "s"}`}
      </p>

      {!loading && items.length === 0 && !error && (
        <p className="empty muted">No books match these filters.</p>
      )}

      <div className="grid">
        {items.map((book) => (
          <BookCard key={book.sha256} book={book} />
        ))}
      </div>

      {pageCount > 1 && (
        <div className="pager">
          <button disabled={page === 1} onClick={() => setPage((p) => p - 1)}>
            Previous
          </button>
          <span>
            Page {page} of {pageCount}
          </span>
          <button disabled={page >= pageCount} onClick={() => setPage((p) => p + 1)}>
            Next
          </button>
        </div>
      )}
    </div>
  );
}
