// One fetch per page load, shared by every component that needs it (the
// metadata form, the fix-metadata block, the convert button, the Jobs run
// panel). A module-level cache instead of a context provider: this is the
// only cross-component shared fetch on this screen, so a provider would be
// scaffolding for one consumer shape.
//
// The cache also broadcasts: Settings.tsx changes ai.provider server-side
// and needs every other mounted useCapabilities() consumer (the resolve
// button's disabled state, the AI rematch button, ...) to pick that up
// immediately - refreshCapabilities() re-fetches once and pushes the result
// to every listener, no reload required.
import { useEffect, useState } from "react";
import type { Capabilities } from "../api";
import { getCapabilities } from "../api";

let cached: Capabilities | null = null;
let inFlight: Promise<Capabilities> | null = null;
const listeners = new Set<(caps: Capabilities | null) => void>();

function fetchAndBroadcast(): Promise<Capabilities> {
  const request = getCapabilities().then((caps) => {
    cached = caps;
    for (const listener of listeners) listener(caps);
    return caps;
  });
  inFlight = request;
  return request;
}

/** Re-fetches capabilities and pushes the result to every mounted
 * useCapabilities() consumer. Callers that don't need the value (Settings,
 * after a save) can ignore the returned promise's resolution. */
export function refreshCapabilities(): Promise<Capabilities> {
  return fetchAndBroadcast();
}

export default function useCapabilities(): Capabilities | null {
  const [caps, setCaps] = useState<Capabilities | null>(cached);
  useEffect(() => {
    listeners.add(setCaps);
    if (cached === null) {
      (inFlight ?? fetchAndBroadcast()).catch(() => setCaps(null));
    }
    return () => {
      listeners.delete(setCaps);
    };
  }, []);
  return caps;
}
