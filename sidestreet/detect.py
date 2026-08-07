"""Roboflow hosted inference: a frame in, vehicle/person counts out.

Density, not tracking. At ~0.5fps a car moves ~27m between frames, so anything
trajectory-based (ByteTrack, line counters) breaks down. Counting what is in the
frame right now is robust to the frame rate, the rain, and the dark.
"""

import base64
from dataclasses import dataclass

import httpx

from . import config


class InferenceError(RuntimeError):
    pass


@dataclass
class Detection:
    vehicles: int
    people: int
    by_class: dict[str, int]
    raw_count: int

    def as_dict(self) -> dict:
        return {
            "vehicles": self.vehicles,
            "people": self.people,
            "by_class": self.by_class,
            "raw_count": self.raw_count,
        }


async def detect(image_bytes: bytes, client: httpx.AsyncClient) -> Detection:
    if not config.ROBOFLOW_API_KEY:
        raise InferenceError(
            "ROBOFLOW_API_KEY is not set. Add it to .env "
            "(get one at https://app.roboflow.com/settings/api)."
        )

    # Hosted API wants the base64 image as a form body, not JSON.
    r = await client.post(
        f"{config.ROBOFLOW_URL}/{config.MODEL_ID}",
        params={
            "api_key": config.ROBOFLOW_API_KEY,
            "confidence": config.CONFIDENCE,
        },
        content=base64.b64encode(image_bytes),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    if r.status_code != 200:
        raise InferenceError(f"roboflow {r.status_code}: {r.text[:200]}")

    preds = r.json().get("predictions", [])
    by_class: dict[str, int] = {}
    for p in preds:
        cls = p.get("class", "?")
        by_class[cls] = by_class.get(cls, 0) + 1

    return Detection(
        vehicles=sum(n for c, n in by_class.items() if c in config.VEHICLE_CLASSES),
        people=sum(n for c, n in by_class.items() if c in config.PERSON_CLASSES),
        by_class=by_class,
        raw_count=len(preds),
    )
