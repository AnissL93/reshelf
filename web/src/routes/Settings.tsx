import { useCallback, useEffect, useState } from "react";
import type { Settings as SettingsData } from "../api";
import { ApiError, getSettings, putSettings } from "../api";
import { refreshCapabilities } from "../hooks/useCapabilities";

type SettingValue = string | number | boolean | null;

// The backend's SETTABLE tuple (meta.py) is the source of truth for which
// keys exist; this only decides how to group and label them.
const GROUPS: { title: string; keys: string[] }[] = [
  // metadata.layout is deliberately absent: it is config.yaml-only (see
  // SETTABLE in meta.py), because changing it on a populated library
  // leaves every existing sidecar where the new layout will not look.
  { title: "Library", keys: ["library.commit_mode"] },
  { title: "Web", keys: ["web.host", "web.port"] },
  { title: "Write-back defaults", keys: ["write_back.library_file", "write_back.embed"] },
  { title: "AI", keys: ["ai.provider", "ai.model", "ai.api_key", "ai.base_url"] },
  { title: "Matching", keys: ["matching.auto_accept", "matching.review_below", "convert.timeout"] },
];

const LABELS: Record<string, string> = {
  "library.commit_mode": "Commit mode",
  "web.host": "Host",
  "web.port": "Port",
  "write_back.library_file": "Write a library-file sidecar by default",
  "write_back.embed": "Embed metadata by default",
  "ai.provider": "Provider",
  "ai.model": "Model",
  "ai.api_key": "API key",
  "ai.base_url": "Base URL",
  "matching.auto_accept": "Auto-accept threshold",
  "matching.review_below": "Review-below threshold",
  "convert.timeout": "Convert timeout (seconds)",
};

// FastAPI's ValidationError.errors(): each entry's `loc` is a tuple like
// ("library", "commit_mode") - joined with "." it's exactly one of our
// dotted setting keys, so a per-field error can be placed right next to the
// field that caused it.
type PydanticError = { loc: (string | number)[]; msg: string };

function fieldErrorsFrom(detail: unknown): Record<string, string> {
  if (!Array.isArray(detail)) return {};
  const out: Record<string, string> = {};
  for (const err of detail as PydanticError[]) {
    if (!err || !Array.isArray(err.loc)) continue;
    out[err.loc.join(".")] = err.msg ?? "invalid value";
  }
  return out;
}

function renderInput(
  key: string,
  value: SettingValue,
  onChange: (v: SettingValue) => void,
) {
  switch (key) {
    case "library.commit_mode":
      return (
        <select value={String(value ?? "copy")} onChange={(e) => onChange(e.target.value)}>
          <option value="copy">copy</option>
          <option value="move">move</option>
        </select>
      );
    case "ai.provider":
      return (
        <select
          value={value === null || value === undefined ? "" : String(value)}
          onChange={(e) => onChange(e.target.value === "" ? null : e.target.value)}
        >
          <option value="">(off)</option>
          <option value="claude-cli">claude-cli</option>
          <option value="api">api</option>
        </select>
      );
    case "ai.api_key":
      return (
        <input
          type="password"
          autoComplete="off"
          value={value == null ? "" : String(value)}
          onChange={(e) => onChange(e.target.value === "" ? null : e.target.value)}
        />
      );
    case "write_back.library_file":
    case "write_back.embed":
      return (
        <input
          type="checkbox"
          checked={Boolean(value)}
          onChange={(e) => onChange(e.target.checked)}
        />
      );
    case "web.port":
    case "convert.timeout":
      return (
        <input
          type="number"
          value={value == null ? "" : Number(value)}
          onChange={(e) => onChange(e.target.value === "" ? 0 : Number(e.target.value))}
        />
      );
    case "matching.auto_accept":
    case "matching.review_below":
      return (
        <input
          type="number"
          step="0.01"
          min="0"
          max="1"
          value={value == null ? "" : Number(value)}
          onChange={(e) => onChange(e.target.value === "" ? 0 : Number(e.target.value))}
        />
      );
    default:
      // web.host, ai.model, ai.base_url: plain strings.
      return (
        <input
          type="text"
          value={value == null ? "" : String(value)}
          onChange={(e) => onChange(e.target.value)}
        />
      );
  }
}

function Field({
  settingKey,
  value,
  onChange,
  error,
}: {
  settingKey: string;
  value: SettingValue;
  onChange: (v: SettingValue) => void;
  error?: string;
}) {
  return (
    <div className="settings-field">
      <label>
        <span>{LABELS[settingKey] ?? settingKey}</span>
        {renderInput(settingKey, value, onChange)}
      </label>
      {settingKey === "ai.api_key" && (
        <p className="hint">
          A stored key is never sent back here - the dots mean one is set. Leave the field
          untouched to keep it. Can be supplied via the RESHELF_AI_API_KEY environment
          variable instead of being stored in config.yaml at all.
        </p>
      )}
      {settingKey === "ai.provider" && (
        <p className="hint">Clearing this turns AI off everywhere - matching, resolve, and rematch.</p>
      )}
      {settingKey === "library.commit_mode" && value === "move" && (
        <p className="notice notice-warn">
          Move mode removes originals from incoming/ once committed - only Rollback restores them.
        </p>
      )}
      {error && <p className="error">{error}</p>}
    </div>
  );
}

export default function Settings() {
  const [values, setValues] = useState<SettingsData | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [saved, setSaved] = useState(false);

  const load = useCallback(() => {
    getSettings()
      .then((s) => {
        setValues(s);
        setLoadError(null);
      })
      .catch((e: unknown) =>
        setLoadError(e instanceof Error ? e.message : "failed to load settings"),
      );
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const set = (key: string, value: SettingValue) => {
    setSaved(false);
    setValues((v) => (v ? { ...v, [key]: value } : v));
  };

  const handleSave = useCallback(() => {
    if (!values) return;
    setSaving(true);
    setSaveError(null);
    setFieldErrors({});
    setSaved(false);
    putSettings(values)
      .then((updated) => {
        setValues(updated);
        setSaved(true);
        // Re-render from the server's own values above; this only makes the
        // rest of the app (the resolve button, the AI rematch button, ...)
        // notice the change too, without a reload.
        void refreshCapabilities();
      })
      .catch((e: unknown) => {
        if (e instanceof ApiError && e.status === 422) {
          const fields = fieldErrorsFrom(e.detail);
          setFieldErrors(fields);
          if (Object.keys(fields).length === 0) {
            setSaveError(typeof e.detail === "string" ? e.detail : "invalid settings");
          }
        } else {
          setSaveError(e instanceof Error ? e.message : "failed to save settings");
        }
      })
      .finally(() => setSaving(false));
  }, [values]);

  if (loadError && !values) return <p className="error">{loadError}</p>;
  if (!values) return <p className="muted">Loading…</p>;

  return (
    <div className="settings">
      <h1>Settings</h1>
      {GROUPS.map((group) => (
        <section key={group.title} className="settings-group">
          <h2>{group.title}</h2>
          {group.keys.map((key) => (
            <Field
              key={key}
              settingKey={key}
              value={values[key]}
              onChange={(v) => set(key, v)}
              error={fieldErrors[key]}
            />
          ))}
        </section>
      ))}
      {saveError && <p className="error">{saveError}</p>}
      {saved && <p className="notice">Saved.</p>}
      <button type="button" disabled={saving} onClick={handleSave}>
        {saving ? "Saving…" : "Save settings"}
      </button>
    </div>
  );
}
