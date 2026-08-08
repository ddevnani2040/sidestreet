import contextlib
import os

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response

from sidestreet import config, detect, poller, routing
from sidestreet.ui import INDEX_HTML
from sidestreet.store import JAMMED, MODERATE, STORE


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    poller.start()
    yield
    await poller.stop()


app = FastAPI(title="Sidestreet", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/hello")
def hello():
    return {
        "message": "hello world",
        "service": os.environ.get("K_SERVICE", "local"),
        "revision": os.environ.get("K_REVISION", "local"),
    }


@app.get("/api/status")
def status():
    return {
        "summary": STORE.summary(),
        "mean_vehicles": STORE.mean_vehicles(),
        "config": {
            "model_id": config.MODEL_ID,
            "poll_interval_s": config.POLL_INTERVAL,
            "max_cameras": config.MAX_CAMERAS,
            "roboflow_configured": bool(config.ROBOFLOW_API_KEY),
            "archiving": config.ARCHIVE_FRAMES,
        },
    }


@app.get("/api/density")
def density(all: bool = False):
    """Cameras with a current reading. all=1 includes the unpolled remainder."""
    source = (
        STORE.cameras.values()
        if all
        else [c for c in STORE.cameras.values() if c.vehicles is not None]
    )
    cams = sorted(
        (c.as_dict() for c in source),
        key=lambda c: (c["avenue"], -(c["cross_street"] or 0)),
    )
    return {"summary": STORE.summary(), "cameras": cams}


@app.post("/api/simulate/{cam_id}")
def simulate(cam_id: str, level: str = JAMMED):
    """Force a camera's level for the demo. level=clear to release."""
    state = STORE.cameras.get(cam_id)
    if not state:
        raise HTTPException(404, "unknown camera")
    if level == "clear":
        state.simulated = None
    elif level in (JAMMED, MODERATE):
        state.simulated = level
    else:
        raise HTTPException(400, "level must be jammed, moderate, or clear")
    return state.as_dict()


@app.get("/api/geocode")
async def geocode(q: str):
    try:
        lat, lng, formatted = await routing.geocode(q)
    except Exception as exc:
        raise HTTPException(400, str(exc)[:200])
    return {"lat": lat, "lng": lng, "address": formatted}


@app.get("/api/best")
async def best(limit: int = 6):
    """Rank candidate demo trips by how convincing they look right now."""
    try:
        return await routing.best_demo(limit)
    except Exception as exc:
        raise HTTPException(502, f"{type(exc).__name__}: {exc}"[:300])


@app.get("/api/route")
async def route(
    from_lat: float = 40.7488,
    from_lng: float = -73.9700,
    to_lat: float = 40.7681,
    to_lng: float = -73.9819,
    origin: str | None = None,
    destination: str | None = None,
):
    """Google's recommended route vs Sidestreet's, over the same candidates.

    Defaults run Madison Sq up to the Park Ave / 60s, straight through the
    camera-covered Midtown rungs.
    """
    try:
        o = origin or (from_lat, from_lng)
        d = destination or (to_lat, to_lng)
        out = await routing.compare(o, d)
        if origin:
            out["origin"] = origin
        if destination:
            out["destination"] = destination
        return out
    except Exception as exc:
        raise HTTPException(502, f"{type(exc).__name__}: {exc}"[:300])


@app.get("/api/detect/{cam_id}")
async def detect_one(cam_id: str):
    """Pull one live frame and run detection on it. The eligibility-gate demo."""
    async with httpx.AsyncClient() as client:
        r = await client.get(
            config.CAMERA_IMAGE_URL.format(cam_id=cam_id), timeout=20
        )
        if r.status_code != 200:
            raise HTTPException(502, "camera unavailable")
        try:
            d = await detect.detect(r.content, client)
        except detect.InferenceError as exc:
            raise HTTPException(503, str(exc))
    return {"camera": cam_id, "bytes": len(r.content), **d.as_dict()}


@app.get("/api/cameras")
async def cameras():
    async with httpx.AsyncClient(timeout=20) as client:
        cams = (await client.get(config.CAMERAS_URL)).json()
    online = [c for c in cams if str(c.get("isOnline")).lower() == "true"]
    return {"total": len(cams), "online": len(online), "cameras": online}


@app.get("/api/cameras/{cam_id}/image")
async def camera_image(cam_id: str):
    """Proxy the JPEG so the browser isn't hotlinking the DOT host directly."""
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(config.CAMERA_IMAGE_URL.format(cam_id=cam_id))
    if r.status_code != 200:
        raise HTTPException(502, "camera unavailable")
    return Response(
        content=r.content,
        media_type=r.headers.get("content-type", "image/jpeg"),
        headers={"Cache-Control": "no-store"},
    )


@app.get("/", response_class=HTMLResponse)
def index():
    return INDEX_HTML



if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
