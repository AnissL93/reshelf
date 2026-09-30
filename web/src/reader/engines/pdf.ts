// pdf.js behind the Engine interface. Scrolling canvas, one canvas per
// page, an absolutely-positioned overlay per page for highlights, and
// pdf.js's own text layer on top where the PDF has one.
//
// 64% of this library's PDFs are scans with no text layer, so the area
// tool - not text selection - is the interaction that has to work.
import * as pdfjs from "pdfjs-dist";
import type { PDFDocumentProxy, PDFPageProxy } from "pdfjs-dist";
import { TextLayer } from "pdfjs-dist";
import { denormalizeRect, mergeRects, normalizeRect } from "../anchors";
import { FILL } from "../colors";
import type {
  Anchor, Annotation, Engine, Locator, NormRect, PageBox,
} from "./types";

pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url,
).toString();

type PageView = {
  num: number;
  box: PageBox;
  scale: number;
  wrapper: HTMLDivElement;
  overlay: HTMLDivElement;
};

export class PdfEngine implements Engine {
  readonly supportsArea = true;

  private doc: PDFDocumentProxy | null = null;
  private host: HTMLElement | null = null;
  private views = new Map<number, PageView>();
  private annotations: Annotation[] = [];
  private current = 1;
  private scale: number;
  private areaMode = false;
  private onText: ((a: Anchor, t: string) => void) | null = null;
  private onArea: ((a: Anchor) => void) | null = null;
  private cleanup: (() => void)[] = [];

  /** The shell owns the zoom level, so it is passed in rather than
   * defaulted here - otherwise the first zoom click jumps from the
   * engine's private default to the shell's. */
  constructor(scale = 1.4) {
    this.scale = scale;
  }

  async mount(host: HTMLElement, fileUrl: string): Promise<void> {
    this.host = host;
    host.classList.add("pdf-host");
    // Range requests are the point of A's file endpoint: a 200MB scan
    // must not be downloaded whole before the first page shows.
    this.doc = await pdfjs.getDocument({
      url: fileUrl,
      rangeChunkSize: 1 << 18,
    }).promise;

    for (let num = 1; num <= this.doc.numPages; num++) {
      await this.renderPage(num);
    }
    this.watchScroll();
    this.watchSelection();
  }

  private async renderPage(num: number): Promise<void> {
    const page: PDFPageProxy = await this.doc!.getPage(num);
    const viewport = page.getViewport({ scale: this.scale });

    const wrapper = document.createElement("div");
    wrapper.className = "pdf-page";
    wrapper.dataset.page = String(num);
    wrapper.style.position = "relative";
    wrapper.style.width = `${viewport.width}px`;
    wrapper.style.height = `${viewport.height}px`;

    const canvas = document.createElement("canvas");
    canvas.width = Math.floor(viewport.width * devicePixelRatio);
    canvas.height = Math.floor(viewport.height * devicePixelRatio);
    canvas.style.width = `${viewport.width}px`;
    canvas.style.height = `${viewport.height}px`;
    wrapper.append(canvas);

    const overlay = document.createElement("div");
    overlay.className = "pdf-overlay";
    Object.assign(overlay.style, {
      position: "absolute", inset: "0", pointerEvents: "none",
    });
    wrapper.append(overlay);

    this.host!.append(wrapper);

    const ctx = canvas.getContext("2d")!;
    ctx.scale(devicePixelRatio, devicePixelRatio);
    await page.render({ canvasContext: ctx, viewport, canvas }).promise;

    // A scanned page has no text content; TextLayer then renders nothing
    // and selection simply never fires. That is the intended behaviour,
    // not a case to special-case.
    const textDiv = document.createElement("div");
    textDiv.className = "textLayer";
    textDiv.style.position = "absolute";
    textDiv.style.inset = "0";
    // setProperty, not Object.assign: assigning a custom property onto
    // the style object writes an ordinary JS property that never reaches
    // CSS, leaving pdf.js's spans laid out at scale 1 over a canvas
    // rendered at `this.scale` - every selection off by the zoom factor.
    textDiv.style.setProperty("--scale-factor", String(this.scale));
    wrapper.append(textDiv);
    const textContent = await page.getTextContent();
    if (textContent.items.length) {
      await new TextLayer({ textContentSource: textContent, container: textDiv, viewport })
        .render();
    }

    // The rotation the page declares, which is what anchors normalize
    // against - not the viewport's post-rotation size.
    const raw = page.getViewport({ scale: 1, rotation: 0 });
    this.views.set(num, {
      num,
      box: {
        width: raw.width,
        height: raw.height,
        rotation: ((page.rotate % 360) + 360) % 360 as PageBox["rotation"],
      },
      scale: this.scale,
      wrapper,
      overlay,
    });

    this.attachAreaTool(this.views.get(num)!);
  }

  // -- area highlights: the primary interaction on a scanned PDF --------

  private attachAreaTool(view: PageView): void {
    let start: { x: number; y: number } | null = null;
    let ghost: HTMLDivElement | null = null;

    const rectOf = (e: PointerEvent) => {
      const r = view.wrapper.getBoundingClientRect();
      return { x: e.clientX - r.left, y: e.clientY - r.top };
    };

    const down = (e: PointerEvent) => {
      if (!this.areaMode || e.button !== 0) return;
      e.preventDefault();
      start = rectOf(e);
      ghost = document.createElement("div");
      Object.assign(ghost.style, {
        position: "absolute", border: "2px dashed #2979ff",
        background: "rgba(41,121,255,0.12)", pointerEvents: "none",
      });
      view.wrapper.append(ghost);
      view.wrapper.setPointerCapture(e.pointerId);
    };

    const move = (e: PointerEvent) => {
      if (!start || !ghost) return;
      const now = rectOf(e);
      Object.assign(ghost.style, {
        left: `${Math.min(start.x, now.x)}px`,
        top: `${Math.min(start.y, now.y)}px`,
        width: `${Math.abs(now.x - start.x)}px`,
        height: `${Math.abs(now.y - start.y)}px`,
      });
    };

    const up = (e: PointerEvent) => {
      if (!start) return;
      const end = rectOf(e);
      ghost?.remove();
      ghost = null;
      const px = {
        x: start.x / view.scale,
        y: start.y / view.scale,
        width: (end.x - start.x) / view.scale,
        height: (end.y - start.y) / view.scale,
      };
      start = null;
      // normalizeRect returns null for a click with no drag. Dropping it
      // here is what stops a stray click becoming an invisible highlight.
      const rect = normalizeRect(px, view.box);
      if (rect) this.onArea?.({ kind: "pdf-area", page: view.num, rect });
    };

    view.wrapper.addEventListener("pointerdown", down);
    view.wrapper.addEventListener("pointermove", move);
    view.wrapper.addEventListener("pointerup", up);
    this.cleanup.push(() => {
      view.wrapper.removeEventListener("pointerdown", down);
      view.wrapper.removeEventListener("pointermove", move);
      view.wrapper.removeEventListener("pointerup", up);
    });
  }

  setAreaMode(on: boolean): void {
    this.areaMode = on;
    this.host?.classList.toggle("area-mode", on);
  }

  // -- text selection: only fires where the PDF has a text layer --------

  private watchSelection(): void {
    const handler = () => {
      const sel = document.getSelection();
      if (!sel || sel.isCollapsed || !this.host) return;
      const text = sel.toString().trim();
      if (!text) return;
      const wrapper = (sel.anchorNode?.parentElement)?.closest<HTMLElement>(".pdf-page");
      if (!wrapper) return;
      const view = this.views.get(Number(wrapper.dataset.page));
      if (!view) return;

      const base = wrapper.getBoundingClientRect();
      const rects: NormRect[] = [];
      for (const r of Array.from(sel.getRangeAt(0).getClientRects())) {
        const norm = normalizeRect({
          x: (r.left - base.left) / view.scale,
          y: (r.top - base.top) / view.scale,
          width: r.width / view.scale,
          height: r.height / view.scale,
        }, view.box);
        if (norm) rects.push(norm);
      }
      if (!rects.length) return;
      this.onText?.(
        { kind: "pdf-text", page: view.num, rects: mergeRects(rects), text },
        text,
      );
    };
    document.addEventListener("selectionchange", handler);
    this.cleanup.push(() => document.removeEventListener("selectionchange", handler));
  }

  // -- painting ---------------------------------------------------------

  paint(annotations: Annotation[]): void {
    this.annotations = annotations;
    for (const view of this.views.values()) view.overlay.replaceChildren();
    for (const ann of annotations) {
      const anchor = ann.anchor as Anchor;
      if (anchor.kind === "pdf-area") {
        this.drawRect(anchor.page, anchor.rect, ann);
      } else if (anchor.kind === "pdf-text") {
        for (const rect of anchor.rects) this.drawRect(anchor.page, rect, ann);
      }
      // Any other kind - including one from a future version - is simply
      // not drawn here. The sidebar still lists it.
    }
  }

  private drawRect(page: number, rect: NormRect, ann: Annotation): void {
    const view = this.views.get(page);
    if (!view) return;
    const px = denormalizeRect(rect, view.box);
    const el = document.createElement("div");
    el.dataset.annotation = ann.id;
    Object.assign(el.style, {
      position: "absolute",
      left: `${px.x * view.scale}px`,
      top: `${px.y * view.scale}px`,
      width: `${px.width * view.scale}px`,
      height: `${px.height * view.scale}px`,
      background: FILL[ann.color] ?? FILL.yellow,
      borderRadius: "2px",
    });
    view.overlay.append(el);
  }

  // -- navigation and position ------------------------------------------

  async goTo(anchor: Anchor): Promise<void> {
    const page = "page" in anchor ? anchor.page : 1;
    this.views.get(page)?.wrapper.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  locate(): Locator {
    const total = this.doc?.numPages ?? 1;
    return { locator: `page=${this.current}`, percent: this.current / total };
  }

  private watchScroll(): void {
    const observer = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting) {
          this.current = Number((entry.target as HTMLElement).dataset.page);
        }
      }
    }, { threshold: 0.5 });
    for (const view of this.views.values()) observer.observe(view.wrapper);
    this.cleanup.push(() => observer.disconnect());
  }

  async setScale(scale: number): Promise<void> {
    this.scale = scale;
    this.host!.replaceChildren();
    this.views.clear();
    for (let num = 1; num <= (this.doc?.numPages ?? 0); num++) {
      await this.renderPage(num);
    }
    this.paint(this.annotations);
  }

  onTextSelect(cb: (a: Anchor, t: string) => void): void {
    this.onText = cb;
  }

  onAreaSelect(cb: (a: Anchor) => void): void {
    this.onArea = cb;
  }

  destroy(): void {
    for (const fn of this.cleanup) fn();
    this.cleanup = [];
    this.views.clear();
    // pdfjs-dist 6.3 has no PDFDocumentProxy.destroy(); the loading task
    // owns teardown of the document and its worker.
    void this.doc?.loadingTask.destroy();
    this.doc = null;
    this.host?.replaceChildren();
  }
}
