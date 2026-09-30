import { describe, expect, it } from "vitest";
import { denormalizeRect, mergeRects, normalizeRect } from "./anchors";
import type { NormRect, PageBox } from "./engines/types";

const A4: PageBox = { width: 600, height: 800, rotation: 0 };

describe("normalizeRect", () => {
  it("divides by the page's own size", () => {
    expect(normalizeRect({ x: 60, y: 80, width: 120, height: 160 }, A4))
      .toEqual([0.1, 0.1, 0.2, 0.2]);
  });

  it("round-trips through denormalizeRect", () => {
    const px = { x: 60, y: 80, width: 120, height: 160 };
    const back = denormalizeRect(normalizeRect(px, A4)!, A4);
    expect(back).toEqual(px);
  });

  // [RF-1] Scanned PDFs frequently mix page sizes within one file.
  it("uses the given page's size, so a different page scales differently", () => {
    const wide: PageBox = { width: 1200, height: 800, rotation: 0 };
    const px = { x: 60, y: 80, width: 120, height: 160 };
    expect(normalizeRect(px, A4)).not.toEqual(normalizeRect(px, wide));
    expect(normalizeRect(px, wide)).toEqual([0.05, 0.1, 0.1, 0.2]);
  });

  // [RF-2] Scans are often stored sideways.
  // [RF-2] The round-trip below is symmetric and would pass even with the
  // axes swapped the wrong way, so assert the swap itself first.
  it("measures a 90-degree page against its swapped axes", () => {
    const sideways: PageBox = { width: 600, height: 800, rotation: 90 };
    // Displayed, a /Rotate 90 page is 800 wide and 600 tall.
    expect(normalizeRect({ x: 400, y: 300, width: 80, height: 60 }, sideways))
      .toEqual([0.5, 0.5, 0.1, 0.1]);
  });

  it("round-trips on a rotated page", () => {
    for (const rotation of [90, 180, 270] as const) {
      const page: PageBox = { width: 600, height: 800, rotation };
      const px = { x: 60, y: 80, width: 120, height: 160 };
      const back = denormalizeRect(normalizeRect(px, page)!, page);
      expect(back.x).toBeCloseTo(px.x, 6);
      expect(back.y).toBeCloseTo(px.y, 6);
      expect(back.width).toBeCloseTo(px.width, 6);
      expect(back.height).toBeCloseTo(px.height, 6);
    }
  });

  // [RF-3] A click with no drag, or a drag up and to the left.
  it("returns null for a zero-area rect", () => {
    expect(normalizeRect({ x: 10, y: 10, width: 0, height: 50 }, A4)).toBeNull();
    expect(normalizeRect({ x: 10, y: 10, width: 50, height: 0 }, A4)).toBeNull();
  });

  it("normalizes a backwards drag instead of storing a negative size", () => {
    expect(normalizeRect({ x: 180, y: 240, width: -120, height: -160 }, A4))
      .toEqual([0.1, 0.1, 0.2, 0.2]);
  });

  it("clamps a drag that leaves the page", () => {
    const r = normalizeRect({ x: -60, y: -80, width: 1200, height: 1600 }, A4)!;
    expect(r).toEqual([0, 0, 1, 1]);
  });
});

describe("mergeRects", () => {
  it("joins rects that share a line into one box", () => {
    const merged = mergeRects([
      [0.1, 0.2, 0.3, 0.02],
      [0.4, 0.2, 0.2, 0.02],
    ]);
    expect(merged).toHaveLength(1);
    // 0.4 + 0.2 - 0.1 is not exactly 0.5 in floating point.
    [0.1, 0.2, 0.5, 0.02].forEach((v, i) => expect(merged[0][i]).toBeCloseTo(v, 9));
  });

  it("keeps rects on different lines apart", () => {
    const rects: [number, number, number, number][] = [
      [0.1, 0.2, 0.3, 0.02],
      [0.1, 0.5, 0.3, 0.02],
    ];
    expect(mergeRects(rects)).toHaveLength(2);
  });

  it("returns an empty array unchanged", () => {
    expect(mergeRects([])).toEqual([]);
  });

  // The engines call paint() repeatedly with the same annotation objects.
  // Merging in place would rewrite the stored anchor a little more on
  // every repaint until the highlight no longer matches what was saved.
  it("does not mutate the rects it was given", () => {
    const rects: NormRect[] = [
      [0.1, 0.2, 0.3, 0.02],
      [0.4, 0.2, 0.2, 0.02],
    ];
    const snapshot = structuredClone(rects);
    mergeRects(rects);
    expect(rects).toEqual(snapshot);
  });
});
