// The annotation mutation queue as a pure reducer: no fetch, no React, no
// timers. `reduce(q, action)` returns the next queue plus the network
// effects the caller must perform; the caller reports each result back as
// another action. The hook owns I/O and React state and makes no decision
// of its own.
//
// Central rule, enforced by `select`: a failed save never removes a mark
// from the screen. Unsaved marks live in `marks` (keyed by local id) and
// unsaved edits live in `patches`; both are overlaid on whatever the
// server list says, and neither is dropped by a list arriving, a delete
// failing, or a file switch. Everything is keyed by file, so switching
// files hides the other file's queue without discarding it.
import type { Annotation, AnnotationColor } from "../api";

export type Scope = { key: string; sha: string; fileSha: string };
export type PatchBody = { note?: string; color?: AnnotationColor };
export type CreateBody = {
  type: "highlight" | "bookmark";
  file_sha: string;
  anchor: object;
  color?: AnnotationColor;
  note?: string;
};

type Mark = {
  scope: Scope;
  annotation: Annotation; // optimistic, `pending-…` id
  body: CreateBody; // what a (re)send will POST, including later edits
  sent: PatchBody; // note/colour as of the last POST
  status: "inflight" | "failed" | "cancelled";
};
type Patch = {
  scope: Scope;
  id: string;
  seq: number;
  body: PatchBody;
  status: "inflight" | "failed";
};
type Saved = { a: Annotation; at: number };

export type Q = {
  clock: number;
  seq: number;
  marks: Map<string, Mark>; // by local id
  patches: Map<string, Patch>; // by server id
  alias: Map<string, string>; // local id -> server id, once saved
  server: Map<string, Saved[]>; // by file key
  tomb: Map<string, number>; // deleted server id -> clock
  error: { key: string; msg: string } | null;
};

export type Action =
  | { type: "create"; scope: Scope; localId: string; annotation: Annotation; body: CreateBody }
  | { type: "edit"; scope: Scope; id: string; body: PatchBody }
  | { type: "remove"; scope: Scope; id: string }
  | { type: "createOk"; scope: Scope; localId: string; saved: Annotation }
  | { type: "createFail"; scope: Scope; localId: string; error: string }
  | { type: "patchOk"; scope: Scope; id: string; seq: number; saved: Annotation }
  | { type: "patchFail"; scope: Scope; id: string; seq: number; error: string }
  | { type: "deleteFail"; scope: Scope; id: string; cleanup: boolean; error: string }
  | { type: "listOk"; scope: Scope; issuedAt: number; list: Annotation[] }
  | { type: "listFail"; scope: Scope; error: string }
  | { type: "reload"; scope: Scope }
  | { type: "retry"; scope: Scope };

export type Effect =
  | { kind: "create"; scope: Scope; localId: string; body: CreateBody }
  | { kind: "patch"; scope: Scope; id: string; seq: number; body: PatchBody }
  | { kind: "delete"; scope: Scope; id: string; cleanup: boolean }
  | { kind: "list"; scope: Scope; issuedAt: number };

export const LOCAL_PREFIX = "pending-";

export const emptyQueue = (): Q => ({
  clock: 0, seq: 0, marks: new Map(), patches: new Map(), alias: new Map(),
  server: new Map(), tomb: new Map(), error: null,
});

const defined = (b: PatchBody): PatchBody => ({
  ...(b.note !== undefined ? { note: b.note } : {}),
  ...(b.color !== undefined ? { color: b.color } : {}),
});

const next = (q: Q): Q => ({
  ...q, clock: q.clock + 1,
  marks: new Map(q.marks), patches: new Map(q.patches), alias: new Map(q.alias),
  server: new Map(q.server), tomb: new Map(q.tomb),
});

function startPatch(n: Q, fx: Effect[], scope: Scope, id: string, body: PatchBody) {
  const merged = { ...n.patches.get(id)?.body, ...defined(body) };
  n.seq += 1;
  n.patches.set(id, { scope, id, seq: n.seq, body: merged, status: "inflight" });
  fx.push({ kind: "patch", scope, id, seq: n.seq, body: merged });
}

const fail = (n: Q, scope: Scope, msg: string) => {
  n.error = { key: scope.key, msg };
};

export function reduce(q: Q, a: Action): { q: Q; fx: Effect[] } {
  const n = next(q);
  const fx: Effect[] = [];
  switch (a.type) {
    case "create": {
      n.marks.set(a.localId, {
        scope: a.scope, annotation: a.annotation, body: a.body,
        sent: { note: a.body.note, color: a.body.color }, status: "inflight",
      });
      fx.push({ kind: "create", scope: a.scope, localId: a.localId, body: a.body });
      break;
    }
    case "edit": {
      const id = n.alias.get(a.id) ?? a.id; // G2: a stale local id still works
      const m = n.marks.get(id);
      if (m) {
        if (m.status !== "cancelled") {
          n.marks.set(id, { ...m, body: { ...m.body, ...defined(a.body) } });
        }
      } else if (!n.tomb.has(id)) {
        startPatch(n, fx, a.scope, id, a.body);
      }
      break;
    }
    case "remove": {
      const id = n.alias.get(a.id) ?? a.id;
      const m = n.marks.get(id);
      if (m) {
        // No server id yet: never a request. In flight, the create's
        // success handler cleans up what the server made.
        if (m.status === "inflight") n.marks.set(id, { ...m, status: "cancelled" });
        else n.marks.delete(id);
      } else {
        n.patches.delete(id); // an edit to a deleted mark is moot
        n.tomb.set(id, n.clock);
        const list = n.server.get(a.scope.key);
        if (list) n.server.set(a.scope.key, list.filter((e) => e.a.id !== id));
        fx.push({ kind: "delete", scope: a.scope, id, cleanup: false });
      }
      break;
    }
    case "createOk": {
      const m = n.marks.get(a.localId);
      if (!m) break;
      n.marks.delete(a.localId);
      if (m.status === "cancelled") {
        fx.push({ kind: "delete", scope: m.scope, id: a.saved.id, cleanup: true });
        break;
      }
      n.server.set(m.scope.key, [
        ...(n.server.get(m.scope.key) ?? []), { a: a.saved, at: n.clock },
      ]);
      n.alias.set(a.localId, a.saved.id);
      // An edit that arrived while the POST was in flight: the server has
      // the original, so send the edit now. It stays on screen meanwhile.
      if (m.body.note !== m.sent.note || m.body.color !== m.sent.color) {
        startPatch(n, fx, m.scope, a.saved.id, { note: m.body.note, color: m.body.color });
      }
      break;
    }
    case "createFail": {
      const m = n.marks.get(a.localId);
      if (!m) break;
      if (m.status === "cancelled") {
        n.marks.delete(a.localId);
      } else {
        n.marks.set(a.localId, { ...m, status: "failed" }); // stays on screen
        fail(n, a.scope, a.error);
      }
      break;
    }
    case "patchOk": {
      const p = n.patches.get(a.id);
      if (!p || p.seq !== a.seq) break; // deleted, or a newer edit owns it
      n.patches.delete(a.id);
      const list = n.server.get(p.scope.key);
      if (list) {
        n.server.set(p.scope.key, list.map((e) => (e.a.id === a.id ? { ...e, a: a.saved } : e)));
      }
      break;
    }
    case "patchFail": {
      const p = n.patches.get(a.id);
      if (!p || p.seq !== a.seq) break; // deleted or superseded: not an error
      n.patches.set(a.id, { ...p, status: "failed" });
      fail(n, a.scope, a.error);
      break;
    }
    case "deleteFail": {
      fail(n, a.scope, a.error);
      // A 404 means our picture was stale: refetch. Merge keeps unsaved marks.
      if (!a.cleanup) fx.push({ kind: "list", scope: a.scope, issuedAt: n.clock });
      break;
    }
    case "listOk": {
      const fresh: Saved[] = a.list
        .filter((x) => (n.tomb.get(x.id) ?? -1) <= a.issuedAt)
        .map((x) => ({ a: x, at: 0 }));
      // Marks saved after this list was requested are newer than it.
      const newer = (n.server.get(a.scope.key) ?? []).filter(
        (e) => e.at > a.issuedAt && !a.list.some((x) => x.id === e.a.id),
      );
      n.server.set(a.scope.key, [...fresh, ...newer]);
      break;
    }
    case "listFail":
      fail(n, a.scope, a.error);
      break;
    case "reload":
      fx.push({ kind: "list", scope: a.scope, issuedAt: n.clock });
      break;
    case "retry": {
      n.error = null; // G1: retry always clears the banner
      let sent = 0;
      for (const [id, m] of n.marks) {
        if (m.scope.key !== a.scope.key || m.status !== "failed") continue;
        // Flip before sending so a second retry finds nothing failed.
        n.marks.set(id, { ...m, status: "inflight", sent: { note: m.body.note, color: m.body.color } });
        fx.push({ kind: "create", scope: m.scope, localId: id, body: m.body });
        sent += 1;
      }
      for (const p of [...n.patches.values()]) {
        if (p.scope.key !== a.scope.key || p.status !== "failed") continue;
        startPatch(n, fx, p.scope, p.id, p.body);
        sent += 1;
      }
      if (sent === 0) fx.push({ kind: "list", scope: a.scope, issuedAt: n.clock });
      break;
    }
  }
  return { q: n, fx };
}

/** What to render for one file: server list with queued edits overlaid,
 * then every live unsaved mark. */
export function select(q: Q, key: string) {
  const list: Annotation[] = (q.server.get(key) ?? []).map((e) => {
    const p = q.patches.get(e.a.id);
    return p ? { ...e.a, ...defined(p.body) } : e.a;
  });
  let unsaved = 0;
  for (const m of q.marks.values()) {
    if (m.scope.key !== key) continue;
    if (m.status === "failed") unsaved += 1;
    if (m.status !== "cancelled") {
      list.push({ ...m.annotation, ...defined({ note: m.body.note, color: m.body.color }) });
    }
  }
  for (const p of q.patches.values()) {
    if (p.scope.key === key && p.status === "failed") unsaved += 1;
  }
  return { list, unsaved, error: q.error?.key === key ? q.error.msg : null };
}
