#!/usr/bin/env python3
"""Auto-label people in camera frames with a pretrained detector (pseudo-labels).

Runs a COCO-trained YOLOv8n on the PC (not on the board) and writes an Edge Impulse
`bounding_boxes.labels` file with the label `person`, plus contact sheets with the boxes
drawn so a human can review the labels before training.

    docker run --rm -v "$PWD/datasets:/data" -v "$PWD/scripts:/scripts:ro" python:3.12.7-slim sh -c \
      "pip install -q ultralytics==8.3.27 --extra-index-url https://download.pytorch.org/whl/cpu && \
       python /scripts/autolabel_people.py /data/real-camera/raw /data/real-camera/labeled"

Rules: confidence >= --min-conf (default 0.45), boxes smaller than 2 % of the image are
ignored, images without people are copied to <out>/no_person/ (not labelled, kept for
false-positive checks). Pseudo-labels are not ground truth: review the contact sheets.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from PIL import Image, ImageDraw
from ultralytics import YOLO

PERSON = 0  # COCO class id


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("src")
    ap.add_argument("out")
    ap.add_argument("--min-conf", type=float, default=0.45)
    ap.add_argument("--model", default="yolov8n.pt")
    args = ap.parse_args()

    src, out = Path(args.src), Path(args.out)
    (out / "no_person").mkdir(parents=True, exist_ok=True)
    model = YOLO(args.model)
    labels: dict[str, list[dict]] = {}
    review: list[tuple[Path, list[dict]]] = []
    files = sorted(p for p in src.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
    for path in files:
        img = Image.open(path).convert("RGB")
        w, h = img.size
        res = model.predict(img, conf=args.min_conf, classes=[PERSON], verbose=False)[0]
        boxes = []
        for (x1, y1, x2, y2), conf in zip(res.boxes.xyxy.tolist(), res.boxes.conf.tolist()):
            bw, bh = x2 - x1, y2 - y1
            if bw * bh < 0.02 * w * h:
                continue
            boxes.append({"label": "person", "x": int(x1), "y": int(y1), "width": int(bw), "height": int(bh),
                          "_conf": round(conf, 2)})
        if boxes:
            shutil.copyfile(path, out / path.name)
            labels[path.name] = [{k: v for k, v in b.items() if not k.startswith("_")} for b in boxes]
        else:
            shutil.copyfile(path, out / "no_person" / path.name)
        review.append((path, boxes))

    (out / "bounding_boxes.labels").write_text(
        json.dumps({"version": 1, "type": "bounding-box-labels", "boundingBoxes": labels}, indent=1),
        encoding="utf-8")

    # contact sheets (8 x 6 thumbnails each) for human review
    cols, rows, tw, th = 8, 6, 160, 120
    per = cols * rows
    for s in range(0, len(review), per):
        sheet = Image.new("RGB", (cols * tw, rows * th), (30, 30, 30))
        d = ImageDraw.Draw(sheet)
        for i, (path, boxes) in enumerate(review[s:s + per]):
            im = Image.open(path).convert("RGB")
            sx, sy = tw / im.width, th / im.height
            im = im.resize((tw, th))
            ox, oy = (i % cols) * tw, (i // cols) * th
            sheet.paste(im, (ox, oy))
            for b in boxes:
                d.rectangle([ox + b["x"] * sx, oy + b["y"] * sy, ox + (b["x"] + b["width"]) * sx,
                             oy + (b["y"] + b["height"]) * sy], outline=(255, 120, 0), width=2)
                d.text((ox + b["x"] * sx + 2, oy + b["y"] * sy + 1), f'{b["_conf"]}', fill=(255, 255, 0))
            d.text((ox + 2, oy + th - 11), f"{s + i}", fill=(255, 255, 255))
        sheet.save(out / f"review_{s // per:02d}.jpg", quality=85)

    n_box = sum(len(v) for v in labels.values())
    print(f"{len(files)} images: {len(labels)} with people ({n_box} boxes), "
          f"{len(files) - len(labels)} without -> {out}")


if __name__ == "__main__":
    main()
