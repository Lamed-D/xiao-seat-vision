"""Per-person thumbnails: seat assignment and crops from FOMO detections.

Same rules as supabase/functions/_shared/summary.ts (buildPeople) and the firmware's
seat mapper: a person belongs to the seat whose ROI contains its centre (nearest ROI
centre wins). FOMO only returns centres, so the crop is a fixed window around it.
"""

from __future__ import annotations

import io
from typing import Any

from PIL import Image

CROP_W = 0.30
CROP_H = 0.60
THUMB_MIN_HEIGHT = 240  # px; small crops are upscaled for display


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def centres(detections: list[Any]) -> list[tuple[float, float, float | None]]:
    """(cx, cy, confidence) per valid detection, sorted left to right."""
    out = []
    for d in detections or []:
        if not isinstance(d, dict):
            continue
        cx, cy = _num(d.get("cx")), _num(d.get("cy"))
        if cx is None or cy is None:
            x, y, w, h = (_num(d.get(k)) for k in ("x", "y", "width", "height"))
            if None in (x, y, w, h):
                continue
            cx, cy = x + w / 2, y + h / 2
        out.append((cx, cy, _num(d.get("confidence"))))
    return sorted(out, key=lambda c: c[0])


def seat_for(cx: float, cy: float, seats: list[dict]) -> dict | None:
    best, best_dist = None, float("inf")
    for s in seats:
        r = s.get("roi")
        if not r:
            continue
        if not (r["x"] <= cx <= r["x"] + r["width"] and r["y"] <= cy <= r["y"] + r["height"]):
            continue
        dist = ((cx - (r["x"] + r["width"] / 2)) / r["width"]) ** 2 + \
               ((cy - (r["y"] + r["height"] / 2)) / r["height"]) ** 2
        if dist < best_dist:
            best, best_dist = s, dist
    return best


def crop_window(cx: float, cy: float) -> dict:
    clamp = lambda v: min(1.0, max(0.0, v))  # noqa: E731
    return {
        "x": clamp(min(cx - CROP_W / 2, 1 - CROP_W)),
        "y": clamp(min(cy - CROP_H / 2, 1 - CROP_H)),
        "width": CROP_W,
        "height": CROP_H,
    }


def build_people(detections: list[Any], seats: list[dict]) -> list[dict]:
    """Summary-safe list (no scores): index, seat, crop window."""
    people = []
    for i, (cx, cy, _conf) in enumerate(centres(detections), start=1):
        seat = seat_for(cx, cy, seats)
        people.append({
            "index": i,
            "seat_id": seat["seat_id"] if seat else None,
            "seat_label": seat.get("label") if seat else None,
            "crop": crop_window(cx, cy),
        })
    return people


def crop_jpeg(jpeg: bytes, crop: dict, quality: int = 85) -> bytes:
    """Cut the crop window out of the snapshot and return a JPEG thumbnail."""
    with Image.open(io.BytesIO(jpeg)) as img:
        img = img.convert("RGB")
        w, h = img.size
        box = (
            round(crop["x"] * w),
            round(crop["y"] * h),
            round((crop["x"] + crop["width"]) * w),
            round((crop["y"] + crop["height"]) * h),
        )
        part = img.crop(box)
        if part.height < THUMB_MIN_HEIGHT:
            scale = THUMB_MIN_HEIGHT / part.height
            part = part.resize((round(part.width * scale), THUMB_MIN_HEIGHT), Image.LANCZOS)
        buf = io.BytesIO()
        part.save(buf, "JPEG", quality=quality)
        return buf.getvalue()
