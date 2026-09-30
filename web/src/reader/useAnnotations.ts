// Annotation state for one file of one book. A thin shell: React state,
// the network, and nothing else. Every decision (what the queue becomes,
// what to send, what to render) is in annotationQueue.ts, which is pure
// and tested. See there for the rule that a failed save never removes a
// mark from the screen.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  createAnnotation, deleteAnnotation, listAnnotations, patchAnnotation,
} from "../api";
import type { Annotation, AnnotationColor } from "../api";
import { emptyQueue, LOCAL_PREFIX, reduce, select } from "./annotationQueue";
import type { Action, Effect, PatchBody, Q, Scope } from "./annotationQueue";

const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e));

export default function useAnnotations(sha: string, fileSha: string | null) {
  const [q, setQ] = useState<Q>(emptyQueue);
  const qRef = useRef<Q>(q); // synchronous truth; state is only for rendering
  const scope = useMemo<Scope>(
    () => ({ key: `${sha}/${fileSha ?? ""}`, sha, fileSha: fileSha ?? "" }),
    [sha, fileSha],
  );

  const dispatch = useCallback(function dispatch(action: Action) {
    const { q: nextQ, fx } = reduce(qRef.current, action);
    qRef.current = nextQ;
    setQ(nextQ);
    for (const e of fx) run(e, dispatch);
  }, []);

  useEffect(() => {
    if (scope.fileSha) dispatch({ type: "reload", scope });
  }, [scope, dispatch]);

  const create = useCallback(
    (anchor: object, type: "highlight" | "bookmark", color: AnnotationColor, note = "") => {
      const s = scope;
      if (!s.fileSha) return;
      const localId = `${LOCAL_PREFIX}${crypto.randomUUID()}`;
      const now = new Date().toISOString();
      const annotation: Annotation = {
        id: localId, type, file_sha: s.fileSha, color, note,
        anchor: anchor as Annotation["anchor"], created_at: now, updated_at: now,
      };
      dispatch({
        type: "create", scope: s, localId, annotation,
        body: { type, file_sha: s.fileSha, anchor, color, note },
      });
    },
    [dispatch, scope],
  );
  const update = useCallback(
    (id: string, body: PatchBody) => dispatch({ type: "edit", scope, id, body }),
    [dispatch, scope],
  );
  const remove = useCallback(
    (id: string) => dispatch({ type: "remove", scope, id }),
    [dispatch, scope],
  );
  const reload = useCallback(() => dispatch({ type: "reload", scope }), [dispatch, scope]);
  const retry = useCallback(() => dispatch({ type: "retry", scope }), [dispatch, scope]);

  const view = select(q, scope.key);
  return {
    annotations: view.list, create, update, remove, reload,
    error: view.error, unsaved: view.unsaved, retry,
  };
}

// Perform one effect and report the outcome back as an action.
function run(e: Effect, dispatch: (a: Action) => void) {
  const { scope } = e;
  switch (e.kind) {
    case "create":
      createAnnotation(scope.sha, e.body).then(
        (saved) => dispatch({ type: "createOk", scope, localId: e.localId, saved }),
        (err: unknown) =>
          dispatch({ type: "createFail", scope, localId: e.localId, error: errMsg(err) }),
      );
      break;
    case "patch":
      patchAnnotation(scope.sha, e.id, e.body).then(
        (saved) => dispatch({ type: "patchOk", scope, id: e.id, seq: e.seq, saved }),
        (err: unknown) =>
          dispatch({ type: "patchFail", scope, id: e.id, seq: e.seq, error: errMsg(err) }),
      );
      break;
    case "delete":
      deleteAnnotation(scope.sha, e.id).catch((err: unknown) =>
        dispatch({ type: "deleteFail", scope, id: e.id, cleanup: e.cleanup, error: errMsg(err) }),
      );
      break;
    case "list":
      listAnnotations(scope.sha, scope.fileSha).then(
        (list) => dispatch({ type: "listOk", scope, issuedAt: e.issuedAt, list }),
        (err: unknown) => dispatch({ type: "listFail", scope, error: errMsg(err) }),
      );
      break;
  }
}
