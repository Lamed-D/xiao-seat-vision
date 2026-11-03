#!/usr/bin/env python3
"""Save JPEG snapshots from the XIAO over USB serial (camera test / dataset collection).

The firmware prints a frame as base64 between markers when it receives 'c':
  -----BEGIN JPEG <len> <w> <h>-----  ...  -----END JPEG-----

usage:  pip install pyserial
        python firmware/tools/capture_snapshot.py COM5 --count 20 --interval 2 --out dataset/empty

Close the Arduino IDE Serial Monitor first (only one program can open the port).
For a live browser view use camera_viewer.py.
"""

from __future__ import annotations

import argparse
import base64
import time
from pathlib import Path
from typing import Callable

import serial  # pyserial


def capture(
    port: serial.Serial,
    timeout_s: float = 10.0,
    on_log: Callable[[str], None] | None = None,
) -> tuple[bytes, int, int]:
    """Request one frame ('c') and return (jpeg, width, height).
    Other firmware log lines seen meanwhile are passed to on_log."""
    port.write(b"c")
    deadline = time.monotonic() + timeout_s
    header = None
    lines: list[str] = []
    while time.monotonic() < deadline:
        line = port.readline().decode("ascii", errors="replace").strip()
        if not line:
            continue
        if line.startswith("-----BEGIN JPEG"):
            header = line.strip("-").split()
            lines = []
        elif line.startswith("-----END JPEG") and header:
            data = base64.b64decode("".join(lines))
            expected = int(header[2])
            if len(data) != expected:
                raise ValueError(f"length mismatch: got {len(data)} expected {expected}")
            return data, int(header[3]), int(header[4])
        elif header:
            lines.append(line)
        else:
            if on_log:
                on_log(line)
            if "[snapshot] capture failed" in line:
                raise RuntimeError("device reported capture failure")
    raise TimeoutError("no snapshot received (is the firmware running? serial monitor closed?)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("port", help="serial port, e.g. COM5 or /dev/ttyACM0")
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--interval", type=float, default=1.0, help="seconds between captures")
    ap.add_argument("--out", default="snapshots")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with serial.Serial(args.port, 115200, timeout=1) as port:
        time.sleep(0.5)
        port.reset_input_buffer()
        for i in range(args.count):
            jpeg, w, h = capture(port)
            path = out / f"{time.strftime('%Y%m%d-%H%M%S')}-{i:04d}.jpg"
            path.write_bytes(jpeg)
            print(f"saved {path} ({w}x{h}, {len(jpeg)} bytes)")
            if i + 1 < args.count:
                time.sleep(args.interval)


if __name__ == "__main__":
    main()
