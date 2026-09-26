import { useCallback, useEffect, useState } from "react";
import type { Job, JobArgs, Journal, JournalSummary, Plan } from "../api";
import {
  cancelJob,
  createConfirmedJob,
  createJob,
  getJournal,
  getPlan,
  listJobs,
  listJournals,
} from "../api";
import PlanPreview from "../components/PlanPreview";
import useCapabilities from "../hooks/useCapabilities";
import useJob from "../hooks/useJob";

function isActive(job: Job): boolean {
  return job.status === "queued" || job.status === "running";
}

function elapsed(job: Job): string {
  if (!job.started_at) return "-";
  const start = new Date(job.started_at).getTime();
  const end = job.finished_at ? new Date(job.finished_at).getTime() : Date.now();
  return `${Math.max(0, Math.round((end - start) / 1000))}s`;
}

// A `plan` job's message is `JSON.stringify(str(plan_path))` (jobs.py:
// COMMANDS["plan"] returns the Path, and _finish json.dumps's the result) -
// a quoted absolute path, not the plan id alone.
function planPathFromMessage(message: string | null): string | null {
  if (!message) return null;
  try {
    const parsed: unknown = JSON.parse(message);
    return typeof parsed === "string" ? parsed : null;
  } catch {
    return null;
  }
}

function planIdFromPath(path: string): string | null {
  const name = path.split("/").pop() ?? path;
  const match = /^plan-(.+)\.json$/.exec(name);
  return match ? match[1] : null;
}

export default function Jobs() {
  const caps = useCapabilities();
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [runError, setRunError] = useState<string | null>(null);

  const [matchOffline, setMatchOffline] = useState(false);
  const [extractForce, setExtractForce] = useState(false);
  const [resolveLimit, setResolveLimit] = useState("");
  const [resolveIncludeUnresolved, setResolveIncludeUnresolved] = useState(false);

  const refresh = useCallback(() => {
    listJobs()
      .then((js) => {
        setJobs(js);
        setLoadError(null);
      })
      .catch((e: unknown) =>
        setLoadError(e instanceof Error ? e.message : "failed to load jobs"),
      );
  }, []);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 5000);
    return () => clearInterval(timer);
  }, [refresh]);

  // Live-tail the newest non-terminal job (jobs is newest-first) instead of
  // polling for it - the 5s interval above is only the backstop.
  const liveJobId = jobs?.find(isActive)?.id ?? null;
  const { job: liveJob } = useJob(liveJobId);
  useEffect(() => {
    if (liveJob && !isActive(liveJob)) refresh(); // just went terminal - pick up what's next
  }, [liveJob, refresh]);

  const displayJobs = jobs?.map((j) => (liveJob && j.id === liveJob.id ? liveJob : j)) ?? null;

  const start = useCallback(
    (command: string, args: JobArgs = {}) => {
      setRunError(null);
      createJob(command, args)
        .then(refresh)
        .catch((e: unknown) =>
          setRunError(e instanceof Error ? e.message : `failed to start ${command}`),
        );
    },
    [refresh],
  );

  const handleCancel = useCallback(
    (id: number) => {
      cancelJob(id)
        .then(refresh)
        .catch((e: unknown) => setRunError(e instanceof Error ? e.message : "failed to cancel"));
    },
    [refresh],
  );

  const latestPlanJob = jobs?.find((j) => j.command === "plan" && j.status === "done") ?? null;

  if (loadError && !jobs) return <p className="error">{loadError}</p>;

  return (
    <div className="jobs">
      <h1>Jobs</h1>

      <section className="run-panel">
        <h2>Run</h2>
        {runError && <p className="error">{runError}</p>}
        <div className="run-stage">
          <button type="button" onClick={() => start("scan")}>
            Scan
          </button>
        </div>
        <div className="run-stage">
          <button type="button" onClick={() => start("extract", { force: extractForce })}>
            Extract
          </button>
          <label className="checkbox-row">
            <input
              type="checkbox"
              checked={extractForce}
              onChange={(e) => setExtractForce(e.target.checked)}
            />
            force
          </label>
        </div>
        <div className="run-stage">
          <button type="button" onClick={() => start("match", { offline: matchOffline })}>
            Match
          </button>
          <label className="checkbox-row">
            <input
              type="checkbox"
              checked={matchOffline}
              onChange={(e) => setMatchOffline(e.target.checked)}
            />
            offline
          </label>
        </div>
        <div className="run-stage">
          <button
            type="button"
            disabled={!caps?.ai}
            title={caps?.ai ? "" : "Configure an AI provider in Settings first"}
            onClick={() =>
              start("resolve", {
                limit: resolveLimit ? Number(resolveLimit) : undefined,
                include_unresolved: resolveIncludeUnresolved,
              })
            }
          >
            Resolve
          </button>
          <input
            type="number"
            placeholder="limit"
            value={resolveLimit}
            onChange={(e) => setResolveLimit(e.target.value)}
          />
          <label className="checkbox-row">
            <input
              type="checkbox"
              checked={resolveIncludeUnresolved}
              onChange={(e) => setResolveIncludeUnresolved(e.target.checked)}
            />
            include unresolved
          </label>
        </div>
        <div className="run-stage">
          <button type="button" onClick={() => start("plan")}>
            Plan
          </button>
        </div>
        <div className="run-stage">
          <button type="button" onClick={() => start("reindex")}>
            Reindex
          </button>
        </div>
      </section>

      <CommitSection latestPlanJob={latestPlanJob} onCommitted={refresh} />
      <RollbackSection onRolledBack={refresh} />

      <section className="job-history">
        <h2>Job history</h2>
        {loadError && <p className="error">{loadError}</p>}
        {!displayJobs ? (
          <p className="muted">Loading…</p>
        ) : displayJobs.length === 0 ? (
          <p className="muted">No jobs yet.</p>
        ) : (
          <ul className="job-list">
            {displayJobs.map((job) => (
              <li key={job.id} className="job-row">
                <div className="job-row-header">
                  <span className="job-command">{job.command}</span>
                  <span className={`badge ${job.status === "done" ? "ok" : job.status === "failed" ? "bad" : "muted"}`}>
                    {job.status}
                  </span>
                  <span className="muted">
                    {job.total ? `${job.progress}/${job.total}` : job.progress}
                  </span>
                  <span className="muted">{elapsed(job)}</span>
                  {job.message && <span className="muted job-message">{job.message}</span>}
                  {isActive(job) && (
                    <button type="button" onClick={() => handleCancel(job.id)}>
                      Cancel
                    </button>
                  )}
                </div>
                {isActive(job) && <progress value={job.progress} max={job.total ?? undefined} />}
                {job.error && <p className="error">{job.error}</p>}
                {job.log && (
                  <details>
                    <summary>Log</summary>
                    <pre className="job-log">{job.log}</pre>
                  </details>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

// -- the commit gate -------------------------------------------------------
//
// `commit` is never a button here. The only path to it is: a `plan` job has
// finished, its plan has been fetched and rendered, and only then does
// PlanPreview render a confirm button at all.

function CommitSection({
  latestPlanJob,
  onCommitted,
}: {
  latestPlanJob: Job | null;
  onCommitted: () => void;
}) {
  const planPath = planPathFromMessage(latestPlanJob?.message ?? null);
  const planId = planPath ? planIdFromPath(planPath) : null;

  const [plan, setPlan] = useState<Plan | null>(null);
  const [loadingPlan, setLoadingPlan] = useState(false);
  const [planError, setPlanError] = useState<string | null>(null);
  const [committing, setCommitting] = useState(false);
  const [commitError, setCommitError] = useState<string | null>(null);
  const [commitStarted, setCommitStarted] = useState(false);

  useEffect(() => {
    setPlan(null);
    setPlanError(null);
    setCommitStarted(false);
  }, [planId]);

  const loadPlan = () => {
    if (!planId) return;
    setLoadingPlan(true);
    setPlanError(null);
    getPlan(planId)
      .then(setPlan)
      .catch((e: unknown) => setPlanError(e instanceof Error ? e.message : "failed to load plan"))
      .finally(() => setLoadingPlan(false));
  };

  const applyPlan = () => {
    if (!planPath) return;
    setCommitting(true);
    setCommitError(null);
    createConfirmedJob("commit", { confirmed: true, plan: planPath })
      .then(() => {
        setCommitStarted(true);
        setPlan(null);
        onCommitted();
      })
      .catch((e: unknown) =>
        setCommitError(e instanceof Error ? e.message : "failed to start commit"),
      )
      .finally(() => setCommitting(false));
  };

  return (
    <section className="commit-section">
      <h2>Commit</h2>
      {!planPath && (
        <p className="muted">
          Run <strong>Plan</strong> above, then come back here to preview it before applying.
        </p>
      )}
      {planPath && !plan && (
        <button type="button" disabled={loadingPlan} onClick={loadPlan}>
          {loadingPlan ? "Loading…" : `Preview plan ${planId ?? ""}`}
        </button>
      )}
      {planError && <p className="error">{planError}</p>}
      {plan && (
        <PlanPreview
          title={`Plan ${plan.plan_id}`}
          rows={plan.actions.map((a) => ({ action: a.action, src: a.file, dest: a.dest }))}
          confirmLabel="Apply this plan"
          onConfirm={applyPlan}
          confirming={committing}
          error={commitError}
        />
      )}
      {commitStarted && <p className="notice">Commit job started - see it in Job history below.</p>}
    </section>
  );
}

// -- the rollback gate ------------------------------------------------------

function RollbackSection({ onRolledBack }: { onRolledBack: () => void }) {
  const [journals, setJournals] = useState<JournalSummary[] | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string>("");
  const [journal, setJournal] = useState<Journal | null>(null);
  const [journalError, setJournalError] = useState<string | null>(null);
  const [rollingBack, setRollingBack] = useState(false);
  const [rollbackError, setRollbackError] = useState<string | null>(null);
  const [rollbackStarted, setRollbackStarted] = useState(false);

  const loadJournals = useCallback(() => {
    listJournals()
      .then((js) => {
        setJournals(js);
        setListError(null);
      })
      .catch((e: unknown) =>
        setListError(e instanceof Error ? e.message : "failed to load commits"),
      );
  }, []);

  useEffect(() => {
    loadJournals();
  }, [loadJournals]);

  useEffect(() => {
    setJournal(null);
    setJournalError(null);
    setRollbackStarted(false);
  }, [selected]);

  const loadJournal = () => {
    if (!selected) return;
    getJournal(selected)
      .then(setJournal)
      .catch((e: unknown) =>
        setJournalError(e instanceof Error ? e.message : "failed to load commit"),
      );
  };

  const applyRollback = () => {
    if (!selected) return;
    setRollingBack(true);
    setRollbackError(null);
    createConfirmedJob("rollback", { confirmed: true, commit_id: selected })
      .then(() => {
        setRollbackStarted(true);
        setJournal(null);
        onRolledBack();
        loadJournals();
      })
      .catch((e: unknown) =>
        setRollbackError(e instanceof Error ? e.message : "failed to start rollback"),
      )
      .finally(() => setRollingBack(false));
  };

  return (
    <section className="rollback-section">
      <h2>Rollback</h2>
      {listError && <p className="error">{listError}</p>}
      {journals && journals.length === 0 && <p className="muted">No commits to roll back yet.</p>}
      {journals && journals.length > 0 && (
        <div className="rollback-picker">
          <select value={selected} onChange={(e) => setSelected(e.target.value)}>
            <option value="">Select a commit…</option>
            {journals.map((j) => (
              <option key={j.commit_id} value={j.commit_id}>
                {j.commit_id} · {j.created_at ?? "unknown time"} · {j.actions} actions
              </option>
            ))}
          </select>
          <button type="button" disabled={!selected} onClick={loadJournal}>
            Preview
          </button>
        </div>
      )}
      {journalError && <p className="error">{journalError}</p>}
      {journal && (
        <PlanPreview
          title={`Commit ${journal.commit_id}`}
          rows={journal.actions.map((a) => ({ action: a.action, src: a.src, dest: a.dest }))}
          confirmLabel="Roll back this commit"
          onConfirm={applyRollback}
          confirming={rollingBack}
          error={rollbackError}
        />
      )}
      {rollbackStarted && (
        <p className="notice">Rollback job started - see it in Job history below.</p>
      )}
    </section>
  );
}
