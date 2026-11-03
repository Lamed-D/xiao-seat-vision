// Dashboard read API + layout editing.
//
//   GET /dashboard-api?view=summary[&data=test]   viewer or admin token
//   GET /dashboard-api?view=debug[&data=test]     admin token only (developer mode)
//   PUT /dashboard-api?view=layout                admin token only (ROI editor)
//
// Token header: x-dashboard-token. Developer mode in the UI only changes what is
// rendered; the debug data itself is gated here by the admin token.

import { tokenMatches } from "../_shared/auth.ts";
import { ConstraintError, json, logError, readJson } from "../_shared/http.ts";
import type { Store } from "../_shared/store.ts";
import { buildDebug, buildSummary } from "../_shared/summary.ts";
import { validateLayout } from "../_shared/validation.ts";

export interface DashboardDeps {
  viewerTokens: readonly string[];
  adminTokens: readonly string[];
  allowedOrigins: readonly string[]; // exact origins, or "*"
  store: Pick<Store, "loadDashboard" | "recentEvents" | "replaceLayout">;
  now?: () => Date;
}

function corsHeaders(req: Request, allowed: readonly string[]): Record<string, string> {
  const origin = req.headers.get("origin");
  if (!origin) return {};
  if (!allowed.includes("*") && !allowed.includes(origin)) return {};
  return {
    "access-control-allow-origin": allowed.includes("*") ? "*" : origin,
    "access-control-allow-methods": "GET, PUT, OPTIONS",
    "access-control-allow-headers": "content-type, x-dashboard-token",
    "access-control-max-age": "600",
    vary: "origin",
  };
}

export function createDashboardHandler(
  deps: DashboardDeps,
): (req: Request) => Promise<Response> {
  return async (req: Request): Promise<Response> => {
    const cors = corsHeaders(req, deps.allowedOrigins);
    if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: cors });

    if (deps.viewerTokens.length === 0 && deps.adminTokens.length === 0) {
      logError("dashboard-api", "no DASHBOARD_*_TOKENS configured");
      return json(500, { error: "server_misconfigured" }, cors);
    }
    const presented = req.headers.get("x-dashboard-token");
    const isAdmin = await tokenMatches(presented, deps.adminTokens);
    const isViewer = isAdmin || await tokenMatches(presented, deps.viewerTokens);
    if (!isViewer) return json(401, { error: "unauthorized" }, cors);

    const url = new URL(req.url);
    const view = url.searchParams.get("view") ?? "summary";
    const isTest = url.searchParams.get("data") === "test";

    try {
      if (req.method === "GET" && view === "summary") {
        const data = await deps.store.loadDashboard(isTest);
        return json(200, buildSummary(data, deps.now?.() ?? new Date()), cors);
      }
      if (req.method === "GET" && view === "debug") {
        if (!isAdmin) return json(403, { error: "admin_token_required" }, cors);
        const [data, events] = await Promise.all([
          deps.store.loadDashboard(isTest),
          deps.store.recentEvents(isTest, 50),
        ]);
        return json(200, buildDebug(data, events), cors);
      }
      if (req.method === "PUT" && view === "layout") {
        if (!isAdmin) return json(403, { error: "admin_token_required" }, cors);
        const body = await readJson(req);
        if (!body.ok) return body.response;
        const layout = validateLayout(body.value);
        if (!layout.ok) {
          return json(422, { error: "validation_failed", details: layout.errors }, cors);
        }
        await deps.store.replaceLayout(layout.value.device_id, layout.value.seats);
        return json(200, { status: "stored", seats: layout.value.seats.length }, cors);
      }
      return json(404, { error: "unknown_view_or_method" }, cors);
    } catch (err) {
      if (err instanceof ConstraintError) {
        return json(422, { error: "constraint_violation" }, cors);
      }
      logError("dashboard-api", err);
      return json(500, { error: "storage_error" }, cors);
    }
  };
}
