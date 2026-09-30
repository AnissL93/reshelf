// The reader shell talks to this and never to pdf.js or foliate-js
// directly. Both engines implement it; the shell stays format-blind.

/** Width and height in the PDF's own units, plus the page's /Rotate.
 * Rotation matters: a scanned page is often stored sideways with
 * /Rotate 90, so the box the user drew and the box the page stores are
 * in different frames. */
export type PageBox = {
  width: number;
  height: number;
  rotation: 0 | 90 | 180 | 270;
};

/** [x, y, width, height], each 0..1 of the page, origin top-left of the
 * page as displayed. Normalized so a highlight survives zoom, resize and
 * device pixel ratio. */
export type NormRect = [number, number, number, number];

export type PixelRect = { x: number; y: number; width: number; height: number };

export type Anchor =
  | { kind: "pdf-text"; page: number; rects: NormRect[]; text: string }
  | { kind: "pdf-area"; page: number; rect: NormRect }
  | { kind: "pdf-page"; page: number }
  | { kind: "epub"; cfi: string; text?: string };

export type Locator = { locator: string; percent: number };

export type AnnotationColor = "yellow" | "green" | "blue" | "pink";

/** The one definition. `api.ts` re-exports it rather than declaring a
 * second one - two spellings of the same record is how a colour becomes
 * a bare string on one side and a union on the other. */
export type Annotation = {
  id: string;
  type: "highlight" | "bookmark";
  file_sha: string;
  color: AnnotationColor;
  note: string;
  /** Opaque to the server, which stores and returns it verbatim. That is
   * what lets an anchor kind this build does not know survive a write. */
  anchor: Anchor | { kind: string; [k: string]: unknown };
  created_at: string;
  updated_at: string;
};

export interface Engine {
  /** False for EPUB: reflowable text has no stable page geometry to box,
   * so the shell hides the area tool rather than offering a dead control. */
  readonly supportsArea: boolean;
  mount(host: HTMLElement, fileUrl: string): Promise<void>;
  goTo(anchor: Anchor): Promise<void>;
  locate(): Locator;
  paint(annotations: Annotation[]): void;
  onTextSelect(cb: (anchor: Anchor, text: string) => void): void;
  onAreaSelect(cb: (anchor: Anchor) => void): void;
  destroy(): void;
}
