#!/usr/bin/env python3
"""Paste real people (Penn-Fudan instance masks) onto backgrounds from OUR camera.

Creates training/evaluation images "a person in this camera's scene" with exact boxes,
for when the real camera has no people in view yet. Pseudo data: replace with real
photos of people at the real seats as soon as possible.

    python -I scripts/make_composites.py BACKGROUNDS OUT --count 80 --people Fudan --seed 1
    python -I scripts/make_composites.py BACKGROUNDS OUT --count 20 --people Penn  --seed 2

--people selects which Penn-Fudan subset supplies the people (Fudan* / Penn* / all), so
training and evaluation can use different persons. Writes OUT/*.jpg and
OUT/bounding_boxes.labels (Edge Impulse format, label "person").
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
PF = ROOT / "datasets" / "raw" / "PennFudanPed"


def cutouts(subset: str) -> list[Image.Image]:
    """RGBA images of single pedestrians (alpha = instance mask), tightly cropped."""
    out = []
    for img_path in sorted((PF / "PNGImages").glob("*.png")):
        if subset != "all" and not img_path.name.startswith(subset):
            continue
        img = np.array(Image.open(img_path).convert("RGB"))
        mask = np.array(Image.open(PF / "PedMasks" / img_path.name.replace(".png", "_mask.png")))
        for inst in np.unique(mask)[1:]:
            m = mask == inst
            ys, xs = np.where(m)
            if len(ys) < 2000:  # tiny / heavily occluded
                continue
            y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
            rgba = np.dstack([img[y0:y1, x0:x1], (m[y0:y1, x0:x1] * 255).astype(np.uint8)])
            out.append(Image.fromarray(rgba, "RGBA"))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("backgrounds")
    ap.add_argument("out")
    ap.add_argument("--count", type=int, default=80)
    ap.add_argument("--people", choices=["Fudan", "Penn", "all"], default="all")
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()

    rnd = random.Random(args.seed)
    bgs = sorted(p for p in Path(args.backgrounds).iterdir() if p.suffix.lower() in (".jpg", ".png"))
    people = cutouts(args.people)
    if not bgs or not people:
        raise SystemExit("need backgrounds and Penn-Fudan data (run scripts/prepare_mock_dataset.py)")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    labels = {}
    for i in range(args.count):
        bg = Image.open(rnd.choice(bgs)).convert("RGB").resize((320, 240))
        boxes = []
        for _ in range(rnd.choice([1, 1, 1, 2])):
            p = rnd.choice(people)
            h = rnd.randint(110, 230)  # seated/standing person filling a good part of the frame
            w = max(8, round(p.width * h / p.height))
            p = p.resize((w, h), Image.LANCZOS)
            # match the camera's look: brightness/contrast jitter + slight blur
            rgb = ImageEnhance.Brightness(p.convert("RGB")).enhance(rnd.uniform(0.6, 1.1))
            rgb = ImageEnhance.Contrast(rgb).enhance(rnd.uniform(0.7, 1.1)).filter(
                ImageFilter.GaussianBlur(rnd.uniform(0.3, 1.0)))
            x = rnd.randint(-w // 5, 320 - w + w // 5)
            y = rnd.randint(max(0, 240 - h - 40), max(0, 240 - h)) if h < 240 else 0
            bg.paste(rgb, (x, y), p.split()[3])
            bx0, by0 = max(0, x), max(0, y)
            bx1, by1 = min(320, x + w), min(240, y + h)
            if (bx1 - bx0) * (by1 - by0) > 0.02 * 320 * 240:
                boxes.append({"label": "person", "x": bx0, "y": by0, "width": bx1 - bx0, "height": by1 - by0})
        if not boxes:
            continue
        name = f"comp_{args.people.lower()}_{args.seed}_{i:03d}.jpg"
        bg.save(out / name, quality=88)
        labels[name] = boxes
    (out / "bounding_boxes.labels").write_text(
        json.dumps({"version": 1, "type": "bounding-box-labels", "boundingBoxes": labels}, indent=1),
        encoding="utf-8")
    print(f"{len(labels)} composites with {sum(map(len, labels.values()))} people -> {out}")


if __name__ == "__main__":
    main()
