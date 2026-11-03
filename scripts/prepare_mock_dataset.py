#!/usr/bin/env python3
"""Download a public pedestrian dataset and convert it to an Edge Impulse `person` dataset.

    python -I scripts/prepare_mock_dataset.py          # -> datasets/mock-person/

MOCK DATA ONLY: Penn-Fudan street pedestrians (170 images, 423 boxes; research use,
https://www.cis.upenn.edu/~jshi/ped_html/). Good enough to exercise the full pipeline
(upload -> FOMO training -> Arduino library -> on-device inference), NOT for real seat
accuracy. Replace it with photos/videos from the real camera position later.

Output (git-ignored):
  datasets/mock-person/<image>.png
  datasets/mock-person/bounding_boxes.labels   # Edge Impulse bounding-box label file
Upload with the Edge Impulse CLI:
  npx edge-impulse-uploader --api-key <key> --category split --directory datasets/mock-person
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

URL = "https://www.cis.upenn.edu/~jshi/ped_html/PennFudanPed.zip"
ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "datasets" / "raw"
OUT = ROOT / "datasets" / "mock-person"
BOX_RE = re.compile(r"\((\d+),\s*(\d+)\)\s*-\s*\((\d+),\s*(\d+)\)")


def download() -> Path:
    RAW.mkdir(parents=True, exist_ok=True)
    zpath = RAW / "PennFudanPed.zip"
    if not zpath.exists():
        print(f"downloading {URL} ...")
        # The server omits its intermediate certificate: OpenSSL (Python) cannot build the
        # chain, while curl on Windows (schannel) fetches it. Verification stays ON either way.
        curl = shutil.which("curl")
        if curl:
            subprocess.run([curl, "-fsSL", "-o", str(zpath), URL], check=True)
        else:
            urllib.request.urlretrieve(URL, zpath)
    extract = RAW / "PennFudanPed"
    if not extract.exists():
        with zipfile.ZipFile(zpath) as zf:
            for member in zf.namelist():  # refuse path traversal
                if member.startswith("/") or ".." in Path(member).parts:
                    sys.exit(f"unsafe path in zip: {member}")
            zf.extractall(RAW)
    return extract


def main() -> None:
    src = download()
    OUT.mkdir(parents=True, exist_ok=True)
    labels: dict[str, list[dict]] = {}
    for ann in sorted((src / "Annotation").glob("*.txt")):
        text = ann.read_text(encoding="latin-1")
        image_name = ann.stem + ".png"
        image = src / "PNGImages" / image_name
        if not image.exists():
            continue
        boxes = []
        for line in text.splitlines():
            if line.startswith("Bounding box for object"):
                m = BOX_RE.search(line)
                if m:
                    x1, y1, x2, y2 = map(int, m.groups())
                    boxes.append({"label": "person", "x": x1, "y": y1,
                                  "width": x2 - x1, "height": y2 - y1})
        if boxes:
            shutil.copyfile(image, OUT / image_name)
            labels[image_name] = boxes
    (OUT / "bounding_boxes.labels").write_text(
        json.dumps({"version": 1, "type": "bounding-box-labels", "boundingBoxes": labels}, indent=1),
        encoding="utf-8",
    )
    total = sum(len(b) for b in labels.values())
    print(f"wrote {len(labels)} images, {total} person boxes -> {OUT}")


if __name__ == "__main__":
    main()
