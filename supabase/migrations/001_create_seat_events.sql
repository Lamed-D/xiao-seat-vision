-- 001: seat state change events (written only by Edge Functions via service role)

create table if not exists public.seat_events (
  id            bigint generated always as identity primary key,
  event_id      text        not null,
  device_id     text        not null,
  state         text        not null,
  confidence    real        not null,
  inference_ms  integer     not null,
  created_at    timestamptz not null default now(),

  -- idempotency: QoS 1 / retries may deliver the same event more than once
  constraint seat_events_event_id_key unique (event_id),
  constraint seat_events_event_id_format check (event_id ~ '^[A-Za-z0-9_-]{1,128}$'),
  constraint seat_events_device_id_format check (device_id ~ '^[A-Za-z0-9_-]{1,32}$'),
  constraint seat_events_event_id_prefix check (starts_with(event_id, device_id || '-')),
  constraint seat_events_state_check check (state in ('empty', 'occupied')),
  -- NaN is rejected too: in PostgreSQL NaN > every number
  constraint seat_events_confidence_range check (confidence >= 0 and confidence <= 1),
  constraint seat_events_inference_ms_range check (inference_ms >= 0 and inference_ms <= 600000)
);

create index if not exists seat_events_created_at_idx
  on public.seat_events (created_at desc);
create index if not exists seat_events_device_created_idx
  on public.seat_events (device_id, created_at desc);

-- RLS on with no policies: anon/authenticated get nothing.
-- Edge Functions use the service role key, which bypasses RLS.
alter table public.seat_events enable row level security;
revoke all on table public.seat_events from anon, authenticated;

comment on table public.seat_events is
  'xiao-seat-vision: seat occupancy state changes. Inserted by the log-detection Edge Function.';
