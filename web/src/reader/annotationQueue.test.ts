import { describe, expect, it } from "vitest";
import type { Annotation } from "../api";
import { emptyQueue, reduce, select } from "./annotationQueue";
import type { Action, Effect, Q, Scope } from "./annotationQueue";

const A: Scope = { key: "b/fa", sha: "b", fileSha: "fa" };
const B: Scope = { key: "b/fb", sha: "b", fileSha: "fb" };

// A fake server plus the effect plumbing the hook does for real. Effects
// stay pending until the test settles them, so interleavings are explicit.
class Sim {
  q: Q = emptyQueue();
  pend: Effect[] = [];
  issued: Effect[] = []; // every effect ever emitted
  server = new Map<string, Annotation>();
  private n = 0;

  send(a: Action) {
    const r = reduce(this.q, a);
    this.q = r.q;
    this.pend.push(...r.fx);
    this.issued.push(...r.fx);
  }
  create(scope: Scope, note = "", localId = `pending-${++this.n}`) {
    const annotation: Annotation = {
      id: localId, type: "highlight", file_sha: scope.fileSha, color: "yellow", note,
      anchor: { kind: "pdf-page", page: 1 }, created_at: "", updated_at: "",
    };
    this.send({
      type: "create", scope, localId, annotation,
      body: { type: "highlight", file_sha: scope.fileSha, anchor: annotation.anchor, color: "yellow", note },
    });
    return localId;
  }
  take(kind: Effect["kind"], i = 0) {
    const e = this.pend.filter((x) => x.kind === kind)[i];
    if (!e) throw new Error(`no pending ${kind} #${i}`);
    this.pend.splice(this.pend.indexOf(e), 1);
    return e;
  }
  count(kind: Effect["kind"]) {
    return this.issued.filter((x) => x.kind === kind).length;
  }
  /** Settle the i-th pending effect of a kind against the fake server. */
  settle(kind: Effect["kind"], ok = true, i = 0, list?: Annotation[]) {
    const e = this.take(kind, i);
    const error = "boom";
    if (e.kind === "create") {
      if (!ok) return this.send({ type: "createFail", scope: e.scope, localId: e.localId, error });
      const id = `s${++this.n}`;
      const saved: Annotation = {
        id, type: e.body.type, file_sha: e.body.file_sha, color: e.body.color ?? "yellow",
        note: e.body.note ?? "", anchor: e.body.anchor as Annotation["anchor"],
        created_at: "", updated_at: "",
      };
      this.server.set(id, saved);
      this.send({ type: "createOk", scope: e.scope, localId: e.localId, saved });
    } else if (e.kind === "patch") {
      const cur = this.server.get(e.id);
      if (!ok || !cur) return this.send({ type: "patchFail", scope: e.scope, id: e.id, seq: e.seq, error });
      const saved = { ...cur, ...e.body };
      this.server.set(e.id, saved);
      this.send({ type: "patchOk", scope: e.scope, id: e.id, seq: e.seq, saved });
    } else if (e.kind === "delete") {
      if (ok && this.server.delete(e.id)) return;
      this.send({ type: "deleteFail", scope: e.scope, id: e.id, cleanup: e.cleanup, error });
    } else {
      const rows = list ?? [...this.server.values()].filter((a) => a.file_sha === e.scope.fileSha);
      this.send(ok
        ? { type: "listOk", scope: e.scope, issuedAt: e.issuedAt, list: rows }
        : { type: "listFail", scope: e.scope, error });
    }
  }
  view(scope: Scope) {
    return select(this.q, scope.key);
  }
}

describe("annotation queue", () => {
  it("1. edit of a pending mark shows at once and reaches the server", () => {
    const s = new Sim();
    const id = s.create(A, "one");
    s.send({ type: "edit", scope: A, id, body: { note: "two" } });
    expect(s.count("patch")).toBe(0); // no request against an id the server lacks
    expect(s.view(A).list[0].note).toBe("two");
    s.settle("create");
    expect(s.view(A).list[0].note).toBe("two"); // still on screen after the swap
    s.settle("patch");
    expect([...s.server.values()].map((a) => a.note)).toEqual(["two"]);
    expect(s.view(A).list.map((a) => a.note)).toEqual(["two"]);
  });

  it("2. delete of a pending mark leaves nothing on the server", () => {
    const s = new Sim();
    const id = s.create(A);
    s.send({ type: "remove", scope: A, id });
    expect(s.count("delete")).toBe(0);
    expect(s.view(A).list).toEqual([]);
    s.settle("create");
    s.settle("delete"); // the cleanup of what the POST made
    expect(s.server.size).toBe(0);
    expect(s.view(A).list).toEqual([]);
    expect(s.view(A).error).toBeNull();
  });

  it("3. create fails, mark deleted, retry creates nothing", () => {
    const s = new Sim();
    const id = s.create(A);
    s.settle("create", false);
    expect(s.view(A).list).toHaveLength(1); // failed save keeps the mark
    s.send({ type: "remove", scope: A, id });
    s.send({ type: "retry", scope: A });
    expect(s.count("create")).toBe(1);
    expect(s.server.size).toBe(0);
  });

  it("4. two failed creates, retry twice, exactly two annotations", () => {
    const s = new Sim();
    s.create(A, "x");
    s.create(A, "y");
    s.settle("create", false);
    s.settle("create", false);
    expect(s.view(A).unsaved).toBe(2);
    s.send({ type: "retry", scope: A });
    s.send({ type: "retry", scope: A });
    s.settle("create");
    s.settle("create");
    expect(s.pend.filter((e) => e.kind === "create")).toHaveLength(0);
    expect(s.server.size).toBe(2);
    expect(s.view(A).list).toHaveLength(2);
    expect(s.view(A).unsaved).toBe(0);
  });

  it("5. a failed delete leaves an unrelated unsaved mark visible", () => {
    const s = new Sim();
    s.create(A, "saved");
    s.settle("create");
    const savedId = s.view(A).list[0].id;
    s.create(A, "unsaved");
    s.settle("create", false);
    s.send({ type: "remove", scope: A, id: savedId });
    s.settle("delete", false);
    s.settle("list"); // the refetch the failure triggers
    const notes = s.view(A).list.map((a) => a.note).sort();
    expect(notes).toEqual(["saved", "unsaved"]);
    expect(s.view(A).error).toBe("boom");
  });

  it("6. a stale list for a previous file never displaces the current file", () => {
    const s = new Sim();
    s.send({ type: "reload", scope: A });
    s.create(B, "mine");
    s.settle("create", true, 0); // B's create (A's list is still pending)
    const stale: Annotation = {
      id: "srv-a", type: "highlight", file_sha: "fa", color: "yellow", note: "a",
      anchor: { kind: "pdf-page", page: 1 }, created_at: "", updated_at: "",
    };
    s.settle("list", true, 0, [stale]); // lands late, for file A
    expect(s.view(B).list.map((a) => a.note)).toEqual(["mine"]);
    expect(s.view(A).list.map((a) => a.id)).toEqual(["srv-a"]);
  });

  it("7. a failed patch is re-sent by retry", () => {
    const s = new Sim();
    s.create(A, "old");
    s.settle("create");
    const id = s.view(A).list[0].id;
    s.send({ type: "edit", scope: A, id, body: { note: "new" } });
    s.settle("patch", false);
    expect(s.view(A).list[0].note).toBe("new"); // edit stays on screen
    expect(s.view(A).unsaved).toBe(1);
    s.send({ type: "retry", scope: A });
    expect(s.view(A).unsaved).toBe(0); // in flight now, not stale-counted
    s.settle("patch");
    expect(s.server.get(id)?.note).toBe("new");
  });

  it("G1. retry with nothing queued clears the error and reloads", () => {
    const s = new Sim();
    s.send({ type: "reload", scope: A });
    s.settle("list", false);
    expect(s.view(A).error).toBe("boom");
    s.send({ type: "retry", scope: A });
    expect(s.view(A).error).toBeNull();
    expect(s.pend.some((e) => e.kind === "list")).toBe(true);
  });

  it("localId survives the id swap when a create settles", () => {
    const s = new Sim();
    const local = s.create(A, "x");
    const before = s.view(A).list[0];
    expect([before.id, before.localId]).toEqual([local, local]);
    s.settle("create");
    const after = s.view(A).list[0];
    expect(after.id).not.toBe(local);
    expect(after.localId).toBe(local);
    // and a server-born annotation is its own localId
    s.send({ type: "listOk", scope: A, issuedAt: 99, list: [{ ...after, id: "srv", }] });
    expect(s.view(A).list[0].localId).toBe("srv");
  });

  it("G2. edit and remove through a stale pending id reach the server id", () => {
    const s = new Sim();
    const id = s.create(A, "x");
    s.settle("create");
    const real = s.view(A).list[0].id;
    s.send({ type: "edit", scope: A, id, body: { note: "typed later" } });
    const p = s.take("patch");
    expect(p.kind === "patch" && p.id).toBe(real);
    s.send({ type: "remove", scope: A, id });
    const d = s.take("delete");
    expect(d.kind === "delete" && d.id).toBe(real);
  });

  it("G3. a file switch keeps the previous file's failed marks", () => {
    const s = new Sim();
    s.create(A, "lost?");
    s.settle("create", false);
    expect(s.view(B)).toMatchObject({ list: [], unsaved: 0 }); // B does not show A's
    expect(s.view(A).unsaved).toBe(1); // switching back restores it
    expect(s.view(A).list).toHaveLength(1);
    s.send({ type: "retry", scope: A });
    s.settle("create");
    expect(s.server.size).toBe(1);
  });

  it("a list issued before a create cannot drop the newly saved mark", () => {
    const s = new Sim();
    s.send({ type: "reload", scope: A }); // issued first
    s.create(A, "new");
    s.settle("create");
    s.settle("list", true, 0, []); // stale: does not include the new mark
    expect(s.view(A).list.map((a) => a.note)).toEqual(["new"]);
  });

  it("a list issued before a delete cannot resurrect the deleted mark", () => {
    const s = new Sim();
    s.create(A, "gone");
    s.settle("create");
    const saved = [...s.server.values()];
    const id = saved[0].id;
    s.send({ type: "reload", scope: A });
    s.send({ type: "remove", scope: A, id });
    s.settle("delete");
    s.settle("list", true, 0, saved);
    expect(s.view(A).list).toEqual([]);
  });

  it("a patch failing for a since-deleted mark raises no error", () => {
    const s = new Sim();
    s.create(A, "x");
    s.settle("create");
    const id = s.view(A).list[0].id;
    s.send({ type: "edit", scope: A, id, body: { note: "y" } });
    s.send({ type: "remove", scope: A, id });
    s.settle("patch", false);
    expect(s.view(A).error).toBeNull();
  });
});
