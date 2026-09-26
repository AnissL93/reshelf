// Fixed bottom bar, rendered on every route (see App.tsx). Polls listJobs()
// every 5s to notice a newly-queued job; once one is queued/running it hands
// off to subscribeJob (via useJob) for that one job so the bar updates live
// without polling. Renders nothing while everything is idle - no empty bar
// eating screen space, and no polling once idle would only ever get "[]"
// back is still needed for the *next* job to start showing up.
import { useEffect, useState } from "react";
import type { Job } from "../api";
import { cancelJob, listJobs } from "../api";
import useJob from "../hooks/useJob";

function isActive(job: Job): boolean {
  return job.status === "queued" || job.status === "running";
}

export default function JobStatusBar() {
  const [jobs, setJobs] = useState<Job[]>([]);

  useEffect(() => {
    let cancelled = false;
    const poll = () => {
      listJobs().then((js) => {
        if (!cancelled) setJobs(js);
      }).catch(() => {
        // Transient - the next poll or the page itself will surface a
        // persistent API outage; the status bar just skips this tick.
      });
    };
    poll();
    const timer = setInterval(poll, 5000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  // jobs is newest-first (GET /jobs), so the first match is the newest
  // non-terminal job - the one thing the single worker thread is doing now
  // or about to do next.
  const activeId = jobs.find(isActive)?.id ?? null;
  const { job: liveJob } = useJob(activeId);
  // Between activeId changing and the new subscription's first snapshot
  // arriving, liveJob is briefly null - fall back to the poll's own copy of
  // that job so the bar doesn't flash empty mid-handoff.
  const active = liveJob ?? jobs.find((j) => j.id === activeId) ?? null;

  if (!active || !isActive(active)) return null;

  return (
    <div className="status-bar">
      <span className="status-bar-command">{active.command}</span>
      <progress value={active.progress} max={active.total ?? undefined} />
      <span className="muted">{active.message ?? ""}</span>
      <button type="button" onClick={() => cancelJob(active.id).catch(() => {})}>
        Cancel
      </button>
    </div>
  );
}
