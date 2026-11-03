# Supabase

## Schema (`supabase/migrations`)
- `001_create_seat_events.sql` — `seat_events` (event_id UNIQUE, CHECKs, created_at index, RLS).
- `002_multi_seat_dashboard.sql` — adds `seat_id`, `unknown` state, `is_test`/`source`, `device_ts`;
  view `seat_current_state`; tables `device_status`, `seat_snapshots`, `seat_layouts`; private Storage
  bucket `snapshots` (JPEG, ≤ 256 KB). Existing rows stay valid (`seat_01`, camera, not test).

All tables: RLS enabled, no policies, privileges revoked from `anon`/`authenticated`.
Only Edge Functions (service role) access data.

## Edge Functions (`supabase/functions`)
| Function | Caller | Auth header | Purpose |
|---|---|---|---|
| `log-detection` | bridge | `x-bridge-token` | insert seat event (201 created / 200 duplicate) |
| `device-status` | bridge | `x-bridge-token` | online/offline (LWT) + telemetry |
| `ingest-snapshot` | bridge | `x-bridge-token` | JPEG → Storage + `seat_snapshots` row |
| `dashboard-api` | dashboard | `x-dashboard-token` | `GET ?view=summary` (viewer), `GET ?view=debug` (admin), `PUT ?view=layout` (admin) |

Responses: 400 invalid JSON, 401 bad token, 403 admin required, 405 method, 413 too large, 415 content-type,
422 validation/constraint (details), 500 storage error (no internals). Token values are never logged.

### Why `verify_jwt = false`
Callers are not Supabase Auth users. With JWT verification on, the bridge would need a project JWT in
addition to its own token. Each function instead checks its own random ≥32-char tokens (constant-time,
comma-separated lists for rotation). `supabase/config.toml` sets this per function.

## Deploy
```bash
npx supabase login
npx supabase link --project-ref <ref>
npx supabase db push
cp supabase/.env.example supabase/.env.secrets   # fill: openssl rand -hex 32
npx supabase secrets set --env-file supabase/.env.secrets
npx supabase functions deploy log-detection device-status ingest-snapshot dashboard-api
```
Smoke test (replace values):
```bash
curl -i -X POST "https://<ref>.supabase.co/functions/v1/log-detection" \
  -H "content-type: application/json" -H "x-bridge-token: <token>" \
  -d '{"event_id":"xiao-01-manual-000001","device_id":"xiao-01","seat_id":"seat_01","state":"occupied","confidence":0.9,"inference_ms":100}'
```
Repeat the same call → `200 {"status":"duplicate"}`.

## Not verified yet
Deployment to a real Supabase project was not performed (no project/credentials in this environment).
Locally verified: Deno fmt/lint/type-check, 15 handler/validation tests with an in-memory store, and the
migrations against PostgreSQL 15 with Supabase-specific objects stubbed (`supabase/tests`).
