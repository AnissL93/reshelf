import { Fragment, useState } from "react";
import type { Candidate } from "../api";

type CandidateListProps = {
  candidates: Candidate[];
  onChoose: (editionId: number) => void;
};

function prettyEvidence(json: string | null): string {
  if (!json) return "no evidence recorded";
  try {
    return JSON.stringify(JSON.parse(json), null, 2);
  } catch {
    return json;
  }
}

export default function CandidateList({ candidates, onChoose }: CandidateListProps) {
  const [expanded, setExpanded] = useState<number | null>(null);

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
            {candidates.map((c) => (
              <Fragment key={c.edition_id}>
                <tr>
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
                    <td colSpan={8}>
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
