// Common request pipeline for endpoints called by the MQTT bridge:
// POST only -> x-bridge-token -> JSON body -> validation -> action.

import { tokenMatches } from "./auth.ts";
import { ConstraintError, json, logError, readJson } from "./http.ts";
import type { Validation } from "./validation.ts";

export interface BridgeEndpointOptions<T> {
  name: string;
  tokens: readonly string[];
  maxBytes?: number;
  validate: (body: unknown) => Validation<T>;
  action: (value: T) => Promise<Response>;
}

export function createBridgeEndpoint<T>(
  opts: BridgeEndpointOptions<T>,
): (req: Request) => Promise<Response> {
  return async (req: Request): Promise<Response> => {
    if (req.method !== "POST") {
      return json(405, { error: "method_not_allowed" }, { allow: "POST" });
    }
    if (opts.tokens.length === 0) {
      // Misconfiguration: never accept requests without a configured secret.
      logError(opts.name, "no bridge token configured (BRIDGE_API_TOKENS)");
      return json(500, { error: "server_misconfigured" });
    }
    if (!(await tokenMatches(req.headers.get("x-bridge-token"), opts.tokens))) {
      return json(401, { error: "unauthorized" });
    }

    const body = await readJson(req, opts.maxBytes);
    if (!body.ok) return body.response;

    const result = opts.validate(body.value);
    if (!result.ok) {
      return json(422, { error: "validation_failed", details: result.errors });
    }

    try {
      return await opts.action(result.value);
    } catch (err) {
      if (err instanceof ConstraintError) {
        return json(422, { error: "constraint_violation", details: [err.message] });
      }
      logError(opts.name, err);
      return json(500, { error: "storage_error" });
    }
  };
}
