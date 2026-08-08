"""Per-camera density state and classification.

Cameras are not comparable on raw counts -- one points down an avenue and sees
twenty cars at free flow, another watches a single intersection and sees four.
So each camera is classified against its own observed distribution, with a fixed
fallback until it has enough samples to have a distribution at all.
"""

import statistics
import time
from collections import deque
from dataclasses import dataclass, field

FREE = "free"
MODERATE = "moderate"
JAMMED = "jammed"
UNKNOWN = "unknown"

# Used until a camera has MIN_SAMPLES observations of its own.
BOOTSTRAP_FREE_MAX = 4
BOOTSTRAP_MODERATE_MAX = 9

# How far above a camera's own baseline counts as busy, and the absolute vehicle
# counts below which a street is never called busy regardless of its baseline.
# Detection undercounts heavily on 352x240 night footage, so these are low: 6
# detected vehicles on a dark avenue is a lot of real metal.
MODERATE_RATIO = 1.0
JAMMED_RATIO = 1.35
MIN_VEHICLES_MODERATE = 4
MIN_VEHICLES_JAMMED = 6

# Highway/expressway cameras. A motorway frame legitimately holds many more
# moving vehicles than a city block, so the city thresholds read normal traffic
# as a jam -- Belt Pkwy at 18 vehicles is free-flowing, not stopped. Density is a
# weak congestion proxy on motorways anyway (fast and full looks like slow and
# full), and it is exactly where Google's phone-speed data is strongest, so these
# are held to a much higher bar and flagged low-value.
HIGHWAY_HINTS = (
    "expy", "pkwy", "expwy", "thruway", "bridge", " br", "tunnel", "i-",
    "belt ", "cross bronx", "lie", "bqe", "fdr", "gcp", "hwy", "-eb_at_",
    "-wb_at_", "-nb_at_", "-sb_at_", "van wyck", "grand central pkwy",
)
HIGHWAY_THRESHOLD_MULTIPLIER = 2.5

MIN_SAMPLES = 8
PARKED_MIN_SAMPLES = 20  # need a quiet moment on record before trusting the floor
HISTORY = 240  # ~1 hour at a 15s poll interval


@dataclass
class CameraState:
    cam_id: str
    label: str
    avenue: str
    cross_street: int | None
    lat: float
    lon: float
    direction: str

    counts: deque = field(default_factory=lambda: deque(maxlen=HISTORY))
    vehicles: int | None = None
    people: int | None = None
    by_class: dict = field(default_factory=dict)
    frame_time: str | None = None
    updated_at: float | None = None
    error: str | None = None
    simulated: str | None = None  # forced level for the demo toggle

    def record(self, vehicles: int, people: int, by_class: dict) -> None:
        self.vehicles = vehicles
        self.people = people
        self.by_class = by_class
        # Raw counts. The parked floor and direction factor are applied when
        # reading, not when writing, so both are corrected in exactly one place.
        self.counts.append(vehicles)
        self.updated_at = time.time()
        self.error = None

    def baseline(self) -> float:
        """This camera's typical occupancy -- the relative half of the score."""
        if len(self.counts) < MIN_SAMPLES:
            return float(BOOTSTRAP_FREE_MAX)
        p75 = _percentile(sorted(self.counts), 0.75)
        return max((p75 - self.parked_floor) * self.direction_factor, 2.0)

    @property
    def is_highway(self) -> bool:
        n = (self.label or "").lower()
        return any(h in n for h in HIGHWAY_HINTS)

    def thresholds(self) -> tuple[float, float]:
        """Vehicle counts at which this camera turns moderate, then jammed."""
        b = self.baseline()
        m = HIGHWAY_THRESHOLD_MULTIPLIER if self.is_highway else 1.0
        return (
            max(MIN_VEHICLES_MODERATE, b * MODERATE_RATIO) * m,
            max(MIN_VEHICLES_JAMMED, b * JAMMED_RATIO) * m,
        )

    @property
    def direction_factor(self) -> float:
        """Share of counted vehicles that are travelling your way.

        A camera on a two-way avenue (Park) sees both directions in one frame,
        so its raw count roughly doubles the traffic actually in front of you.
        Every other corridor avenue is one-way, where every counted vehicle is
        going your way. Without this, Park is penalised for the opposite
        carriageway and never wins a diversion it deserves.
        """
        return 0.5 if self.direction == "both" else 1.0

    @property
    def parked_floor(self) -> float:
        """Vehicles parked at the kerb, which are present in every frame.

        Cars parked either side of the street are counted by the detector but
        are not traffic -- they add a constant offset that never varies with
        congestion, so a street lined with parked cars looks permanently busier
        than an identical street with a bare kerb.

        Estimated as the camera's quietest observed reading: whatever is still
        in frame at the emptiest moment is, by definition, not moving through.
        Deliberately *not* done by tracking which boxes stay put between polls --
        at a 25s cycle a car stopped in a jam looks just as static as a parked
        one, so that approach would subtract away the exact congestion being
        measured.

        Needs history to mean anything; returns 0 until there are enough samples,
        which degrades to plain counting rather than to a wrong answer.
        """
        if len(self.counts) < PARKED_MIN_SAMPLES:
            return 0.0
        return _percentile(sorted(self.counts), 0.10)

    @property
    def effective_vehicles(self) -> float | None:
        """Moving traffic in your direction: counted, de-parked, de-doubled."""
        if self.vehicles is None:
            return None
        moving = max(0.0, self.vehicles - self.parked_floor)
        return moving * self.direction_factor

    @property
    def level(self) -> str:
        """Busier than this camera's own norm *and* actually crowded.

        A purely relative score is degenerate: scoring against a camera's own
        recent percentiles guarantees a fixed share of readings look jammed no
        matter how empty the street is -- three cars outranking twelve at a
        busier camera. The absolute floors below stop a quiet street from ever
        being called jammed, while the ratio keeps cameras with different fields
        of view comparable.
        """
        if self.simulated:
            return self.simulated
        if self.vehicles is None:
            return UNKNOWN
        moderate_at, jammed_at = self.thresholds()
        eff = self.effective_vehicles
        if eff >= jammed_at:
            return JAMMED
        if eff >= moderate_at:
            return MODERATE
        return FREE

    @property
    def weight(self) -> float:
        """Routing cost multiplier for the segment this camera watches.

        Motorway readings are damped toward neutral: a camera cannot tell a full
        fast motorway from a full stopped one, and Google already models
        motorway flow well from phone data.
        """
        w = {FREE: 1.0, MODERATE: 1.8, JAMMED: 4.0, UNKNOWN: 1.3}[self.level]
        return 1.0 + (w - 1.0) * 0.35 if self.is_highway else w

    def as_dict(self) -> dict:
        free_max, moderate_max = self.thresholds()
        return {
            "id": self.cam_id,
            "label": self.label,
            "avenue": self.avenue,
            "cross_street": self.cross_street,
            "lat": self.lat,
            "lon": self.lon,
            "direction": self.direction,
            "vehicles": self.vehicles,
            "effective_vehicles": self.effective_vehicles,
            "direction_factor": self.direction_factor,
            "parked_floor": round(self.parked_floor, 1),
            "is_highway": self.is_highway,
            "people": self.people,
            "by_class": self.by_class,
            "level": self.level,
            "weight": self.weight,
            "simulated": bool(self.simulated),
            "frame_time": self.frame_time,
            "updated_at": self.updated_at,
            "samples": len(self.counts),
            "thresholds": {"moderate_at": round(free_max,1), "jammed_at": round(moderate_max,1), "baseline": round(self.baseline(),1)},
            "error": self.error,
        }


def _percentile(ordered: list[int], q: float) -> float:
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return float(ordered[0])
    pos = q * (len(ordered) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    frac = pos - lo
    return ordered[lo] * (1 - frac) + ordered[hi] * frac


class Store:
    def __init__(self) -> None:
        self.cameras: dict[str, CameraState] = {}
        self.hot: set[str] = set()  # continuously polled subset
        self.started_at = time.time()
        self.poll_count = 0
        self.last_poll: float | None = None
        self.last_error: str | None = None

    def summary(self) -> dict:
        levels = [c.level for c in self.cameras.values()]
        return {
            "cameras": len(self.cameras),
            "polled": len(self.hot),
            "jammed": levels.count(JAMMED),
            "moderate": levels.count(MODERATE),
            "free": levels.count(FREE),
            "unknown": levels.count(UNKNOWN),
            "poll_count": self.poll_count,
            "last_poll": self.last_poll,
            "uptime_s": round(time.time() - self.started_at, 1),
            "last_error": self.last_error,
        }

    def mean_vehicles(self) -> float:
        vals = [c.vehicles for c in self.cameras.values() if c.vehicles is not None]
        return round(statistics.mean(vals), 2) if vals else 0.0


STORE = Store()
