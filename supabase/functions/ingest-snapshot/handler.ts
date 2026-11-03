import { createBridgeEndpoint } from "../_shared/bridge_endpoint.ts";
import { json } from "../_shared/http.ts";
import type { Store } from "../_shared/store.ts";
import { validateSnapshot } from "../_shared/validation.ts";

/** Max request size: base64 JPEG (<= 256 KB raw) + metadata. */
export const SNAPSHOT_MAX_BODY = 400 * 1024;

/**
 * POST a JPEG snapshot (base64 JSON from the MQTT snapshot topic).
 * Stores the image in the private `snapshots` bucket and a row in seat_snapshots.
 */
export function createIngestSnapshotHandler(deps: {
  tokens: readonly string[];
  store: Pick<Store, "saveSnapshot">;
}): (req: Request) => Promise<Response> {
  return createBridgeEndpoint({
    name: "ingest-snapshot",
    tokens: deps.tokens,
    maxBytes: SNAPSHOT_MAX_BODY,
    validate: validateSnapshot,
    action: async (snap) => {
      const outcome = await deps.store.saveSnapshot(snap);
      return outcome === "created"
        ? json(201, { status: "created", snapshot_id: snap.snapshot_id })
        : json(200, { status: "duplicate", snapshot_id: snap.snapshot_id });
    },
  });
}
