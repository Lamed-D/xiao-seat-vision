#!/usr/bin/env python3
"""Measure what the model ON THE BOARD detects for a folder of images.

Uses the dashboard's developer test-image endpoint (MQTT path): each image is sent to the
device as TEST_VIDEO frames and the device's own inference results are collected.

    python scripts/eval_on_device.py datasets/real-camera/eval/empty --expect 0
    python scripts/eval_on_device.py datasets/real-camera/eval/person --expect 1

Prints per-image person counts and a summary (images with >= 1 detection).
Note: each call also creates test events in Supabase (is_test=true).
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import urllib.request
from pathlib import Path

URL = "http://127.0.0.1:8080/api/dev/test-image"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("folder")
    ap.add_argument("--expect", type=int, choices=[0, 1], required=True,
                    help="0 = images contain nobody, 1 = images contain a person")
    args = ap.parse_args()

    files = sorted(p for p in Path(args.folder).iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
    if not files:
        sys.exit("no images")
    hits = 0
    for f in files:
        req = urllib.request.Request(URL, data=f.read_bytes(), method="POST",
                                     headers={"content-type": "image/jpeg"})
        body = json.load(urllib.request.urlopen(req, timeout=60))
        counts = [r.get("persons", 0) for r in body.get("results", []) if r.get("status") == "ok"]
        persons = statistics.median(counts) if counts else None
        detected = persons is not None and persons >= 1
        hits += detected
        print(f"{f.name:40s} persons per frame {counts} -> {'PERSON' if detected else 'none'}")
    rate = hits / len(files)
    label = "false-positive rate" if args.expect == 0 else "detection rate"
    print(f"\n{len(files)} images, {hits} with a detection -> {label} {rate:.0%}")


if __name__ == "__main__":
    main()
