// Supabase Edge Function: log-detection (xiao-seat-vision)
// Secrets: BRIDGE_API_TOKENS (comma-separated, >= 32 chars each).
// Injected by Supabase: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY.
import { parseTokens } from "../_shared/auth.ts";
import { json } from "../_shared/http.ts";
import { createSupabaseStoreFromEnv } from "../_shared/supabase_store.ts";
import { createLogDetectionHandler } from "./handler.ts";

const store = createSupabaseStoreFromEnv();
const tokens = parseTokens(Deno.env.get("BRIDGE_API_TOKENS"));
if (!store) console.error("[log-detection] SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY missing");
if (tokens.length === 0) console.error("[log-detection] BRIDGE_API_TOKENS missing or too short");

const handler = store
  ? createLogDetectionHandler({ tokens, store })
  : () => Promise.resolve(json(500, { error: "server_misconfigured" }));

Deno.serve(handler);
