"""Bind observation-only omissions to the full source and execution scope."""
import copy
import re
from pathlib import Path

from _lib.contracts.conversion_policy import digest
from _lib.contracts.v3 import ContractError
from _lib.contracts.core_lane_continuity import validate_source_coverage
from _lib.contracts.core_scene_scope import actor_references
from _lib.contracts.behavior_handoff import transient_actors

REVISION = 'core-projection-source-disposition.v3'
V2_REVISION = 'core-projection-source-disposition.v2'
LEGACY_REVISION = 'core-projection-source-disposition.v1'
ANALYSIS_BINDING_KEY = 'core_projection_source_binding'
ANALYSIS_BINDING_REVISION = 'core-projection-analysis-binding.v1'
_LANE = re.compile(r'\blane_id=(lane_\d+)\b')
_VISIBILITY_OCCLUSION = re.compile(
    r'后续.{0,24}(?:遮挡|被挡)|此前.{0,24}(?:遮挡|被挡)|'
    r'(?:later|subsequent|previously|remains?|continues?).{0,32}'
    r'(?:occlud|hidden behind|blocked by)', re.I)


def _fail(actor_id, reason):
    error = ContractError(reason, rule_id='core_scene.execution_scope', actor_id=actor_id)
    error.diagnostics = dict(actor_id=actor_id, reason=reason)
    raise error


def _unresolved_review_dependency(row, actor_id):
    alternatives = set((row.get('identity_assessment') or {}).get('alternative_actor_ids', []))
    linked = (set(row.get('candidate_actor_ids', []))
              | set(row.get('affected_actor_ids', [])) | alternatives)
    if actor_id not in linked:
        return False
    proof = row.get('scope_proof')
    if not isinstance(proof, dict):
        proof = {}
    if (row.get('outcome') == 'distinct' and proof.get('distinct') is True
            and not row.get('candidate_actor_ids') and not alternatives
            and not row.get('affected_event_ids')
            and {proof.get('actor_id'), proof.get('compared_actor_id')}
                == set(row.get('affected_actor_ids', []))):
        return False
    return True


def _local_question(item, actor_id):
    """An unknown own lane is not a road or another Actor's dependency."""
    return (item.get('resolution') not in {'resolved', 'confirmed', 'non_core'}
            and item.get('actor_ids') == [actor_id]
            and (set(item.get('impact_domains', [])) == {'actor_lane'}
                 and item.get('world_paths') == [
                     f'world.actor_hypotheses.{actor_id}.hypothesized_lane']
                 or set(item.get('impact_domains', [])) == {'executor_primitive'}
                 and item.get('subject_field') == 'actor_motion'
                 and not item.get('world_paths'))
            and not any(item.get(key) for key in
                        ('affected_event_ids', 'related_world_paths', 'scope_binding',
                         'event_ids', 'event_id', 'census_issue_ids')))


def _owned_resolution(item, actor_id, behavior, frame_ids):
    """Replay a structured Behavior verdict for its own World ambiguity."""
    challenge_id = item.get('ambiguity_id')
    if (not challenge_id or item.get('owner') != 'behavior'
            or item.get('resolution') in {'resolved', 'confirmed', 'non_core'}
            or item.get('actor_ids') != [actor_id]
            or set(item.get('impact_domains', [])) != {'executor_primitive'}
            or item.get('subject_field') not in {'actor_state', 'actor_motion'}):
        return None
    matches = [row for row in behavior.get('challenge_resolutions', [])
               if row.get('challenge_id') == challenge_id]
    if len(matches) != 1:
        return None
    resolution = matches[0]
    evidence = resolution.get('evidence_frame_ids')
    if (resolution.get('resolution') not in {'resolved', 'confirmed', 'non_core'}
            or resolution.get('execution_effect') != 'retain_actor_state'
            or not isinstance(evidence, list) or not evidence
            or len(evidence) != len(set(evidence)) or not set(evidence) <= frame_ids
            or any(not isinstance(resolution.get(key), str)
                   or not resolution[key].strip()
                   for key in ('decision', 'rationale', 'consistency'))):
        return None
    return dict(issue_id=challenge_id, issue_sha256=digest(item),
                resolution_sha256=digest(resolution))


def execution_required_actors(world, behavior, full, timeline):
    """Retain a declared execution dependency only when its observed track replays."""
    world_actors = {a['actor_id']: a for a in world.get('actor_hypotheses', [])}
    full_actors = {a['actor_id']: a for a in full.get('actors', [])}
    groups = {(g['actor_id'], g['frame_id']): g for g in timeline.get('groups', [])}
    required = set()
    for actor in behavior['actors']:
        aid = actor['actor_id']
        claim = actor.get('execution_scope_evidence') or {}
        if aid == 'ego' or claim.get('kind') != 'execution_relevant':
            continue
        points = actor.get('trajectory', [])
        source_points = {p.get('frame_id'): p for p in full_actors.get(aid, {}).get('trajectory', [])}
        if (actor.get('actor_type') != 'vehicle' or actor.get('simulation_role') != 'participant'
                or claim.get('no_interaction') is not False or not points
                or len({p.get('frame_id') for p in points}) != len(points)
                or set(claim.get('visible_frame_ids', [])) != {p.get('frame_id') for p in points}
                or aid not in world_actors):
            _fail(aid, 'Execution-relevant Actor lacks a replayable observed track')
        world_lane = world_actors[aid].get('hypothesized_lane')
        for point in points:
            frame_id = point.get('frame_id')
            group = groups.get((aid, frame_id))
            source_point = source_points.get(frame_id)
            if (group is None or source_point is None
                    or group['source_observation_id'] not in source_point.get('source_observation_ids', [])
                    or not any(s.get('kind') == 'vehicle_description'
                               and s.get('status') == 'observed'
                               for s in group.get('sources', []))
                    or point.get('lane_id') is None
                    or world_lane is not None and point['lane_id'] != world_lane):
                _fail(aid, 'Execution-relevant Actor track lacks matching World/Batch sources')
        required.add(aid)
    return required


def required_background_actors(world, behavior, frames, timeline=None):
    """Retain every background vehicle not discharged by an existing scope proof."""
    from _lib.contracts.actors import is_vehicle
    from _lib.runtime.execution_scope import opposing_preservation_ids

    opposing = opposing_preservation_ids(world, behavior)
    transient = transient_actors(world, behavior, frames)
    if timeline is not None:
        sourced = {(group.get('actor_id'), group.get('frame_id'))
                   for group in timeline.get('groups', []) if any(
                       source.get('kind') == 'vehicle_description'
                       and source.get('status') == 'observed'
                       for source in group.get('sources', []))}
        transient = {aid for aid in transient if all(
            (aid, point.get('frame_id')) in sourced
            for point in next(actor for actor in behavior['actors']
                              if actor['actor_id'] == aid).get('trajectory', []))}
    # ``opposing_preservation_ids`` is already the existing source-replayed
    # physical-isolation proof.  It does not depend on the Batch-only Actor
    # timeline, which legitimately has no row for some World-registered
    # opposing vehicles.  Transient same-direction exclusions still require
    # complete positive timeline coverage.
    safely_excluded = opposing | transient
    return {actor['actor_id'] for actor in behavior.get('actors', [])
            if actor['actor_id'] != 'ego' and is_vehicle(actor)
            and actor.get('simulation_role') == 'background'
            and actor['actor_id'] not in safely_excluded}


def make(case, world, behavior, full, core_actor_ids, frames, timeline, review,
         representatives=(), *, revision=REVISION):
    """Fail closed before dropping any registered vehicle from executable Analysis."""
    if revision not in {REVISION, V2_REVISION, LEGACY_REVISION}:
        raise ContractError('Unknown core source disposition revision',
                            rule_id='core_scene.execution_scope')
    validate_source_coverage(case, full, timeline=timeline, world_override=world)
    actors = {a['actor_id']: a for a in behavior['actors'] if a['actor_id'] != 'ego'}
    full_actors = {a['actor_id']: a for a in full['actors']}
    representative_ids = {row['actor_id'] for row in representatives}
    if (len(representative_ids) != len(representatives)
            or representative_ids & set(actors)
            or set(full_actors) != set(actors) | representative_ids):
        raise ContractError('Full Analysis Actor set differs from source Behavior',
                            rule_id='core_scene.execution_scope')
    core = set(core_actor_ids)
    omitted = set(actors) - core
    groups = timeline.get('groups', [])
    def directly_observed(group):
        return any(source.get('kind') == 'vehicle_description'
                   and source.get('status') == 'observed'
                   for source in group.get('sources', []))
    all_by_actor = {aid: [g for g in groups if g['actor_id'] == aid] for aid in omitted}
    by_actor = ({aid: [g for g in all_by_actor[aid] if directly_observed(g)]
                 for aid in omitted} if revision == REVISION else all_by_actor)
    world_actors = {a['actor_id']: a for a in world.get('actor_hypotheses', [])}
    ego = behavior.get('ego') or next(a for a in behavior['actors']
                                      if a['actor_id'] == 'ego')
    core_lanes = {p['lane_id'] for actor in [ego,
                  *(full_actors[aid] for aid in core - {'ego'} if aid in full_actors)]
                  for p in actor.get('trajectory', []) if p.get('lane_id')}
    core_lanes.update(lane for event in behavior.get('events', [])
                      for lane in (event.get('from_lane_id'), event.get('to_lane_id')) if lane)
    review_rows = (review or {}).get('decisions', [])
    frame_ids = {frame['frame_id'] for frame in frames}
    from _lib.runtime.execution_scope import opposing_preservation_ids
    opposing_background = (opposing_preservation_ids(world, behavior)
                           if revision == REVISION else set())
    rows = []
    for aid in sorted(omitted):
        actor = actors[aid]
        if actor.get('actor_type') != 'vehicle':
            if by_actor[aid]:
                _fail(aid, 'An omitted non-vehicle has uncovered Actor/frame sources')
            continue
        claim = actor.get('execution_scope_evidence') or {}
        opposing = aid in opposing_background
        if (not opposing
                and claim.get('kind') not in {'edge_fragment', 'distant_transient'}):
            _fail(aid, 'Omitted vehicle lacks a complete background scope claim')
        if not opposing and _VISIBILITY_OCCLUSION.search(claim.get('reason', '')):
            _fail(aid, 'Occlusion leaves later interaction or identity unresolved')
        if any(aid in actor_references(item, set(actors)) for item in
               [*behavior.get('events', []), *behavior.get('executor_primitives', []),
                *world.get('relations', []), *world.get('constraints', []),
                *world.get('interactions', [])]):
            _fail(aid, 'Omitted Actor participates in a retained relation or action')
        if any(_unresolved_review_dependency(row, aid) for row in review_rows):
            _fail(aid, 'Omitted Actor has an unresolved Census or event dependency')
        local = []
        resolved_world_issues = []
        resolved_challenges = []
        filtered_world = copy.deepcopy(world)
        for key in ('questions_to_verify', 'ambiguities'):
            filtered = []
            for item in filtered_world.get(key, []):
                if aid not in actor_references(item, set(actors)):
                    filtered.append(item)
                elif revision == REVISION and item.get('resolution') in {
                        'resolved', 'confirmed', 'non_core'}:
                    resolved_world_issues.append(dict(
                        issue_id=item.get('question_id', item.get('ambiguity_id')),
                        issue_sha256=digest(item)))
                elif _local_question(item, aid):
                    local.append(dict(issue_id=item.get('question_id', item.get('ambiguity_id')),
                                      issue_sha256=digest(item)))
                elif revision == REVISION and (resolved := _owned_resolution(
                        item, aid, behavior, frame_ids)) is not None:
                    resolved_challenges.append(resolved)
                else:
                    _fail(aid, 'Unresolved World issue may affect execution scope')
            filtered_world[key] = filtered
        if not opposing and aid not in transient_actors(filtered_world, behavior, frames):
            _fail(aid, 'Frame-bound Behavior scope does not authorize omission')
        points = actor.get('trajectory', [])
        lane = world_actors.get(aid, {}).get('hypothesized_lane')
        observed = by_actor[aid]
        nontrajectory = [group for group in all_by_actor[aid] if group not in observed]
        if revision == REVISION and not opposing and any(
                group.get('frame_id') not in claim.get('checked_frame_ids', [])
                or group.get('frame_id') in claim.get('visible_frame_ids', [])
                for group in nontrajectory):
            _fail(aid, 'Non-trajectory source is inconsistent with the frame-bound scope claim')
        if (_VISIBILITY_OCCLUSION.search(world_actors.get(aid, {}).get('identity_description', ''))
                or any(_VISIBILITY_OCCLUSION.search(s.get('text', '')) for group in observed
                       for s in group.get('sources', []))):
            _fail(aid, 'Source observations leave occlusion unresolved')
        if (not opposing and (not observed or not points
                or {p['frame_id'] for p in points} != {g['frame_id'] for g in observed})):
            _fail(aid, 'Independent source observations do not cover the omitted Actor')
        scope_reviews = []
        if opposing:
            scope_reviews = []
        elif lane is None and revision in {REVISION, V2_REVISION}:
            from _lib.runtime.question_review import bound_scope_relation
            if any(p.get('lane_id') is not None for p in points):
                _fail(aid, 'Unknown World lane contradicts a known Behavior lane')
            ego_events = [event for event in behavior.get('events', [])
                          if 'ego' in event.get('involved_actor_ids', [])]
            if not ego_events:
                _fail(aid, 'Unknown-lane omission has no bound Ego core event')
            observed_frames = {g['frame_id'] for g in observed}
            for event in ego_events:
                relation = bound_scope_relation(case, aid, event['event_id'], behavior)
                if relation is None or relation['scope_relation']['outcome'] != 'path_separated':
                    _fail(aid, 'World has not proved this Actor separate from the Ego core event')
                occupancy = set(relation['scope_relation']['occupancy_frame_ids'])
                if (not occupancy <= observed_frames
                        or any(not any(s.get('kind') == 'vehicle_description'
                                       and s.get('status') == 'observed'
                                       for s in group.get('sources', []))
                               for group in observed if group['frame_id'] in occupancy)):
                    _fail(aid, 'World visual verdict lacks bound Batch Actor occupancy sources')
                scope_reviews.append(relation)
        elif (lane is None or lane in core_lanes
              or any(p.get('lane_id') != lane for p in points)
              or any(not any(s.get('kind') == 'vehicle_description'
                                 and s.get('status') == 'observed'
                                 and lane in _LANE.findall(s.get('text', ''))
                                 for s in g.get('sources', [])) for g in observed)):
            _fail(aid, 'Independent Batch/World lane evidence does not separate this Actor from the core path')
        full_points = {p['frame_id']: p for p in full_actors[aid].get('trajectory', [])}
        source_rows = []
        for group in observed:
            point = full_points.get(group['frame_id'])
            if (point is None or group['source_observation_id'] not in
                    point.get('source_observation_ids', [])):
                _fail(aid, 'Full Analysis cannot trace an omitted source point')
            source_rows.append(dict(source_observation_id=group['source_observation_id'],
                frame_id=group['frame_id'], time_s=group['time_s'],
                group_sha256=digest(group), full_point_sha256=digest(point)))
        scope_evidence = (dict(kind='opposing_preservation',
                               direction_evidence=actor.get('direction_evidence'))
                          if opposing else claim)
        row = dict(actor_id=aid, handling='observation_retained_execution_excluded',
                         lane_id=lane, scope_evidence_sha256=digest(scope_evidence),
                         world_actor_sha256=digest(world_actors[aid]),
                         full_actor_sha256=digest(full_actors[aid]),
                         unresolved_local_questions=local,
                         source_rows=sorted(source_rows, key=lambda r: r['source_observation_id']))
        if opposing:
            row['background_scope_kind'] = 'opposing_preservation'
        if resolved_challenges:
            row['behavior_resolved_challenges'] = sorted(
                resolved_challenges, key=lambda item: item['issue_id'])
        if resolved_world_issues:
            row['resolved_world_issues'] = sorted(
                resolved_world_issues, key=lambda item: item['issue_id'])
        if nontrajectory:
            row['nontrajectory_source_rows'] = sorted((dict(
                source_observation_id=group['source_observation_id'],
                frame_id=group['frame_id'], time_s=group['time_s'],
                group_sha256=digest(group)) for group in nontrajectory),
                key=lambda item: item['source_observation_id'])
        if scope_reviews:
            row['world_scope_reviews'] = scope_reviews
            row['uncertainties_retained'] = ['exact_lane_unknown', 'later_motion_unknown']
        rows.append(row)
    proof = dict(revision=revision, world_sha256=digest(world),
                 behavior_sha256=digest(behavior), full_analysis_sha256=digest(full),
                 timeline_sha256=digest(timeline), review_sha256=digest(review),
                 core_actor_ids=sorted(core), representatives_sha256=digest(representatives),
                 excluded=rows)
    return dict(proof, sha256=digest(proof))


def validate(case, world, behavior, full, projected, frames, timeline, review, proof,
             representatives=()):
    if not isinstance(proof, dict) or proof != make(
            case, world, behavior, full,
            {a['actor_id'] for a in [projected['ego'], *projected['actors']]},
            frames, timeline, review, representatives,
            revision=proof.get('revision', REVISION)):
        raise ContractError('Core source disposition changed or expired',
                            rule_id='core_scene.execution_scope')
    return {source['source_observation_id'] for row in proof['excluded']
            for key in ('source_rows', 'nontrajectory_source_rows')
            for source in row.get(key, [])}


def bind_analysis(case, owner_case, candidate, fact_version, source_hashes,
                  behavior, normalizations, full_path, proof, representatives=()):
    """Bind a private Core Analysis to the same frozen sources as its omission proof.

    Core Approximate intentionally does not commit its raw Behavior as strict
    Case state.  This binding gives downstream bounded execution an equivalent
    replayable source authority without changing that admission boundary.
    """
    from _lib.runtime.case_core import sha256
    body = dict(
        revision=ANALYSIS_BINDING_REVISION,
        case_path=str(Path(case).resolve()),
        owner_case_path=str(Path(owner_case).resolve()),
        candidate_path=str(Path(candidate).resolve()),
        fact_version=fact_version,
        source_hashes={str(Path(name).resolve()): value
                       for name, value in source_hashes.items()},
        normalized_behavior_digest=digest(behavior),
        normalizations=copy.deepcopy(normalizations),
        full_analysis_path=str(Path(full_path).resolve()),
        full_analysis_file_sha256=sha256(full_path),
        source_disposition=copy.deepcopy(proof),
        representatives=copy.deepcopy(list(representatives)),
    )
    return dict(body, sha256=digest(body))


def validate_analysis_binding(analysis):
    """Replay a Core Approximate source binding before bounded conversion."""
    binding = (analysis.get('metadata') or {}).get(ANALYSIS_BINDING_KEY)
    if (not isinstance(binding, dict)
            or binding.get('revision') != ANALYSIS_BINDING_REVISION
            or binding.get('sha256') != digest({k: v for k, v in binding.items()
                                                if k != 'sha256'})):
        raise ContractError('Core projection Analysis lacks a valid source binding',
                            rule_id='core_scene.execution_scope')
    from _lib.runtime.case_core import read_json, sha256
    for name, expected in binding.get('source_hashes', {}).items():
        path = Path(name)
        if not path.is_absolute() or not path.is_file() or sha256(path) != expected:
            raise ContractError('Core projection source changed: ' + str(path),
                                rule_id='core_scene.execution_scope')
    case = Path(binding['case_path']).resolve(strict=True)
    owner = Path(binding['owner_case_path']).resolve(strict=True)
    candidate = Path(binding['candidate_path']).resolve(strict=True)
    if (not candidate.is_file()
            or binding.get('source_hashes', {}).get(str(candidate)) != sha256(candidate)):
        raise ContractError('Core projection candidate is not source-bound',
                            rule_id='core_scene.execution_scope')
    from _lib.runtime.fact_versions import current_facts
    view = current_facts(case, binding['fact_version'])
    from _lib.behavior.compile import evidence_frames
    frames = evidence_frames(case)
    from _lib.contracts.census_scope import split
    raw, review = split(read_json(candidate))
    from _lib.runtime.core_approximate_delivery import prepare_source_behavior
    behavior, normalizations = prepare_source_behavior(owner, view, raw, frames)
    if (digest(behavior) != binding.get('normalized_behavior_digest')
            or normalizations != binding.get('normalizations')):
        raise ContractError('Core projection Behavior normalization changed',
                            rule_id='core_scene.execution_scope')
    full_path = Path(binding['full_analysis_path']).resolve(strict=True)
    if (not full_path.is_file()
            or sha256(full_path) != binding.get('full_analysis_file_sha256')):
        raise ContractError('Core projection full Analysis changed',
                            rule_id='core_scene.execution_scope')
    full = read_json(full_path)
    from _lib.behavior.input_binding import current_evidence_timeline
    timeline = current_evidence_timeline(case)
    proof = binding.get('source_disposition')
    validate(case, view.world, behavior, full, analysis, frames, timeline, review,
             proof, binding.get('representatives', []))
    bounded = (analysis.get('metadata') or {}).get('bounded_execution') or {}
    if bounded.get('behavior_sha256') != digest(behavior):
        raise ContractError('Bounded execution differs from the Core source Behavior',
                            rule_id='core_scene.execution_scope')
    return digest(dict(binding_sha256=binding['sha256'],
                       core_actor_ids=proof['core_actor_ids']))
