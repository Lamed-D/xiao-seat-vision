// Supabase Edge Function: dashboard-api (xiao-seat-vision)
// Secrets: DASHBOARD_VIEWER_TOKENS, DASHBOARD_ADMIN_TOKENS (comma-separated, >= 32 chars),
//          DASHBOARD_ALLOWED_ORIGINS (e.g. "http://localhost:8080").
// Injected by Supabase: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY.
import { parseTokens } from "../_shared/auth.ts";
import { json } from "../_shared/http.ts";
import { createSupabaseStoreFromEnv } from "../_shared/supabase_store.ts";
import { createDashboardHandler } from "./handler.ts";

const store = createSupabaseStoreFromEnv();
if (!store) console.error("[dashboard-api] SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY missing");

const allowedOrigins = (Deno.env.get("DASHBOARD_ALLOWED_ORIGINS") ?? "")
  .split(",")
  .map((o) => o.trim())
  .filter((o) => o.length > 0);

const handler = store
  ? createDashboardHandler({
    viewerTokens: parseTokens(Deno.env.get("DASHBOARD_VIEWER_TOKENS")),
    adminTokens: parseTokens(Deno.env.get("DASHBOARD_ADMIN_TOKENS")),
    allowedOrigins,
    store,
  })
  : () => Promise.resolve(json(500, { error: "server_misconfigured" }));

Deno.serve(handler);
