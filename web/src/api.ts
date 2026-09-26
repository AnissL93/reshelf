// Typed transport layer for the reshelf web API. Pure fetch wrappers - no
// React, no component state. Every later screen imports from here.
//
// Matches the real FastAPI routers, not the design brief's earlier guesses:
// src/reshelf/web/api/{meta,books,actions,jobs}.py and web/schemas.py.

// -- capabilities / stats / settings (meta.py) --------------------------

export type Capabilities = {
  ai: boolean;
  ai_provider: string | null;
  commit_mode: "copy" | "move";
  embeddable: string[];
  convert: Record<string, string>;
  converters_available: Record<string, boolean>;
  metadata_layout: string;
};

export type Stats = {
  total: number;
  by_status: Record<string, number>;
};

// The only settings keys the server will accept via PUT (meta.SETTABLE),
// each dotted key mapped to its value. Values come back as strings for
// anything that isn't already JSON-primitive (e.g. Path fields).
export type Settings = Record<string, string | number | boolean | null>;

// -- books (books.py / schemas.py) ---------------------------------------

export type BookListItem = {
  sha256: string;
  title: string | null;
  authors: string | null;
  series: string | null;
  pubdate: string | null;
  primary_format: string | null;
  status: string | null;
  resolver: string | null;
  confidence: number | null;
  has_cover: boolean;
};

export type BookList = {
  items: BookListItem[];
  total: number;
  page: number;
  page_size: number;
};

// reshelf.store.models.BookMetadata, as (de)serialized by pydantic.
export type BookMetadata = {
  title: string | null;
  subtitle: string | null;
  authors: string[];
  translators: string[];
  series: string | null;
  series_index: number | null;
  publisher: string | null;
  pubdate: string | null;
  language: string | null;
  isbn10: string | null;
  isbn13: string | null;
  tags: string[];
  description: string | null;
  cover: string | null;
};

export type FileEntry = {
  path: string;
  format: string;
  size: number;
  mtime: number;
  role: "original" | "converted";
  sha256: string | null;
};

export type Source = {
  resolver: "embedded" | "deterministic" | "ai" | "human";
  provider: string | null;
  provider_id: string | null;
  confidence: number;
  decided_at: string | null;
};

export type Reading = {
  locator: string | null;
  percent: number;
  updated_at: string | null;
};

// reshelf.store.models.Book, dumped with by_alias=True (schema_version ->
// "schema"). The model allows unknown extra keys, so this type does too.
export type Sidecar = {
  schema: number;
  sha256: string;
  files: FileEntry[];
  metadata: BookMetadata;
  source: Source;
  reading: Reading;
  annotations: Record<string, unknown>[];
  created_at: string;
  updated_at: string;
  [extra: string]: unknown;
};

// One row from the `matches` table joined with Database.edition_metadata.
// Both sides are loosely typed on the backend (schemas.py: list[dict]); kept
// loose here too, with the fields that are actually always present typed.
export type Candidate = {
  edition_id: number;
  score: number;
  confidence: number;
  resolver: string;
  status: string;
  evidence_json: string | null;
  id?: number;
  title?: string | null;
  isbn13?: string | null;
  isbn10?: string | null;
  publisher?: string | null;
  publication_date?: string | null;
  language?: string | null;
  authors?: string | null;
  [extra: string]: unknown;
};

export type BookDetail = {
  sha256: string;
  sidecar: Sidecar;
  status: string | null;
  paths: string[];
  candidates: Candidate[];
  has_cover: boolean;
};

export type WriteBack = {
  library_file: boolean;
  embed: boolean;
};

export type WriteBackResult = {
  sidecar: Sidecar;
  library_file: string | null;
  embedded: boolean;
  warnings: string[];
};

// PATCH /books/{sha}/metadata can 422 with this shape (see ApiError below).
// `reason` is what a screen should switch on to pick a remedy:
//   - "convert_first" - the format cannot hold metadata; offer Convert.
//   - "not_in_library" - the only copy is the untouchable original; offer Commit.
//   - "unwritable"     - the file is present but malformed (src/reshelf/writeback.py);
//                        no automatic remedy, just surface `message`.
export type MetadataErrorDetail = {
  reason: "convert_first" | "not_in_library" | "unwritable";
  message: string;
};

// -- jobs (jobs.py) -------------------------------------------------------

export type JobStatus =
  | "queued"
  | "running"
  | "done"
  | "failed"
  | "cancelled"
  | "interrupted";

export type Job = {
  id: number;
  command: string;
  args_json: string;
  status: JobStatus;
  progress: number;
  total: number | null;
  message: string | null;
  log: string | null;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
};

export const TERMINAL_JOB_STATUSES: readonly JobStatus[] = [
  "done",
  "failed",
  "cancelled",
  "interrupted",
];

export type JobArgs = Record<string, unknown>;

// A `plan` job's `message` is only the plan file's path - fetching its
// actions (to show `src -> dest` before a commit may be confirmed) needs
// this. `dest` is added server-side (jobs.py get_plan): it isn't in the
// plan file itself, which only records the source file until commit time.
export type PlanAction = {
  file: string;
  action: "import" | "quarantine" | "mark_duplicate";
  dest?: string;
  metadata_changes?: Record<string, unknown>;
  [extra: string]: unknown;
};

export type Plan = {
  plan_id: string;
  created_at: string;
  actions: PlanAction[];
};

// A `reports/commit-*.json` journal, as written by planner/committer.py's
// apply_plan and read back by jobs.py's /journals endpoints.
export type JournalAction = {
  action: string;
  src: string;
  dest: string;
  moved?: boolean;
  [extra: string]: unknown;
};

export type JournalSummary = {
  commit_id: string;
  created_at: string | null;
  actions: number;
};

export type Journal = {
  commit_id: string;
  plan_id?: string | null;
  created_at: string;
  dry_run?: boolean;
  actions: JournalAction[];
  skipped: { file: string; action: string; reason: string }[];
};

/** `commit`/`rollback` need this. The server checks args.confirmed === true
 * by identity, not truthiness, and 409s on anything else (a stringified
 * "true" included). The `confirmed: true` literal type - not `boolean` -
 * is what stops a caller from passing a string/number here at compile time. */
export type ConfirmableCommand = "commit" | "rollback";

// -- transport --------------------------------------------------------

/** Thrown for any non-2xx response. `detail` carries the FastAPI error body
 * verbatim (a string, or an object like MetadataErrorDetail) so callers can
 * branch on it instead of only seeing a flattened message. */
export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(status: number, statusText: string, detail: unknown) {
    super(statusText || `request failed with ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new ApiError(res.status, res.statusText, body.detail);
  }
  if (res.status === 204) {
    return undefined as T;
  }
  return res.json();
}

// -- meta -------------------------------------------------------------

export const getCapabilities = () => req<Capabilities>("/capabilities");
export const getStats = () => req<Stats>("/stats");
export const getSettings = () => req<Settings>("/settings");
export const putSettings = (patch: Partial<Settings>) =>
  req<Settings>("/settings", { method: "PUT", body: JSON.stringify(patch) });

// -- books --------------------------------------------------------------

export type ListBooksParams = {
  q?: string;
  status?: string;
  format?: string;
  tag?: string;
  resolver?: string;
  sort?: "title" | "authors" | "pubdate" | "updated_at" | "added";
  order?: "asc" | "desc";
  page?: number;
  page_size?: number;
};

export const listBooks = (params: ListBooksParams = {}) => {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== "") qs.set(k, String(v));
  }
  const query = qs.toString();
  return req<BookList>(`/books${query ? `?${query}` : ""}`);
};

export const getBook = (sha256: string) => req<BookDetail>(`/books/${sha256}`);
export const getCandidates = (sha256: string) =>
  req<Candidate[]>(`/books/${sha256}/candidates`);
export const listTags = () => req<string[]>("/tags");

export const patchMetadata = (
  sha256: string,
  metadata: BookMetadata,
  write_back: WriteBack = { library_file: false, embed: false },
) =>
  req<WriteBackResult>(`/books/${sha256}/metadata`, {
    method: "PATCH",
    body: JSON.stringify({ metadata, write_back }),
  });

// -- actions (actions.py) ------------------------------------------------

export const convertBook = (sha256: string) =>
  req<{ job_id: number }>(`/books/${sha256}/convert`, { method: "POST" });

export const rematchBook = (
  sha256: string,
  body: { query?: Record<string, string>; ai?: boolean } = {},
) =>
  req<{ job_id: number }>(`/books/${sha256}/rematch`, {
    method: "POST",
    body: JSON.stringify(body),
  });

export const chooseCandidate = (sha256: string, candidate_id: number) =>
  req<BookDetail>(`/books/${sha256}/choose`, {
    method: "POST",
    body: JSON.stringify({ candidate_id }),
  });

// -- jobs -----------------------------------------------------------------

export const listJobs = (limit = 50) => req<Job[]>(`/jobs?limit=${limit}`);
export const getJob = (id: number) => req<Job>(`/jobs/${id}`);

// For `commit`/`rollback` use createConfirmedJob instead - this signature
// accepts any args, including a string/truthy `confirmed` that would only
// fail late, at the server's 409.
export const createJob = (command: string, args: JobArgs = {}) =>
  req<{ job_id: number }>("/jobs", {
    method: "POST",
    body: JSON.stringify({ command, args }),
  });

/** For `commit`/`rollback` only - see ConfirmableCommand above. The
 * `confirmed: true` literal in the parameter type rejects a stringy/truthy
 * substitute at compile time, before it can reach the server's 409. */
export const createConfirmedJob = (
  command: ConfirmableCommand,
  args: JobArgs & { confirmed: true },
) => createJob(command, args);

export const cancelJob = (id: number) =>
  req<{ cancelled: boolean }>(`/jobs/${id}`, { method: "DELETE" });

export const getPlan = (planId: string) => req<Plan>(`/plans/${encodeURIComponent(planId)}`);
export const listJournals = () => req<JournalSummary[]>("/journals");
export const getJournal = (commitId: string) =>
  req<Journal>(`/journals/${encodeURIComponent(commitId)}`);

/** Live job progress over SSE. Returns an unsubscribe function. The server
 * closes/keeps sending snapshots until a terminal status is reached. */
export function subscribeJob(id: number, onEvent: (job: Job) => void): () => void {
  const source = new EventSource(`/api/jobs/${id}/events`);
  source.onmessage = (e) => {
    const job: Job = JSON.parse(e.data);
    onEvent(job);
    if (TERMINAL_JOB_STATUSES.includes(job.status)) {
      source.close();
    }
  };
  source.onerror = () => source.close();
  return () => source.close();
}

// -- file / cover URLs (books.py; plain <img>/<a> src, not fetched via req) --

export const fileUrl = (sha256: string, original = false) =>
  `/api/books/${sha256}/file${original ? "?original=true" : ""}`;

export const coverUrl = (sha256: string, size: "thumb" | "full" = "full") =>
  `/api/books/${sha256}/cover?size=${size}`;
