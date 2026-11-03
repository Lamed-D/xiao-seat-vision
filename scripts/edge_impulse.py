#!/usr/bin/env python3
"""Edge Impulse pipeline for xiao-seat-vision (uses EI_API_KEY from the repo .env).

    python scripts/edge_impulse.py info                         # project + sample counts
    python scripts/edge_impulse.py upload datasets/mock-person   # images + bounding_boxes.labels
    python scripts/edge_impulse.py train                         # impulse: 96x96 grayscale FOMO
    python scripts/edge_impulse.py deploy                        # Arduino library (int8) -> firmware/ei-model/

The API key is read from .env and never printed. Re-running `upload` skips files whose
name already exists in the project.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STUDIO = "https://studio.edgeimpulse.com/v1/api"
INGEST = "https://ingestion.edgeimpulse.com/api"


def api_key() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        m = re.match(r"^EI_API_KEY=(.*)$", line)
        if m and m.group(1).strip():
            return m.group(1).strip()
    sys.exit("EI_API_KEY missing in .env (see .env.example)")


KEY = api_key()


def request(method: str, url: str, body=None, headers=None, raw: bytes | None = None, timeout=120):
    hdrs = {"x-api-key": KEY, "accept": "application/json", **(headers or {})}
    data = raw
    if body is not None:
        data = json.dumps(body).encode()
        hdrs["content-type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = r.read()
            ctype = r.headers.get("content-type", "")
    except urllib.error.HTTPError as e:
        payload, ctype = e.read(), e.headers.get("content-type", "")
        if "json" not in ctype:
            raise RuntimeError(f"{method} {url} -> HTTP {e.code}: {payload[:300]!r}") from None
    if "json" in ctype:
        out = json.loads(payload or b"{}")
        if isinstance(out, dict) and out.get("success") is False:
            raise RuntimeError(f"{method} {url.split('?')[0]} failed: {out.get('error')}")
        return out
    return payload


def project() -> dict:
    projects = request("GET", f"{STUDIO}/projects")["projects"]
    if not projects:
        sys.exit("no project for this API key")
    return projects[0]


def multipart(field: str, filename: str, content: bytes, ctype: str) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; filename=\"{filename}\"\r\n"
        f"Content-Type: {ctype}\r\n\r\n"
    ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


def existing_names(pid: int) -> set[str]:
    names: set[str] = set()
    for category in ("training", "testing"):
        offset = 0
        while True:
            res = request("GET", f"{STUDIO}/{pid}/raw-data?category={category}&limit=1000&offset={offset}")
            samples = res.get("samples", [])
            names.update(s["filename"].split(".")[0] for s in samples)
            if len(samples) < 1000:
                break
            offset += 1000
    return names


def cmd_info(_args) -> None:
    p = project()
    print(f"project {p['id']} '{p['name']}' labelingMethod={p.get('labelingMethod')}")
    for category in ("training", "testing"):
        res = request("GET", f"{STUDIO}/{p['id']}/raw-data/count?category={category}")
        print(f"  {category}: {res.get('count')} samples")


def cmd_upload(args) -> None:
    p = project()
    pid = p["id"]
    if p.get("labelingMethod") != "object_detection":
        request("POST", f"{STUDIO}/{pid}", {"labelingMethod": "object_detection"})
        print("labeling method set to object_detection (bounding boxes)")
    folder = Path(args.folder)
    labels = json.loads((folder / "bounding_boxes.labels").read_text(encoding="utf-8"))["boundingBoxes"]
    have = existing_names(pid)
    ok = skipped = failed = 0
    for i, (name, boxes) in enumerate(sorted(labels.items())):
        if Path(name).stem in have:
            skipped += 1
            continue
        # deterministic ~80/20 split by file name
        category = "testing" if zlib.crc32(name.encode()) % 5 == 0 else "training"
        body, ctype = multipart("data", name, (folder / name).read_bytes(), "image/png")
        res = request("POST", f"{INGEST}/{category}/files", raw=body, headers={
            "content-type": ctype,
            "x-bounding-boxes": json.dumps(boxes),
            "x-label": "person",
        })
        files = res.get("files", []) if isinstance(res, dict) else []
        if files and all(f.get("success") for f in files):
            ok += 1
        else:
            failed += 1
            print(f"  FAILED {name}: {res}")
        if (i + 1) % 25 == 0:
            print(f"  {i + 1}/{len(labels)} ...")
    print(f"uploaded {ok}, skipped {skipped} (already in project), failed {failed}")
    cmd_info(args)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("info").set_defaults(fn=cmd_info)
    up = sub.add_parser("upload")
    up.add_argument("folder")
    up.set_defaults(fn=cmd_upload)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
