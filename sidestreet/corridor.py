"""The demo corridor: the East Side avenue grid, 23rd to 96th.

Chosen from the live camera list rather than by intuition. Crosstown streets are
camera-poor (Canal St has 4), while the East Side avenues carry ~58 cameras and,
critically, share cross streets -- 23/34/42/49/57/72/86/96 all appear on several
avenues at once. Those shared intersections are what make a reroute expressible:
leave 2 Ave at 49th, run over to 3 Ave, rejoin at 57th.
"""

import re
from dataclasses import dataclass, field

# Avenue running direction. NYC avenues are mostly one-way, and honouring that is
# the difference between "generic graph demo" and "routes like a local".
AVENUE_DIRECTION = {
    "1 Ave": "north",
    "2 Ave": "south",
    "3 Ave": "north",
    "Lexington Ave": "south",
    "Park Ave": "both",
    "Madison Ave": "north",
    "5 Ave": "south",
}

CORRIDOR_AVENUES = list(AVENUE_DIRECTION)

# Latitude box: 23rd St up to 96th St.
LAT_MIN = 40.739
LAT_MAX = 40.789

# Cross streets shared by several avenues -- the rejoin points.
ANCHOR_CROSS_STREETS = [23, 34, 42, 49, 57, 72, 79, 86, 96]


@dataclass
class Camera:
    id: str
    name: str
    avenue: str
    cross_street: int | None
    lat: float
    lon: float
    direction: str = field(default="both")

    @property
    def label(self) -> str:
        if self.cross_street:
            return f"{self.avenue} @ {self.cross_street} St"
        return self.name


def _primary_street(name: str) -> str:
    return re.split(r"\s*@\s*|\s+at\s+", (name or "").strip())[0].strip()


def _cross_street(name: str) -> int | None:
    """Pull the numbered cross street out of e.g. '2 Ave @ E 110 Street'."""
    tail = re.split(r"\s*@\s*|\s+at\s+", (name or "").strip())
    if len(tail) < 2:
        return None
    m = re.search(r"(\d{1,3})\s*(?:st|nd|rd|th|street|st\.)\b", tail[1], re.I)
    if not m:
        m = re.search(r"\b(\d{1,3})\b", tail[1])
    return int(m.group(1)) if m else None


# Manhattan streets carrying traffic in both directions. Their cameras see both
# carriageways at once, so counts are halved when judging one direction.
TWO_WAY = {
    "Park Ave", "Broadway", "Canal St", "Houston St", "E Houston St",
    "W Houston St", "14 St", "E 14 St", "W 14 St", "23 St", "E 23 St",
    "34 St", "E 34 St", "W 34 St", "42 St", "E 42 St", "W 42 St",
    "57 St", "E 57 St", "W 57 St", "72 St", "79 St", "86 St", "E 86 St",
    "96 St", "125 St", "W 125 St", "E 125 St", "110 St", "116 St",
    "Central Park West", "Riverside Dr", "West St", "12 Ave", "11 Ave",
}


def all_manhattan(all_cameras: list[dict]) -> list[Camera]:
    """Every online Manhattan camera, so any address has something nearby.

    These are registered but not all polled -- see the poller. Detection runs on
    demand for whichever of them land on a requested route.
    """
    out: list[Camera] = []
    for c in all_cameras:
        if str(c.get("isOnline")).lower() != "true":
            continue
        if c.get("area") != "Manhattan":
            continue
        if c.get("latitude") is None or c.get("longitude") is None:
            continue
        street = _primary_street(c.get("name", ""))
        out.append(
            Camera(
                id=c["id"],
                name=c.get("name", ""),
                avenue=street,
                cross_street=_cross_street(c.get("name", "")),
                lat=c["latitude"],
                lon=c["longitude"],
                direction=AVENUE_DIRECTION.get(
                    street, "both" if street in TWO_WAY else "one"
                ),
            )
        )
    return out


def select_corridor(all_cameras: list[dict], limit: int) -> list[Camera]:
    """Pick the corridor cameras, preferring anchor cross streets.

    Anchors come first so the graph keeps its rejoin points even at a small
    limit -- dropping 49 St would cost a diversion route, dropping 62 St costs
    almost nothing.
    """
    out: list[Camera] = []
    for c in all_cameras:
        if str(c.get("isOnline")).lower() != "true":
            continue
        if c.get("area") != "Manhattan":
            continue
        avenue = _primary_street(c.get("name", ""))
        if avenue not in AVENUE_DIRECTION:
            continue
        lat = c.get("latitude")
        if lat is None or not (LAT_MIN <= lat <= LAT_MAX):
            continue
        out.append(
            Camera(
                id=c["id"],
                name=c.get("name", ""),
                avenue=avenue,
                cross_street=_cross_street(c.get("name", "")),
                lat=lat,
                lon=c.get("longitude"),
                direction=AVENUE_DIRECTION[avenue],
            )
        )

    # Deduplicate: a few intersections carry two cameras.
    seen: set[tuple] = set()
    deduped = []
    for cam in out:
        key = (cam.avenue, cam.cross_street)
        if cam.cross_street is not None and key in seen:
            continue
        seen.add(key)
        deduped.append(cam)

    return _build_rungs(deduped, limit)


def _build_rungs(cams: list[Camera], limit: int) -> list[Camera]:
    """Select whole crosstown rungs rather than whole avenues.

    A reroute needs somewhere to rejoin, which means several avenues observed at
    the *same* cross street. Filling the budget avenue-by-avenue produces long
    unconnected strips; filling it cross-street-by-cross-street produces a grid
    you can actually route around.
    """
    by_cross: dict[int, list[Camera]] = {}
    for cam in cams:
        if cam.cross_street is None:
            continue
        by_cross.setdefault(cam.cross_street, []).append(cam)

    def rung_rank(item: tuple[int, list[Camera]]) -> tuple:
        cross, group = item
        avenues = len({c.avenue for c in group})
        return (-avenues, 0 if cross in ANCHOR_CROSS_STREETS else 1, cross)

    chosen: list[Camera] = []
    for cross, group in sorted(by_cross.items(), key=rung_rank):
        if len(chosen) >= limit:
            break
        # Keep the rung intact if it fits; a partial rung loses rejoin points.
        if len(chosen) + len(group) > limit and chosen:
            continue
        group.sort(key=lambda c: CORRIDOR_AVENUES.index(c.avenue))
        chosen.extend(group[: limit - len(chosen)])

    chosen.sort(key=lambda c: (CORRIDOR_AVENUES.index(c.avenue), -c.cross_street))
    return chosen
