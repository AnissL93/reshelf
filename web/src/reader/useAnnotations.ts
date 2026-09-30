// Annotation state for one file of one book.
//
// Writes are optimistic, and a failed write does NOT roll the mark back
// off the screen. Losing a highlight the user just drew is worse than
// showing one that is not yet saved: the mark stays, an error surfaces,
// and `retry` re-sends.
//
// An annotation with no server id yet has a lifecycle (in flight, failed,
// edited while in flight, cancelled while in flight). That lifecycle lives
// in `queue`, a ref keyed by local id, so no operation ever reads a stale
// closure snapshot. Operations on a not-yet-saved annotation mutate its
// queued create instead of touching the network.
import { useCallback, useEffect, useRef, useState } from "react";
import {
  createAnnotation, deleteAnnotation, listAnnotations, patchAnnotation,
} from "../api";
import type { Annotation, AnnotationColor } from "../api";

type CreateBody = Parameters<typeof createAnnotation>[1];
type PatchBody = { note?: string; color?: AnnotationColor };
type Mutation =
  | { kind: "create"; body: CreateBody; state: "inflight" | "failed" | "cancelled" }
  | { kind: "patch"; id: string; body: PatchBody; state: "inflight" | "failed" };
type Queue = Map<string, Mutation>;

// One object so a file switch resets list, error and counter together,
// during render (no effect, no set-state-in-effect).
type S = { key: string; list: Annotation[]; error: string | null; unsaved: number };

const LOCAL = "pending-";
const patchKey = (id: string) => `patch:${id}`;
const unsavedCount = (q: Queue) =>
  [...q.values()].filter((m) => m.state === "failed").length;
const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e));

export default function useAnnotations(sha: string, fileSha: string | null) {
  const key = `${sha}/${fileSha ?? ""}`;
  const [state, setState] = useState<S>({ key, list: [], error: null, unsaved: 0 });
  const queueRef = useRef<Queue>(new Map());

  // I6: a different file starts from empty, so the previous file's marks
  // never show against the new one.
  if (state.key !== key) setState({ key, list: [], error: null, unsaved: 0 });
  const live = state.key === key ? state : { key, list: [], error: null, unsaved: 0 };

  // Writes tagged with the key they were issued under; a response that
  // arrives after a file switch is a no-op.
  const apply = useCallback(
    (k: string, fn: (s: S) => S) => setState((s) => (s.key === k ? fn(s) : s)),
    [],
  );

  const fail = useCallback(
    (k: string, q: Queue, e: unknown) =>
      apply(k, (s) => ({ ...s, error: errMsg(e), unsaved: unsavedCount(q) })),
    [apply],
  );

  const load = useCallback(
    (q: Queue, stale: () => boolean) => {
      if (!fileSha) return;
      listAnnotations(sha, fileSha)
        .then((server) => {
          if (stale()) return;
          // I1: merge, never replace. Server list, with queued edits laid
          // over it, plus every local mark that has not been saved.
          apply(key, (s) => {
            const merged = server.map((a) => {
              const p = q.get(patchKey(a.id));
              return p?.kind === "patch" ? { ...a, ...p.body } : a;
            });
            const local = s.list.filter((a) => {
              const m = q.get(a.id);
              return m?.kind === "create" && m.state !== "cancelled";
            });
            return { ...s, list: [...merged, ...local] };
          });
        })
        .catch((e: unknown) => {
          if (!stale()) fail(key, q, e);
        });
    },
    [sha, fileSha, key, apply, fail],
  );

  useEffect(() => {
    // Fresh queue per file; handlers hold the queue they started with.
    const q: Queue = new Map();
    queueRef.current = q;
    let ignore = false; // I6: discard a superseded response
    load(q, () => ignore);
    return () => {
      ignore = true;
    };
  }, [load]);

  const reload = useCallback(() => load(queueRef.current, () => false), [load]);

  const runPatch = useCallback(
    (q: Queue, id: string, body: PatchBody) => {
      const k = key;
      const pk = patchKey(id);
      const prior = q.get(pk);
      const m: Mutation = {
        kind: "patch", id, state: "inflight",
        body: { ...(prior?.kind === "patch" ? prior.body : {}), ...body },
      };
      q.set(pk, m);
      patchAnnotation(sha, id, m.body)
        .then((saved) => {
          if (q.get(pk) !== m) return; // a newer edit owns this id now
          q.delete(pk);
          apply(k, (s) => ({
            ...s, unsaved: unsavedCount(q),
            list: s.list.map((a) => (a.id === id ? saved : a)),
          }));
        })
        .catch((e: unknown) => {
          // I5: the edit stays on screen and stays queued for retry.
          if (q.get(pk) === m) m.state = "failed";
          fail(k, q, e);
        });
    },
    [sha, key, apply, fail],
  );

  const runCreate = useCallback(
    (q: Queue, localId: string, m: Extract<Mutation, { kind: "create" }>) => {
      const k = key;
      const sent = { note: m.body.note, color: m.body.color };
      createAnnotation(sha, m.body)
        .then((saved) => {
          q.delete(localId);
          if (m.state === "cancelled") {
            // I3: deleted while in flight; undo the server side.
            deleteAnnotation(sha, saved.id).catch((e: unknown) => fail(k, q, e));
            return;
          }
          // I2: carry over an edit that arrived while in flight.
          const edited = m.body.note !== sent.note || m.body.color !== sent.color;
          const merged: Annotation = edited
            ? { ...saved, note: m.body.note ?? "", color: m.body.color ?? saved.color }
            : saved;
          apply(k, (s) => {
            const list = s.list.map((a) => (a.id === localId ? merged : a));
            return {
              ...s, unsaved: unsavedCount(q),
              list: list.filter((a, i) => list.findIndex((b) => b.id === a.id) === i),
            };
          });
          if (edited) runPatch(q, saved.id, { note: m.body.note, color: m.body.color });
        })
        .catch((e: unknown) => {
          if (m.state === "cancelled") {
            q.delete(localId);
            return;
          }
          m.state = "failed"; // the mark stays on screen. Deliberately.
          fail(k, q, e);
        });
    },
    [sha, key, apply, fail, runPatch],
  );

  const create = useCallback(
    (anchor: object, type: "highlight" | "bookmark", color: AnnotationColor, note = "") => {
      if (!fileSha) return;
      const q = queueRef.current;
      const id = `${LOCAL}${crypto.randomUUID()}`;
      const now = new Date().toISOString();
      const optimistic: Annotation = {
        id, type, file_sha: fileSha, color, note,
        anchor: anchor as Annotation["anchor"],
        created_at: now, updated_at: now,
      };
      const m: Extract<Mutation, { kind: "create" }> = {
        kind: "create", state: "inflight",
        body: { type, file_sha: fileSha, anchor, color, note },
      };
      q.set(id, m);
      apply(key, (s) => ({ ...s, list: [...s.list, optimistic] }));
      runCreate(q, id, m);
    },
    [fileSha, key, apply, runCreate],
  );

  const update = useCallback(
    (id: string, body: PatchBody) => {
      const q = queueRef.current;
      apply(key, (s) => ({
        ...s, list: s.list.map((a) => (a.id === id ? { ...a, ...body } : a)),
      }));
      if (id.startsWith(LOCAL)) {
        // I2: no server id yet. Edit the queued create; send nothing.
        const m = q.get(id);
        if (m?.kind === "create") m.body = { ...m.body, ...body };
        return;
      }
      runPatch(q, id, body);
    },
    [key, apply, runPatch],
  );

  const remove = useCallback(
    (id: string) => {
      const q = queueRef.current;
      const k = key;
      apply(k, (s) => ({ ...s, list: s.list.filter((a) => a.id !== id) }));
      if (id.startsWith(LOCAL)) {
        // I3: no server id. Cancel the queued create; no request. If its
        // POST is in flight the success handler deletes what it made.
        const m = q.get(id);
        if (m?.kind === "create") {
          if (m.state === "inflight") m.state = "cancelled";
          else q.delete(id);
        }
        apply(k, (s) => ({ ...s, unsaved: unsavedCount(q) }));
        return;
      }
      q.delete(patchKey(id)); // a queued edit to a deleted mark is moot
      apply(k, (s) => ({ ...s, unsaved: unsavedCount(q) }));
      deleteAnnotation(sha, id).catch((e: unknown) => {
        // A 404 means our state was stale, so refetch rather than guess.
        // reload merges, so unsaved marks survive it (I1).
        fail(k, q, e);
        load(q, () => false);
      });
    },
    [sha, key, apply, fail, load],
  );

  const retry = useCallback(() => {
    const q = queueRef.current;
    // I4: drain the ref. Flip to inflight synchronously, before sending,
    // so a second call finds nothing failed and does nothing.
    const failed = [...q.entries()].filter(([, m]) => m.state === "failed");
    if (failed.length === 0) return;
    for (const [, m] of failed) m.state = "inflight";
    apply(key, (s) => ({ ...s, error: null, unsaved: unsavedCount(q) }));
    for (const [id, m] of failed) {
      if (m.kind === "create") runCreate(q, id, m);
      else runPatch(q, m.id, m.body); // I5
    }
  }, [key, apply, runCreate, runPatch]);

  return {
    annotations: live.list, create, update, remove, reload,
    error: live.error, unsaved: live.unsaved, retry,
  };
}
