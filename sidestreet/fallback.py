"""Routing without Google, for when no Google credentials are available.

The hackathon project was decommissioned and its service account deleted, which
kills the Routes API. The camera layer is unaffected -- the DOT feeds are public
and the Roboflow key is on a personal account -- so the only missing piece is the
routing baseline.

OSRM's public server fills it: real drivable geometry, real one-ways, real turn
restrictions. What it does *not* have is live traffic, so its durations are
free-flow estimates and run well below reality in Midtown. That difference is
surfaced rather than hidden: the baseline is labelled as OSRM, never as Google,
because presenting a free-flow estimate as Google's traffic-aware prediction
would be a lie about where the number came from.
"""

import asyncio

import httpx

OSRM_URL = "https://router.project-osrm.org/route/v1/driving/{coords}"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

# Nominatim asks for a real identifying User-Agent.
UA = {"User-Agent": "Sidestreet/1.0 (NYC Vision Hack demo)"}

# Bias geocoding to the five boroughs.
NYC_VIEWBOX = "-74.05,40.90,-73.70,40.55"

# Nominatim asks for at most one request a second, and the demo scanner reuses
# the same handful of landmarks repeatedly -- without this, most of the scan is
# dropped by rate limiting. Addresses do not move, so the cache never expires.
_geo_cache: dict[str, tuple[float, float]] = {}
_geo_lock = asyncio.Lock()


async def geocode(query: str, client: httpx.AsyncClient) -> tuple[float, float]:
    if query in _geo_cache:
        return _geo_cache[query]
    async with _geo_lock:  # serialise, to respect the 1 req/s policy
        if query in _geo_cache:
            return _geo_cache[query]
        return await _geocode_uncached(query, client)


async def _geocode_uncached(query: str, client: httpx.AsyncClient):
    r = await client.get(
        NOMINATIM_URL,
        params={
            "q": query,
            "format": "json",
            "limit": 1,
            "viewbox": NYC_VIEWBOX,
            "bounded": 1,
        },
        headers=UA,
        timeout=25,
    )
    if r.status_code == 429:
        raise RuntimeError(
            "address lookup is rate-limited right now — use a preset trip, "
            "or enter coordinates as 'lat,lng'"
        )
    try:
        hits = r.json()
    except Exception:
        raise RuntimeError(f"address lookup failed ({r.status_code})")
    if not hits:
        raise RuntimeError(f"could not find {query!r}")
    out = (float(hits[0]["lat"]), float(hits[0]["lon"]))
    _geo_cache[query] = out
    await asyncio.sleep(1.05)  # policy: no more than one lookup a second
    return out


async def resolve(point, client: httpx.AsyncClient) -> tuple[float, float]:
    if not isinstance(point, str):
        return point
    # "40.7359,-73.9911" needs no geocoder.
    parts = point.split(",")
    if len(parts) == 2:
        try:
            return float(parts[0]), float(parts[1])
        except ValueError:
            pass
    return await geocode(point, client)


async def route(
    origin, destination, via=None, alternatives: bool = False
) -> list[dict]:
    """Return routes shaped like the Google Routes API responses we consume."""
    async with httpx.AsyncClient() as client:
        o = await resolve(origin, client)
        d = await resolve(destination, client)
        pts = [o] + ([via] if via else []) + [d]
        coords = ";".join(f"{lon},{lat}" for lat, lon in pts)

        r = await client.get(
            OSRM_URL.format(coords=coords),
            params={
                "overview": "full",
                "geometries": "polyline",
                "alternatives": "true" if alternatives else "false",
            },
            timeout=30,
        )
        j = r.json()

    if j.get("code") != "Ok" or not j.get("routes"):
        raise RuntimeError(f"osrm: {j.get('code')} {j.get('message', '')}"[:200])

    return [
        {
            "duration": f"{int(rt['duration'])}s",
            "distanceMeters": int(rt["distance"]),
            "description": "",
            "polyline": {"encodedPolyline": rt["geometry"]},
        }
        for rt in j["routes"]
    ]
