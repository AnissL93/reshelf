// The one colour table. The engines need a translucent fill to paint
// with and the UI needs a solid chip to click; keeping both here stops
// "blue" meaning one thing in the overlay and another in the sidebar.
import type { AnnotationColor } from "./engines/types";

export const COLORS: AnnotationColor[] = ["yellow", "green", "blue", "pink"];

/** Painted over the page, so translucent. */
export const FILL: Record<AnnotationColor, string> = {
  yellow: "rgba(255, 214, 0, 0.35)",
  green: "rgba(0, 200, 83, 0.30)",
  blue: "rgba(41, 121, 255, 0.28)",
  pink: "rgba(255, 64, 129, 0.28)",
};

/** Shown as a chip in the toolbar and sidebar, so solid. */
export const SWATCH: Record<AnnotationColor, string> = {
  yellow: "#ffd600",
  green: "#00c853",
  blue: "#2979ff",
  pink: "#ff4081",
};
