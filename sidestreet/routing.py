"""Google Routes API vs Sidestreet: same candidate routes, different ranking.

The comparison only means something if both sides are real. So every candidate
here is a genuine Google route -- legal turns, real one-ways, live traffic-aware
durations. Google ranks them by predicted time. Sidestreet re-ranks the *same*
set by what the cameras can actually see on the ground right now.

Alternatives come from forcing a waypoint on each parallel avenue rather than
from computeAlternativeRoutes, which returns only one alternative in a grid this
dense.
"""

import math
import subprocess
from dataclasses import dataclass

import httpx

from . import config, poller
from .store import STORE

ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
FIELD_MASK = (
    "routes.duration,routes.distanceMeters,routes.description,"
    "routes.polyline.encodedPolyline"
)

# How close a camera must be to the path to count as observing it.
CAMERA_MATCH_METRES = 130.0

_token_cache: dict = {}


def _access_token() -> str:
    """Token from the metadata server on Cloud Run, gcloud CLI locally."""
    errors = []
    try:
        import google.auth
        import google.auth.transport.requests

        creds, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        creds.refresh(google.auth.transport.requests.Request())
        return creds.token
    except Exception as exc:
        errors.append(f"google.auth: {type(exc).__name__}: {exc}")

    try:
        # Local dev without application-default credentials.
        return subprocess.check_output(
            ["gcloud", "auth", "print-access-token"], text=True
        ).strip()
    except Exception as exc:
        errors.append(f"gcloud: {type(exc).__name__}: {exc}")

    raise RuntimeError("could not obtain access token — " + " | ".join(errors))


def decode_polyline(encoded: str) -> list[tuple[float, float]]:
    """Standard Google encoded-polyline decoder."""
    points, index, lat, lng = [], 0, 0, 0
    while index < len(encoded):
        for is_lat in (True, False):
            result, shift = 0, 0
            while True:
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else (result >> 1)
            if is_lat:
                lat += delta
            else:
                lng += delta
        points.append((lat / 1e5, lng / 1e5))
    return points


def _haversine(a: tuple[float, float], b: tuple[float, float]) -> float:
    r = 6371000.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp = p2 - p1
    dl = math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def _point_to_segment_m(p, a, b) -> float:
    """Approximate metres from p to segment ab, flat-earth over a few blocks."""
    scale = math.cos(math.radians(p[0]))
    ax, ay = a[1] * scale, a[0]
    bx, by = b[1] * scale, b[0]
    px, py = p[1] * scale, p[0]
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return _haversine(p, a)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    proj = (ay + t * dy, (ax + t * dx) / scale)
    return _haversine(p, proj)


def cameras_on_path(path: list[tuple[float, float]]) -> list[dict]:
    """Corridor cameras within CAMERA_MATCH_METRES of the path, in trip order."""
    out = []
    for state in STORE.cameras.values():
        best, best_i = float("inf"), 0
        for i in range(len(path) - 1):
            d = _point_to_segment_m((state.lat, state.lon), path[i], path[i + 1])
            if d < best:
                best, best_i = d, i
        if best <= CAMERA_MATCH_METRES:
            rec = state.as_dict()
            rec["distance_m"] = round(best, 1)
            rec["path_index"] = best_i
            out.append(rec)
    out.sort(key=lambda c: c["path_index"])
    return out


@dataclass
class Candidate:
    label: str
    duration_s: int
    distance_m: int
    description: str
    polyline: str
    cameras: list[dict]

    @property
    def jammed(self) -> int:
        return sum(1 for c in self.cameras if c["level"] == "jammed")

    @property
    def moderate(self) -> int:
        return sum(1 for c in self.cameras if c["level"] == "moderate")

    @property
    def density_factor(self) -> float:
        """Camera weights, shrunk toward neutral when few cameras observe it.

        A plain mean overreacts on thin coverage: one moderate camera would
        penalise a whole route by 1.8x on the strength of a single glance down a
        single block. Adding PRIOR_STRENGTH pseudo-observations at neutral means
        a route needs several agreeing cameras to move far from baseline, which
        is the honest reading of the evidence.
        """
        prior_strength, prior = 1.5, 1.15
        total = sum(c["weight"] for c in self.cameras) + prior_strength * prior
        return total / (len(self.cameras) + prior_strength)

    @property
    def confidence(self) -> str:
        n = len(self.cameras)
        return "none" if n == 0 else "low" if n <= 2 else "medium" if n <= 4 else "good"

    @property
    def adjusted_s(self) -> float:
        """Google's predicted time, scaled by what the cameras actually see."""
        return self.duration_s * self.density_factor

    def as_dict(self) -> dict:
        return {
            "label": self.label,
            "duration_s": self.duration_s,
            "duration_min": round(self.duration_s / 60, 1),
            "distance_m": self.distance_m,
            "description": self.description,
            "polyline": self.polyline,
            "path": decode_polyline(self.polyline),
            "cameras": self.cameras,
            "camera_count": len(self.cameras),
            "jammed": self.jammed,
            "moderate": self.moderate,
            "density_factor": round(self.density_factor, 3),
            "adjusted_s": round(self.adjusted_s),
            "adjusted_min": round(self.adjusted_s / 60, 1),
            "confidence": self.confidence,
        }


def _waypoint(p) -> dict:
    """Accept a (lat, lng) pair or a plain address string.

    Routes API resolves addresses itself, which sidesteps the legacy Geocoding
    API -- that one rejects OAuth and demands an API key this account cannot
    mint.
    """
    if isinstance(p, str):
        return {"address": p}
    return {"location": {"latLng": {"latitude": p[0], "longitude": p[1]}}}


def _body(origin, destination, via=None) -> dict:
    b = {
        "origin": _waypoint(origin),
        "destination": _waypoint(destination),
        "travelMode": "DRIVE",
        "routingPreference": "TRAFFIC_AWARE",
    }
    if via:
        b["intermediates"] = [_waypoint(via)]
    return b


async def _call(client: httpx.AsyncClient, body: dict) -> list[dict]:
    token = _token_cache.get("t")
    if not token:
        token = _token_cache["t"] = _access_token()
    r = await client.post(
        ROUTES_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "X-Goog-User-Project": config.PROJECT_ID,
            "X-Goog-FieldMask": FIELD_MASK,
            "Content-Type": "application/json",
        },
        json=body,
        timeout=30,
    )
    if r.status_code == 401:
        _token_cache.pop("t", None)  # refresh once on expiry
        return await _call(client, body)
    if r.status_code != 200:
        raise RuntimeError(f"routes {r.status_code}: {r.text[:200]}")
    return r.json().get("routes", [])


def _lateral_waypoints(path: list[tuple[float, float]]) -> dict[str, tuple]:
    """Waypoints offset sideways from the main route, to force side streets.

    Diverting between parallel *avenues* is not side-street routing -- avenues
    are main arteries too. Offsetting perpendicular to the baseline path pushes
    Google onto whatever smaller road actually runs parallel there, and it works
    anywhere: Manhattan's grid, Queens, Staten Island. Google snaps each offset
    to a real road and returns a real duration, so the side street is costed by
    Google rather than guessed at by us.
    """
    if len(path) < 4:
        return {}
    out: dict[str, tuple] = {}
    for frac in (0.35, 0.65):
        i = int(len(path) * frac)
        a, b = path[max(i - 1, 0)], path[min(i + 1, len(path) - 1)]
        dlat, dlon = b[0] - a[0], b[1] - a[1]
        norm = math.hypot(dlat, dlon) or 1e-9
        # Perpendicular unit vector, scaled to roughly 350m.
        plat, plon = -dlon / norm, dlat / norm
        for sign, side in ((1, "east"), (-1, "west")):
            out[f"side streets ({side}, {int(frac * 100)}%)"] = (
                path[i][0] + plat * sign * 0.0032,
                path[i][1] + plon * sign * 0.0032,
            )
    return out


def _avenue_waypoints(origin, destination) -> dict[str, tuple[float, float]]:
    """One waypoint per avenue: the camera nearest the trip's midpoint.

    Ties the alternatives to cameras we can actually observe -- there is no point
    proposing a diversion down an avenue we are blind to.
    """
    if isinstance(origin, str) or isinstance(destination, str):
        mid_lat = 40.7600  # Midtown, when endpoints are addresses
    else:
        mid_lat = (origin[0] + destination[0]) / 2
    best: dict[str, tuple[float, tuple[float, float]]] = {}
    for s in STORE.cameras.values():
        d = abs(s.lat - mid_lat)
        if s.avenue not in best or d < best[s.avenue][0]:
            best[s.avenue] = (d, (s.lat, s.lon))
    return {av: pos for av, (_, pos) in best.items()}


async def geocode(address: str) -> tuple[float, float, str]:
    """Address to lat/lng, biased to NYC."""
    token = _token_cache.get("t") or _access_token()
    _token_cache["t"] = token
    async with httpx.AsyncClient() as client:
        r = await client.get(
            "https://maps.googleapis.com/maps/api/geocode/json",
            params={
                "address": address,
                "bounds": "40.68,-74.03|40.88,-73.90",
                "region": "us",
            },
            headers={
                "Authorization": f"Bearer {token}",
                "X-Goog-User-Project": config.PROJECT_ID,
            },
            timeout=20,
        )
    j = r.json()
    if j.get("status") != "OK" or not j.get("results"):
        raise RuntimeError(f"geocode failed for {address!r}: {j.get('status')}")
    top = j["results"][0]
    loc = top["geometry"]["location"]
    return loc["lat"], loc["lng"], top.get("formatted_address", address)


async def compare(origin, destination) -> dict:
    """Google's recommendation vs Sidestreet's, over the same candidate set."""
    async with httpx.AsyncClient() as client:
        base = await _call(client, _body(origin, destination))
        if not base:
            raise RuntimeError("no route returned")

        candidates: list[Candidate] = []
        seen_polylines: set[str] = set()

        raw: list[tuple[str, dict]] = [("Google Maps", base[0])]
        base_path = decode_polyline(base[0]["polyline"]["encodedPolyline"])

        # Surface-street variant: pushes the route off FDR/BQE-type highways,
        # which is often exactly the "take the side streets" answer.
        try:
            b = _body(origin, destination)
            b["routeModifiers"] = {"avoidHighways": True}
            rs = await _call(client, b)
            if rs:
                raw.append(("avoiding highways", rs[0]))
        except Exception:
            pass

        vias = dict(_avenue_waypoints(origin, destination))
        vias.update(_lateral_waypoints(base_path))
        for name, via in vias.items():
            try:
                rs = await _call(client, _body(origin, destination, via))
            except Exception:
                continue
            if rs:
                raw.append((name if name.startswith(("side", "avoid")) else f"via {name}", rs[0]))

        # Detect on demand for any camera these routes cross that the background
        # poller does not cover, so an arbitrary address still gets real data.
        paths = {}
        needed: list[str] = []
        for label, r in raw:
            poly = r["polyline"]["encodedPolyline"]
            if poly in seen_polylines:
                continue
            seen_polylines.add(poly)
            path = decode_polyline(poly)
            paths[label] = (r, poly, path)
            needed += [c["id"] for c in cameras_on_path(path)]
        await poller.ensure_fresh(list(dict.fromkeys(needed)), client)

        google_pick = None
        for label, (r, poly, path) in paths.items():
            c = Candidate(
                label=label,
                duration_s=int(str(r["duration"]).rstrip("s")),
                distance_m=r["distanceMeters"],
                description=r.get("description", ""),
                polyline=poly,
                cameras=cameras_on_path(path),
            )
            candidates.append(c)
            if label == "Google Maps":
                google_pick = c

    ranked = sorted(candidates, key=lambda c: c.adjusted_s)
    sidestreet_pick = ranked[0]

    # Modelled saving, not a measurement: Google's own time for each route,
    # scaled by the congestion the cameras can actually see on it. The headline
    # number the demo quotes.
    saved_s = 0.0
    if google_pick:
        saved_s = google_pick.adjusted_s - sidestreet_pick.adjusted_s

    return {
        "google": google_pick.as_dict() if google_pick else None,
        "sidestreet": sidestreet_pick.as_dict(),
        "diverted": bool(google_pick and sidestreet_pick.label != google_pick.label),
        "saved_min": round(saved_s / 60, 1),
        "extra_distance_min": (
            round((sidestreet_pick.duration_s - google_pick.duration_s) / 60, 1)
            if google_pick
            else 0.0
        ),
        "candidates": [c.as_dict() for c in ranked],
        "explanation": _explain(google_pick, sidestreet_pick),
    }


def _explain(google: Candidate | None, pick: Candidate) -> str:
    if not google:
        return "No Google baseline available."

    if not google.cameras:
        return (
            f"Google routes via {google.description}, but no corridor camera "
            f"watches it — Sidestreet has nothing to add here."
        )

    if pick.label == google.label:
        if google.jammed:
            return (
                f"Google routes via {google.description}. {google.jammed} "
                f"camera(s) on it are jammed, but every alternative looks worse "
                f"— staying put."
            )
        return (
            f"Google routes via {google.description}. The "
            f"{len(google.cameras)} camera(s) watching it are clear. Agreed."
        )

    jammed = [c["label"] for c in google.cameras if c["level"] == "jammed"]
    moderate = [c["label"] for c in google.cameras if c["level"] == "moderate"]
    if jammed:
        detail = "jammed at " + ", ".join(jammed)
    elif moderate:
        detail = "building at " + ", ".join(moderate)
    else:
        detail = "denser traffic than the alternative"

    delta = round((pick.duration_s - google.duration_s) / 60, 1)
    if abs(delta) < 0.5:
        cost = "for effectively the same predicted time"
    elif delta > 0:
        cost = f"costing {delta} min more on Google's own estimate"
    else:
        cost = f"and {abs(delta)} min faster on Google's own estimate"

    return (
        f"Google routes via {google.description}; cameras show {detail}. "
        f"Sidestreet takes {pick.label} instead, {cost}. "
        f"Observed by {len(pick.cameras)} camera(s) "
        f"({pick.jammed} jammed, {pick.moderate} moderate) "
        f"vs {len(google.cameras)} on Google's ({google.jammed} jammed, "
        f"{google.moderate} moderate)."
    )
