"""Read-only, source-bound fact alignment before a new Behavior request."""
from pathlib import Path

from _lib.behavior.input_binding import evidence_timeline
from _lib.contracts.conversion_policy import digest
from _lib.runtime.case_core import read_json, sha256
from _lib.runtime.fact_versions import POINTER, current_facts, verified_reconciliation_lane_points


REVISION = 'reconciliation-alignment.v1'
SPATIAL_DOMAINS = {'actor_lane', 'lane_count', 'road_topology'}
ROAD_PATHS = ('world.road.',)


def road_question(world, question):
    """Locate a World-owned road fact without interpreting observation prose."""
    if not question or question.get('owner') == 'behavior':
        return None
    paths = question.get('world_paths') or []
    if not paths or not all(isinstance(path, str) and path.startswith(ROAD_PATHS)
                            for path in paths):
        return None
    roads = world.get('world', {}).get('road', {}).get('roads', [])
    indices = {int(path.split('roads[', 1)[1].split(']', 1)[0]) for path in paths
               if 'roads[' in path and path.split('roads[', 1)[1].split(']', 1)[0].isdigit()}
    if len(indices) > 1 or (indices and max(indices) >= len(roads)):
        return None
    if indices:
        road = roads[next(iter(indices))]
    elif len(roads) == 1:
        road = roads[0]
    else:
        locator = question.get('road_id') or (question.get('localization') or {}).get('road_id')
        area_indices = {int(path.split('roadside_areas[', 1)[1].split(']', 1)[0])
                        for path in paths if 'roadside_areas[' in path
                        and path.split('roadside_areas[', 1)[1].split(']', 1)[0].isdigit()}
        areas = world.get('world', {}).get('road', {}).get('roadside_areas', [])
        if not locator and len(area_indices) == 1 and max(area_indices) < len(areas):
            locator = areas[next(iter(area_indices))].get('road_id')
        matches = [row for row in roads if row.get('road_id') == locator]
        if len(matches) != 1:
            return None
        road = matches[0]
    road_id = road.get('road_id')
    if not road_id:
        return None
    if all('roadside_areas' in path for path in paths):
        field = 'shoulder_presence'
    elif any('lane_ids' in path or 'ego_direction_lanes' in path for path in paths):
        field = 'lane_count_boundary'
    else:
        field = 'road_boundary'
    return road_id, field


def _reviewed_road_sources(case, view, timeline):
    """Replay source-bound road explanations and scoped dependency handoffs."""
    if view is None:
        return set(), set(), {}, set()
    accepted, deferred, partial, contrary = set(), set(), {}, set()
    root = Path(case)/'diagnostics/question_review'
    for result_path in root.glob('alignment-*.result.json'):
        result = read_json(result_path)
        if result.get('revision') != 'reconciliation-world-point-review.v2' or result.get('effective_version') != view.version_id:
            continue
        request_path = result_path.with_name(result_path.name.replace('.result.json', '.request.json'))
        candidate_path = result_path.with_name(result_path.name.replace('.result.json', '.candidate.json'))
        if not request_path.is_file() or not candidate_path.is_file():
            continue
        request = read_json(request_path)
        candidate = read_json(candidate_path)
        body = dict(request)
        signature = body.pop('request_sha256', None)
        if (signature != digest(body) or result.get('request_sha256') != signature
                or result.get('candidate_sha256') != sha256(candidate_path)
                or result.get('road_decisions') != candidate.get('road_decisions')
                or request.get('timeline_sha256') != digest(timeline)
                or request.get('parent_version') != view.version_id):
            continue
        if any(not Path(path).is_file() or sha256(path) != expected
               for path, expected in request.get('source_hashes', {}).items()):
            continue
        if any(not Path(frame['path']).is_file() or sha256(frame['path']) != frame['sha256']
               for frame in request.get('source_frames', [])):
            continue
        for decision in result.get('road_decisions', []):
            for source in decision.get('sources', []):
                if decision.get('outcome') == 'contradiction' or source.get('assessment') == 'contradictory':
                    contrary.add((source['source_id'], source['frame_id']))
                if decision.get('outcome') == 'partially_explained' and source.get('assessment') == 'partial':
                    partial[(source['source_id'], source['frame_id'])] = source['components']
                if (decision.get('outcome') in {'explained', 'partially_explained'}
                        and source.get('assessment') in {'covered', 'visibility_limited'}):
                    accepted.add((source['source_id'], source['frame_id']))
                elif (decision.get('outcome') == 'evidence_insufficient'
                      and decision.get('road_fact') == 'shoulder_presence'
                      and decision.get('same_segment') is True
                      and source.get('assessment') == 'unresolved'):
                    deferred.add((source['source_id'], source['frame_id']))
    return accepted, deferred, partial, contrary


def enabled(case):
    path = Path(case)/'state/execution_policy.json'
    return path.is_file() and read_json(path).get('reconciliation_alignment') == REVISION


def _ego_lane_review_points(case, view, world, source_world, source_hash, timeline, replayed):
    """Select unconfirmed endpoints of a change in Ego's physical corridor."""
    from _lib.behavior.input_binding import accepted_ego_lane_points
    from _lib.contracts.lane_locations import lane_locations
    from _lib.behavior.compile import evidence_frames

    candidates = timeline.get('ego_lane_candidates', [])
    if not candidates or not source_world:
        return {}
    accepted = {row['frame_id']: row for row in accepted_ego_lane_points(case, view, replayed=replayed)}
    road = world['world']['road']
    locations = lane_locations(road)
    source_locations = lane_locations(source_world['world']['road'])
    layout = world['world'].get('metadata', {}).get('lane_layout_review', {})
    initial = layout.get('observation', {}).get('ego_initial', {})
    initial_lanes = [lane['lane_id'] for lane in road.get('lanes', [])
                     if lane.get('travel_direction', 'same_as_ego') == 'same_as_ego'
                     and locations.get(lane['lane_id'], (None, None))[1] == initial.get('ordinal_from_right')
                     and type(initial.get('ordinal_from_right')) is int]
    if len(initial_lanes) != 1:
        return {}
    initial_lane = initial_lanes[0]
    ego_road = locations[initial_lane][0]
    lane_map = layout.get('adjudication', {}).get('lane_id_map', {})
    frame_times = {row['frame_id']: row['time_s'] for row in evidence_frames(case, include_census=False)}
    known = {fid: initial_lane for fid in initial.get('evidence_frame_ids', []) if fid in frame_times}
    known.update((fid, row['value']) for fid, row in accepted.items() if fid in frame_times)
    if not known:
        return {}

    def corridor(lane_id):
        # Canonical physical ordinal on this road, independent of lane labels.
        owner, ordinal = locations.get(lane_id, (None, None))
        return ordinal if owner == ego_road else None

    by_frame = {}
    for row in candidates:
        fid, lane, ordinal = row['frame_id'], row['lane_id'], row['ordinal_from_right']
        if row['status'] != 'observed' or fid not in frame_times or row['world_sha256'] != source_hash:
            continue
        if lane is None:
            options = [lid for lid, (_, position) in source_locations.items()
                       if position == ordinal and corridor(lane_map.get(lid, lid)) is not None]
            if len(options) != 1:
                continue
            lane = options[0]
        if lane not in source_locations:
            continue
        source_position = source_locations[lane][1]
        if ordinal is not None and source_position is not None and ordinal != source_position:
            continue
        effective_lane = lane_map.get(lane, lane)
        position = corridor(effective_lane)
        if position is None:
            continue
        point = accepted.get(fid)
        if point:
            if (row['source_id'] not in point.get('review_source_ids', [])
                    and position != corridor(point['value'])):
                from _lib.contracts.v3 import ContractError
                raise ContractError(f'New Ego lane observation conflicts with the accepted point at {fid}',
                                    rule_id='fact_conflict')
            continue
        if fid in known:
            if position != corridor(known[fid]):
                from _lib.contracts.v3 import ContractError
                raise ContractError(f'Ego lane observation conflicts with the accepted initial lane at {fid}',
                                    rule_id='fact_conflict')
            continue
        by_frame.setdefault(fid, []).append(dict(row, effective_lane_id=effective_lane))

    selected = {}
    previous_position = previous_candidate = None
    for fid in sorted(known.keys() | by_frame.keys(), key=lambda fid: (frame_times[fid], fid)):
        if fid in known:
            previous_position, previous_candidate = corridor(known[fid]), None
            continue
        rows = by_frame[fid]
        positions = {corridor(row['effective_lane_id']) for row in rows}
        if previous_position is None:
            continue
        if positions != {previous_position}:
            if previous_candidate is not None:
                selected[previous_candidate] = by_frame[previous_candidate]
            selected[fid] = rows
        if len(positions) == 1:
            previous_position = next(iter(positions))
            previous_candidate = fid
    return selected


def inspect(case, *, timeline=None):
    """Compare known facts; preserve opaque prose for targeted review."""
    case = Path(case).resolve()
    timeline = evidence_timeline(case) if timeline is None else timeline
    view = current_facts(case) if (case/POINTER).is_file() else None
    reviewed_points = bool(view and verified_reconciliation_lane_points(case, view))
    if timeline.get('fact_version_id') != (view.version_id if view else None):
        raise ValueError('Reconciliation timeline targets another fact version')
    world = view.world if view else read_json(case/'state/world_context.json')
    registry = view.registry if view else read_json(case/'state/actor_registry.json')
    from _lib.contracts.census_adjudication import proof
    from _lib.contracts.behavior_census_scope import pending
    from _lib.contracts.core_lane_continuity import (_source_world, _source_world_path,
                                                      obligations)

    certificate = proof(case)
    scope_pending = pending(case, certificate)
    source_world = _source_world(case)
    source_path = _source_world_path(case)
    source_ref = (str(source_path), sha256(source_path)) if source_path.is_file() else None
    rows = []

    def record(actor_id, frame_id, field, status, action, sources, reason, *, values=None):
        row = dict(actor_id=actor_id, frame_id=frame_id, field=field,
                   fact_status=status, next_action=action, sources=sources, reason=reason)
        if values is not None:
            row['values'] = values
        rows.append(row)

    def ordinal(world_doc, lane_id):
        for road in world_doc.get('world', {}).get('road', {}).get('roads', []):
            for anchor in road.get('lane_knowledge', {}).get('anchors', []):
                if anchor.get('lane_id') == lane_id:
                    return anchor.get('ordinal_from_right')
        return None

    def point_claim(actor_id, frame_id, lane_id):
        return reviewed_points and any(row.get('actor_id') == actor_id and row.get('frame_id') == frame_id
                   and row.get('field') == 'actor_lane' and row.get('value') == lane_id
                   and row.get('frame_sha256') and row.get('source_sha256')
                   for row in (view.claims.values() if view else ()))

    if source_ref and source_world and (case/'state/lane_layout.commit.json').is_file():
        lane_map = world.get('world', {}).get('metadata', {}).get(
            'lane_layout_review', {}).get('adjudication', {}).get('lane_id_map', {})
        for old_lane, new_lane in lane_map.items():
            old_ordinal = ordinal(source_world, old_lane)
            new_ordinal = ordinal(world, new_lane)
            if old_ordinal is not None and new_ordinal is not None and old_ordinal != new_ordinal:
                record(None, None, 'lane_id_map', 'conflict', 'world_targeted_review',
                       [dict(path=source_ref[0], sha256=source_ref[1])],
                       'Lane ID mapping changes the physical lane ordinal',
                       values=dict(source_lane_id=old_lane, effective_lane_id=new_lane,
                                   source_ordinal=old_ordinal, effective_ordinal=new_ordinal))
        old_layout = source_world.get('world', {}).get('metadata', {}).get(
            'lane_layout_review', {}).get('observation', {})
        new_layout = world.get('world', {}).get('metadata', {}).get(
            'lane_layout_review', {}).get('observation', {})
        for old, new in [(old_layout.get('ego_initial'), new_layout.get('ego_initial'))]:
            if old and new and old.get('ordinal_from_right') != new.get('ordinal_from_right'):
                frames = set(old.get('evidence_frame_ids', [])) & set(new.get('evidence_frame_ids', []))
                for frame_id in sorted(frames):
                    lane_id = next((anchor.get('lane_id') for road in world['world']['road']['roads']
                                    for anchor in road.get('lane_knowledge', {}).get('anchors', [])
                                    if anchor.get('ordinal_from_right') == new['ordinal_from_right']), None)
                    decided = point_claim('ego', frame_id, lane_id)
                    record('ego', frame_id, 'actor_lane',
                           'adjudicated' if decided else 'conflict',
                           'none' if decided else 'world_targeted_review',
                           [dict(path=source_ref[0], sha256=source_ref[1]),
                            dict(path=str(view.world_path if view else case/'state/world_context.json'),
                                 sha256=sha256(view.world_path if view else case/'state/world_context.json'))],
                           'Original and effective World assign different physical lane ordinals in one frame',
                           values=dict(source_ordinal=old['ordinal_from_right'],
                                       effective_ordinal=new['ordinal_from_right']))
        original = {a.get('actor_id'): a for a in source_world.get('actor_hypotheses', [])}
        for actor in world.get('actor_hypotheses', []):
            aid = actor.get('actor_id')
            old = original.get(aid)
            frame_id = actor.get('first_seen_frame_id')
            if not old or not frame_id or old.get('first_seen_frame_id') != frame_id:
                continue
            before = ordinal(source_world, old.get('hypothesized_lane'))
            after = ordinal(world, actor.get('hypothesized_lane'))
            if before is None or after is None:
                continue
            if before == after:
                record(aid, frame_id, 'actor_lane', 'aligned', 'none',
                       [dict(path=source_ref[0], sha256=source_ref[1])],
                       'Original and effective World have one physical first-frame lane')
                continue
            decided = point_claim(aid, frame_id, actor.get('hypothesized_lane'))
            record(aid, frame_id, 'actor_lane',
                   'adjudicated' if decided else 'conflict',
                   'none' if decided else 'world_targeted_review',
                   [dict(path=source_ref[0], sha256=source_ref[1]),
                    dict(path=str(view.world_path if view else case/'state/world_context.json'),
                         sha256=sha256(view.world_path if view else case/'state/world_context.json'))],
                   'Original and effective World assign different physical lane ordinals in one frame',
                   values=dict(source_ordinal=before, effective_ordinal=after))

    # Claims are already published in the effective fact version. Compare only
    # one Actor, one frame and one property; later positions are motion input.
    claims_by_point = {}
    for claim in (view.claims.values() if view else ()):
        if claim.get('field') != 'actor_lane' or not claim.get('actor_id') or not claim.get('frame_id'):
            continue
        claims_by_point.setdefault((claim['actor_id'], claim['frame_id']), []).append(claim)
    effective_actors = {a.get('actor_id'): a for a in world.get('actor_hypotheses', [])}
    for actor in registry:
        aid = actor.get('actor_id')
        effective = effective_actors.get(aid)
        if not effective or actor.get('first_seen_frame_id') != effective.get('first_seen_frame_id'):
            continue
        fid = actor.get('first_seen_frame_id')
        registry_lane = ordinal(world, actor.get('hypothesized_lane'))
        world_lane = ordinal(world, effective.get('hypothesized_lane'))
        if fid and registry_lane is not None and world_lane is not None:
            record(aid, fid, 'actor_lane',
                   'aligned' if registry_lane == world_lane else 'conflict',
                   'none' if registry_lane == world_lane else 'world_targeted_review',
                   [dict(path=str(view.registry_path if view else case/'state/actor_registry.json'),
                         sha256=sha256(view.registry_path if view else case/'state/actor_registry.json'))],
                   'Effective Registry and World first-frame physical lanes '
                   + ('agree' if registry_lane == world_lane else 'disagree'),
                   values=dict(registry_ordinal=registry_lane, world_ordinal=world_lane))
    for (aid, fid), claims in claims_by_point.items():
        if reviewed_points:
            for claim in claims:
                if claim.get('fact_key', '').startswith('point:'):
                    record(aid, fid, 'actor_lane', 'adjudicated', 'none',
                           [dict(claim_id=claim.get('claim_id'),
                                 fact_version_id=view.version_id,
                                 source_path=claim.get('source_path'),
                                 source_sha256=claim.get('source_sha256'))],
                           'Source-bound single-frame lane decision is published in the effective version')
        positions = {ordinal(world, claim.get('value')) for claim in claims
                     if claim.get('value') is not None}
        if len(positions) > 1:
            record(aid, fid, 'actor_lane', 'conflict', 'world_targeted_review',
                   [dict(claim_id=c.get('claim_id'), fact_version_id=view.version_id)
                    for c in claims], 'Accepted claims disagree on one physical lane')
        expected = None
        if aid == 'ego':
            ego = world.get('world', {}).get('metadata', {}).get(
                'lane_layout_review', {}).get('observation', {}).get('ego_initial', {})
            if fid in ego.get('evidence_frame_ids', []):
                expected = ego.get('ordinal_from_right')
        elif aid in effective_actors and fid == effective_actors[aid].get('first_seen_frame_id'):
            expected = ordinal(world, effective_actors[aid].get('hypothesized_lane'))
        if expected is not None and positions and positions != {expected}:
            record(aid, fid, 'actor_lane', 'conflict', 'world_targeted_review',
                   [dict(claim_id=c.get('claim_id'), fact_version_id=view.version_id)
                    for c in claims], 'Accepted point claim differs from effective World physical lane')

    for path_text, expected_hash in timeline.get('batch_sha256', {}).items():
        path = Path(path_text)
        if not path.is_file() or sha256(path) != expected_hash:
            raise ValueError('Reconciliation Batch source changed')
        batch = read_json(path)
        for index, actor in enumerate(batch.get('registry_after', [])):
            aid, fid = actor.get('actor_id'), actor.get('first_seen_frame_id')
            effective = effective_actors.get(aid)
            if not effective or not fid or fid != effective.get('first_seen_frame_id'):
                continue
            before = ordinal(source_world or world, actor.get('hypothesized_lane'))
            after = ordinal(world, effective.get('hypothesized_lane'))
            if before is None or after is None:
                continue
            source = dict(source_id=f'{path.stem}#/registry_after/{index}/hypothesized_lane',
                          batch_sha256=expected_hash)
            if before == after:
                record(aid, fid, 'actor_lane', 'aligned', 'none', [source],
                       'Frozen Batch and effective World have one physical first-frame lane')
                continue
            decided = point_claim(aid, fid, effective.get('hypothesized_lane'))
            record(aid, fid, 'actor_lane', 'adjudicated' if decided else 'conflict',
                   'none' if decided else 'world_targeted_review',
                   [source],
                   'Frozen Batch first-frame lane differs from effective World physical lane',
                   values=dict(source_ordinal=before, effective_ordinal=after))

    questions = {q.get('question_id'): q for q in world.get('questions_to_verify', [])}
    (accepted_road_sources, deferred_road_sources, partial_road_sources,
     contrary_road_sources) = _reviewed_road_sources(case, view, timeline)
    from _lib.world.readiness import execution_dependencies, _overlapping_path
    parameters = read_json(case/'case.json').get('parameters', {}) if (case/'case.json').is_file() else {}
    preliminary = {row['issue_id']: row for row in
                   execution_dependencies(world, None, parameters)['decisions']}
    # A structured conflict remains an evidence gate, even when the question
    # has a future/roadside scope. Never infer absence of conflict from prose.
    reconciliation_path = case/'state/reconciliation.json'
    reconciliation = (view.reconciliation if view else read_json(reconciliation_path)
                      if reconciliation_path.is_file() else None)
    unresolved_conflicts = set((reconciliation or {}).get('unresolved_conflict_ids', []))
    conflict_paths = {row['world_path'] for path in timeline.get('batch_sha256', {})
                      for row in read_json(path).get('semantic_conflicts', [])
                      if (reconciliation is None or row.get('conflict_id') in unresolved_conflicts)
                      and row.get('world_path')}
    conflicted_questions = {qid for qid, question in questions.items()
                            if any(_overlapping_path(path, conflict) for path in
                                   [*question.get('world_paths', []), *question.get('related_world_paths', [])]
                                   for conflict in conflict_paths)}
    relevant = {'ego', *(a.get('actor_id') for a in registry if a.get('actor_id'))}
    conflicted_points = {(row['actor_id'], row['frame_id']) for row in rows
                         if row['field'] == 'actor_lane' and row['fact_status'] == 'conflict'}
    conflicted_actors = {actor_id for actor_id, _ in conflicted_points}
    for group in timeline.get('groups', []):
        if group['actor_id'] not in relevant:
            continue
        for source in group.get('sources', []):
            # The timeline owns the source inventory. Read only the cited Batch
            # object to learn its structured question/domain, never parse prose.
            sid = source['source_id']
            batch_id, _, pointer = sid.partition('#/observations/')
            question = None
            if pointer and source.get('kind') == 'observation_source_reason':
                index = pointer.split('/', 1)[0]
                if index.isdigit():
                    path = case/'batches'/f'{batch_id}.json'
                    observations = read_json(path).get('observations', [])
                    if int(index) < len(observations):
                        question = questions.get(observations[int(index)].get('question_id'))
            actor_id = group['actor_id']
            has_lane = (actor_id == 'ego' or
                        effective_actors.get(actor_id, {}).get('hypothesized_lane') is not None)
            target_ids = set(question.get('actor_ids') or []) if question else set()
            critical = bool(question and has_lane and
                            SPATIAL_DOMAINS & set(question.get('impact_domains', [])) and
                            (not target_ids or actor_id in target_ids or
                             (actor_id in conflicted_actors and
                              question.get('resolution') != 'resolved')) and
                            (question.get('resolution') != 'resolved' or
                             (actor_id, group['frame_id']) in conflicted_points))
            reviewed = reviewed_points and any(
                claim.get('fact_key') == f'point:{actor_id}:{group["frame_id"]}:actor_lane'
                and sid in claim.get('review_source_ids', [])
                for claim in (view.claims.values() if view else ()))
            record(group['actor_id'], group['frame_id'], 'source_text',
                   'adjudicated' if reviewed else 'unverified',
                   'none' if reviewed else
                   'world_targeted_review' if critical else 'behavior_source_review',
                   [dict(source_id=sid, batch_sha256=timeline['batch_sha256'].get(
                       str(case/'batches'/f'{batch_id}.json')), text=source['text'])],
                   'Original language is covered by a source-bound point review' if reviewed
                   else 'Original language has no deterministic spatial interpretation')

    for issue_id, issue in scope_pending.items():
        record(None, issue.get('frame_id'), 'actor_identity', 'unresolved',
               'census_scope_review', [dict(issue_id=issue_id)],
               'Execution scope or identity has no accepted disposition')
    for source in timeline.get('unresolved', []):
        if source.get('source_binding') == 'observation_actor_unbound':
            question = questions.get(source.get('question_id'))
            road = road_question(world, question)
            critical = bool(question and SPATIAL_DOMAINS & set(question.get('impact_domains', [])))
            explained = road and (source['source_id'], source.get('frame_id')) in accepted_road_sources
            deferred = road and (source['source_id'], source.get('frame_id')) in deferred_road_sources
            planning = preliminary.get(source.get('question_id'), {})
            deferred = deferred or bool(road and planning.get('decision') == 'deferred'
                                        and source.get('question_id') not in conflicted_questions
                                        and (source['source_id'], source.get('frame_id')) not in contrary_road_sources)
            contrary = (source.get('question_id') in conflicted_questions or
                        (source['source_id'], source.get('frame_id')) in contrary_road_sources)
            if contrary:
                explained = deferred = False
            parts = partial_road_sources.get((source['source_id'], source.get('frame_id')))
            source_path = case/'batches'/f'{source["source_id"].split("#/", 1)[0]}.json'
            record(None, source.get('frame_id'), 'road_source_text' if road else 'source_text',
                   'conflict' if contrary else 'explained' if explained else 'partially_confirmed' if parts else 'unverified',
                   'none' if explained else 'behavior_dependency_review' if deferred
                   else 'world_targeted_review' if critical else 'source_binding_review',
                   [dict(source_id=source['source_id'],
                         text=source.get('text', ''), question_id=source.get('question_id'),
                         batch_sha256=timeline.get('batch_sha256', {}).get(str(source_path)),
                         time_s=source.get('time_s'), **(dict(components=parts) if parts else {}))],
                   'Source-bound World road explanation preserves the original observation'
                   if explained else 'Road fact remains unknown; execution dependency is checked after Behavior'
                   if deferred else 'Original observation has no trustworthy Actor/frame fact binding',
                   values=dict(road_id=road[0], road_fact=road[1]) if road else None)
    for frame_id, sources in _ego_lane_review_points(case, view, world, source_world,
            source_ref[1] if source_ref else None, timeline, reviewed_points).items():
        record('ego', frame_id, 'actor_lane', 'unverified', 'world_targeted_review',
               sources, 'Unconfirmed Ego endpoint of a physical corridor change',
               values={'ego_lane_observation': True})
    motion = obligations(case)
    for obligation in motion['obligations']:
        record(obligation['actor_id'], None, 'actor_motion', 'handoff',
               'behavior_motion', [dict(obligation_id=obligation['obligation_id'])],
               'Cross-frame change remains a Behavior motion obligation')
    return dict(revision=REVISION, fact_version_id=view.version_id if view else None,
                timeline_sha256=digest(timeline), motion_obligation_sha256=motion['sha256'],
                motion_obligation_ids=[r['obligation_id'] for r in motion['obligations']],
                unresolved_source_ids=[r['source_id'] for r in timeline.get('unresolved', [])],
                scope_pending_issue_ids=sorted(scope_pending),
                rows=rows, blocking=[i for i, row in enumerate(rows)
                                     if row['next_action'] == 'world_targeted_review'],
                unverified_text=[i for i, row in enumerate(rows)
                                 if row['fact_status'] in {'unverified', 'partially_confirmed'}])
