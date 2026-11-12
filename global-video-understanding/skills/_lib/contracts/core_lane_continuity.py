"""Preserve a bound Actor's directly witnessed cross-lane motion.

Only a narrow, physical-boundary grammar is interpreted. Unrecognized prose
does not become a lane claim; source files and frame times remain unchanged.
"""
from __future__ import annotations

import re
from pathlib import Path

from _lib.runtime.case_core import read_json, sha256
from _lib.contracts.conversion_policy import digest

REVISION = 'motion-obligations.v1'


_BETWEEN = re.compile(r'wheel area\s+(?:lies\s+)?between\s+(?:the\s+)?'
                      r'([A-Za-z][A-Za-z0-9_]*)\s+and\s+(?:the\s+)?'
                      r'([A-Za-z][A-Za-z0-9_]*)\s+rows', re.I)
_LANE = re.compile(r'\blane_id=(lane_\d+)\b')
_ORDINAL = re.compile(r'\bordinal_from_right=(\d+)\b')
_BOUND_LANE = re.compile(r'\b(actor_\d+)\s+in\s+(lane_\d+)\b', re.I)


def _source_world_path(case):
    """Resolve the World immediately preceding this layout adjudication."""
    case = Path(case)
    commit_path = case/'state/lane_layout.commit.json'
    if not commit_path.is_file():
        return case/'state/world_context.json'
    commit = read_json(commit_path)
    parent_version = commit.get('fact_parent_version')
    parent_manifest = commit.get('fact_parent_manifest_sha256')
    if parent_version is not None or parent_manifest is not None:
        if not isinstance(parent_version, str) or not isinstance(parent_manifest, str):
            raise ValueError('Frozen pre-layout World is missing or changed')
        from _lib.runtime.fact_versions import FactVersionError, facts_at
        try:
            before = facts_at(case, parent_version, parent_manifest).world_path
        except FactVersionError as error:
            raise ValueError('Frozen pre-layout World is missing or changed') from error
    else:
        # Old frozen commits predate fact-version lineage and retain their
        # original, single-generation source contract.
        before = case/'diagnostics/world_census_adjudication/initial/before/world.json'
    if not before.is_file() or sha256(before) != commit.get('old_world_sha256'):
        raise ValueError('Frozen pre-layout World is missing or changed')
    return before


def _source_world(case):
    path = _source_world_path(case)
    if not path.is_file():
        return None
    return read_json(path)


def witnesses(case):
    """Return only source-bound changes with an observed start and later lane."""
    case = Path(case)
    source_world_path = _source_world_path(case)
    world = read_json(source_world_path) if source_world_path.is_file() else None
    if world is None:
        return []
    layout = world.get('layout_observation') or world.get('world', {}).get('metadata', {}).get(
        'lane_layout_review', {}).get('observation', {})
    bounds = layout.get('boundaries_right_to_left', [])
    if not bounds:
        return []
    frames_path = case/'state/global_frames.json'
    frame_times = ({row['frame_id']: row['time_s'] for row in read_json(frames_path)['frames']}
                   if frames_path.is_file() else {})
    lanes = {row.get('lane_id'): row.get('ordinal_from_right')
             for road in world.get('world', {}).get('road', {}).get('roads', [])
             for row in road.get('lane_knowledge', {}).get('anchors', [])}
    by_ordinal = {ordinal: lane_id for lane_id, ordinal in lanes.items()}
    sources = {}
    source_ids = {}
    from _lib.runtime.fact_versions import POINTER, current_facts
    view = current_facts(case) if (case/POINTER).is_file() else None
    point_claims = {(row['actor_id'], row['frame_id']): row
                    for row in (view.claims.values() if view else [])
                    if row.get('fact_key', '').startswith('point:')
                    and row.get('field') == 'actor_lane'
                    and row.get('actor_id') != 'ego'}
    for actor in world.get('actor_hypotheses', []):
        aid, lane = actor.get('actor_id'), actor.get('hypothesized_lane')
        proof = world.get('field_evidence', {}).get(f'world.actors.{aid}.hypothesized_lane', [])
        observed = [row for row in proof if row.get('status') == 'observed' and row.get('frame_id')]
        if aid and lane in lanes and observed:
            first = next((row for row in observed if row['frame_id'] in frame_times), None)
            if first:
                sources[aid] = (lanes[lane], first['frame_id'], lane,
                                first.get('reason', ''), str(source_world_path))
                source_ids[aid] = f'world:{aid}:hypothesized_lane:{first["frame_id"]}'
    for (aid, fid), claim in point_claims.items():
        if aid in sources and fid == sources[aid][1] and claim['value'] in lanes:
            sources[aid] = (lanes[claim['value']], fid, claim['value'],
                            claim['reason'], str(case/claim['source_path']))
            source_ids[aid] = claim['fact_key']
    found = []
    for path in sorted((case/'batches').glob('batch_*.json')):
        batch = read_json(path)
        # A prose mention alone cannot bind an observation to an Actor. Require
        # the same frozen Batch/frame to identify that object as an existing Actor.
        bound_objects = {}
        for frame in batch.get('vehicle_census', []):
            fid = frame.get('frame_id')
            if fid not in frame_times or frame.get('time_s') != frame_times[fid]:
                continue
            for vehicle in frame.get('vehicles', []):
                if (vehicle.get('binding') == 'existing' and vehicle.get('actor_id') in sources
                        and vehicle.get('object_id')):
                    bound_objects.setdefault((fid, vehicle['actor_id']), set()).add(vehicle['object_id'])
        # Tighten the source-side bound only with an explicit, observed lane
        # claim for this already registered Actor. Relative prose is not enough.
        for observation in batch.get('observations', []):
            for evidence in observation.get('evidence', []):
                fid = evidence.get('frame_id')
                reason = evidence.get('reason', '')
                if (evidence.get('status') != 'observed' or fid not in frame_times
                        or evidence.get('time_s') != frame_times[fid]):
                    continue
                for match in _BOUND_LANE.finditer(reason):
                    aid, lane = match.groups()
                    if (aid in sources and not any(key[0] == aid for key in point_claims)
                            and lanes.get(lane) == sources[aid][0]
                            and len(bound_objects.get((fid, aid), ())) == 1
                            and frame_times[fid] > frame_times[sources[aid][1]]):
                        sources[aid] = (lanes[lane], fid, lane, reason, str(path))
                        source_ids[aid] = (f'{path.stem}:{fid}:'
                                           f'{next(iter(bound_objects[(fid, aid)]))}')
        for frame in batch.get('vehicle_census', []):
            fid = frame.get('frame_id')
            if fid not in frame_times or frame.get('time_s') != frame_times[fid]:
                continue
            for vehicle in frame.get('vehicles', []):
                aid = vehicle.get('actor_id') if vehicle.get('binding') == 'existing' else None
                if (aid not in sources or not fid or not vehicle.get('object_id')
                        or len(bound_objects.get((fid, aid), ())) != 1):
                    continue
                description = vehicle.get('description', '')
                lane_match, ordinal_match = _LANE.search(description), _ORDINAL.search(description)
                accepted = point_claims.get((aid, fid))
                if accepted and path != case/accepted['source_path']:
                    continue
                if accepted:
                    ordinal = lanes[accepted['value']]
                elif lane_match and ordinal_match and lanes.get(lane_match.group(1)) == int(ordinal_match.group(1)):
                    ordinal = int(ordinal_match.group(1))
                else:
                    match = _BETWEEN.search(description)
                    if not match or match.group(1) not in bounds or match.group(2) not in bounds:
                        continue
                    pair = {bounds.index(match.group(1)), bounds.index(match.group(2))}
                    if len(pair) != 2 or max(pair)-min(pair) != 1:
                        continue
                    ordinal = min(pair)
                start_ordinal, start_frame, start_lane, start_quote, start_path = sources[aid]
                if frame_times[fid] <= frame_times[start_frame] or ordinal == start_ordinal:
                    continue
                if ordinal not in by_ordinal:
                    continue
                found.append(dict(actor_id=aid, source_ordinal=start_ordinal,
                    target_ordinal=ordinal, source_lane_id=start_lane,
                    target_lane_id=by_ordinal[ordinal],
                    source_frame_id=start_frame, target_frame_id=fid,
                    source_observation_id=source_ids[aid],
                    target_observation_id=f'{path.stem}:{fid}:{vehicle["object_id"]}',
                    observation_envelope_s=[frame_times[start_frame], frame_times[fid]],
                    source_path=str(path), object_id=vehicle.get('object_id'),
                    source_witness_path=start_path, source_quote=start_quote,
                    target_quote=accepted['reason'] if accepted else description))
    covered = {(row['actor_id'], row['target_frame_id']) for row in found}
    for (aid, fid), claim in point_claims.items():
        if aid not in sources or (aid, fid) in covered or fid not in frame_times:
            continue
        start_ordinal, start_frame, start_lane, start_quote, start_path = sources[aid]
        ordinal = lanes[claim['value']]
        if frame_times[fid] <= frame_times[start_frame] or ordinal == start_ordinal:
            continue
        found.append(dict(actor_id=aid, source_ordinal=start_ordinal,
            target_ordinal=ordinal, source_lane_id=start_lane,
            target_lane_id=claim['value'], source_frame_id=start_frame,
            target_frame_id=fid, source_observation_id=source_ids[aid],
            target_observation_id=claim['fact_key'],
            observation_envelope_s=[frame_times[start_frame], frame_times[fid]],
            source_path=str(case/claim['source_path']), object_id=None,
            source_witness_path=start_path, source_quote=start_quote,
            target_quote=claim['reason']))
    return found


def _ego_lane_witnesses(case, view):
    from _lib.behavior.input_binding import accepted_ego_lane_points
    from _lib.behavior.compile import evidence_frames
    from _lib.contracts.lane_locations import lane_locations

    points = accepted_ego_lane_points(case, view)
    if not points:
        return []
    world = view.world['world']
    locations = lane_locations(world['road'])
    frames = {row['frame_id']: row['time_s'] for row in evidence_frames(case, include_census=False)}
    initial = world.get('metadata', {}).get('lane_layout_review', {}).get('observation', {}).get('ego_initial', {})
    ordinal = initial.get('ordinal_from_right')
    initial_lanes = [lane['lane_id'] for lane in world['road'].get('lanes', [])
                     if lane.get('travel_direction', 'same_as_ego') == 'same_as_ego'
                     and type(ordinal) is int and locations.get(lane['lane_id'], (None, None))[1] == ordinal]
    anchors = {}
    if len(initial_lanes) == 1:
        for fid in initial.get('evidence_frame_ids', []):
            if fid in frames:
                anchors[fid] = (initial_lanes[0], initial.get('reason', 'Accepted Ego initial lane'),
                                view.world_path)
    for point in points:
        anchors[point['frame_id']] = (point['value'], point['reason'], case/point['source_path'])

    ordered = sorted(anchors, key=lambda fid: (frames[fid], fid))
    rows = []
    for before, after in zip(ordered, ordered[1:]):
        source_lane, source_quote, source_path = anchors[before]
        target_lane, target_quote, target_path = anchors[after]
        if frames[before] >= frames[after] or source_lane == target_lane:
            continue
        source_road, source_ordinal = locations[source_lane]
        target_road, target_ordinal = locations[target_lane]
        if (source_road, source_ordinal) == (target_road, target_ordinal):
            continue
        rows.append(dict(actor_id='ego', source_ordinal=source_ordinal, target_ordinal=target_ordinal,
            source_lane_id=source_lane, target_lane_id=target_lane,
            source_road_id=source_road, target_road_id=target_road,
            source_frame_id=before, target_frame_id=after,
            source_observation_id=f'actor-frame:ego:{before}', target_observation_id=f'actor-frame:ego:{after}',
            observation_envelope_s=[frames[before], frames[after]], source_path=str(target_path),
            object_id=None, source_witness_path=str(source_path), source_quote=source_quote,
            target_quote=target_quote))
    return rows


def obligations(case, *, world_override=None):
    """Derive current obligations from frozen observations and effective facts."""
    from _lib.runtime.fact_versions import POINTER, current_facts, enabled as facts_enabled

    case = Path(case)
    pointer = case/POINTER
    view = current_facts(case) if pointer.is_file() or facts_enabled(case) else None
    batch_paths = sorted((case/'batches').glob('batch_*.json'))
    reconciliation_path = view.directory/'reconciliation.json' if view else case/'state/reconciliation.json'
    reconciliation = view.reconciliation if view else (
        read_json(reconciliation_path) if reconciliation_path.is_file() else {})
    if reconciliation.get('batch_sha256') and reconciliation['batch_sha256'] != {
            path.name: sha256(path) for path in batch_paths}:
        from _lib.contracts.v3 import ContractError
        raise ContractError('Motion obligation Batch inputs differ from accepted reconciliation',
                            rule_id='core_scene.motion_continuity')
    rows = witnesses(case)
    from _lib.behavior.input_binding import ego_lane_point_handoff_enabled
    if ego_lane_point_handoff_enabled(case):
        rows = [row for row in rows if row['actor_id'] != 'ego']
        rows.extend(_ego_lane_witnesses(case, view))
    world_path = view.world_path if view else case/'state/world_context.json'
    registry_path = view.registry_path if view else case/'state/actor_registry.json'
    world = world_override if world_override is not None else (
        view.world if view else read_json(world_path) if world_path.is_file() else {})
    registry = view.registry if view else (
        read_json(registry_path) if registry_path.is_file() else [])
    lane_map = world.get('world', {}).get('metadata', {}).get('lane_layout_review', {}).get(
        'adjudication', {}).get('lane_id_map', {})
    source_world = _source_world(case) or {}
    road_by_lane = {anchor.get('lane_id'): road.get('road_id')
                    for road in source_world.get('world', {}).get('road', {}).get('roads', [])
                    for anchor in road.get('lane_knowledge', {}).get('anchors', [])}
    known = {actor.get('actor_id') for actor in registry}
    source_paths = {case/'state/global_frames.json', world_path, registry_path}
    source_paths.update(batch_paths)
    if reconciliation_path.is_file():
        source_paths.add(reconciliation_path)
    if (case/'state/lane_layout.commit.json').is_file():
        source_paths.add(case/'state/lane_layout.commit.json')
    if pointer.is_file():
        source_paths.add(pointer)
        source_paths.add(view.directory/'manifest.json')
    for row in rows:
        source_paths.update((Path(row['source_witness_path']), Path(row['source_path'])))
    source_hashes = {str(path): sha256(path) for path in sorted(source_paths) if path.is_file()}
    binding_hashes = {path: value for path, value in source_hashes.items()
                      if path != str(world_path)}
    prepared = []
    for row in rows:
        ego_point = row['actor_id'] == 'ego' and 'source_road_id' in row
        if ego_point:
            source_lane, target_lane = row['source_lane_id'], row['target_lane_id']
            source_road, target_road = row['source_road_id'], row['target_road_id']
        else:
            source_lane = lane_map.get(row['source_lane_id'], row['source_lane_id'])
            target_lane = lane_map.get(row['target_lane_id'], row['target_lane_id'])
            source_road = road_by_lane.get(row['source_lane_id'])
            target_road = road_by_lane.get(row['target_lane_id'])
        identity_bound = ego_point or row['actor_id'] in known
        identity_disputed = not ego_point and any(
            item.get('resolution') == 'unresolved'
            and 'actor_identity' in item.get('impact_domains', [])
            and row['actor_id'] in item.get('actor_ids', [])
            and {row['source_frame_id'], row['target_frame_id']} & {
                evidence.get('frame_id') for evidence in item.get('evidence', [])}
            for item in world.get('questions_to_verify', []))
        body = dict(row, source_lane_id_effective=source_lane,
                    target_lane_id_effective=target_lane,
                    source_road_id=source_road, target_road_id=target_road,
                    identity_bound=identity_bound, identity_disputed=identity_disputed)
        prepared.append(dict(body, obligation_id='motion:'+digest(dict(
            revision=REVISION, fact_version_id=view.version_id if view else None,
            source_hashes=binding_hashes, world_sha256=digest(world),
            source_observation_id=row['source_observation_id'],
            target_observation_id=row['target_observation_id'], actor_id=row['actor_id']))[:24]))
    packet = dict(revision=REVISION, fact_version_id=view.version_id if view else None,
                  lane_id_map=lane_map, source_hashes=source_hashes,
                  proposed_world_sha256=digest(world) if world_override is not None else None,
                  obligations=prepared)
    return dict(packet, sha256=digest(packet))


def _validate_ego_lane_endpoints(case, ego, motion_rows):
    from _lib.behavior.input_binding import accepted_ego_lane_points
    from _lib.contracts.v3 import ContractError
    from _lib.runtime.fact_versions import POINTER, current_facts

    if not (Path(case)/POINTER).is_file():
        return motion_rows
    points = accepted_ego_lane_points(case, current_facts(case))
    if not points:
        return motion_rows
    if motion_rows is None:
        motion_rows = obligations(case)['obligations']
    expected = {point['frame_id']: (point['time_s'], point['value']) for point in points}
    for row in motion_rows:
        if row['actor_id'] == 'ego':
            start, end = row['observation_envelope_s']
            expected[row['source_frame_id']] = (start, row['source_lane_id_effective'])
            expected[row['target_frame_id']] = (end, row['target_lane_id_effective'])
    actual = {}
    for point in (ego or {}).get('trajectory', []):
        actual.setdefault(point.get('frame_id'), []).append((point.get('time_s', point.get('timestamp_s')),
                                                             point.get('lane_id')))
    for fid, (time_s, lane_id) in expected.items():
        if actual.get(fid) != [(time_s, lane_id)]:
            error = ContractError(f'Behavior must preserve the accepted Ego lane point at {fid}',
                                  rule_id='core_scene.motion_continuity', actor_id='ego')
            error.diagnostics = dict(rule_id=error.rule_id, actor_id='ego', frame_id=fid,
                expected_time_s=time_s, expected_lane_id=lane_id, actual=actual.get(fid, []))
            raise error
    return motion_rows


def validate_source_coverage(case, behavior, *, timeline=None, world_override=None,
                             motion_rows=None, projection_context=None):
    """Require a source-bound disposition, never a new trajectory sample per row."""
    from _lib.contracts.v3 import ContractError
    from _lib.behavior.input_binding import (current_evidence_timeline, ego_lane_point_handoff_enabled,
                                             timeline_enabled)

    if timeline is None:
        if not timeline_enabled(case):
            return True
        timeline = current_evidence_timeline(case)
    if timeline.get('revision') != 'actor-time-evidence.v1':
        raise ContractError('Behavior source evidence timeline revision is invalid',
                            rule_id='core_scene.source_coverage')
    groups = timeline.get('groups', [])
    by_id = {row['source_observation_id']: row for row in groups}
    if len(by_id) != len(groups):
        raise ContractError('Behavior source evidence timeline has duplicate groups',
                            rule_id='core_scene.source_coverage')
    excluded_sources = set()
    if projection_context is not None:
        from _lib.contracts.core_projection_sources import validate as validate_projection
        from _lib.runtime.fact_versions import current_facts
        context = projection_context
        excluded_sources = validate_projection(
            case, current_facts(case).world, context['source_behavior'],
            context['full_analysis'], behavior, context['frames'], timeline,
            context['review'], context['disposition'],
            context.get('representatives', ()))
    actors = {a.get('actor_id'): a for a in [behavior.get('ego'), *behavior.get('actors', [])]
              if isinstance(a, dict)}
    ego_handoff = ego_lane_point_handoff_enabled(case)
    if ego_handoff:
        motion_rows = _validate_ego_lane_endpoints(case, actors.get('ego'), motion_rows)
    events = behavior.get('events', [])
    limitations = behavior.get('semantic_limitations', behavior.get('limitations', []))
    cited = set()

    def references(node, actor_id=None):
        value = node.get('source_observation_ids', [])
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ContractError('Behavior source_observation_ids must be a string list',
                                rule_id='core_scene.source_coverage', actor_id=actor_id)
        if len(value) != len(set(value)):
            raise ContractError('Behavior source_observation_ids repeats a source',
                                rule_id='core_scene.source_coverage', actor_id=actor_id)
        unknown = set(value) - set(by_id)
        if unknown:
            raise ContractError('Behavior cites an unknown source observation: '+str(sorted(unknown)),
                                rule_id='core_scene.source_coverage', actor_id=actor_id)
        cited.update(value)
        return set(value)

    point_refs = {}
    for actor_id, actor in actors.items():
        for point in actor.get('trajectory', []):
            for source_id in references(point, actor_id):
                row = by_id[source_id]
                if row['actor_id'] != actor_id or row['frame_id'] != point.get('frame_id') or (
                        point.get('time_s', point.get('timestamp_s')) not in (None, row['time_s'])):
                    raise ContractError('Behavior trajectory source actor/frame/time differs',
                                        rule_id='core_scene.source_coverage', actor_id=actor_id)
                if not str(point.get('detail', '')).strip() and not point.get('evidence'):
                    raise ContractError('Behavior trajectory source citation lacks an explanation',
                                        rule_id='core_scene.source_coverage', actor_id=actor_id)
                point_refs[source_id] = point
    event_refs = set()
    for event in events:
        involved = event.get('involved_actor_ids', [])
        owner = involved[0] if len(involved) == 1 else None
        refs = references(event, owner)
        for evidence in event.get('evidence', []):
            if isinstance(evidence, dict):
                refs.update(references(evidence, owner))
        for source_id in refs:
            row = by_id[source_id]
            if row['actor_id'] not in event.get('involved_actor_ids', []) or not any(
                    isinstance(item, dict) and item.get('frame_id') == row['frame_id']
                    and item.get('time_s', row['time_s']) == row['time_s']
                    for item in event.get('evidence', [])):
                raise ContractError('Behavior event source lacks matching actor/frame evidence',
                                    rule_id='core_scene.source_coverage', actor_id=row['actor_id'])
            event_refs.add(source_id)
    limitation_refs = set()
    for limitation in limitations:
        owners = limitation.get('actor_ids', [])
        owner = owners[0] if len(owners) == 1 else None
        for source_id in references(limitation, owner):
            row = by_id[source_id]
            if (row['actor_id'] not in limitation.get('actor_ids', [])
                    or not str(limitation.get('description', '')).strip()
                    or not set(limitation.get('impact_domains', [])) & {
                        'actor_lane', 'actor_motion', 'actor_identity', 'event', 'path'}
                    or limitation.get('execution_effect') not in {
                        'blocks_execution', 'bounded_uncertainty'}):
                raise ContractError('Behavior source limitation lacks a precise unresolved effect',
                                    rule_id='core_scene.source_coverage', actor_id=row['actor_id'])
            limitation_refs.add(source_id)
    if not groups:
        return True
    from _lib.runtime.fact_versions import ROOT as FACT_ROOT, current_facts
    view = current_facts(case) if (Path(case)/FACT_ROOT/'active.json').is_file() else None
    world = world_override if world_override is not None else (
        view.world if view else read_json(Path(case)/'state/world_context.json'))
    core_actor_ids = {actor_id for item in [*world.get('questions_to_verify', []),
                                             *world.get('ambiguities', [])]
                      if set(item.get('impact_domains', [])) & {
                          'actor_lane', 'actor_motion', 'actor_identity', 'event', 'path'}
                      for actor_id in item.get('actor_ids', []) or []}
    core_actor_ids.update(actor_id for event in events
                          for actor_id in event.get('involved_actor_ids', []))
    core_actor_ids.update(row['actor_id'] for row in (
        motion_rows if motion_rows is not None else obligations(case)['obligations']))
    from _lib.runtime.execution_scope import opposing_preservation_ids
    excluded = opposing_preservation_ids(world, behavior)
    for row in groups:
        source_id, actor_id = row['source_observation_id'], row['actor_id']
        required = (any(source['kind'] == 'vehicle_description' for source in row['sources'])
                    or actor_id in core_actor_ids)
        objects_by_batch = {}
        for source in row['sources']:
            if source.get('kind') == 'vehicle_description' and source.get('object_id'):
                objects_by_batch.setdefault(source['source_id'].split('#', 1)[0], set()).add(
                    source['object_id'])
        if any(len(objects) > 1 for objects in objects_by_batch.values()):
            raise ContractError('One Actor/frame binds multiple distinct Batch objects',
                                rule_id='core_scene.source_coverage', actor_id=actor_id)
        if required and source_id not in point_refs and source_id not in event_refs and source_id not in limitation_refs \
                and actor_id not in excluded and source_id not in excluded_sources:
            error = ContractError('Accepted Batch Actor/frame observation is not carried into Behavior',
                                  rule_id='core_scene.source_coverage', actor_id=actor_id)
            error.diagnostics = dict(source_observation_id=source_id,
                                     actor_id=actor_id, frame_id=row['frame_id'])
            raise error
        claims = [claim for claim in row.get('accepted_claims', []) if claim.get('field') == 'actor_lane'
                  and claim.get('value') is not None]
        if claims:
            mapping = {} if ego_handoff and actor_id == 'ego' else timeline.get('lane_id_map', {})
            expected = {mapping.get(claim['value'], claim['value'])
                        for claim in claims}
            points = [point for point in actors.get(actor_id, {}).get('trajectory', [])
                      if point.get('frame_id') == row['frame_id'] and point.get('lane_id') is not None]
            if len(expected) != 1 or any(point['lane_id'] not in expected for point in points):
                raise ContractError('Behavior source point contradicts an accepted lane claim',
                                    rule_id='core_scene.source_coverage', actor_id=actor_id)
    return True


def validate(case, behavior, *, world_override=None, return_dispositions=False,
             projection_context=None):
    """Every source-bound lane difference needs a supported disposition."""
    from _lib.contracts.v3 import ContractError
    from _lib.behavior.lane_change import is_lane_change_kind

    packet = obligations(case, world_override=world_override)
    rows = packet['obligations']
    if not rows:
        validate_source_coverage(case, behavior, world_override=world_override, motion_rows=rows,
                                 projection_context=projection_context)
        return [] if return_dispositions else True
    dispositions = []
    actors = {a.get('actor_id'): a for a in [behavior.get('ego'), *behavior.get('actors', [])]
              if isinstance(a, dict)}
    events = behavior.get('events', [])
    for row in rows:
        actor = actors.get(row['actor_id'])
        trajectory = actor.get('trajectory', []) if actor else []
        lanes = {p.get('lane_id') for p in trajectory if p.get('lane_id')}
        source_lane = row['source_lane_id_effective']
        target_lane = row['target_lane_id_effective']
        lane_event = next((e for e in events if is_lane_change_kind(e.get('event_type')) and
                           row['actor_id'] in e.get('involved_actor_ids', []) and
                           e.get('from_lane_id') == source_lane and e.get('to_lane_id') == target_lane), None)
        has_event = lane_event is not None
        source_point = any(p.get('frame_id') == row['source_frame_id'] and
                           p.get('lane_id') == source_lane for p in trajectory)
        target_point = any(p.get('frame_id') == row['target_frame_id'] and
                           p.get('lane_id') == target_lane for p in trajectory)
        source_road_point = any(p.get('frame_id') == row['source_frame_id'] and
                                p.get('road_id') == row['source_road_id'] for p in trajectory)
        target_road_point = any(p.get('frame_id') == row['target_frame_id'] and
                                p.get('road_id') == row['target_road_id'] for p in trajectory)
        explanations = [item for item in behavior.get('semantic_limitations', behavior.get('limitations', []))
                        if item.get('motion_obligation_id') == row['obligation_id']
                        and item.get('source_observation_id') == row['source_observation_id']
                        and item.get('target_observation_id') == row['target_observation_id']
                        and row['actor_id'] in item.get('actor_ids', [])
                        and set(item.get('impact_domains', [])) & {'actor_lane', 'actor_motion', 'actor_identity'}
                        and item.get('execution_effect') in {'blocks_execution', 'bounded_uncertainty'}]
        explanation = explanations[0] if len(explanations) == 1 else {}
        kind = explanation.get('motion_explanation')
        road_event = next((e for e in events if e.get('event_type') == 'road_transition'
                           and row['actor_id'] in e.get('involved_actor_ids', [])
                           and e.get('from_road_id') == row['source_road_id']
                           and e.get('to_road_id') == row['target_road_id']), None)
        road_transition = (kind == 'road_transition' and row['source_road_id'] is not None
                           and row['target_road_id'] is not None
                           and row['source_road_id'] != row['target_road_id']
                           and source_road_point and target_road_point
                           and road_event is not None)
        lane_remap = (kind == 'lane_remap' and source_lane == target_lane
                      and row['source_lane_id'] != row['target_lane_id'])
        identity_dispute = (kind == 'identity_dispute' and row['identity_disputed']
                            and explanation.get('execution_effect') == 'blocks_execution')
        insufficient = (kind == 'insufficient_evidence' and row['identity_bound']
                        and explanation.get('execution_effect') in {
                            'blocks_execution', 'bounded_uncertainty'})
        physical = (source_point and target_point and row['identity_bound'] and has_event
                    and source_lane != target_lane and row['source_road_id'] is not None
                    and row['source_road_id'] == row['target_road_id'])
        resolution = ('physical_crossing' if physical else
                      'road_transition' if source_point and target_point and road_transition else
                      'lane_remap' if source_point and target_point and lane_remap else
                      'identity_dispute' if identity_dispute else
                      'insufficient_evidence' if source_point and target_point and insufficient else None)
        if resolution:
            dispositions.append(dict(obligation_id=row['obligation_id'], resolution=resolution,
                source_observation_id=row['source_observation_id'],
                target_observation_id=row['target_observation_id'],
                event_id=(lane_event if physical else road_event or {}).get('event_id')
                    if resolution in {'physical_crossing', 'road_transition'} else None,
                limitation_id=explanation.get('limitation_id') if not physical else None,
                execution_effect=explanation.get('execution_effect') if not physical else None))
            continue
        error = ContractError('Bound lane observations lack a supported motion explanation',
                              rule_id='core_scene.motion_continuity', actor_id=row['actor_id'])
        error.code = 'core_motion_semantic_recovery_required'
        error.diagnostics = dict(**row, behavior_lanes=sorted(lanes),
                                 source_point=source_point, target_point=target_point,
                                 lane_change_event=has_event, explanation=kind,
                                 obligation_sha256=packet['sha256'],
                                 fidelity_gap='motion_obligation_unexplained')
        raise error
    if (Path(case)/'state/fact_versions/active.json').is_file():
        from _lib.runtime.fact_versions import current_facts
        current_facts(case, packet['fact_version_id'])
    for path, expected in packet['source_hashes'].items():
        if not Path(path).is_file() or sha256(path) != expected:
            raise ContractError('Motion obligation source changed during validation',
                                rule_id='core_scene.motion_continuity')
    validate_source_coverage(case, behavior, world_override=world_override, motion_rows=rows,
                             projection_context=projection_context)
    return dispositions if return_dispositions else True


def validate_analysis_motion(case, source_behavior, analysis, *, allow_omission=False,
                             projection_source=None):
    """Replay independent lane obligations and retain accepted source events.

    A projected peripheral Actor may be omitted by an existing scope decision.
    Events involving retained Actors (or ego) have no such implicit disposal.
    The minimum scene may report omissions, but cannot call them fidelity.
    """
    from _lib.contracts.v3 import ContractError

    retained = {item.get('actor_id') for item in [analysis.get('ego'), *analysis.get('actors', [])]
                if isinstance(item, dict)}
    missing = []
    for event in source_behavior.get('events', []):
        if not isinstance(event, dict):
            continue  # The existing Behavior Schema owns malformed events.
        involved = set(event.get('involved_actor_ids', []))
        if involved and not (involved <= retained or 'ego' in involved):
            continue  # Existing execution scope owns the omitted Actor.
        if not any(isinstance(current, dict) and all(current.get(key) == value
                   for key, value in event.items()) for current in analysis.get('events', [])):
            missing.append(dict(event_id=event.get('event_id'), event_type=event.get('event_type'),
                                involved_actor_ids=sorted(involved), source_sha256=digest(event)))
    case = Path(case)
    from _lib.behavior.input_binding import timeline_enabled
    # Historical completed Cases may predate retention of the pre-layout World.
    # They have no independent witness packet to replay; their accepted events
    # are still checked above. Current fact/timeline Cases must have that source.
    legacy_without_source = (
        (case/'state/lane_layout.commit.json').is_file()
        and not (case/'diagnostics/world_census_adjudication/initial/before/world.json').is_file()
        and not ((case/'state/fact_versions/active.json').is_file() or timeline_enabled(case))
        and (case/'state/progress.json').is_file()
        and read_json(case/'state/progress.json').get('status') in {'done', 'done_degraded'})
    if not legacy_without_source:
        try:
            context = None
            if projection_source is not None:
                from _lib.behavior.input_binding import current_evidence_timeline
                from _lib.behavior.compile import evidence_frames
                full, proof, review = (projection_source[key] for key in
                                       ('full_analysis', 'disposition', 'review'))
                current_evidence_timeline(case)  # Recheck frozen source hashes first.
                context = dict(source_behavior=source_behavior, full_analysis=full,
                               frames=evidence_frames(case), review=review, disposition=proof,
                               representatives=projection_source.get('representatives', ()))
            validate(case, analysis, projection_context=context)
        except ContractError as error:
            if not allow_omission or error.rule_id != 'core_scene.motion_continuity':
                raise
            missing.append(dict(rule_id=error.rule_id, source=error.diagnostics))
    if missing and not allow_omission:
        error = ContractError('Accepted core motion is absent from recovered Analysis',
                              rule_id='core_scene.motion_retention')
        error.diagnostics = dict(missing_motion=missing)
        raise error
    return missing
