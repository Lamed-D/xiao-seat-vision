import { createBridgeEndpoint } from "../_shared/bridge_endpoint.ts";
import { json } from "../_shared/http.ts";
import type { Store } from "../_shared/store.ts";
import { validateDeviceStatus } from "../_shared/validation.ts";

/**
 * POST device status (online/offline, from the MQTT status topic + LWT) and
 * periodic telemetry. Body: { device_id, kind: "status"|"telemetry", payload }.
 */
export function createDeviceStatusHandler(deps: {
  tokens: readonly string[];
  store: Pick<Store, "upsertDeviceStatus">;
}): (req: Request) => Promise<Response> {
  return createBridgeEndpoint({
    name: "device-status",
    tokens: deps.tokens,
    validate: validateDeviceStatus,
    action: async (input) => {
      await deps.store.upsertDeviceStatus(input);
      return json(200, { status: "stored", device_id: input.device_id, kind: input.kind });
    },
  });
}
