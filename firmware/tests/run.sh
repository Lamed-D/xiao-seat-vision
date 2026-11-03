#!/usr/bin/env sh
# Host unit tests for pure firmware logic (occupancy state machine, seat mapper).
# Usage (repo root):  sh firmware/tests/run.sh
# Docker (no local g++): docker run --rm -v "$PWD/firmware:/src/firmware:ro" -w /src gcc:14 sh firmware/tests/run.sh
set -eu
SRC="$(dirname "$0")/../xiao_seat_vision"
OUT="${TMPDIR:-/tmp}/xsv_test_logic"
g++ -std=c++17 -Wall -Wextra -Werror -I "$SRC" \
  "$(dirname "$0")/test_logic.cpp" "$SRC/occupancy_manager.cpp" "$SRC/seat_mapper.cpp" "$SRC/chunk_assembler.cpp" \
  -o "$OUT"
"$OUT"
