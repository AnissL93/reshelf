// pdf.js behind the Engine interface. Scrolling canvas, one canvas per
// page, an absolutely-positioned overlay per page for highlights, and
// pdf.js's own text layer on top where the PDF has one.
//
// 64% of this library's PDFs are scans with no text layer, so the area
// tool - not text selection - is the interaction that has to work.
import * as pdfjs from "pdfjs-dist";
import type { PDFDocumentLoadingTask, PDFDocumentProxy, PDFPageProxy } from "pdfjs-dist";
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
  page: PDFPageProxy;
  box: PageBox;
  scale: number;
  wrapper: HTMLDivElement;
  overlay: HTMLDivElement;
  // Set only while the page is rasterised.
  canvas: HTMLCanvasElement | null;
  textDiv: HTMLDivElement | null;
  rendered: boolean;
  rendering: Promise<void> | null;
  failed: boolean;
};

/** Rasterised pages kept at once. A Letter page at 1.4x and dpr 2 is
 * ~15 MB of canvas, so eight is ~120 MB; the rest are placeholders. */
const MAX_RENDERED = 8;

const isRect = (v: unknown): v is NormRect =>
  Array.isArray(v) && v.length === 4 && v.every((n) => typeof n === "number" && Number.isFinite(n));

/** Anchors come from hand-editable sidecars: anything that does not match
 * the expected shape yields null and is simply not drawn. */
function rectsOf(anchor: Annotation["anchor"]): { page: number; rects: NormRect[] } | null {
  const a: Record<string, unknown> = anchor;
  const page = a.page;
  if (typeof page !== "number") return null;
  if (a.kind === "pdf-area") return isRect(a.rect) ? { page, rects: [a.rect] } : null;
  if (a.kind === "pdf-text" && Array.isArray(a.rects)) {
    return { page, rects: a.rects.filter(isRect) };
  }
  return null;
}

export class PdfEngine implements Engine {
  readonly supportsArea = true;

  private task: PDFDocumentLoadingTask | null = null;
  private doc: PDFDocumentProxy | null = null;
  private host: HTMLElement | null = null;
  private views = new Map<number, PageView>();
  private annotations: Annotation[] = [];
  private current = 1;
  private scale: number;
  private areaMode = false;
  private onText: ((a: Anchor, t: string) => void) | null = null;
  private onArea: ((a: Anchor) => void) | null = null;
  /** Document-level handlers (selectionchange). Removed only in destroy. */
  private cleanup: (() => void)[] = [];
  /** Teardown for things bound to the CURRENT page wrappers. Cleared and
   * rebuilt on every render pass, because setScale throws the wrappers
   * away. Kept apart from `cleanup`, which holds document-level handlers
   * that must survive a re-render. */
  private pageCleanup: (() => void)[] = [];
  /** Progress: which page is at least half on screen. */
  private observer: IntersectionObserver | null = null;
  /** Rendering: rasterises a page when it is within ~one viewport. */
  private renderObserver: IntersectionObserver | null = null;
  /** Bumped by mount, setScale and destroy. Every await in those paths
   * checks it and bails if a newer call has started. */
  private generation = 0;

  /** The shell owns the zoom level, so it is passed in rather than
   * defaulted here - otherwise the first zoom click jumps from the
   * engine's private default to the shell's. */
  constructor(scale = 1.4) {
    this.scale = scale;
  }

  async mount(host: HTMLElement, fileUrl: string): Promise<void> {
    const mine = ++this.generation;
    this.host = host;
    host.classList.add("pdf-host");
    // Range requests are the point of A's file endpoint: a 200MB scan
    // must not be downloaded whole before the first page shows.
    const task = pdfjs.getDocument({ url: fileUrl, rangeChunkSize: 1 << 18 });
    this.task = task;
    let doc: PDFDocumentProxy;
    try {
      doc = await task.promise;
    } catch (e) {
      if (this.generation !== mine) return; // destroyed mid-load
      throw e;
    }
    if (this.generation !== mine) {
      void task.destroy();
      return;
    }
    this.doc = doc;

    if (!(await this.buildPlaceholders(mine))) return;
    this.watchSelection();
    this.watchScroll();
    this.watchRender(mine);
    // Resolve once the first page is on screen, not the whole document.
    const first = this.views.get(1);
    if (first) await this.renderView(first, mine);
  }

  /** One correctly-sized, empty wrapper per page. getPage + getViewport
   * parse the page dictionary but rasterise nothing, so this is cheap, and
   * sizing each page individually keeps mixed-size PDFs from jumping as
   * they render. Returns false if superseded. */
  private async buildPlaceholders(mine: number): Promise<boolean> {
    const doc = this.doc;
    if (!doc || !this.host) return false;
    for (let num = 1; num <= doc.numPages; num++) {
      const page = await doc.getPage(num);
      if (this.generation !== mine || !this.host) return false;
      const viewport = page.getViewport({ scale: this.scale });

      const wrapper = document.createElement("div");
      wrapper.className = "pdf-page";
      wrapper.dataset.page = String(num);
      wrapper.style.position = "relative";
      wrapper.style.width = `${viewport.width}px`;
      wrapper.style.height = `${viewport.height}px`;

      const overlay = document.createElement("div");
      overlay.className = "pdf-overlay";
      Object.assign(overlay.style, {
        position: "absolute", inset: "0", pointerEvents: "none",
      });
      wrapper.append(overlay);
      this.host.append(wrapper);

      // The rotation the page declares, which is what anchors normalize
      // against - not the viewport's post-rotation size.
      const raw = page.getViewport({ scale: 1, rotation: 0 });
      const view: PageView = {
        num,
        page,
        box: {
          width: raw.width,
          height: raw.height,
          rotation: ((page.rotate % 360) + 360) % 360 as PageBox["rotation"],
        },
        scale: this.scale,
        wrapper,
        overlay,
        canvas: null,
        textDiv: null,
        rendered: false,
        rendering: null,
        failed: false,
      };
      this.views.set(num, view);
      this.attachAreaTool(view);
    }
    return true;
  }

  private renderView(view: PageView, mine: number): Promise<void> {
    if (view.rendered || view.failed) return Promise.resolve();
    view.rendering ??= this.rasterise(view, mine).finally(() => {
      view.rendering = null;
    });
    return view.rendering;
  }

  private async rasterise(view: PageView, mine: number): Promise<void> {
    const viewport = view.page.getViewport({ scale: view.scale });
    const canvas = document.createElement("canvas");
    try {
      canvas.width = Math.floor(viewport.width * devicePixelRatio);
      canvas.height = Math.floor(viewport.height * devicePixelRatio);
      canvas.style.width = `${viewport.width}px`;
      canvas.style.height = `${viewport.height}px`;
      const ctx = canvas.getContext("2d")!;
      ctx.scale(devicePixelRatio, devicePixelRatio);
      await view.page.render({ canvasContext: ctx, viewport, canvas }).promise;
      if (this.generation !== mine) return;

      // A scanned page has no text content; TextLayer then renders
      // nothing and selection simply never fires. That is the intended
      // behaviour, not a case to special-case.
      const textDiv = document.createElement("div");
      textDiv.className = "textLayer";
      textDiv.style.position = "absolute";
      textDiv.style.inset = "0";
      // setProperty, not Object.assign: assigning a custom property onto
      // the style object writes an ordinary JS property that never
      // reaches CSS, leaving pdf.js's spans laid out at scale 1 over a
      // canvas rendered at `view.scale` - every selection off by the
      // zoom factor.
      textDiv.style.setProperty("--scale-factor", String(view.scale));
      const textContent = await view.page.getTextContent();
      if (this.generation !== mine) return;
      if (textContent.items.length) {
        await new TextLayer({ textContentSource: textContent, container: textDiv, viewport })
          .render();
        if (this.generation !== mine) return;
      }

      view.wrapper.prepend(canvas);
      view.wrapper.append(textDiv);
      view.canvas = canvas;
      view.textDiv = textDiv;
      view.rendered = true;
      this.paintView(view);
      this.evictFarPages(view);
    } catch (e) {
      canvas.width = canvas.height = 0;
      if (this.generation !== mine) return;
      // One bad page must not take the document down: keep the placeholder
      // and say so.
      console.error(`pdf page ${view.num} failed to render`, e);
      view.failed = true;
      const note = document.createElement("div");
      note.textContent = "This page could not be rendered.";
      Object.assign(note.style, {
        position: "absolute", inset: "0", display: "flex",
        alignItems: "center", justifyContent: "center", color: "#888",
      });
      view.wrapper.append(note);
    }
  }

  /** Release the backing store of pages far from the one just rendered.
   * The wrapper and overlay stay, so geometry and scroll never change. */
  private evictFarPages(keep: PageView): void {
    const rendered = [...this.views.values()].filter((v) => v.rendered);
    while (rendered.length > MAX_RENDERED) {
      let far = 0;
      for (let i = 1; i < rendered.length; i++) {
        if (Math.abs(rendered[i].num - keep.num) > Math.abs(rendered[far].num - keep.num)) far = i;
      }
      const [victim] = rendered.splice(far, 1);
      if (victim === keep) continue;
      if (victim.canvas) victim.canvas.width = victim.canvas.height = 0;
      victim.canvas?.remove();
      victim.textDiv?.remove();
      victim.canvas = victim.textDiv = null;
      victim.rendered = false;
      victim.overlay.replaceChildren();
    }
  }

  private watchRender(mine: number): void {
    this.renderObserver?.disconnect();
    this.renderObserver = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        const view = this.views.get(Number((entry.target as HTMLElement).dataset.page));
        if (view) void this.renderView(view, mine);
      }
    }, { rootMargin: "100% 0px" });
    for (const view of this.views.values()) this.renderObserver.observe(view.wrapper);
  }

  // -- area highlights: the primary interaction on a scanned PDF --------

  private attachAreaTool(view: PageView): void {
    let start: { x: number; y: number } | null = null;
    let ghost: HTMLDivElement | null = null;

    const rectOf = (e: PointerEvent) => {
      const r = view.wrapper.getBoundingClientRect();
      return { x: e.clientX - r.left, y: e.clientY - r.top };
    };

    const reset = () => {
      start = null;
      ghost?.remove();
      ghost = null;
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
      const px = {
        x: start.x / view.scale,
        y: start.y / view.scale,
        width: (end.x - start.x) / view.scale,
        height: (end.y - start.y) / view.scale,
      };
      reset();
      // normalizeRect returns null for a click with no drag. Dropping it
      // here is what stops a stray click becoming an invisible highlight.
      const rect = normalizeRect(px, view.box);
      if (rect) this.onArea?.({ kind: "pdf-area", page: view.num, rect });
    };

    view.wrapper.addEventListener("pointerdown", down);
    view.wrapper.addEventListener("pointermove", move);
    view.wrapper.addEventListener("pointerup", up);
    view.wrapper.addEventListener("pointercancel", reset);
    view.wrapper.addEventListener("lostpointercapture", reset);
    this.pageCleanup.push(() => {
      view.wrapper.removeEventListener("pointercancel", reset);
      view.wrapper.removeEventListener("lostpointercapture", reset);
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

  /** Repaints pages that are currently rasterised. A page that is not
   * rasterised is skipped; it paints from `this.annotations` when it is. */
  paint(annotations: Annotation[]): void {
    this.annotations = annotations;
    for (const view of this.views.values()) if (view.rendered) this.paintView(view);
  }

  private paintView(view: PageView): void {
    view.overlay.replaceChildren();
    for (const ann of this.annotations) {
      const hit = rectsOf(ann.anchor);
      if (!hit || hit.page !== view.num) continue;
      for (const rect of hit.rects) this.drawRect(view, rect, ann);
      // Any other kind - including one from a future version - is simply
      // not drawn here. The sidebar still lists it.
    }
  }

  private drawRect(view: PageView, rect: NormRect, ann: Annotation): void {
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
    const view = this.views.get(page);
    if (!view) return;
    // Set synchronously: the scroll observer only confirms it later, and a
    // progress write in between must see the destination, not page 1.
    this.current = page;
    // Instant, not smooth: a smooth scroll to page 200 passes through every
    // page in between and current would follow it.
    view.wrapper.scrollIntoView({ behavior: "auto", block: "start" });
  }

  locate(): Locator {
    const total = this.doc?.numPages ?? 1;
    return { locator: `page=${this.current}`, percent: this.current / total };
  }

  private watchScroll(): void {
    this.observer?.disconnect();
    this.observer = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting) {
          this.current = Number((entry.target as HTMLElement).dataset.page);
        }
      }
    }, { threshold: 0.5 });
    for (const view of this.views.values()) this.observer.observe(view.wrapper);
  }

  async setScale(scale: number): Promise<void> {
    const mine = ++this.generation;
    const keep = this.current;
    this.scale = scale;
    this.teardownPages();
    this.host!.replaceChildren();
    if (!(await this.buildPlaceholders(mine))) return;
    // Placeholders are correctly sized, so this lands on the same page.
    this.views.get(keep)?.wrapper.scrollIntoView({ block: "start" });
    this.watchScroll();
    this.watchRender(mine);
    const view = this.views.get(keep);
    if (view) await this.renderView(view, mine);
  }

  private teardownPages(): void {
    this.observer?.disconnect();
    this.observer = null;
    this.renderObserver?.disconnect();
    this.renderObserver = null;
    for (const fn of this.pageCleanup) fn();
    this.pageCleanup = [];
    this.views.clear();
  }

  onTextSelect(cb: (a: Anchor, t: string) => void): void {
    this.onText = cb;
  }

  onAreaSelect(cb: (a: Anchor) => void): void {
    this.onArea = cb;
  }

  destroy(): void {
    this.generation++;
    for (const fn of this.cleanup) fn();
    this.cleanup = [];
    this.teardownPages();
    // pdfjs-dist 6.3 has no PDFDocumentProxy.destroy(); the loading task
    // owns teardown of the document and its worker.
    void this.task?.destroy();
    this.task = null;
    this.doc = null;
    this.host?.replaceChildren();
    this.host?.classList.remove("area-mode", "pdf-host");
  }
}
