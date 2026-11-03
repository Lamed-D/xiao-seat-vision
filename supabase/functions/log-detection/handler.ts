import { createBridgeEndpoint } from "../_shared/bridge_endpoint.ts";
import { json } from "../_shared/http.ts";
import type { Store } from "../_shared/store.ts";
import { validateSeatEvent } from "../_shared/validation.ts";

/**
 * POST seat state-change events (from the MQTT bridge).
 * 201 created | 200 duplicate (same event_id, idempotent) | 401 | 405 | 413 | 415 | 400 | 422 | 500
 */
export function createLogDetectionHandler(deps: {
  tokens: readonly string[];
  store: Pick<Store, "insertSeatEvent">;
}): (req: Request) => Promise<Response> {
  return createBridgeEndpoint({
    name: "log-detection",
    tokens: deps.tokens,
    validate: validateSeatEvent,
    action: async (row) => {
      const outcome = await deps.store.insertSeatEvent(row);
      return outcome === "created"
        ? json(201, { status: "created", event_id: row.event_id })
        : json(200, { status: "duplicate", event_id: row.event_id });
    },
  });
}
