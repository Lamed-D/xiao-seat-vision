#!/usr/bin/env sh
# Applies supabase/migrations/*.sql to a throw-away PostgreSQL 15 container and runs
# behavioural assertions. No Supabase project or secrets required.
# Usage (repo root): sh supabase/tests/run_migration_tests.sh
set -eu
NAME="xsv-migration-test-$$"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT

docker run -d --name "$NAME" -e POSTGRES_PASSWORD=test postgres:15-alpine >/dev/null
i=0
until docker exec "$NAME" pg_isready -U postgres >/dev/null 2>&1; do
  i=$((i + 1)); [ "$i" -gt 60 ] && { echo "postgres did not start"; exit 1; }
  sleep 1
done
sleep 2  # the image restarts once after initdb

run_sql() {
  echo "--> $1"
  docker exec -i "$NAME" psql -q -v ON_ERROR_STOP=1 -U postgres -d postgres < "$1"
}

run_sql "$ROOT/supabase/tests/sql/00_supabase_stubs.sql"
for f in "$ROOT"/supabase/migrations/*.sql; do run_sql "$f"; done
run_sql "$ROOT/supabase/tests/sql/10_assertions.sql"
