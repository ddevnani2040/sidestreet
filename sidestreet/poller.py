"""Background poll loop: fetch frames, count vehicles, update density state.

Frame archiving runs even when Roboflow is unconfigured. The cameras get dark
and unreliable at night and the demo is after sunset, so having the evening on
disk is worth more than having it only in memory.
"""

import asyncio
import contextlib
import time
from datetime import datetime

import httpx

from . import config, detect
from .corridor import all_manhattan, select_citywide
from .store import STORE, CameraState

_task: asyncio.Task | None = None


async def _fetch_cameras(client: httpx.AsyncClient) -> list[dict]:
    r = await client.get(config.CAMERAS_URL, timeout=20)
    r.raise_for_status()
    return r.json()


async def bootstrap(client: httpx.AsyncClient) -> None:
    """Register every Manhattan camera; mark a subset for continuous polling.

    Registering all of them is what lets an arbitrary address find cameras near
    its route. Polling all of them is not an option -- ~960 cameras on a 15s
    cycle is 64 inference calls a second, which would exhaust the Roboflow quota
    within minutes. So a core set is polled continuously and the rest are
    detected on demand when a route actually crosses them.
    """
    raw = await _fetch_cameras(client)
    for cam in all_manhattan(raw):
        STORE.cameras[cam.id] = CameraState(
            cam_id=cam.id,
            label=cam.label,
            avenue=cam.avenue,
            cross_street=cam.cross_street,
            lat=cam.lat,
            lon=cam.lon,
            direction=cam.direction,
        )
    STORE.hot = {c.id for c in select_citywide(raw, config.MAX_CAMERAS)}


async def ensure_fresh(cam_ids: list[str], client: httpx.AsyncClient) -> None:
    """Detect on demand for cameras that are stale or never seen."""
    now = time.time()
    stale = [
        STORE.cameras[i]
        for i in cam_ids
        if i in STORE.cameras
        and (
            STORE.cameras[i].updated_at is None
            or now - STORE.cameras[i].updated_at > config.ON_DEMAND_TTL
        )
    ][: config.ON_DEMAND_MAX]
    if not stale:
        return
    sem = asyncio.Semaphore(config.ON_DEMAND_CONCURRENCY)
    await asyncio.gather(*(_poll_one(s, client, sem) for s in stale))


def _archive(cam_id: str, image: bytes) -> None:
    if not config.ARCHIVE_FRAMES:
        return
    day = datetime.now().strftime("%Y%m%d")
    out = config.FRAME_DIR / day / cam_id
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%H%M%S")
    (out / f"{stamp}.jpg").write_bytes(image)


async def _poll_one(
    state: CameraState, client: httpx.AsyncClient, sem: asyncio.Semaphore
) -> None:
    async with sem:
        try:
            r = await client.get(
                config.CAMERA_IMAGE_URL.format(cam_id=state.cam_id), timeout=20
            )
            if r.status_code != 200:
                state.error = f"camera {r.status_code}"
                return
            image = r.content
            _archive(state.cam_id, image)
            state.frame_time = datetime.now().isoformat(timespec="seconds")

            if not config.ROBOFLOW_API_KEY:
                # Archive-only mode: keep recording, skip inference.
                state.error = "no ROBOFLOW_API_KEY — archiving only"
                return

            d = await detect.detect(image, client)
            state.record(d.vehicles, d.people, d.by_class)
        except Exception as exc:  # keep one bad camera from killing the cycle
            state.error = f"{type(exc).__name__}: {exc}"[:200]


async def poll_once(client: httpx.AsyncClient) -> None:
    sem = asyncio.Semaphore(config.POLL_CONCURRENCY)
    hot = [s for s in STORE.cameras.values() if s.cam_id in STORE.hot]
    await asyncio.gather(*(_poll_one(s, client, sem) for s in hot))
    STORE.poll_count += 1
    STORE.last_poll = time.time()


async def _loop() -> None:
    async with httpx.AsyncClient() as client:
        try:
            await bootstrap(client)
        except Exception as exc:
            STORE.last_error = f"bootstrap failed: {exc}"
            return
        while True:
            try:
                await poll_once(client)
                STORE.last_error = None
            except Exception as exc:
                STORE.last_error = f"{type(exc).__name__}: {exc}"[:200]
            await asyncio.sleep(config.POLL_INTERVAL)


def start() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_loop())


async def stop() -> None:
    global _task
    if _task and not _task.done():
        _task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _task
    _task = None
