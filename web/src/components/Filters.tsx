import type { ChangeEvent } from "react";
import type { ListBooksParams, Stats } from "../api";

export type SortKey = NonNullable<ListBooksParams["sort"]>;
export type StatusFilter =
  | ""
  | "UNRESOLVED"
  | "REVIEW"
  | "MATCHED"
  | "COMMITTED"
  | "DUPLICATE"
  | "ERROR";
export type FormatFilter = "" | "epub" | "pdf" | "mobi" | "azw3" | "txt" | "djvu";

export type FilterPatch = Partial<{
  status: StatusFilter;
  format: FormatFilter;
  tag: string;
}>;

const STATUSES: StatusFilter[] = [
  "UNRESOLVED",
  "REVIEW",
  "MATCHED",
  "COMMITTED",
  "DUPLICATE",
  "ERROR",
];

const FORMATS: FormatFilter[] = ["pdf", "epub", "mobi", "azw3", "txt", "djvu"];

const SORTS: { value: SortKey; label: string }[] = [
  { value: "title", label: "Title" },
  { value: "authors", label: "Author" },
  { value: "pubdate", label: "Publication date" },
  { value: "updated_at", label: "Last updated" },
  { value: "added", label: "Date added" },
];

type FiltersProps = {
  q: string;
  onQ: (q: string) => void;
  status: StatusFilter;
  format: FormatFilter;
  tag: string;
  onFilterChange: (patch: FilterPatch) => void;
  sort: SortKey;
  onSort: (sort: SortKey) => void;
  tags: string[];
  stats: Stats | null;
};

export default function Filters({
  q,
  onQ,
  status,
  format,
  tag,
  onFilterChange,
  sort,
  onSort,
  tags,
  stats,
}: FiltersProps) {
  return (
    <div className="filters">
      <input
        type="search"
        className="search"
        placeholder="Search title, author, series…"
        value={q}
        onChange={(e: ChangeEvent<HTMLInputElement>) => onQ(e.target.value)}
        aria-label="Search books"
      />

      {stats && (
        <div className="status-chips" role="group" aria-label="Filter by status">
          <button
            type="button"
            className={status === "" ? "chip active" : "chip"}
            onClick={() => onFilterChange({ status: "" })}
          >
            All <span className="chip-count">{stats.total}</span>
          </button>
          {STATUSES.filter((s) => (stats.by_status[s] ?? 0) > 0).map((s) => (
            <button
              key={s}
              type="button"
              className={status === s ? "chip active" : "chip"}
              onClick={() => onFilterChange({ status: s })}
            >
              {s} <span className="chip-count">{stats.by_status[s]}</span>
            </button>
          ))}
        </div>
      )}

      <div className="filter-row">
        <select
          value={format}
          onChange={(e) => onFilterChange({ format: e.target.value as FormatFilter })}
          aria-label="Filter by format"
        >
          <option value="">All formats</option>
          {FORMATS.map((f) => (
            <option key={f} value={f}>
              {f.toUpperCase()}
            </option>
          ))}
        </select>

        <select
          value={tag}
          onChange={(e) => onFilterChange({ tag: e.target.value })}
          aria-label="Filter by tag"
        >
          <option value="">All tags</option>
          {tags.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>

        <select
          value={sort}
          onChange={(e) => onSort(e.target.value as SortKey)}
          aria-label="Sort by"
        >
          {SORTS.map((s) => (
            <option key={s.value} value={s.value}>
              {s.label}
            </option>
          ))}
        </select>
      </div>
    </div>
  );
}
