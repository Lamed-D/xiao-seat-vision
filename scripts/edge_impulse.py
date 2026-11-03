#!/usr/bin/env python3
"""Edge Impulse pipeline for xiao-seat-vision (uses EI_API_KEY from the repo .env).

    python scripts/edge_impulse.py info                         # project + sample counts
    python scripts/edge_impulse.py upload datasets/mock-person   # images + bounding_boxes.labels
    python scripts/edge_impulse.py train                         # impulse: 96x96 grayscale FOMO
    python scripts/edge_impulse.py deploy                        # Arduino library (int8, TFLite, arena x2) -> firmware/ei-model/

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
import zipfile
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
        mime = "image/png" if name.lower().endswith(".png") else "image/jpeg"
        body, ctype = multipart("data", name, (folder / name).read_bytes(), mime)
        res = request("POST", f"{INGEST}/{category}/files", raw=body, headers={
            "content-type": ctype,
            # empty list = background-only image (teaches FOMO what is NOT a person)
            "x-bounding-boxes": json.dumps(boxes),
            "x-label": "person" if boxes else "background",
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
    verify_boxes(pid, labels)
    cmd_info(args)


def verify_boxes(pid: int, labels: dict) -> None:
    """Compare stored boxes with the label file and re-apply missing ones.
    (Observed: a few uploads succeed but end up without boxes.)"""
    fixed = mismatched = 0
    for category in ("training", "testing"):
        samples = request("GET", f"{STUDIO}/{pid}/raw-data?category={category}&limit=1000")["samples"]
        for s in samples:
            stem = s["filename"].split(".")[0]
            expected = labels.get(stem + ".png")
            if not expected or len(s.get("boundingBoxes") or []) == len(expected):
                continue
            mismatched += 1
            request("POST", f"{STUDIO}/{pid}/raw-data/{s['id']}/bounding-boxes",
                    {"boundingBoxes": expected})
            fixed += 1
    print(f"box check: {mismatched} sample(s) differed from the label file, {fixed} re-applied")


IMPULSE = {
    # fit-short == centre crop, same as the firmware's crop_and_interpolate_rgb888()
    "inputBlocks": [{"id": 1, "type": "image", "name": "Image", "title": "Image data",
                     "imageWidth": 96, "imageHeight": 96, "resizeMode": "fit-short"}],
    "dspBlocks": [{"id": 2, "type": "image", "name": "Image", "axes": ["image"], "title": "Image",
                   "implementationVersion": 1, "input": 1}],
    "learnBlocks": [{"id": 3, "type": "keras-object-detection", "name": "Object detection",
                     "dsp": [2], "title": "Object Detection (Images)"}],
}


def wait_job(pid: int, job_id: int, what: str, timeout_s: int = 3600) -> None:
    start = time.time()
    last = ""
    while time.time() - start < timeout_s:
        st = request("GET", f"{STUDIO}/{pid}/jobs/{job_id}/status")["job"]
        if st.get("finished"):
            if not st.get("finishedSuccessful"):
                stdout = request("GET", f"{STUDIO}/{pid}/jobs/{job_id}/stdout")
                tail = "\n".join(str(x.get("data", "")).strip() for x in stdout.get("stdout", [])[-15:])
                raise RuntimeError(f"{what} job {job_id} failed:\n{tail}")
            print(f"{what}: done in {int(time.time() - start)} s")
            return
        msg = f"{what}: running ({int(time.time() - start)} s)"
        if msg[:-6] != last:
            last = msg[:-6]
        time.sleep(10)
    raise TimeoutError(f"{what} job {job_id} did not finish in {timeout_s} s")


def cmd_train(args) -> None:
    pid = project()["id"]
    cur = request("GET", f"{STUDIO}/{pid}/impulse").get("impulse") or {}
    if [b.get("type") for b in cur.get("learnBlocks", [])] != ["keras-object-detection"]:
        request("POST", f"{STUDIO}/{pid}/impulse", IMPULSE)
        print("impulse created: 96x96 fit-short -> Image (grayscale) -> FOMO")
    request("POST", f"{STUDIO}/{pid}/dsp/2", {"config": {"channels": "Grayscale"}})
    job = request("POST", f"{STUDIO}/{pid}/jobs/generate-features", {"dspId": 2})["id"]
    wait_job(pid, job, "generate features")
    job = request("POST", f"{STUDIO}/{pid}/jobs/train/keras/3", {
        "mode": "visual",
        "trainingCycles": args.epochs,
        "learningRate": 0.001,
        "visualLayers": [{"type": "fomo_mobilenet_v2_a35"}],
        "selectedModelType": "int8",
    })["id"]
    wait_job(pid, job, f"train FOMO ({args.epochs} epochs)")
    meta = request("GET", f"{STUDIO}/{pid}/training/keras/3/metadata")
    for m in meta.get("modelValidationMetrics", []):
        if m.get("type") == "int8":
            print(f"int8 validation: F1/accuracy={m.get('accuracy')}, loss={m.get('loss')}")
    print("labels:", meta.get("labels"))


def cmd_deploy(args) -> None:
    pid = project()["id"]
    job = request("POST", f"{STUDIO}/{pid}/jobs/build-ondevice-model?type=arduino",
                  {"engine": args.engine, "modelType": "int8"})["id"]
    wait_job(pid, job, f"build Arduino library (int8, {args.engine})")
    data = request("GET", f"{STUDIO}/{pid}/deployment/download?type=arduino&modelType=int8&engine={args.engine}",
                   headers={"accept": "application/zip"}, timeout=300)
    if not isinstance(data, (bytes, bytearray)) or data[:2] != b"PK":
        raise RuntimeError("download did not return a zip file")
    out = ROOT / "firmware" / "ei-model"
    out.mkdir(parents=True, exist_ok=True)
    path = out / args.name
    path.write_bytes(data)
    if args.engine == "tflite" and args.arena_factor > 1:
        patch_arena(path, args.arena_factor)
    with zipfile.ZipFile(path) as zf:
        headers = [n for n in zf.namelist() if n.endswith("_inferencing.h") and n.count("/") == 2]
    print(f"saved {path.relative_to(ROOT)} ({path.stat().st_size // 1024} KB); "
          f"header: {headers[0].split('/')[-1] if headers else '?'}")


ARENA_RE = re.compile(rb"(const size_t tflite_learn_\w+_arena_size = )(\d+);")


def patch_arena(path: Path, factor: float) -> None:
    """Enlarge the TFLite tensor arena inside the exported library zip.

    Verified on XIAO ESP32S3 + esp32 core 3.3.12: with ESP-NN enabled the arena computed by
    Edge Impulse is too small for the ESP-NN scratch buffers. The EON build then overwrites
    the heap (CORRUPT HEAP, reboot loop) and the plain TFLite build fails with
    'AllocateTensors() failed'. Doubling the arena (allocated in PSRAM) fixes both cases for
    the TFLite engine; disabling ESP-NN also works but is ~26x slower (3.2 s vs 122 ms).
    """
    tmp = path.with_suffix(".tmp")
    patched = []
    with zipfile.ZipFile(path) as src, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if "/tflite-model/" in item.filename and item.filename.endswith(".h"):
                def repl(m: re.Match) -> bytes:
                    new = int(int(m.group(2)) * factor)
                    patched.append((int(m.group(2)), new))
                    return m.group(1) + str(new).encode() + b";  // xiao-seat-vision: enlarged for ESP-NN scratch"
                data = ARENA_RE.sub(repl, data)
            dst.writestr(item, data)
    tmp.replace(path)
    if not patched:
        raise RuntimeError("arena size not found in the library - check the Edge Impulse export format")
    for old, new in patched:
        print(f"tensor arena patched: {old} -> {new} bytes")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("info").set_defaults(fn=cmd_info)
    up = sub.add_parser("upload")
    up.add_argument("folder")
    up.set_defaults(fn=cmd_upload)
    tr = sub.add_parser("train")
    tr.add_argument("--epochs", type=int, default=60)
    tr.set_defaults(fn=cmd_train)
    dp = sub.add_parser("deploy")
    dp.add_argument("--name", default="xiao-seat-vision-person.zip")
    # tflite (not EON) + enlarged arena: see patch_arena() for the measured reasons
    dp.add_argument("--engine", default="tflite", choices=["tflite", "tflite-eon"])
    dp.add_argument("--arena-factor", type=float, default=2.0)
    dp.set_defaults(fn=cmd_deploy)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
