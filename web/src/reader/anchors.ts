// Pure geometry for anchors. No DOM, no engine imports - this is the
// module that gets tested, so everything that can live here does.
// @ts-expect-error - vendored plain ESM, no type declarations upstream.
import { compare as compareCfi } from "../../vendor/foliate-js/epubcfi.js";
import type { Anchor, NormRect, PageBox, PixelRect } from "./engines/types";

const clamp = (n: number, max: number) => Math.min(max, Math.max(0, n));

/** Rotation swaps the axes a /Rotate 90 or 270 page is measured on. */
function displaySize(page: PageBox): { w: number; h: number } {
  return page.rotation === 90 || page.rotation === 270
    ? { w: page.height, h: page.width }
    : { w: page.width, h: page.height };
}

/** A drawn box in display pixels -> 0..1 of the page. Null when the drag
 * has no area: a click with no movement must not become an invisible
 * highlight that cannot be selected or deleted. */
export function normalizeRect(px: PixelRect, page: PageBox): NormRect | null {
  // A backwards drag arrives as a negative width or height. Normalize it
  // rather than storing a negative size no renderer can draw.
  const x0 = Math.min(px.x, px.x + px.width);
  const y0 = Math.min(px.y, px.y + px.height);
  const x1 = Math.max(px.x, px.x + px.width);
  const y1 = Math.max(px.y, px.y + px.height);

  const { w, h } = displaySize(page);
  if (w <= 0 || h <= 0) return null;

  // Clamp in page units, then divide once: subtracting two already
  // divided values (0.3 - 0.1) picks up float error a stored width should not.
  const cx0 = clamp(x0, w);
  const cy0 = clamp(y0, h);
  const width = (clamp(x1, w) - cx0) / w;
  const height = (clamp(y1, h) - cy0) / h;
  if (width <= 0 || height <= 0) return null;
  return [cx0 / w, cy0 / h, width, height];
}

export function denormalizeRect(rect: NormRect, page: PageBox): PixelRect {
  const { w, h } = displaySize(page);
  return {
    x: rect[0] * w,
    y: rect[1] * h,
    width: rect[2] * w,
    height: rect[3] * h,
  };
}

/** Selection rects arrive one per text run. Joining the ones that share a
 * line keeps a sentence from being drawn as a row of separate boxes. */
export function mergeRects(rects: NormRect[]): NormRect[] {
  if (rects.length === 0) return [];
  // Copy each tuple, not just the outer array: `[...rects]` shares the
  // tuples with the caller, and the merge below writes through them.
  const sorted = rects
    .map((r) => [...r] as NormRect)
    .sort((a, b) => a[1] - b[1] || a[0] - b[0]);
  const out: NormRect[] = [sorted[0]];
  for (const rect of sorted.slice(1)) {
    const last = out[out.length - 1];
    const sameLine = Math.abs(rect[1] - last[1]) < last[3] / 2;
    if (sameLine) {
      const right = Math.max(last[0] + last[2], rect[0] + rect[2]);
      last[2] = right - last[0];
      last[3] = Math.max(last[3], rect[3]);
    } else {
      out.push(rect);
    }
  }
  return out;
}

type AnyAnchor = Anchor | { kind: string; [k: string]: unknown };

/** [family, page, y] for a known anchor; null for a kind this build does
 * not know (a sidecar from a future version). */
function pdfKey(a: Anchor): [number, number] | null {
  switch (a.kind) {
    case "pdf-page": return [a.page, 0]; // top of its page
    case "pdf-area": return [a.page, a.rect[1]];
    case "pdf-text": return [a.page, Math.min(...a.rects.map((r) => r[1]))];
    default: return null;
  }
}

/** Reading order. Unknown kinds sort last; a pdf-* against an epub is
 * unorderable (annotations are bound to one file) and returns 0. */
export function compareAnchors(a: AnyAnchor, b: AnyAnchor): number {
  const known = (x: AnyAnchor) => x.kind === "epub" || pdfKey(x as Anchor) !== null;
  const ka = known(a), kb = known(b);
  if (!ka || !kb) return Number(!ka) - Number(!kb);

  if (a.kind === "epub" || b.kind === "epub") {
    if (a.kind !== b.kind) return 0;
    try {
      return compareCfi((a as Anchor & { kind: "epub" }).cfi, (b as Anchor & { kind: "epub" }).cfi);
    } catch {
      return 0; // malformed CFI: keep it, do not crash the list
    }
  }
  const [pa, ya] = pdfKey(a as Anchor)!;
  const [pb, yb] = pdfKey(b as Anchor)!;
  return pa - pb || ya - yb;
}
