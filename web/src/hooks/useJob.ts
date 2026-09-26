// Live progress for one job, used inline on the book detail page rather than
// via the /jobs list. Resets to null whenever `id` changes so a stale job's
// terminal status can't leak into a freshly started one.
import { useEffect, useState } from "react";
import type { Job } from "../api";
import { subscribeJob } from "../api";

export default function useJob(id: number | null): { job: Job | null; running: boolean } {
  const [job, setJob] = useState<Job | null>(null);
  useEffect(() => {
    setJob(null);
    if (id === null) return;
    return subscribeJob(id, setJob);
  }, [id]);
  const running = job !== null && ["queued", "running"].includes(job.status);
  return { job, running };
}
