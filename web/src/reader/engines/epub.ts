// foliate-js behind the Engine interface.
//
// EPUB is reflowable: there is no page geometry to draw a box on, so
// supportsArea is false and the shell hides the area tool. Anchors are
// CFIs, which foliate-js generates from a Range and resolves back to one.
import { SWATCH } from "../colors";
import type { Anchor, Annotation, Engine, Locator } from "./types";

// @ts-expect-error - vendored plain ESM, no type declarations upstream.
import { Overlayer } from "../../../vendor/foliate-js/overlayer.js";
// Registers <foliate-view>. Its open() takes a File and picks the zip
// reader itself, so there is no zip dependency to wire here.
import "../../../vendor/foliate-js/view.js";

type FoliateView = HTMLElement & {
  open(book: File): Promise<void>;
  init(opts: { lastLocation?: string; showTextStart?: boolean }): Promise<void>;
  goTo(target: string): Promise<unknown>;
  addAnnotation(a: { value: string }, remove?: boolean): Promise<unknown>;
  deleteAnnotation(a: { value: string }): Promise<unknown>;
  getCFI(index: number, range: Range): string;
  next(): Promise<void>;
  prev(): Promise<void>;
  close(): void;
  renderer?: { setStyles?(css: string): void };
};

const cfiOf = (ann: Annotation): string | null => {
  const a: Record<string, unknown> = ann.anchor;
  return a.kind === "epub" && typeof a.cfi === "string" ? a.cfi : null;
};

export class EpubEngine implements Engine {
  // Reflowable text has no stable page geometry, so there is nothing to
  // box. The shell hides the area tool rather than offering a dead one.
  readonly supportsArea = false;

  private view: FoliateView | null = null;
  private annotations: Annotation[] = [];
  private position: Locator = { locator: "", percent: 0 };
  private fontSize = 18;
  private onText: ((a: Anchor, t: string) => void) | null = null;
  /** Bumped by mount and destroy. mount awaits a fetch and an open, and
   * StrictMode's destroy-during-mount is certain, so each await checks it. */
  private generation = 0;

  async mount(host: HTMLElement, fileUrl: string): Promise<void> {
    const mine = ++this.generation;
    const res = await fetch(fileUrl);
    if (!res.ok) throw new Error(`epub fetch failed: ${res.status}`);
    const blob = await res.blob();
    if (this.generation !== mine) return;

    // makeBook sniffs the zip magic bytes and needs a File with a size.
    const file = new File([blob], "book.epub", { type: "application/epub+zip" });
    const view = document.createElement("foliate-view") as FoliateView;
    this.view = view;
    host.append(view);

    const on = <T,>(name: string, cb: (detail: T) => void) =>
      view.addEventListener(name, (e) => cb((e as CustomEvent<T>).detail));

    on<{ cfi: string; fraction?: number }>("relocate", (d) => {
      this.position = { locator: d.cfi, percent: d.fraction ?? 0 };
    });

    // foliate-js asks us how to draw each annotation it is showing.
    on<{ draw: (fn: unknown, opts: unknown) => void; annotation: { value: string } }>(
      "draw-annotation",
      ({ draw, annotation }) => {
        const ann = this.annotations.find((a) => cfiOf(a) === annotation.value);
        // SWATCH, not FILL (which the PDF engine uses): Overlayer.highlight
        // applies its own 0.3 group opacity, so the already-translucent FILL
        // would compound to ~0.09. A solid colour lands at ~0.30, matching PDF.
        draw(Overlayer.highlight, { color: SWATCH[ann?.color ?? "yellow"] });
      },
    );

    // A section's overlayer only exists while it is loaded, so anything
    // painted before then is drawn now. Other sections' calls are no-ops.
    on<{ index: number }>("create-overlay", () => this.addAll());

    // One event per section load, each with a fresh document.
    on<{ doc: Document; index: number }>("load", ({ doc, index }) => {
      doc.addEventListener("selectionchange", () => {
        const sel = doc.getSelection();
        if (!sel || sel.isCollapsed || sel.rangeCount === 0) return;
        const text = sel.toString().trim();
        if (!text) return;
        const cfi = view.getCFI(index, sel.getRangeAt(0));
        this.onText?.({ kind: "epub", cfi, text }, text);
      });
    });

    await view.open(file);
    if (this.generation !== mine) return;
    this.applyFontSize();
    await view.init({ showTextStart: true });
  }

  private addAll(): void {
    for (const ann of this.annotations) {
      const cfi = cfiOf(ann);
      // A pdf-* anchor on an EPUB file, or a kind from a future version,
      // is not drawable here. The sidebar still lists it.
      if (cfi) this.view?.addAnnotation({ value: cfi }).catch(() => {});
    }
  }

  paint(annotations: Annotation[]): void {
    for (const ann of this.annotations) {
      const cfi = cfiOf(ann);
      if (cfi) this.view?.deleteAnnotation({ value: cfi }).catch(() => {});
    }
    this.annotations = annotations;
    this.addAll();
  }

  async goTo(anchor: Anchor): Promise<void> {
    if (anchor.kind === "epub") await this.view?.goTo(anchor.cfi);
  }

  next(): void {
    void this.view?.next();
  }

  prev(): void {
    void this.view?.prev();
  }

  /** Reflowable text resizes rather than zooming, so this is the EPUB
   * counterpart to PdfEngine.setScale. Remembered so a call before mount
   * finishes still applies; foliate-js re-applies it on every section. */
  setFontSize(px: number): void {
    this.fontSize = px;
    this.applyFontSize();
  }

  private applyFontSize(): void {
    this.view?.renderer?.setStyles?.(`html, body { font-size: ${this.fontSize}px !important; }`);
  }

  locate(): Locator {
    return this.position;
  }

  onTextSelect(cb: (a: Anchor, t: string) => void): void {
    this.onText = cb;
  }

  onAreaSelect(): void {
    // Never fires. Part of the interface so the shell stays format-blind.
  }

  destroy(): void {
    this.generation++;
    this.view?.close();
    this.view?.remove();
    this.view = null;
  }
}
