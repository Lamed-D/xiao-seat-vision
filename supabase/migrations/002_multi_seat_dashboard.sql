-- 002: multi-seat events, unknown state, test-video source, device status/telemetry,
--      JPEG snapshots (Storage) and dashboard seat layouts.
-- Backward compatible: existing 001 rows become seat_id='seat_01', is_test=false, source='camera'.

-- ---------------------------------------------------------------- seat_events
alter table public.seat_events
  add column if not exists seat_id   text        not null default 'seat_01',
  add column if not exists is_test   boolean     not null default false,
  add column if not exists source    text        not null default 'camera',
  add column if not exists device_ts timestamptz;

alter table public.seat_events drop constraint if exists seat_events_state_check;
alter table public.seat_events
  add constraint seat_events_state_check check (state in ('empty', 'occupied', 'unknown')),
  add constraint seat_events_seat_id_format check (seat_id ~ '^[A-Za-z0-9_-]{1,32}$'),
  add constraint seat_events_source_check check (source in ('camera', 'test_video')),
  add constraint seat_events_test_source_consistency check ((source = 'test_video') = is_test);

create index if not exists seat_events_device_seat_created_idx
  on public.seat_events (device_id, seat_id, created_at desc);

-- Latest state per (device, seat). security_invoker: respects the caller's RLS.
create or replace view public.seat_current_state
  with (security_invoker = true) as
select distinct on (device_id, seat_id, is_test)
  device_id, seat_id, is_test, source, state, confidence, inference_ms,
  event_id, device_ts, created_at
from public.seat_events
order by device_id, seat_id, is_test, created_at desc, id desc;

revoke all on public.seat_current_state from anon, authenticated;

-- -------------------------------------------------------------- device_status
create table if not exists public.device_status (
  device_id         text primary key,
  status            text not null default 'unknown',
  status_changed_at timestamptz,
  telemetry         jsonb,
  telemetry_at      timestamptz,
  last_seen_at      timestamptz not null default now(),
  constraint device_status_device_id_format check (device_id ~ '^[A-Za-z0-9_-]{1,32}$'),
  constraint device_status_status_check check (status in ('online', 'offline', 'unknown'))
);
alter table public.device_status enable row level security;
revoke all on table public.device_status from anon, authenticated;

-- ------------------------------------------------------------- seat_snapshots
create table if not exists public.seat_snapshots (
  id           bigint generated always as identity primary key,
  snapshot_id  text not null,
  device_id    text not null,
  storage_path text not null,
  reason       text not null,
  is_test      boolean not null default false,
  source       text not null default 'camera',
  width        integer not null,
  height       integer not null,
  seats        jsonb not null default '[]'::jsonb,
  detections   jsonb not null default '[]'::jsonb,
  device_ts    timestamptz,
  created_at   timestamptz not null default now(),
  constraint seat_snapshots_snapshot_id_key unique (snapshot_id),
  constraint seat_snapshots_snapshot_id_format check (snapshot_id ~ '^[A-Za-z0-9_-]{1,128}$'),
  constraint seat_snapshots_device_id_format check (device_id ~ '^[A-Za-z0-9_-]{1,32}$'),
  constraint seat_snapshots_reason_check check (reason in ('state_change', 'periodic')),
  constraint seat_snapshots_source_check check (source in ('camera', 'test_video')),
  constraint seat_snapshots_test_source_consistency check ((source = 'test_video') = is_test),
  constraint seat_snapshots_size_check check (width between 1 and 1920 and height between 1 and 1920)
);
create index if not exists seat_snapshots_device_created_idx
  on public.seat_snapshots (device_id, created_at desc);
alter table public.seat_snapshots enable row level security;
revoke all on table public.seat_snapshots from anon, authenticated;

-- ---------------------------------------------------------------- seat_layouts
-- Desired ROI layout edited from the dashboard (normalized 0..1 coordinates).
-- NOTE: the firmware currently uses seats_config.h; syncing this table to the
-- device is a planned step (see docs/dashboard.md).
create table if not exists public.seat_layouts (
  device_id  text not null,
  seat_id    text not null,
  label      text not null,
  roi_x      real not null,
  roi_y      real not null,
  roi_width  real not null,
  roi_height real not null,
  sort_order integer not null default 0,
  updated_at timestamptz not null default now(),
  primary key (device_id, seat_id),
  constraint seat_layouts_seat_id_format check (seat_id ~ '^[A-Za-z0-9_-]{1,32}$'),
  constraint seat_layouts_device_id_format check (device_id ~ '^[A-Za-z0-9_-]{1,32}$'),
  constraint seat_layouts_label_length check (char_length(label) between 1 and 64),
  constraint seat_layouts_roi_range check (
    roi_x >= 0 and roi_y >= 0 and roi_width > 0 and roi_height > 0
    and roi_x + roi_width <= 1.0001 and roi_y + roi_height <= 1.0001)
);
alter table public.seat_layouts enable row level security;
revoke all on table public.seat_layouts from anon, authenticated;

-- ------------------------------------------------------------ storage bucket
-- Private bucket; the dashboard gets short-lived signed URLs from dashboard-api.
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('snapshots', 'snapshots', false, 262144, array['image/jpeg'])
on conflict (id) do nothing;
