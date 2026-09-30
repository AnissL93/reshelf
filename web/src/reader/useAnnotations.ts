// Annotation state for one file of one book.
//
// Writes are optimistic, and a failed write does NOT roll the mark back
// off the screen. Losing a highlight the user just drew is worse than
// showing one that is not yet saved: the mark stays, an error surfaces,
// and `retry` re-sends.
import { useCallback, useEffect, useState } from "react";
import {
  createAnnotation, deleteAnnotation, listAnnotations, patchAnnotation,
} from "../api";
import type { Annotation, AnnotationColor } from "../api";

type Pending = { annotation: Annotation; send: () => Promise<Annotation> };

export default function useAnnotations(sha: string, fileSha: string | null) {
  const [annotations, setAnnotations] = useState<Annotation[]>([]);
  const [pending, setPending] = useState<Pending[]>([]);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(() => {
    if (!fileSha) return;
    listAnnotations(sha, fileSha).then(setAnnotations).catch(
      (e: Error) => setError(e.message),
    );
  }, [sha, fileSha]);

  useEffect(reload, [reload]);

  const create = useCallback(
    (anchor: object, type: "highlight" | "bookmark", color: AnnotationColor, note = "") => {
      if (!fileSha) return;
      const optimistic: Annotation = {
        id: `pending-${crypto.randomUUID()}`,
        type, file_sha: fileSha, color, note,
        anchor: anchor as Annotation["anchor"],
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      };
      setAnnotations((prev) => [...prev, optimistic]);
      const send = () =>
        createAnnotation(sha, { type, file_sha: fileSha, anchor, color, note });
      send()
        .then((saved) =>
          setAnnotations((prev) => prev.map((a) => (a.id === optimistic.id ? saved : a))),
        )
        .catch((e: Error) => {
          // The mark stays on screen. Deliberately.
          setError(e.message);
          setPending((p) => [...p, { annotation: optimistic, send }]);
        });
    },
    [sha, fileSha],
  );

  const update = useCallback(
    (id: string, body: { note?: string; color?: AnnotationColor }) => {
      setAnnotations((prev) => prev.map((a) => (a.id === id ? { ...a, ...body } : a)));
      patchAnnotation(sha, id, body)
        .then((saved) => setAnnotations((p) => p.map((a) => (a.id === id ? saved : a))))
        .catch((e: Error) => setError(e.message));
    },
    [sha],
  );

  const remove = useCallback(
    (id: string) => {
      setAnnotations((prev) => prev.filter((a) => a.id !== id));
      deleteAnnotation(sha, id).catch((e: Error) => {
        // A 404 means our state was stale, so refetch rather than guess.
        setError(e.message);
        reload();
      });
    },
    [sha, reload],
  );

  const retry = useCallback(() => {
    const queued = pending;
    setPending([]);
    setError(null);
    for (const item of queued) {
      item.send()
        .then((saved) =>
          setAnnotations((prev) =>
            prev.map((a) => (a.id === item.annotation.id ? saved : a)),
          ),
        )
        .catch((e: Error) => {
          setError(e.message);
          setPending((p) => [...p, item]);
        });
    }
  }, [pending]);

  return {
    annotations, create, update, remove, reload,
    error, unsaved: pending.length, retry,
  };
}
