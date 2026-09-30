// The active file's annotations, in reading order. Clicking one jumps to
// it; the note is edited in place.
import { useState } from "react";
import { COLORS, SWATCH } from "./colors";
import { compareAnchors } from "./anchors";
import type { Annotation, AnnotationColor } from "../api";

function where(ann: Annotation): string {
  const anchor = ann.anchor as { kind: string; page?: number };
  if (typeof anchor.page === "number") return `p.${anchor.page}`;
  if (anchor.kind === "epub") return "location";
  // A kind this build does not know. Listed, not hidden - it is the
  // user's data and the next version may understand it (spec section 7).
  return "unsupported in this version";
}

function quoted(ann: Annotation): string | null {
  const text = (ann.anchor as { text?: unknown }).text;
  return typeof text === "string" && text ? text : null;
}

export default function Sidebar({
  annotations, onJump, onUpdate, onRemove, unsaved, error, onRetry,
}: {
  annotations: Annotation[];
  onJump: (a: Annotation) => void;
  onUpdate: (id: string, body: { note?: string; color?: AnnotationColor }) => void;
  onRemove: (id: string) => void;
  unsaved: number;
  error: string | null;
  onRetry: () => void;
}) {
  const sorted = [...annotations].sort((x, y) => compareAnchors(x.anchor, y.anchor));
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");

  return (
    <aside className="reader-sidebar">
      {error && (
        <div className="banner error">
          <span>{unsaved > 0 ? `${unsaved} unsaved` : "Error"}: {error}</span>
          <button onClick={onRetry}>Retry</button>
        </div>
      )}
      {annotations.length === 0 && (
        <p className="empty">No highlights yet. Select text, or use the area
          tool to draw a box on the page.</p>
      )}
      <ul className="annotations">
        {sorted.map((ann) => (
          <li key={ann.id} className={`annotation ${ann.type}`}>
            <button className="jump" onClick={() => onJump(ann)}>
              <span
                className="swatch"
                style={{ background: ann.type === "bookmark" ? "none" : SWATCH[ann.color] }}
              >
                {ann.type === "bookmark" ? "⚑" : ""}
              </span>
              <span className="where" title={(ann.anchor as { kind: string }).kind}>
                {where(ann)}
              </span>
              {quoted(ann) && <q className="quote">{quoted(ann)}</q>}
            </button>

            {editing === ann.id ? (
              <div className="note-edit">
                <textarea
                  value={draft}
                  autoFocus
                  onChange={(e) => setDraft(e.target.value)}
                />
                <button
                  onClick={() => {
                    onUpdate(ann.id, { note: draft });
                    setEditing(null);
                  }}
                >
                  Save
                </button>
                <button onClick={() => setEditing(null)}>Cancel</button>
              </div>
            ) : (
              <button
                className="note"
                onClick={() => {
                  setEditing(ann.id);
                  setDraft(ann.note);
                }}
              >
                {ann.note || <em>Add a note</em>}
              </button>
            )}

            <div className="annotation-actions">
              {ann.type === "highlight" &&
                COLORS.map((c) => (
                  <button
                    key={c}
                    aria-label={`Colour ${c}`}
                    className={`swatch-button${ann.color === c ? " on" : ""}`}
                    style={{ background: SWATCH[c] }}
                    onClick={() => onUpdate(ann.id, { color: c })}
                  />
                ))}
              <button
                className="delete"
                onClick={() => {
                  if (confirm("Delete this annotation?")) onRemove(ann.id);
                }}
              >
                Delete
              </button>
            </div>
          </li>
        ))}
      </ul>
    </aside>
  );
}
