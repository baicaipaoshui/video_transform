# Text-only conversion continuation — conversion-continuation.v1

Read the session request at `$request_path` completely. It binds the stage,
root_request_path, analysis_path, previous checkpoint, accepted_relations and
feedback. Read the frozen root request completely. You may read the bound
analysis for necessary context. Do not read images, videos, other Cases, source
code, target.json or conversion_result.json. Do not delegate to another agent.

Only write `$output_dir/save_0001.json` through `save_0256.json`. Each save is a
complete standalone UTF-8 JSON document, in increasing filename order. Never
overwrite an earlier save. Do not write the request, owner state, accepted
checkpoints, final decision or target.json. Do not call the Converter or pipeline.

If the session request contains `program_owned_unobserved_motion`, Python owns
exactly its `source_paths` and `actor_ids`: omit those coverage rows and numeric
plans from your saves. This is an exception to the complete-coverage rules
below. Read them as context; do not remove their vehicles or infer hidden actions.
Python binds the initial alongside relation only at time zero and completes a
bounded unseen-tail execution plan, with all ordinary final checks. Preserve
every other source, actor plan and accepted major action. `complete:true` means
all work outside that exact program-owned set is complete. Without that field,
the original full-coverage rules apply.

If `program_owned_source_relations` is present, also omit its exact source_paths
from coverage and relations: Python inherits those verified witness constraints.
Do not overwrite, invert or extend those relations. Report any genuinely new
conflicting evidence through the remaining sources. Complete coverage means all
sources outside both program-owned sets, including previously reviewed sources.
The owner may complete the numerical stage deterministically without a model task.

If `program_owned_witness_receipts` is present, omit its exact `source_paths`
from coverage and relation citations as well. Python has already validated those
text witnesses and will add their `program_verified` rows and inherited
relations. They are context, not worker-owned coverage. In this case “all
evidence” below means all `remaining_sources` plus any prior worker checkpoint;
do not copy a receipt path back merely because it appears in the root request.

## Save useful work early within this SAME session

- Start from `previous` when present. Retain its reviewed evidence / actor plans.
- Save after each coherent group of about 10–20 evidence items, or one vehicle
  plan. These are FILE SAVES, not new sessions or external tasks.
- Every save is cumulative, including all previously saved work in this stage.
  You may correct earlier conclusions using the frozen text; do not silently
  remove reviewed sources/actors. Keep cross-vehicle and cross-time context.
- Use `complete:false` while unfinished. Set `complete:true` only after the
  entire stage is done. After the final save, stop. A textual answer or hidden
  reasoning is NOT a checkpoint. Save a first useful partial result before
  attempting a long global solution.
- Copy this session request's `request_sha256` (not the root request hash) into
  every save. Do not copy the previous generation's request hash.

## Stage conversion_relations: interpret accepted TEXT only

Save exactly:
`{"request_sha256":"…","complete":false,"coverage":[],"relations":[]}`.

Read every root `evidence` item, including background/late vehicles, events and
limitations. Cover each `source` exactly once in coverage with exactly
`{source,status,reason}`. Status is `used`, `no_motion`, `unobserved`,
`identity_unresolved`, or `motion_unresolved`. Explain each disposition. Never
classify a stated approach/overtake/alongside as no_motion to reduce work. Do
not infer identity or unseen motion as an observation. Unresolved execution
facts must remain explicitly unresolved for the upstream owner.

For an unverified description projection, `request.limitations` may also
contain generated `execution_gap_*` items that are absent from `evidence`.
They remain disclosed execution risks for later conversion; do not invent
coverage rows or Actor relations for them. An archived Behavior limitation
alone does not prove a disputed lane or a measured longitudinal relation.
Use the isolated Analysis event and its observed frame endpoints for the
confirmed core maneuver. A lane position observation does not by itself prove
an `ahead_of` relation across the whole video interval.
`metadata.ego_lane_execution_correction.contested_behavior` preserves the
old Behavior values for audit only; cite the corrected executable ego track,
never that archived prose, when interpreting ego lane position.

An evidence item explicitly carrying `usage: audit_only` instead requires
coverage status `audit_only`. A core identity execution context states a selected
assumption, unselected alternatives, source evidence and risk. It is not a
video observation and cannot establish a motion relation. An early identity
uncertainty is preserved under a verified execution-independence certificate;
it also cannot be cited as a motion relation. `early_association_bounds` gives
the first observed time per protected
Actor. No relation involving that Actor may start earlier. Plan its unseen prefix
from zero only as an explicit execution approximation, preserving later relations,
known numeric values and all collision checks. Do not reassign the early blob.

If the stage request's `analysis_path` points to an Analysis whose SHA256 matches
the bound root request's `analysis_sha256`, read
`metadata.core_identity_execution_context` as `audit_only` context. It names
the selected execution Actor for later unresolved sightings and preserves
unselected interpretations. Use it only to interpret the already bound
trajectory and natural-language evidence; do not cite it as a relation source,
mark its assumption as observed motion, or add fields to the save schema.

Each relation has exactly `{type,actor_id,reference_actor_id,time_interval_s,evidence}`.
Types: `ahead_of`, `alongside`, `approaching`, `overtakes`. Actor IDs must come
from eligible; intervals stay within duration_s. A single-time alongside or
ahead_of relation may have equal endpoints; approach/overtake requires a
positive interval. Evidence is a nonempty list of `{source,quote}`, each quote
an exact substring of the bound source, with correct actor and time binding.
Every source marked used must be cited, and every cited source marked used.
Sources with `usage=qualitative_only` preserve the exact bound body-part observation
and its original Actor owner. Keep coverage `status=qualitative_only`, unless the
program supplies an exact witness receipt. Never cite these sources for numerical
whole-vehicle relations: trailer-side or cab alignment does not establish centre
alignment or completed overtaking. Keep their numerical reproduction unresolved;
`qualitative_source_constraints` survives into the delivery limitations.
Relate observations across the full timeline; repeated wording may support a
single relation, but every evidence item still needs a disposition.

Honor `source_witness_relation_gate` when present. In particular, `overtakes`
means the pass is fully completed: it requires both a matching frozen
`event_type=overtake` event and frozen numeric endpoint positions proving full
clearance. Phrases such as “超越中”, “并行超越”, “接近完成”, or a general mention
of overtaking do not establish completion by themselves. When either required
proof is absent, keep the observed state as `alongside` and/or `approaching`;
do not emit an `overtakes` relation.

Do not calculate metre positions or speeds in this stage. Before final save,
account for ALL evidence, not only remaining_sources. Missing items are not
automatically no_motion. Save unresolved outcomes honestly and stop.

## Stage conversion_semantic_gap: jointly solve numerical execution plans

Save exactly `{"request_sha256":"…","complete":false,"plans":[]}`.
Use accepted_relations as fixed constraints. Do not erase/reinterpret them.
Read the root policy, eligible actors, known observations and initial_conditions.
Use previous plans as a starting draft, correct them jointly if required by
feedback. remaining_actor_ids is a work checklist, not permission to omit old
plans. Complete plans must cover EVERY eligible actor exactly once, including
ego, background and later-appearing vehicles.

Each plan has exactly `{actor_id,initial_x_m,speed_knots,assumption}`; knots have
exactly `{time_s,speed_kmh}`. Follow the root policy speed/acceleration/offset
bounds. Knots span 0..duration_s, strictly increasing, 2–32 nodes per vehicle.
Ego initial_x_m is zero; constant_speed_actor_ids keep one speed throughout.
Known source positions/speeds/initial conditions win. Every assumption must
explicitly say numbers are execution approximations, including any unseen
prefix/tail. Never describe approximations as video measurements.

Save structurally valid individual plans promptly, then jointly adjust the
complete set to satisfy all temporal relations and same-lane clearance.
Approaching requires decreasing separation; overtakes requires crossing from
behind to ahead with policy clearance; alongside uses policy tolerance over
the specified interval. Equal speeds cannot implement an overtake. Full final
verification is performed by the owner; partial drafts are never executable.
If constraints cannot be satisfied without changing accepted facts, explain
in your final response and leave the latest valid draft incomplete.
