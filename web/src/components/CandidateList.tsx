import { Fragment, useState } from "react";
import type { Candidate } from "../api";

type CandidateListProps = {
  candidates: Candidate[];
  onChoose: (editionId: number) => void;
  /** Shows each candidate's 1-based position (blank past 9) as a left
   * column, for screens where a keyboard digit picks the row - the review
   * queue. Off by default so the book detail table is unchanged. */
  showIndex?: boolean;
};

function prettyEvidence(json: string | null): string {
  if (!json) return "no evidence recorded";
  try {
    return JSON.stringify(JSON.parse(json), null, 2);
  } catch {
    return json;
  }
}

export default function CandidateList({ candidates, onChoose, showIndex = false }: CandidateListProps) {
  const [expanded, setExpanded] = useState<number | null>(null);
  const columnCount = showIndex ? 9 : 8;

  return (
    <section className="candidates">
      <h3>Candidates</h3>
      {candidates.length === 0 ? (
        <p className="muted">
          No candidates yet. Use "Fix metadata" below to search providers.
        </p>
      ) : (
        <table className="candidate-table">
          <thead>
            <tr>
              {showIndex && <th></th>}
              <th>Title</th>
              <th>Author</th>
              <th>ISBN</th>
              <th>Publisher</th>
              <th>Score</th>
              <th>Confidence</th>
              <th>Resolver</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {candidates.map((c, i) => (
              <Fragment key={c.edition_id}>
                <tr>
                  {showIndex && (
                    <td className="candidate-index muted">{i < 9 ? i + 1 : ""}</td>
                  )}
                  <td>{c.title || "—"}</td>
                  <td>{c.authors || "—"}</td>
                  <td>{c.isbn13 || c.isbn10 || "—"}</td>
                  <td>{c.publisher || "—"}</td>
                  <td>{c.score.toFixed(2)}</td>
                  <td>{c.confidence.toFixed(2)}</td>
                  <td>{c.resolver}</td>
                  <td className="candidate-actions">
                    <button
                      type="button"
                      onClick={() => setExpanded(expanded === c.edition_id ? null : c.edition_id)}
                    >
                      {expanded === c.edition_id ? "Hide" : "Evidence"}
                    </button>
                    <button type="button" onClick={() => onChoose(c.edition_id)}>
                      Use this
                    </button>
                  </td>
                </tr>
                {expanded === c.edition_id && (
                  <tr>
                    <td colSpan={columnCount}>
                      <pre className="evidence">{prettyEvidence(c.evidence_json)}</pre>
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
