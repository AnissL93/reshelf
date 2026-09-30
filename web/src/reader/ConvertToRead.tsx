// A format with no browser engine but a converter: MOBI, AZW3 and TXT
// become EPUB; DJVU becomes PDF. Reuses sub-project A's per-book convert
// job rather than adding a second conversion path.
import { convertBook } from "../api";
import type { ReadableFile } from "../api";
import useJob from "../hooks/useJob";
import { useEffect, useState } from "react";

export default function ConvertToRead({
  sha, file, onDone,
}: {
  sha: string;
  file: ReadableFile;
  onDone: () => void;
}) {
  const [jobId, setJobId] = useState<number | null>(null);
  const { job, running } = useJob(jobId);

  // In an effect, not in the render body: calling the parent's setState
  // while rendering re-renders it mid-render and loops.
  useEffect(() => {
    if (job?.status === "done") onDone();
  }, [job?.status, onDone]);

  if (!file.convert_to) {
    return (
      <div className="reader-notice">
        <p>{file.format.toUpperCase()} cannot be read in the browser.</p>
        <a href={`/api/books/${sha}/file?path=${encodeURIComponent(file.path)}`}>
          Download it
        </a>
      </div>
    );
  }

  return (
    <div className="reader-notice">
      <p>This is a {file.format.toUpperCase()} file.</p>
      <button
        disabled={running}
        onClick={() => convertBook(sha).then((r) => setJobId(r.job_id))}
      >
        {running
          ? (job?.message ?? "Converting…")
          : `Convert to ${file.convert_to.toUpperCase()} & read`}
      </button>
      {job?.status === "failed" && <p className="error">{job.error}</p>}
    </div>
  );
}
