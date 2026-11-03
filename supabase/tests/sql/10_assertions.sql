-- Behavioural checks for the migrations (run after 001 + 002). Any failure raises.
\set ON_ERROR_STOP on

create or replace function pg_temp.expect_fail(sql text, label text) returns void as $$
begin
  begin
    execute sql;
  exception when others then
    raise notice 'ok (rejected): %', label;
    return;
  end;
  raise exception 'expected failure but succeeded: %', label;
end $$ language plpgsql;

-- legacy (001-shaped) insert still works and gets defaults
insert into public.seat_events (event_id, device_id, state, confidence, inference_ms)
values ('xiao-01-legacy-1', 'xiao-01', 'occupied', 0.9, 100);

do $$ declare r record; begin
  select seat_id, is_test, source into r from public.seat_events where event_id = 'xiao-01-legacy-1';
  if r.seat_id <> 'seat_01' or r.is_test or r.source <> 'camera' then
    raise exception 'legacy defaults wrong: %', r;
  end if;
end $$;

insert into public.seat_events (event_id, device_id, seat_id, state, confidence, inference_ms)
values ('xiao-01-b-1', 'xiao-01', 'seat_02', 'unknown', 0, 0),
       ('xiao-01-b-2', 'xiao-01', 'seat_02', 'empty', 1, 90);
insert into public.seat_events (event_id, device_id, seat_id, state, confidence, inference_ms, is_test, source)
values ('xiao-01-t-1', 'xiao-01', 'seat_01', 'empty', 0.8, 90, true, 'test_video');

-- idempotency: ON CONFLICT DO NOTHING keeps one row
insert into public.seat_events (event_id, device_id, state, confidence, inference_ms)
values ('xiao-01-legacy-1', 'xiao-01', 'empty', 0.5, 1) on conflict (event_id) do nothing;
do $$ begin
  if (select count(*) from public.seat_events where event_id = 'xiao-01-legacy-1') <> 1 then
    raise exception 'duplicate event stored';
  end if;
  if (select state from public.seat_events where event_id = 'xiao-01-legacy-1') <> 'occupied' then
    raise exception 'duplicate overwrote original';
  end if;
end $$;

select pg_temp.expect_fail($q$insert into public.seat_events (event_id, device_id, state, confidence, inference_ms) values ('xiao-01-x-1','xiao-01','maybe',0.5,1)$q$, 'bad state');
select pg_temp.expect_fail($q$insert into public.seat_events (event_id, device_id, state, confidence, inference_ms) values ('xiao-01-x-2','xiao-01','empty',1.5,1)$q$, 'confidence > 1');
select pg_temp.expect_fail($q$insert into public.seat_events (event_id, device_id, state, confidence, inference_ms) values ('xiao-01-x-3','xiao-01','empty','NaN',1)$q$, 'confidence NaN');
select pg_temp.expect_fail($q$insert into public.seat_events (event_id, device_id, state, confidence, inference_ms) values ('xiao-01-x-4','xiao-01','empty',0.5,-1)$q$, 'negative inference_ms');
select pg_temp.expect_fail($q$insert into public.seat_events (event_id, device_id, state, confidence, inference_ms) values ('other-1','xiao-01','empty',0.5,1)$q$, 'event_id prefix');
select pg_temp.expect_fail($q$insert into public.seat_events (event_id, device_id, seat_id, state, confidence, inference_ms) values ('xiao-01-x-5','xiao-01','seat 1','empty',0.5,1)$q$, 'seat_id format');
select pg_temp.expect_fail($q$insert into public.seat_events (event_id, device_id, state, confidence, inference_ms, is_test, source) values ('xiao-01-x-6','xiao-01','empty',0.5,1,false,'test_video')$q$, 'test/source mismatch');

-- current state view: latest per seat, live and test separated
do $$ begin
  if (select state from public.seat_current_state where seat_id = 'seat_02' and not is_test) <> 'empty' then
    raise exception 'seat_current_state did not return the latest row';
  end if;
  if (select count(*) from public.seat_current_state where is_test) <> 1 then
    raise exception 'test rows not separated';
  end if;
end $$;

-- other tables
insert into public.device_status (device_id, status) values ('xiao-01', 'online');
select pg_temp.expect_fail($q$insert into public.device_status (device_id, status) values ('xiao-02','sleepy')$q$, 'device status');
insert into public.seat_layouts (device_id, seat_id, label, roi_x, roi_y, roi_width, roi_height)
values ('xiao-01', 'seat_01', '좌석 1', 0.05, 0.25, 0.25, 0.60);
select pg_temp.expect_fail($q$insert into public.seat_layouts (device_id, seat_id, label, roi_x, roi_y, roi_width, roi_height) values ('xiao-01','seat_02','x',0.9,0.1,0.5,0.5)$q$, 'roi outside frame');
insert into public.seat_snapshots (snapshot_id, device_id, storage_path, reason, width, height)
values ('xiao-01-a-s1', 'xiao-01', 'xiao-01/2026-11-03/xiao-01-a-s1.jpg', 'periodic', 320, 240);
select pg_temp.expect_fail($q$insert into public.seat_snapshots (snapshot_id, device_id, storage_path, reason, width, height) values ('xiao-01-a-s1','xiao-01','p','periodic',320,240)$q$, 'duplicate snapshot');

-- RLS enabled and anon has no access
do $$ begin
  if exists (select 1 from pg_class c join pg_namespace n on n.oid = c.relnamespace
             where n.nspname = 'public' and c.relkind = 'r'
               and c.relname in ('seat_events','device_status','seat_snapshots','seat_layouts')
               and not c.relrowsecurity) then
    raise exception 'RLS not enabled on all tables';
  end if;
  if has_table_privilege('anon', 'public.seat_events', 'select')
     or has_table_privilege('authenticated', 'public.seat_current_state', 'select') then
    raise exception 'anon/authenticated still have privileges';
  end if;
  if not exists (select 1 from storage.buckets where id = 'snapshots' and not public) then
    raise exception 'private snapshots bucket missing';
  end if;
end $$;

select 'ALL MIGRATION CHECKS PASSED' as result;
