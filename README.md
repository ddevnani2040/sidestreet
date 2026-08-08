# Sidestreet

**Google Maps tells you how fast traffic *should* move. Sidestreet looks at NYC's
own traffic cameras and tells you how full the streets actually are — then routes
you around the mess.**

Built at NYC Vision Hack v.2 (7 Aug 2026). Live on Google Cloud Run:

**https://nyc-vision-agent-825961604723.us-east4.run.app**

---

## What it does

Enter a from and a to. You get two routes on one map:

- **Google Maps** — its own recommendation, with live traffic
- **Sidestreet** — re-ranked by what 56 live DOT cameras can actually see

Plus a plain-English reason: *"Google routes via E 51st St; cameras show jammed
at Madison @ 44th, Madison @ 46th, 8 Ave @ 57th. Sidestreet takes Madison
instead, 2.4 min faster on Google's own estimate."*

Click any camera on the route to see the live frame it was judged on.

## How it works

```
NYC DOT cameras ──> Roboflow (COCO) ──> vehicle counts ──> density level
   963 online          detection         per camera       free/moderate/jammed
                                                                  │
Google Routes API ──> candidate routes ──────────────────────────>├─> re-rank
   real, legal, traffic-aware                                     │
                                                                  v
                                                          Sidestreet's pick
```

**Every candidate route is a real Google route.** We don't build our own street
graph — that risks proposing illegal turns against one-way avenues. Instead we
ask Google for the same trip forced through each parallel avenue (one waypoint
each), then re-rank that set by observed camera density. So when the app says
"2.4 min faster", the number is Google's own estimate, not ours.

Density, not tracking: frames refresh every ~2s, so a car moves ~27m between
them. Anything trajectory-based (ByteTrack, line counters) falls apart at that
rate. Counting what is in frame right now is robust to the frame rate, the rain,
and the dark.

## Measurement honesty

This is the part we'd want a judge to poke at, so it is written down.

**Detection undercounts.** On 352×240 night footage, a frame with ~20 visible
cars detects 3 at confidence 30, 6 at 10, 13 at 5. We run at 10. Counts are not
a census of vehicles; they are a *relative* signal.

**Three corrections are applied before a count means anything:**

| Correction | Why |
| --- | --- |
| **Parked-vehicle floor** | Cars parked at the kerb appear in every frame and never move. Estimated as the camera's 10th-percentile reading — whatever is still there at the quietest moment — and subtracted. |
| **Two-way halving** | Park Ave is two-way, so its camera sees both carriageways at once. Half those cars are not in your way. Every other corridor avenue is one-way. |
| **Per-camera baseline** | One camera looks down an avenue and sees twenty cars at free flow; another watches one intersection and sees four. Each is scored against its own 75th percentile. |

**A jam requires both** an above-baseline reading **and** an absolute vehicle
count. A purely relative score is degenerate — scoring a camera against its own
percentiles guarantees a fixed share of readings look "jammed" no matter how
empty the street is. An early build called 3 cars jammed while 12 read moderate.

**Route scores are shrunk toward neutral** when few cameras observe a route, so a
single glance down a single block cannot condemn a whole route. Each route
reports a `confidence` from its camera count.

**The saving is modelled, not measured.** `saved_min` is Google's own duration for
each route scaled by observed density. We have no ground truth on how long the
trip actually took.

## Endpoints

| Route | |
| --- | --- |
| `GET /` | the map |
| `GET /api/route?origin=…&destination=…` | Google vs Sidestreet (accepts addresses or lat/lng) |
| `GET /api/density` | every camera with a current reading |
| `GET /api/detect/{cam_id}` | run detection on one live frame |
| `GET /api/cameras/{cam_id}/image` | proxied live frame |
| `POST /api/simulate/{cam_id}?level=jammed\|clear` | force a level, for demos |
| `GET /api/status` | poll count, config, health |

## Running it

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env        # add your Roboflow key
.venv/bin/python main.py    # http://localhost:8080
```

Deploy to Cloud Run:

```bash
./deploy.sh
```

### Why `deploy.sh` builds locally

`gcloud run deploy --source .` fails on the hackathon temp accounts:

```
PERMISSION_DENIED: Build failed because the default service account is
missing required IAM permissions.
```

The account has `roles/editor` but not `owner` or
`resourcemanager.projectIamAdmin`, so it cannot grant
`roles/cloudbuild.builds.builder` to the compute service account to fix it.
`deploy.sh` builds the image locally and pushes it, which never invokes Cloud
Build. Needs Docker running, and `--platform linux/amd64` — Apple Silicon builds
arm64 by default and Cloud Run rejects it.

Google APIs are called with the service account's own OAuth token via the
metadata server, so there is no Maps API key to leak. The legacy Geocoding API
rejects OAuth, so addresses are resolved by the Routes API itself.

## Scale note

963 cameras polled every 15s would be ~64 inference calls/second and would
exhaust a Roboflow quota within minutes. So the app covers **one region
completely** rather than the whole city thinly: every DOT camera between 14th and
72nd St, river to river (200 of them), polled on a 45s cycle. All 963 cameras
citywide stay registered and are detected **on demand** if a route leaves the
region.

An earlier build spread a fixed budget evenly across five boroughs, which left
Midtown sampled every ~1.3km -- too coarse to judge a two-mile crosstown trip.

## Does it actually beat Google?

Sometimes, and the app says so when it does not. Google's routing is already
traffic-aware and genuinely good; on a congested Friday evening its choice often
survives camera scrutiny, and Sidestreet reports "no better route" rather than
inventing a diversion. The value shows up when one corridor is jammed and a
parallel one is not -- which is a real condition, not a constant one.

This is deliberate. A router that always claims to beat Google would be lying
most of the time.

## Data

- [NYC DOT traffic cameras](https://webcams.nyctmc.org/api/cameras) — 963 online
- [Roboflow](https://roboflow.com) hosted inference, COCO pretrained
- [Google Routes API](https://developers.google.com/maps/documentation/routes) — traffic-aware routing
- Frames are transient and never stored server-side; the optional local archive
  (`ARCHIVE_FRAMES=1`) writes to disk for demo fallback only.

## Where this works, and where it does not

**Short surface-street trips in dense grids.** That is the whole product. Camera
density per mile is high, and it is exactly where Google's phone-speed data is
most ambiguous -- a slow phone could be a red light or gridlock, and a camera
tells them apart instantly.

**Not motorways.** Density is a weak congestion proxy at speed: a frame holding
eighteen cars at 55mph looks identical to eighteen cars stopped. An early build
called Belt Pkwy "jammed" at normal Friday volume and claimed a 24-minute saving
on a JFK run that was almost certainly nonsense. Motorway cameras are now held to
2.5x the vehicle threshold and their routing weight is damped 65% toward neutral.
Google models motorway flow well from phone data; there is little to add there.

## Known limits

- Night and rain degrade detection badly; the corrections above reduce but do not
  remove the bias.
- The parked-vehicle floor needs ~20 samples per camera before it engages.
- Routes that run on FDR Drive have thin camera coverage — the app reports low
  confidence rather than pretending otherwise.
- No ground-truth validation of travel times. That is the honest next step.

## Roadmap

Citywide graph · historical prediction · speed estimation from the burned-in
per-frame timestamps · turn-by-turn.
