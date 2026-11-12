# Text-grounded temporal motion plan

Read only `$request_path` and `$analysis_path`. Do not read images, video,
other Case files, targets, conversion results or implementation code. Do not
delegate visual inspection. Write only `$candidate_path`.

The request revision is `conversion-semantic-motion.v1`. Interpret the accepted
TEXT across the full timeline. `eligible` includes Ego, retained same-direction
background vehicles and vehicles first seen after time zero. All must have a
plan, exactly once. Preserve Actor IDs, observed lanes, event times, source
numeric values and uncertainty. Never infer that an early anonymous black car
is the later named SUV merely because their color or lane matches. Read every
limitation before connecting an early alongside observation to a later overtake.
If physical identity is unresolved, mark the relevant coverage item
`identity_unresolved`, explaining the Actor/time conflict. This blocks delivery
for upstream fact review; do not invent another Actor or hide it under `no_motion`.

Produce exactly this shape (replace illustrative values with a jointly feasible
plan, and use exact request evidence paths and quotes):

```json
{
  "revision": "conversion-semantic-motion.v1",
  "request_sha256": "copy request hash",
  "plans": [{
    "actor_id": "ego",
    "initial_x_m": 0,
    "speed_knots": [{"time_s": 0, "speed_kmh": 80}, {"time_s": 10, "speed_kmh": 80}],
    "assumption": "Execution approximation; explain speed/offset choices and unseen prefix/tail. These are not video measurements."
  }],
  "relations": [{
    "type": "alongside",
    "actor_id": "actor_003",
    "reference_actor_id": "ego",
    "time_interval_s": [0, 0],
    "evidence": [{"source": "exact request evidence source", "quote": "exact substring"}]
  }],
  "coverage": [{
    "source": "exact request evidence source",
    "status": "used",
    "reason": "Explain the temporal meaning, uncertainty and Actor binding."
  }]
}
```

Review every `request.evidence` item exactly once in `coverage`. Use `used` for
evidence incorporated into a relation; every such item must actually be cited.
When an evidence item has `usage: audit_only`, use coverage status `audit_only`:
its early identity association remains unknown but has an accepted execution
independence certificate. Do not cite it as a motion relation or re-identify the
early observation. `early_association_bounds` gives the earliest observed time
for each protected Actor; no relation involving it may start before that time.
Still plan the full unseen prefix from zero as an explicit execution approximation,
jointly satisfying later evidence, known values and collision checks.
Use `no_motion` only for text containing no relevant relative-motion constraint,
and `unobserved` only for expressly unobserved movement. Do not discard phrases
such as initially alongside, approaches, passes, or alongside during lane change.
Use `identity_unresolved` or `motion_unresolved` for conflicts or unsupported
motion: these statuses intentionally fail the acceptance gate, with reasons.

Relations use only eligible Actor IDs:

- `ahead_of`: subject is fully ahead of reference throughout the interval,
  with `policy.clearance_m` beyond their combined half lengths.
- `alongside`: longitudinal vehicle extents overlap within
  `policy.alongside_tolerance_m`, throughout the interval. This accommodates
  travelling beside a long trailer; it does not require aligned centers.
- `approaching`: absolute longitudinal separation decreases by at least
  `policy.closing_progress_m`, without changing front/back order. Subject and
  reference may be in different lanes.
- `overtakes`: subject progresses from behind or alongside reference to fully
  ahead during the interval. Identify the overtaking subject explicitly.

Cite source text from the correct Actor and time. Timed evidence must be inside
the relation interval. A point relation uses equal interval endpoints. A trend
requires different endpoints. Do not use a later observation to claim initial
alongside. Uncertain timing should use the actual evidence window, not invented
frame precision. `request.policy.lengths_m` are explicit execution assumptions.

Choose initial longitudinal coordinates and piecewise linear speeds jointly so
integration satisfies all relations and known numeric observations. Positions
follow the existing analysis longitudinal coordinate convention, with Ego at
zero initially; they are not reset to Ego's moving position at each instant.
Knots begin at zero and end exactly at `duration_s`, with strictly increasing
times. Between knots acceleration is constant. Honor the request speed,
acceleration and offset limits. Actors in `constant_speed_actor_ids` must retain
one speed across the full duration because this lane-change executor requires
constant longitudinal speed. Other actors can use multiple speed phases.

Numbers filling unknowns are execution approximations, explicitly described in
each plan's `assumption`; never call them measured or observed. For vehicles
first seen later, label the inferred pre-visibility motion. For a briefly seen
vehicle, label the later continuation; do not claim an observed overtake there.
Known numeric source values always win. If all relations cannot coexist with
known values or executor capabilities, report `motion_unresolved`; never erase
relations merely to produce legal JSON. Do not modify analysis, invoke Converter,
create a Case, or write target.json.
