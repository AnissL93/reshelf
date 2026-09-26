// Renders a plan's or a journal's actions grouped by kind, with a readable
// `src -> dest` sample, and gates a single destructive confirm button behind
// having actually rendered them. Used for both the commit gate (Jobs.tsx's
// CommitSection, over a plan's actions) and the rollback gate (its
// RollbackSection, over a journal's actions) - the two shapes only differ in
// field names (`file` vs `src`), which the caller normalizes into rows.

export type PreviewRow = {
  action: string;
  src: string;
  dest?: string;
};

type PlanPreviewProps = {
  title: string;
  rows: PreviewRow[];
  confirmLabel: string;
  onConfirm: () => void;
  confirming: boolean;
  error?: string | null;
};

const SAMPLE_LIMIT = 50;

export default function PlanPreview({
  title,
  rows,
  confirmLabel,
  onConfirm,
  confirming,
  error,
}: PlanPreviewProps) {
  const counts = new Map<string, number>();
  for (const row of rows) counts.set(row.action, (counts.get(row.action) ?? 0) + 1);
  const sample = rows.slice(0, SAMPLE_LIMIT);

  return (
    <div className="plan-preview">
      <h3>{title}</h3>
      {rows.length === 0 ? (
        <p className="muted">No actions in this plan.</p>
      ) : (
        <>
          <ul className="plan-preview-counts">
            {[...counts.entries()].map(([kind, n]) => (
              <li key={kind}>
                <strong>{kind}</strong>: {n}
              </li>
            ))}
          </ul>
          <table className="plan-preview-table">
            <thead>
              <tr>
                <th>kind</th>
                <th>src → dest</th>
              </tr>
            </thead>
            <tbody>
              {sample.map((row, i) => (
                <tr key={i}>
                  <td>{row.action}</td>
                  <td className="plan-preview-path">
                    {row.src}
                    {row.dest ? ` → ${row.dest}` : ""}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {rows.length > sample.length && (
            <p className="muted">…and {rows.length - sample.length} more.</p>
          )}
        </>
      )}
      {error && <p className="error">{error}</p>}
      <button disabled={confirming || rows.length === 0} onClick={onConfirm}>
        {confirming ? "Working…" : confirmLabel}
      </button>
    </div>
  );
}
