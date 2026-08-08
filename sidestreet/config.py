"""Runtime configuration, all overridable by environment variable."""

import os
from pathlib import Path


def _load_dotenv() -> None:
    """Minimal .env reader so we don't take a dependency just for this."""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


_load_dotenv()

ROBOFLOW_API_KEY = os.environ.get("ROBOFLOW_API_KEY", "")
ROBOFLOW_URL = os.environ.get("ROBOFLOW_URL", "https://detect.roboflow.com")

# COCO-pretrained. The documented `yolov8n-640`-style aliases 404 on the hosted
# API for this account -- they resolve only in self-hosted inference. The public
# `coco/3` project works over the hosted endpoint and gives the same COCO
# classes, which is all density needs.
MODEL_ID = os.environ.get("MODEL_ID", "coco/3")

# Hosted API takes confidence as an integer percentage, not a 0-1 float.
#
# Deliberately low. On 352x240 night footage COCO undercounts badly -- a frame
# with ~20 visible cars detects 3 at confidence 30, 6 at 10, 13 at 5. Density is
# a *relative* measure, so recall matters far more than precision here: the
# undercount is roughly consistent per camera, and each camera is scored against
# its own history, so a consistent undercount cancels out. Raising recall also
# lifts baseline counts out of the 0-3 range where percentile bands are too
# quantised to separate free from jammed.
CONFIDENCE = int(os.environ.get("CONFIDENCE", "10"))

# 58 cameras every 2s is ~29 inference calls/sec, which will exhaust a hackathon
# quota within the hour. Traffic density does not meaningfully change in 2s.
POLL_INTERVAL = float(os.environ.get("POLL_INTERVAL", "25"))
MAX_CAMERAS = int(os.environ.get("MAX_CAMERAS", "90"))

# On-demand detection for cameras a requested route crosses but that are not in
# the continuously polled set.
MAX_CANDIDATES = int(os.environ.get("MAX_CANDIDATES", "8"))
ROUTE_CACHE_TTL = float(os.environ.get("ROUTE_CACHE_TTL", "120"))
ON_DEMAND_TTL = float(os.environ.get("ON_DEMAND_TTL", "120"))
ON_DEMAND_MAX = int(os.environ.get("ON_DEMAND_MAX", "8"))
ON_DEMAND_CONCURRENCY = int(os.environ.get("ON_DEMAND_CONCURRENCY", "12"))
POLL_CONCURRENCY = int(os.environ.get("POLL_CONCURRENCY", "4"))

# Archive every fetched frame so there is a replayable clip if the live feed
# dies mid-demo. Set ARCHIVE_FRAMES=0 to disable.
ARCHIVE_FRAMES = os.environ.get("ARCHIVE_FRAMES", "1") not in ("0", "false", "")
FRAME_DIR = Path(os.environ.get("FRAME_DIR", "frames"))

# Vehicle classes in COCO. "person" is tracked separately -- it is not traffic
# density, but it is useful signal for the crosstown story.
VEHICLE_CLASSES = {"car", "truck", "bus", "motorcycle"}
PERSON_CLASSES = {"person"}

CAMERAS_URL = "https://webcams.nyctmc.org/api/cameras"
CAMERA_IMAGE_URL = "https://webcams.nyctmc.org/api/cameras/{cam_id}/image"

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "cloudrun-hack26nyc-4309")
