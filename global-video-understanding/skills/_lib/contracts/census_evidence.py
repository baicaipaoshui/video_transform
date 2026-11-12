"""Deterministic proof requirements, computed before census image packing.

Temporal coverage schedules inspection; it never establishes visual identity.
The fixed gap and source evidence are independent of the selected image count.
"""
from pathlib import Path

from _lib.contracts.v3 import ContractError
from _lib.contracts.fidelity import frame_index
from _lib.runtime.case_core import read_json
from _lib.world.actor_facts import frames_for

REVISION = 'census-evidence.v1'
REUSE_REVISION = 'census-evidence.v2'
OBLIGATION_REVISION = 'census-evidence.v3'
POLICY_KEY = 'census_evidence_revision'
MAX_GAP_S = 1.5
MAX_FRAMES = 12


def upgrades(revision):
    versions = (REVISION, REUSE_REVISION, OBLIGATION_REVISION)
    return versions[versions.index(revision)+1:] if revision in versions else ()


def fact_frames(value, frames=None):
    """Structured observation references, never prose or generic image inventories."""
    ids = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {'frame_id', 'first_seen_frame_id', 'last_seen_frame_id'} and isinstance(child, str):
                ids.add(child)
            elif key == 'evidence_frame_ids' and isinstance(child, list):
                ids.update(child)
            else:
                ids.update(fact_frames(child, frames))
        # Valid observations may cite a frozen timestamp without a frame ID.
        # Preserve that evidence too, using the existing admission tolerance.
        if frames is not None:
            from _lib.contracts.frame_evidence import TOLERANCE_S
            for key in ('time_s', 'timestamp_s', 'first_seen_time_s', 'last_seen_time_s'):
                if key in value:
                    matches = {f['frame_id'] for f in frames if type(value[key]) in (int, float)
                               and abs(f['time_s']-value[key]) <= TOLERANCE_S}
                    if not matches:
                        raise EvidencePlanError('evidence_missing', timestamp=value[key])
                    ids.update(matches)
    elif isinstance(value, list):
        for child in value:
            ids.update(fact_frames(child, frames))
    return ids


def bridge_frames(decoded, required, historical):
    """Minimum inspection path between mandatory witnesses, independent of cap.

    Every edge is <= MAX_GAP_S. Ties prefer already supplied context, then
    chronological frame IDs. No observed transition or source is removable.
    """
    selected = set(required)
    anchors = [f for f in decoded if f['frame_id'] in required]
    for left, right in zip(anchors, anchors[1:]):
        segment = [f for f in decoded if left['time_s'] <= f['time_s'] <= right['time_s']]
        paths = {left['frame_id']: (0, 0, ())}
        for frame in segment[1:]:
            options = []
            fid = frame['frame_id']
            for previous in segment:
                if (previous['frame_id'] in paths and 0 < frame['time_s']-previous['time_s'] <= MAX_GAP_S):
                    count, fresh, chain = paths[previous['frame_id']]
                    options.append((count + (fid not in required),
                                    fresh + (fid not in historical and fid not in required), chain + (fid,)))
            if options:
                paths[fid] = min(options)
        if right['frame_id'] not in paths:
            raise EvidencePlanError('evidence_missing', gap_frame_ids=[left['frame_id'], right['frame_id']])
        selected.update(paths[right['frame_id']][2])
    return selected


class EvidencePlanError(ContractError):
    def __init__(self, reason, **details):
        super().__init__(reason, rule_id='identity.census.' + reason)
        self.details = dict(reason=reason, **details)


def unsubmitted(state):
    """A new selection rule cannot replace any already-reserved model work."""
    diagnostics = state.get('diagnostics', {})
    return (not state.get('attempts', {}).get('world_closure', 0)
        and not diagnostics.get('unresolved_submissions')
        and not any(r.get('stage') == 'world_closure'
                    for r in diagnostics.get('model_task_reservations', []))
        and not any(r.get('stage') == 'world_closure'
                    or r.get('status') in {'submitted', 'pending', 'running'}
                    or (r.get('error_code') in {'task_deadline_exceeded', 'task_status_unavailable'}
                        or r.get('recovered_from_poll_failure')) and not r.get('deadline_task_resolved')
                    for r in state.get('sessions', [])))


def build(case, registry, records, layout=None, *, confirmed_sightings=None, scope_coverage=False,
          revision=REVISION, alternative_actor_ids=(), source_world=None):
    if revision not in {REVISION, REUSE_REVISION, OBLIGATION_REVISION}:
        raise ContractError('Unsupported census evidence revision: ' + str(revision))
    case = Path(case)
    frames = frame_index(frames_for(case))
    decoded = sorted(frames.values(), key=lambda f: (f['time_s'], f['frame_id']))
    if len({f['time_s'] for f in decoded}) != len(decoded):
        raise EvidencePlanError('evidence_index_invalid')
    actors = {a['actor_id']: a for a in registry if a['actor_id'] != 'ego'}
    historical = {f['frame_id'] for f in read_json(case/'state/global_frames.json')['frames']}
    confirmed = {}
    observed = set()
    for spec in read_json(case/'manifest.json').get('batches', []):
        if (case/'batches'/f"{spec['batch_id']}.json").is_file():
            historical.update(f['frame_id'] for f in spec.get('frames', []))
    for path in sorted((case/'batches').glob('batch_*.json')):
        batch = read_json(path)
        if revision == OBLIGATION_REVISION:
            observed.update(fact_frames(batch, decoded))
        for frame in batch.get('vehicle_census', []):
            for row in frame['vehicles']:
                if row['binding'] != 'unresolved':
                    confirmed.setdefault(row['actor_id'], set()).add(frame['frame_id'])
    if confirmed_sightings is not None:
        confirmed = {}
        for fid, ids in confirmed_sightings.items():
            for aid in ids:
                confirmed.setdefault(aid, set()).add(fid)
    # Other records may suggest an alternative, but remain separate questions.
    targets = set().union(*(set(r['candidate_actor_ids']) for r in records))
    targets.update(alternative_actor_ids)
    if any(not r['candidate_actor_ids'] for r in records):
        targets.update(actors)
    if targets - set(actors):
        raise EvidencePlanError('evidence_actor_missing', actor_ids=sorted(targets-set(actors)))
    layout_ids = {f['frame_id'] for f in (layout or {}).get('focused_context', {}).get('evidence_frames', [])}
    references = historical | layout_ids | {r['frame_id'] for r in records}
    references.update(a['first_seen_frame_id'] for a in actors.values())
    references.update(fid for ids in confirmed.values() for fid in ids)
    if references - set(frames):
        raise EvidencePlanError('evidence_missing', frame_ids=sorted(references-set(frames)))
    order = lambda ids: sorted(ids, key=lambda fid: (frames[fid]['time_s'], fid))
    reasons = {}

    def require(ids, reason):
        for fid in ids:
            reasons.setdefault(fid, set()).add(reason)

    require(layout_ids, 'layout')
    if any(not r['candidate_actor_ids'] for r in records):
        require({decoded[0]['frame_id'], decoded[-1]['frame_id']}, 'full_window_exclusion_context')
    # Every registered identity is a possible duplicate for a proposed new car.
    # Its anchor is available for comparison, not automatically a trajectory.
    require({a['first_seen_frame_id'] for a in actors.values()}, 'duplicate_comparison_anchor')

    if revision == OBLIGATION_REVISION:
        # Generic global samples are inspection opportunities. Actual World and
        # Batch references (including unresolved/negative observations) are hard
        # requirements, independent of which candidate ultimately gets bound.
        world = read_json(case/'state/world_context.json') if source_world is None else source_world
        observed.update(fact_frames(world, decoded))
        observed.update(fact_frames(registry, decoded))
        observed.update(fid for ids in confirmed.values() for fid in ids)
        if observed - set(frames):
            raise EvidencePlanError('evidence_missing', frame_ids=sorted(observed-set(frames)))
        require(observed, 'recorded_observation')
        require({r['frame_id'] for r in records}, 'source_observation')
        mandatory = set(reasons)
        timeline = bridge_frames(decoded, mandatory, historical)
        require(timeline-mandatory, 'temporal_inspection_bridge')
        historical = timeline

    def link_ids(row, aid, end):
        source = row['frame_id']; anchor = actors[aid]['first_seen_frame_id']
        start = min(frames[source]['time_s'], frames[anchor]['time_s'])
        ids = {source, anchor} | {fid for fid in historical if start <= frames[fid]['time_s'] <= end}
        ids.update(fid for fid in confirmed.get(aid, set()) if start <= frames[fid]['time_s'] <= end)
        ids.update(f['frame_id'] for f in decoded if f['time_s'] == end)
        # Bridge long gaps without deleting source or transition evidence.
        while True:
            pairs = list(zip(order(ids), order(ids)[1:]))
            gap = next(((a, b) for a, b in pairs if frames[b]['time_s']-frames[a]['time_s'] > MAX_GAP_S), None)
            if gap is None:
                break
            a, b = gap
            choices = [f for f in decoded if frames[a]['time_s'] < f['time_s'] <= frames[a]['time_s']+MAX_GAP_S]
            if not choices:
                raise EvidencePlanError('evidence_missing', issue_id=row['issue_id'], gap_frame_ids=[a, b])
            ids.add(choices[-1]['frame_id'])
        return ids

    # Freeze the reusable pool before iterating over questions. It consists of
    # independently required frames, never of a budget-dependent thinning.
    reusable = set(reasons) | {r['frame_id'] for r in records}
    if revision in {REUSE_REVISION, OBLIGATION_REVISION}:
        for row in records:
            for aid in sorted(targets):
                at = frames[actors[aid]['first_seen_frame_id']]['time_s']
                start, end = sorted((row['time_s'], at))
                later = [fid for fid in confirmed.get(aid, set()) if frames[fid]['time_s'] > at]
                if row['time_s'] < at and later:
                    end = frames[order(later)[0]]['time_s']
                reusable.update(fid for fid in historical | confirmed.get(aid, set())
                                if start <= frames[fid]['time_s'] <= end)
    obligations = []
    for row in records:
        source = row['frame_id']; t = frames[source]['time_s']
        if t != row['time_s']:
            raise EvidencePlanError('evidence_index_invalid', issue_id=row['issue_id'])
        require({source}, row['issue_id'] + ':source')
        links = {}
        for aid in sorted(targets):
            anchor = actors[aid]['first_seen_frame_id']; at = frames[anchor]['time_s']
            end = max(t, at)
            if t < at:
                later = [fid for fid in confirmed.get(aid, set()) if frames[fid]['time_s'] > at]
                if not later and revision in {REUSE_REVISION, OBLIGATION_REVISION}:
                    # Nearby existing context is an inspection opportunity, not
                    # proof that this Actor is actually visible there.
                    candidates = [fid for fid in reusable if at < frames[fid]['time_s'] <= at + MAX_GAP_S]
                    fallback = next((f['frame_id'] for f in decoded if f['time_s'] > at), None)
                    if fallback:
                        candidates.append(fallback)
                    if candidates:
                        # Extending to a shared witness may itself require more
                        # transition frames. Count those too; ties favor reuse.
                        later = [min(candidates, key=lambda fid: (
                            len(link_ids(row, aid, frames[fid]['time_s']) - reusable),
                            fid not in reusable, frames[fid]['time_s'], fid))]
                if not later:
                    later = [f['frame_id'] for f in decoded if f['time_s'] > at]
                if later:
                    end = frames[order(later)[0]]['time_s']
            ids = link_ids(row, aid, end)
            links[aid] = order(ids)
            require(ids, row['issue_id'] + ':link:' + aid)
        # Inspect immediately before the earliest candidate's first appearance.
        # This is a temporal witness opportunity, not a claim of co-visibility.
        future = [actors[a]['first_seen_frame_id'] for a in targets if frames[actors[a]['first_seen_frame_id']]['time_s'] > t]
        if future and t < min(frames[actors[a]['first_seen_frame_id']]['time_s'] for a in targets):
            first = order(future)[0]
            before = [f['frame_id'] for f in decoded if t < f['time_s'] < frames[first]['time_s']]
            if before:
                require({before[-1]}, row['issue_id'] + ':comparison_context')
        obligations.append(dict(issue_id=row['issue_id'], source_frame_id=source,
            links=links, comparison_actor_ids=sorted(actors)))
    if len(reasons) < 2 and len(decoded) >= 2:
        require({decoded[-1]['frame_id']}, 'window_context')
    if scope_coverage:
        from _lib.contracts.census_scope import coverage_frames
        additional = coverage_frames(case, reasons)-set(reasons)
        require(additional, 'full_case_scope_context')
    if not 2 <= len(reasons) <= MAX_FRAMES:
        raise EvidencePlanError('evidence_budget_exceeded', required_count=len(reasons), limit=MAX_FRAMES,
            required_frame_ids=order(reasons))
    result = dict(revision=revision, max_gap_s=MAX_GAP_S, max_frames=MAX_FRAMES,
        review_actor_ids=sorted(targets), obligations=obligations,
        selected_frame_ids=order(reasons), frame_reasons={fid:sorted(reasons[fid]) for fid in order(reasons)})
    if revision == OBLIGATION_REVISION:
        result['mandatory_frame_ids'] = order(mandatory)
    return result
