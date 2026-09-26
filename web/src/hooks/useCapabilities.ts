// One fetch per page load, shared by every component that needs it (the
// metadata form, the fix-metadata block, the convert button). A module-level
// cache instead of a context provider: this is the only cross-component
// shared fetch on this screen, so a provider would be scaffolding for one
// consumer shape.
import { useEffect, useState } from "react";
import type { Capabilities } from "../api";
import { getCapabilities } from "../api";

let cached: Promise<Capabilities> | null = null;

export default function useCapabilities(): Capabilities | null {
  const [caps, setCaps] = useState<Capabilities | null>(null);
  useEffect(() => {
    cached = cached ?? getCapabilities();
    cached.then(setCaps).catch(() => setCaps(null));
  }, []);
  return caps;
}
