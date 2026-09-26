import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import type { BookMetadata, Capabilities, MetadataErrorDetail, WriteBack } from "../api";

// What the route's save handler reports back, so this component can render
// the right remedy instead of a raw error. `ok: false` still means the
// sidecar (tier 1) was written - the PATCH endpoint mutates it before
// attempting the tier that failed - so the message here must never read
// like the edit was lost.
export type SaveOutcome =
  | { ok: true; warnings: string[] }
  | { ok: false; reason: MetadataErrorDetail["reason"]; message: string };

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
  const [saveError, setSaveError] = useState<{
    reason: MetadataErrorDetail["reason"];
    message: string;
  } | null>(null);

  const embeddable = capabilities?.embeddable ?? [];
  const targetFormat = capabilities?.convert[primaryFormat ?? ""];
  const convertible = capabilities ? Boolean(targetFormat) : false;
  const embedDisabledReason = embeddable.includes(primaryFormat ?? "")
    ? null
    : `${primaryFormat ?? "This format"} cannot hold metadata; convert it first.`;
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
        embed: writeBack.embed && embedDisabledReason === null,
      });
      if (outcome.ok) {
        setWarnings(outcome.warnings);
      } else {
        setSaveError({ reason: outcome.reason, message: outcome.message });
      }
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

      <label className="checkbox-row" title={embedDisabledReason ?? ""}>
        <input
          type="checkbox"
          disabled={embedDisabledReason !== null}
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

      {saveError && (
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
