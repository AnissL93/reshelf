import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import type { BookMetadata, Capabilities, WriteBack } from "../api";

// What the route's save handler reports back, so this component can render
// the right thing instead of a raw error - or, worse, nothing. Two distinct
// failure shapes, not one, because they mean opposite things to the user:
//   - `sidecarSaved: true`  - the PATCH 422'd, but only after tier 1 (the
//     sidecar) already wrote server-side; the edit is safe, the optional
//     file-level step just didn't run. `reason` is a free string (not
//     restricted to the three this UI has a specific remedy for) so a
//     reason the backend adds later still shows its `message` instead of
//     disappearing.
//   - `sidecarSaved: false` - a network failure, a 404/500, or anything
//     else where nothing is known to have persisted. Must never be framed
//     as "saved".
// `onSave` (see BookDetail.handleSave) is written to always resolve to one
// of these, never reject - see the `catch` in handleSubmit below for the
// belt-and-braces backstop if it ever does anyway.
export type SaveOutcome =
  | { ok: true; warnings: string[] }
  | { ok: false; sidecarSaved: true; reason: string; message: string }
  | { ok: false; sidecarSaved: false; message: string };

type MetadataFormProps = {
  metadata: BookMetadata;
  capabilities: Capabilities | null;
  primaryFormat: string | null;
  onSave: (metadata: BookMetadata, writeBack: WriteBack) => Promise<SaveOutcome>;
  onConvert: () => void;
  convertRunning: boolean;
};

type FormState = {
  title: string;
  subtitle: string;
  authors: string;
  series: string;
  series_index: string;
  publisher: string;
  pubdate: string;
  language: string;
  isbn13: string;
  tags: string;
  description: string;
};

function toForm(m: BookMetadata): FormState {
  return {
    title: m.title ?? "",
    subtitle: m.subtitle ?? "",
    authors: m.authors.join(", "),
    series: m.series ?? "",
    series_index: m.series_index === null ? "" : String(m.series_index),
    publisher: m.publisher ?? "",
    pubdate: m.pubdate ?? "",
    language: m.language ?? "",
    isbn13: m.isbn13 ?? "",
    tags: m.tags.join(", "),
    description: m.description ?? "",
  };
}

const strOrNull = (s: string): string | null => (s.trim() ? s.trim() : null);
const listOf = (s: string): string[] =>
  s
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean);
const numOrNull = (s: string): number | null => {
  const n = Number.parseFloat(s);
  return Number.isFinite(n) ? n : null;
};

// `base` carries fields this form never edits (isbn10, translators, cover) -
// spreading it first keeps them intact across a save.
function toMetadata(base: BookMetadata, f: FormState): BookMetadata {
  return {
    ...base,
    title: strOrNull(f.title),
    subtitle: strOrNull(f.subtitle),
    authors: listOf(f.authors),
    series: strOrNull(f.series),
    series_index: numOrNull(f.series_index),
    publisher: strOrNull(f.publisher),
    pubdate: strOrNull(f.pubdate),
    language: strOrNull(f.language),
    isbn13: strOrNull(f.isbn13),
    tags: listOf(f.tags),
    description: strOrNull(f.description),
  };
}

export default function MetadataForm({
  metadata,
  capabilities,
  primaryFormat,
  onSave,
  onConvert,
  convertRunning,
}: MetadataFormProps) {
  const [form, setForm] = useState<FormState>(() => toForm(metadata));
  // Re-sync whenever the route hands us fresh metadata (after a save, a
  // chosen candidate, or a finished rematch/convert job) - never on every
  // keystroke, since `form` itself is the keystroke state.
  useEffect(() => setForm(toForm(metadata)), [metadata]);

  const [writeBack, setWriteBack] = useState<WriteBack>({
    library_file: false,
    embed: false,
  });
  const [saving, setSaving] = useState(false);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [saveError, setSaveError] = useState<
    | { sidecarSaved: true; reason: string; message: string }
    | { sidecarSaved: false; message: string }
    | null
  >(null);

  // Capabilities loading is a third state, not a fallback to "not
  // embeddable" - asserting a reason before we actually know one flashes a
  // false "cannot hold metadata" on every format, including EPUB, on every
  // page load.
  const capsLoaded = capabilities !== null;
  const embeddable = capabilities?.embeddable ?? [];
  const targetFormat = capabilities?.convert[primaryFormat ?? ""];
  const convertible = capsLoaded ? Boolean(targetFormat) : false;
  const notEmbeddable = capsLoaded && !embeddable.includes(primaryFormat ?? "");
  const embedDisabledReason = notEmbeddable
    ? `${primaryFormat ?? "This format"} cannot hold metadata; convert it first.`
    : null;
  const embedDisabled = !capsLoaded || notEmbeddable;
  const convertLabel = `Convert to ${(targetFormat ?? "EPUB").toUpperCase()}`;

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setSaving(true);
    setSaveError(null);
    setWarnings([]);
    try {
      // Never submit embed=true for a format the checkbox is currently
      // disabled for, even if `writeBack.embed` was left over from before
      // the format changed underneath it (e.g. a re-fetch after convert).
      const outcome = await onSave(toMetadata(metadata, form), {
        library_file: writeBack.library_file,
        embed: writeBack.embed && !embedDisabled,
      });
      if (outcome.ok) {
        setWarnings(outcome.warnings);
      } else if (outcome.sidecarSaved) {
        setSaveError({ sidecarSaved: true, reason: outcome.reason, message: outcome.message });
      } else {
        setSaveError({ sidecarSaved: false, message: outcome.message });
      }
    } catch (err) {
      // `onSave` is written to always resolve, never reject - but if
      // something upstream throws anyway, the alternative is an unhandled
      // rejection that leaves this form looking like a clean, successful
      // save (the state resets above already cleared any prior error).
      // Never let a failure vanish silently.
      setSaveError({
        sidecarSaved: false,
        message: err instanceof Error ? err.message : "failed to save",
      });
    } finally {
      setSaving(false);
    }
  }

  return (
    <form className="metadata-form" onSubmit={handleSubmit}>
      <h3>Metadata</h3>

      <label>
        Title
        <input
          value={form.title}
          onChange={(e) => setForm({ ...form, title: e.target.value })}
        />
      </label>
      <label>
        Subtitle
        <input
          value={form.subtitle}
          onChange={(e) => setForm({ ...form, subtitle: e.target.value })}
        />
      </label>
      <label>
        Authors (comma-separated)
        <input
          value={form.authors}
          onChange={(e) => setForm({ ...form, authors: e.target.value })}
        />
      </label>
      <div className="form-row">
        <label>
          Series
          <input
            value={form.series}
            onChange={(e) => setForm({ ...form, series: e.target.value })}
          />
        </label>
        <label>
          Series index
          <input
            value={form.series_index}
            onChange={(e) => setForm({ ...form, series_index: e.target.value })}
          />
        </label>
      </div>
      <label>
        Publisher
        <input
          value={form.publisher}
          onChange={(e) => setForm({ ...form, publisher: e.target.value })}
        />
      </label>
      <div className="form-row">
        <label>
          Publication date
          <input
            value={form.pubdate}
            onChange={(e) => setForm({ ...form, pubdate: e.target.value })}
          />
        </label>
        <label>
          Language
          <input
            value={form.language}
            onChange={(e) => setForm({ ...form, language: e.target.value })}
          />
        </label>
      </div>
      <label>
        ISBN-13
        <input
          value={form.isbn13}
          onChange={(e) => setForm({ ...form, isbn13: e.target.value })}
        />
      </label>
      <label>
        Tags (comma-separated)
        <input
          value={form.tags}
          onChange={(e) => setForm({ ...form, tags: e.target.value })}
        />
      </label>
      <label>
        Description
        <textarea
          rows={4}
          value={form.description}
          onChange={(e) => setForm({ ...form, description: e.target.value })}
        />
      </label>

      <label className="checkbox-row">
        <input
          type="checkbox"
          checked={writeBack.library_file}
          onChange={(e) => setWriteBack({ ...writeBack, library_file: e.target.checked })}
        />
        Rename the copy in library/ to match (reversible with rollback)
      </label>

      <label
        className="checkbox-row"
        title={embedDisabledReason ?? (capsLoaded ? "" : "Loading capabilities…")}
      >
        <input
          type="checkbox"
          disabled={embedDisabled}
          checked={writeBack.embed}
          onChange={(e) => setWriteBack({ ...writeBack, embed: e.target.checked })}
        />
        Write the metadata into the file itself
      </label>
      {embedDisabledReason && (
        <p className="hint">
          {embedDisabledReason}{" "}
          {convertible && (
            <button type="button" onClick={onConvert} disabled={convertRunning}>
              {convertLabel}
            </button>
          )}
        </p>
      )}

      <button type="submit" disabled={saving}>
        {saving ? "Saving…" : "Save"}
      </button>

      {saveError && saveError.sidecarSaved && (
        <div className="notice notice-error">
          <p>Saved to the sidecar. The file-level step did not run:</p>
          <p>
            {saveError.message}
            {saveError.reason === "convert_first" && convertible && (
              <>
                {" "}
                <button type="button" onClick={onConvert} disabled={convertRunning}>
                  {convertLabel}
                </button>
              </>
            )}
          </p>
        </div>
      )}

      {saveError && !saveError.sidecarSaved && (
        <div className="notice notice-error">
          <p>Save failed - nothing was changed. {saveError.message}</p>
        </div>
      )}

      {warnings.length > 0 && (
        <ul className="notice notice-warn">
          {warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      )}
    </form>
  );
}
