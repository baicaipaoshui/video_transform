"""Private Behavior request/attempt binding; no public output-schema changes."""
import re
from pathlib import Path

from _lib.contracts.v3 import ContractError
from _lib.contracts.conversion_policy import digest
from _lib.runtime.case_core import atomic_json, read_json, sha256

REVISION = 'behavior-input.v1'
EVIDENCE_TIMELINE = 'actor-time-evidence.v1'
REQUEST = 'state/behavior_input.request.json'
SESSIONS = 'state/behavior_input.sessions.json'
VERSIONED_REQUESTS = 'state/behavior_requests'


def write_behavior(case, behavior, *, world_override=None, source_case=None):
    """Keep the existing atomic Behavior checkpoint behind its motion gate."""
    from contextlib import nullcontext
    from _lib.contracts.core_lane_continuity import validate
    from _lib.runtime.fact_versions import POINTER, _lock
    case = Path(case)
    bound_case = Path(source_case) if source_case is not None else case
    with _lock(bound_case) if (bound_case/POINTER).is_file() else nullcontext():
        validate(bound_case, behavior, world_override=world_override)
        atomic_json(case/'state/behavior.json', behavior)


class InputChanged(ContractError):
    code = 'source_changed'

    def __init__(self, message):
        super().__init__(message, rule_id='behavior.input_binding')
        self.diagnostics = {'rule_id': self.rule_id, 'stage': 'behavior',
                            'retry_kind': 'new_case', 'message': message}


def enabled(case):
    path = Path(case)/'state/execution_policy.json'
    return path.is_file() and read_json(path).get('behavior_input_binding') == REVISION


def timeline_enabled(case):
    path = Path(case)/'state/execution_policy.json'
    return path.is_file() and read_json(path).get('behavior_evidence_timeline') == EVIDENCE_TIMELINE


def _census_view_requested(case):
    """Existing frozen requests keep their original certificate handoff."""
    origin = Path(case)/REQUEST
    if origin.is_file():
        return 'census_read_view' in read_json(origin)
    if not timeline_enabled(case):
        return False
    from _lib.contracts.census_adjudication import proof
    return proof(case) is not None


def accepted_ego_lane_points(case, view, *, replayed=None):
    """Return only Ego points published through a replayable point review."""
    points = {key: row for key, row in (view.claims.items() if view else ())
              if key.startswith('point:ego:') and row.get('field') == 'actor_lane'}
    if not points:
        return []
    from _lib.runtime.fact_versions import verified_reconciliation_lane_points
    published = {f"point:ego:{row['frame_id']}:actor_lane"
                 for marker in view.revisions if marker.get('kind') == 'reconciliation-lane-points.v1'
                 for row in marker['decisions'] if row['actor_id'] == 'ego'}
    if replayed is None:
        replayed = verified_reconciliation_lane_points(case, view)
    if not replayed or points.keys() - published:
        raise InputChanged('Effective Ego lane points lack a replayable point review')
    from _lib.contracts.lane_locations import lane_locations
    world = view.world['world']
    locations = lane_locations(world['road'])
    bounds = world.get('metadata', {}).get('lane_layout_review', {}).get(
        'observation', {}).get('boundaries_right_to_left', [])
    for point in points.values():
        ordinal = point['ordinal_from_right']
        if (locations.get(point['value'], (None, None))[1] != ordinal
                or bounds[ordinal:ordinal + 2] != [point['right_boundary_id'], point['left_boundary_id']]):
            raise InputChanged('Accepted Ego point no longer matches the effective physical corridor')
    return list(points.values())


def ego_lane_point_handoff_enabled(case):
    path = Path(case)/'state/execution_policy.json'
    return path.is_file() and read_json(path).get('ego_lane_point_handoff') == 'ego-lane-point-handoff.v1'


def evidence_timeline(case):
    """Index accepted text without interpreting its visual meaning."""
    case = Path(case).resolve()
    from _lib.runtime.fact_versions import ROOT as FACT_ROOT, current_facts
    from _lib.behavior.compile import evidence_frames
    from _lib.contracts.census_adjudication import proof as census_proof
    from _lib.contracts.reconciliation_alignment import enabled as alignment_enabled
    from _lib.world.actor_facts import (EGO_LANE_OBSERVATIONS_KEY, ego_lane_observations_enabled,
                                       is_ego_lane_observation)
    active_alignment = alignment_enabled(case)
    index_ego_lanes = ego_lane_observations_enabled(case)
    manifest = read_json(case/'manifest.json')
    view = current_facts(case) if (case/FACT_ROOT/'active.json').is_file() else None
    registry = view.registry if view else read_json(case/'state/actor_registry.json')
    world = view.world if view else read_json(case/'state/world_context.json')
    known = {row['actor_id'] for row in registry if row.get('actor_id')}
    if active_alignment:
        known.add('ego')
    canonical = (census_proof(case) or {}).get('result', {}).get('canonical_actor_ids', {})
    times = {row['frame_id']: row['time_s'] for row in evidence_frames(case)}
    groups = {}
    unresolved = []
    source_hashes = {}
    batches = []
    ego_lane_candidates = []
    batch_world_hashes = {spec['batch_id']: spec.get('world_sha256') for spec in manifest.get('batches', [])}

    def add_ego_lane_candidate(row, source_id, source_sha256, *, world_sha256=None):
        if not index_ego_lanes or not is_ego_lane_observation(row):
            return False
        frame_id = row.get('frame_id')
        if frame_id not in times or row.get('time_s') != times[frame_id]:
            raise InputChanged(f'Ego lane observation frame/time differs from its source: {source_id}')
        ego_lane_candidates.append(dict(source_id=source_id, source_sha256=source_sha256, actor_id='ego',
            world_sha256=world_sha256 or source_world_sha256,
            frame_id=frame_id, time_s=times[frame_id], status=row['status'], text=row['reason'],
            lane_id=row.get('lane_id'), ordinal_from_right=row.get('ordinal_from_right')))
        return True

    if index_ego_lanes:
        from _lib.contracts.core_lane_continuity import _source_world_path
        source = _source_world_path(case)
        source_world = read_json(source)
        source_world_sha256 = sha256(source)
        rows = source_world.get('field_evidence', {}).get(EGO_LANE_OBSERVATIONS_KEY, [])
        for index, row in enumerate(rows):
            add_ego_lane_candidate(row, f'world:original#/field_evidence/{EGO_LANE_OBSERVATIONS_KEY}/{index}',
                                   source_world_sha256)

    def canonical_actor(actor_id):
        return canonical.get(actor_id, actor_id)

    def mentioned_actors(value):
        if not isinstance(value, str):
            return set()
        if not active_alignment:
            return set(re.findall(r'\bactor_\d+\b', value))
        identifiers = known | set(canonical)
        return {actor_id for actor_id in identifiers
                if re.search(r'(?<!\w)' + re.escape(actor_id) + r'(?!\w)', value)}

    question_actors = {item.get('question_id', item.get('ambiguity_id')):
                       set(item.get('actor_ids') or [])
                       for item in [*world.get('questions_to_verify', []),
                                    *world.get('ambiguities', [])]}

    def observation_actors(observation, value):
        actors = mentioned_actors(value)
        if active_alignment:
            actors.update(question_actors.get(observation.get('question_id',
                observation.get('ambiguity_id')), ()))
        return actors

    def add(actor_id, frame_id, source_id, text, kind, status='observed', object_id=None):
        actor_id = canonical_actor(actor_id)
        if actor_id not in known or frame_id not in times or not isinstance(text, str) or not text.strip():
            return
        key = (actor_id, frame_id)
        group = groups.setdefault(key, dict(source_observation_id=f'actor-frame:{actor_id}:{frame_id}',
            actor_id=actor_id, frame_id=frame_id, time_s=times[frame_id], sources=[]))
        item = dict(source_id=source_id, kind=kind, status=status, text=text)
        if object_id is not None:
            item['object_id'] = object_id
        if item not in group['sources']:
            group['sources'].append(item)

    for spec in manifest.get('batches', []):
        path = case/'batches'/f"{spec['batch_id']}.json"
        if not path.is_file():
            raise InputChanged(f'Behavior accepted Batch missing: {path}')
        source_hashes[str(path)] = sha256(path)
        batches.append((spec['batch_id'], read_json(path)))
    bound_keys = {(canonical_actor(vehicle['actor_id']), frame.get('frame_id'))
                  for _, batch in batches for frame in batch.get('vehicle_census', [])
                  for vehicle in frame.get('vehicles', [])
                  if vehicle.get('binding') == 'existing' and vehicle.get('actor_id')
                  and canonical_actor(vehicle['actor_id']) in known}
    # Keep original reasons when a structural Actor/frame binding exists or a
    # World issue already makes that Actor central. A prose mention alone does
    # not create a new physical identity or a required trajectory sample.
    core_ids = {actor_id for item in [*world.get('questions_to_verify', []),
                                      *world.get('ambiguities', [])]
                if set(item.get('impact_domains', [])) & {
                    'actor_lane', 'actor_motion', 'actor_identity', 'event', 'path'}
                for actor_id in item.get('actor_ids', []) or []}
    for stem, batch in batches:
        for fi, frame in enumerate(batch.get('vehicle_census', [])):
            frame_id = frame.get('frame_id')
            if frame_id not in times or frame.get('time_s') != times[frame_id]:
                raise InputChanged(f'Behavior Batch census frame/time differs from evidence index: {stem}')
            for vi, vehicle in enumerate(frame.get('vehicles', [])):
                source_id = f'{stem}#/vehicle_census/{fi}/vehicles/{vi}'
                if vehicle.get('binding') == 'existing' and vehicle.get('actor_id'):
                    if not isinstance(vehicle.get('description'), str) or not vehicle['description'].strip():
                        raise InputChanged(f'Behavior bound vehicle description missing: {source_id}')
                    if canonical_actor(vehicle['actor_id']) in known:
                        add(vehicle['actor_id'], frame_id, source_id,
                            vehicle['description'], 'vehicle_description',
                            object_id=vehicle.get('object_id'))
                    else:
                        unresolved.append(dict(source_id=source_id, frame_id=frame_id,
                            time_s=times[frame_id], object_id=vehicle.get('object_id'),
                            candidate_actor_ids=[vehicle['actor_id']],
                            text=vehicle['description'],
                            source_binding='historical_existing_unresolved'))
                elif vehicle.get('binding') == 'unresolved':
                    unresolved.append(dict(source_id=source_id, frame_id=frame_id,
                        time_s=times[frame_id], object_id=vehicle.get('object_id'),
                        candidate_actor_ids=vehicle.get('candidate_actor_ids', []),
                        text=vehicle.get('description', ''), source_binding='unresolved'))
        for ri, actor in enumerate(batch.get('registry_after', [])):
            actor_id = actor.get('actor_id')
            for ei, evidence in enumerate(actor.get('evidence', [])):
                frame_id = evidence.get('frame_id')
                if frame_id in times and evidence.get('time_s') == times[frame_id]:
                    key = (canonical_actor(actor_id), frame_id)
                    if key not in bound_keys and key[0] not in core_ids:
                        continue
                    if any(item['kind'] == 'vehicle_description'
                           for item in groups.get(key, {}).get('sources', [])):
                        continue  # The effective Registry remains separately available.
                    add(actor_id, frame_id, f'{stem}#/registry_after/{ri}/evidence/{ei}',
                        evidence.get('reason', ''), 'registry_source_reason', evidence.get('status'))
        for oi, observation in enumerate(batch.get('observations', [])):
            frame_ids = {row.get('frame_id') for row in observation.get('evidence', [])}
            if observation.get('frame_id'):
                frame_ids.add(observation['frame_id'])
            for ei, evidence in enumerate(observation.get('evidence', [])):
                if add_ego_lane_candidate(evidence, f'{stem}#/observations/{oi}/evidence/{ei}',
                                          source_hashes[str(case/'batches'/f'{stem}.json')],
                                          world_sha256=batch_world_hashes.get(stem)):
                    continue
                frame_id, reason = evidence.get('frame_id'), evidence.get('reason', '')
                if frame_id in times and evidence.get('time_s') == times[frame_id]:
                    actors = [actor_id for actor_id in observation_actors(observation, reason)
                              if (active_alignment and actor_id == 'ego') or
                              (canonical_actor(actor_id), frame_id) in bound_keys or
                              canonical_actor(actor_id) in core_ids]
                    if active_alignment and reason and not actors:
                        unresolved.append(dict(source_id=f'{stem}#/observations/{oi}/evidence/{ei}',
                            frame_id=frame_id, time_s=times[frame_id], text=reason,
                            question_id=observation.get('question_id'), candidate_actor_ids=[],
                            source_binding='observation_actor_unbound'))
                    for actor_id in actors:
                        add(actor_id, frame_id, f'{stem}#/observations/{oi}/evidence/{ei}',
                            reason, 'observation_source_reason', evidence.get('status'))
            reason = observation.get('reason', '')
            for frame_id in frame_ids:
                if frame_id in times:
                    actors = [actor_id for actor_id in observation_actors(observation, reason)
                              if (active_alignment and actor_id == 'ego') or
                              (canonical_actor(actor_id), frame_id) in bound_keys or
                              canonical_actor(actor_id) in core_ids]
                    if active_alignment and reason and not actors:
                        unresolved.append(dict(source_id=f'{stem}#/observations/{oi}/reason',
                            frame_id=frame_id, time_s=times[frame_id], text=reason,
                            question_id=observation.get('question_id'), candidate_actor_ids=[],
                            source_binding='observation_actor_unbound'))
                    for actor_id in actors:
                        add(actor_id, frame_id, f'{stem}#/observations/{oi}/reason',
                            reason, 'observation_source_reason', observation.get('status'))

    claims = view.claims if view else {}
    if ego_lane_point_handoff_enabled(case):
        for point in accepted_ego_lane_points(case, view):
            fid = point['frame_id']
            if times.get(fid) != point['time_s']:
                raise InputChanged('Accepted Ego point frame/time is missing from Behavior evidence')
            groups.setdefault(('ego', fid), dict(source_observation_id=f'actor-frame:ego:{fid}',
                actor_id='ego', frame_id=fid, time_s=point['time_s'], sources=[]))
    for group in groups.values():
        group['sources'].sort(key=lambda row: row['source_id'])
        group['accepted_claims'] = sorted((dict(claim_id=row.get('claim_id'),
            field=row.get('field'), value=row.get('value')) for row in claims.values()
            if row.get('actor_id') == group['actor_id'] and row.get('frame_id') == group['frame_id']),
            key=lambda row: (str(row['field']), str(row['claim_id'])))
    lane_map = world.get('world', {}).get('metadata', {}).get('lane_layout_review', {}).get(
        'adjudication', {}).get('lane_id_map', {})
    return dict(revision=EVIDENCE_TIMELINE,
        fact_version_id=view.version_id if view else None,
        lane_id_map=lane_map, batch_sha256=source_hashes,
        groups=sorted(groups.values(), key=lambda row: (row['time_s'], row['actor_id'], row['frame_id'])),
        unresolved=sorted(unresolved, key=lambda row: (row['time_s'], row['source_id'])),
        **({'ego_lane_candidates': sorted(ego_lane_candidates, key=lambda row: (row['time_s'], row['source_id']))}
           if index_ego_lanes else {}))


def snapshot(case):
    case = Path(case).resolve()
    from _lib.world.single_world import validate_inputs
    validate_inputs(case)
    from _lib.runtime.fact_versions import ROOT as FACT_ROOT, current_facts
    versioned = (case/FACT_ROOT/'active.json').is_file()
    view = current_facts(case) if versioned else None
    world_path = view.world_path if view else case/'state/world_context.json'
    registry_path = view.registry_path if view else case/'state/actor_registry.json'
    receipt_path = view.directory/'reconciliation.json' if view else case/'state/reconciliation.json'
    required = ['case.json', 'state/execution_policy.json', 'state/world_context.json',
                'state/actor_registry.json', 'state/global_frames.json', 'state/behavior_frames.json',
                'state/probe.json', 'manifest.json', 'state/reconciliation.json']
    missing = [name for name in required if not (case/name).is_file()]
    if missing:
        raise InputChanged('Behavior frozen input missing: ' + ', '.join(missing))
    manifest = read_json(case/'manifest.json')
    world = read_json(world_path)
    receipt = read_json(receipt_path)
    if receipt.get('status') not in {'reconciled', 'reconciled_after_geometry_readjudication',
                                    'reconciled_after_identity_review'}:
        raise InputChanged('Behavior requires a completed World reconciliation')
    # World closure may legitimately follow reconciliation; freeze that final
    # snapshot and the reconciliation receipt together, not its earlier epoch.
    for spec in manifest.get('batches', []):
        required.append(f"batches/{spec['batch_id']}.json")
    batch_files = [case/name for name in required if name.startswith('batches/')]
    if any(not path.is_file() for path in batch_files) or receipt.get('batch_sha256') != {
            path.name: sha256(path) for path in batch_files if path.is_file()}:
        raise InputChanged('Behavior Batch inputs differ from completed reconciliation')
    paths = {str(case/name) for name in required}
    if view:
        paths.update(str(path) for path in (world_path, registry_path, receipt_path,
                     view.directory/'claims.json', view.directory/'manifest.json'))
    from _lib.contracts.behavior_census_scope import enabled as deferred, ADOPTION
    if deferred(case) and (case/ADOPTION).is_file():
        paths.add(str(case/ADOPTION))
    from _lib.contracts.census_adjudication import proof as census_proof
    from _lib.runtime.fact_versions import census_path
    census = census_proof(case)
    from _lib.world.post_behavior import proof as revision_proof, PREPARED
    revision = revision_proof(case, census=census)
    if revision:
        request_path = case/'state/post_behavior_world_readjudication.request.json'
        paths.update((str(case/PREPARED), str(request_path)))
        paths.update(read_json(request_path)['input_hashes'])
        if revision.get('previous_commit'):
            paths.add(revision['previous_commit']['path'])
    if census:
        paths.add(str(census_path(case)))
        from _lib.contracts.census_history import certificate_paths
        paths.update(certificate_paths(census))
    from _lib.runtime.identity_revision import ACCEPTED, LINEAGE, proof, validate_lineage
    if (case/LINEAGE).is_file():
        validate_lineage(case)
        paths.add(str(case/LINEAGE))
    if (case/ACCEPTED).is_file():
        proof(case)
        paths.add(str(case/ACCEPTED))
    elif receipt.get('status') == 'reconciled_after_identity_review':
        raise InputChanged('Identity reconciliation requires its accepted certificate')
    from _lib.behavior.compile import evidence_frames
    for frame in evidence_frames(case):
        for row in [frame, *frame.get('variants', [])]:
            for key in ('path', 'source_path'):
                if row.get(key):
                    paths.add(str(Path(row[key]).resolve()))
    missing = [path for path in paths if not Path(path).is_file()]
    if missing:
        raise InputChanged('Behavior frozen input missing: ' + ', '.join(sorted(missing)))
    from _lib.world.actor_facts import strict_enabled, question_contracts
    origin_path = case/REQUEST
    origin = read_json(origin_path) if origin_path.is_file() else None
    contract_enabled = strict_enabled(case) or bool(view and origin and
        origin.get('fact_version_id') != view.version_id)
    authorization = []
    if contract_enabled:
        available = {f['frame_id'] for f in evidence_frames(case)}
        for row in question_contracts(world, frames_for_case(case)):
            if (row['collection'] == 'questions_to_verify' and row['resolution'] == 'unresolved'
                    and row['target_frame'] is not None
                    and row['target_frame']['frame_id'] in available
                    and not set(row['diagnostics']) & {'binding_missing', 'unsupported_temporal_scope'}
                    and len(row['actor_ids']) == 1):
                question = next(q for q in world['questions_to_verify']
                                if q['question_id'] == row['issue_id'])
                if question.get('impact_domains') == ['actor_lane']:
                    authorization.append(dict(collection=row['collection'], issue_id=row['issue_id'],
                        actor_id=row['actor_ids'][0], world_paths=row['world_paths'],
                        target_frame=row['target_frame'], evidence_frame_ids=sorted({
                            e['frame_id'] for e in question.get('evidence', []) if e.get('frame_id')}),
                        fact_version_id=view.version_id if view else None))
    timeline = evidence_timeline(case) if timeline_enabled(case) else None
    if timeline and any(row['source_id'].startswith('world:original#/')
                        for row in timeline.get('ego_lane_candidates', [])):
        from _lib.contracts.core_lane_continuity import _source_world_path
        paths.add(str(_source_world_path(case).resolve()))
    census_read_view = None
    if _census_view_requested(case):
        from _lib.behavior import census_read_view as projection
        if timeline is None:
            raise InputChanged('Behavior Census reading view requires the frozen evidence timeline')
        revision = projection.revision_for(case)
        path = projection.path_for(case, view.version_id if view else 'unversioned', revision).resolve()
        if not path.is_file():
            raise InputChanged('Behavior Census reading view is missing')
        census_read_view = dict(revision=revision, path=str(path), sha256=sha256(path))
        projection.verify(case, census_read_view, timeline)
        paths.add(str(path))
    return dict(revision=REVISION, case_id=read_json(case/'case.json').get('case_id', case.name),
                evidence_epoch=world['evidence_epoch'],
                **({'fact_version_id': view.version_id} if view else {}),
                **({'question_authorization': authorization} if contract_enabled else {}),
                **({'evidence_timeline': timeline} if timeline is not None else {}),
                **({'census_read_view': census_read_view} if census_read_view is not None else {}),
                candidate_path=str(case/'state/behavior.candidate.json'),
                input_hashes={path: sha256(Path(path)) for path in sorted(paths)})


def frames_for_case(case):
    from _lib.world.actor_facts import frames_for
    return frames_for(case)


def verify_origin(case):
    if not enabled(case):
        return None
    path = Path(case)/REQUEST
    if not path.is_file():
        raise InputChanged('Behavior frozen request is missing; cannot retroactively bind a candidate')
    request = read_json(path)
    body = dict(request); signature = body.pop('request_sha256', None)
    if signature != digest(body):
        raise InputChanged('Behavior frozen request digest changed')
    for name, expected in body.get('input_hashes', {}).items():
        if not Path(name).is_file() or sha256(Path(name)) != expected:
            raise InputChanged('Behavior frozen input changed: ' + name)
    return request


def _verified_request(path, *, compare=None):
    request = read_json(path)
    body = dict(request); signature = body.pop('request_sha256', None)
    if signature != digest(body):
        raise InputChanged('Behavior versioned request digest changed')
    for name, expected in body.get('input_hashes', {}).items():
        if not Path(name).is_file() or sha256(Path(name)) != expected:
            raise InputChanged('Behavior versioned input changed: ' + name)
    if compare is not None and body != compare:
        raise InputChanged('Behavior versioned request differs from current facts')
    return request


def _versioned_request_path(case, version_id):
    return Path(case)/VERSIONED_REQUESTS/f'{version_id}.json'


def freeze_current(case):
    """Create a new request only for a genuinely new model call after revision."""
    origin = verify_origin(case)
    if origin is None:
        return None
    from _lib.runtime.fact_versions import ROOT as FACT_ROOT, current_facts
    case = Path(case)
    if not (case/FACT_ROOT/'active.json').is_file():
        return origin
    version = current_facts(case).version_id
    if origin.get('fact_version_id') == version:
        return origin
    path = _versioned_request_path(case, version)
    alignment_timeline_sha256 = _require_reconciliation_alignment(case) if not path.is_file() else None
    if _census_view_requested(case) and not path.is_file():
        from _lib.behavior import census_read_view
        census_read_view.materialize(case, evidence_timeline(case))
    snapshot_value = snapshot(case)
    if path.is_file():
        return _verified_request(path, compare=snapshot_value)
    if alignment_timeline_sha256 is not None and digest(snapshot_value['evidence_timeline']) != alignment_timeline_sha256:
        raise InputChanged('Reconciliation source timeline changed before Behavior freeze')
    request = dict(snapshot_value, request_sha256=digest(snapshot_value))
    atomic_json(path, request)
    return request


def active_request(case):
    origin = verify_origin(case)
    if origin is None:
        return None
    from _lib.runtime.fact_versions import ROOT as FACT_ROOT, current_facts
    case = Path(case)
    if not (case/FACT_ROOT/'active.json').is_file():
        return origin
    version = current_facts(case).version_id
    path = _versioned_request_path(case, version)
    if path.is_file():
        return _verified_request(path, compare=snapshot(case))
    return origin


def _portable_timeline(timeline):
    """Compare evidence by content; the freezing directory must not matter.

    ``batch_sha256`` keys are absolute paths at freeze time. A Case copy (for
    example the isolated core-approximate projection) recomputes the same
    hashes under its own prefix, so only the portable ``batches/<name>`` form
    may take part in equality. Emission stays unchanged for frozen requests.
    """
    if not isinstance(timeline, dict) or not isinstance(timeline.get('batch_sha256'), dict):
        return timeline
    portable = dict(timeline)
    sources = {}
    for name, value in timeline['batch_sha256'].items():
        path = Path(name) if isinstance(name, str) else None
        if (path is None or path.parent.name != 'batches' or path.suffix != '.json'
                or not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value)):
            raise InputChanged('Behavior evidence timeline has an invalid Batch source')
        source_id = 'batches/' + path.name
        if source_id in sources:
            raise InputChanged('Behavior evidence timeline has duplicate Batch sources')
        sources[source_id] = value
    portable['batch_sha256'] = sources
    return portable


def current_evidence_timeline(case):
    """Return only an index frozen against the effective inputs of this Case."""
    if not timeline_enabled(case):
        return None
    request = active_request(case)
    if request is None or request.get('evidence_timeline', {}).get('revision') != EVIDENCE_TIMELINE:
        raise InputChanged('Behavior evidence timeline has no frozen request')
    from _lib.runtime.fact_versions import ROOT as FACT_ROOT, current_facts
    case = Path(case)
    if (case/FACT_ROOT/'active.json').is_file() and request.get('fact_version_id') != current_facts(case).version_id:
        raise InputChanged('Behavior evidence timeline targets an older fact version')
    if _portable_timeline(request['evidence_timeline']) != _portable_timeline(evidence_timeline(case)):
        raise InputChanged('Behavior evidence timeline differs from effective source facts')
    return request['evidence_timeline']


def current_census_read_view(case):
    """Verify a new request's compact view before giving its path to a worker."""
    request = active_request(case)
    if request is None or 'census_read_view' not in request:
        return None
    from _lib.behavior import census_read_view
    timeline = current_evidence_timeline(case)
    if timeline is None:
        raise InputChanged('Behavior Census reading view has no frozen evidence timeline')
    return census_read_view.verify(case, request['census_read_view'], timeline)


def _require_reconciliation_alignment(case):
    """Gate a new request; existing frozen requests keep their own contract."""
    from _lib.contracts.reconciliation_alignment import enabled as alignment_enabled, inspect
    if not alignment_enabled(case):
        return None
    if not timeline_enabled(case):
        raise InputChanged('Reconciliation alignment requires the Actor evidence timeline')
    packet = inspect(case)
    if packet['blocking']:
        error = ContractError('Reconciliation has unresolved execution-relevant facts',
                              rule_id='reconciliation.alignment')
        error.diagnostics = dict(rule_id=error.rule_id, stage='reconciliation',
                                 status='needs_review', fact_version_id=packet['fact_version_id'],
                                 blocking=[packet['rows'][i] for i in packet['blocking']],
                                 unverified_text_count=len(packet['unverified_text']))
        raise error
    return packet['timeline_sha256']


def closure_request(case):
    """Return a verified current Behavior request, without creating authority."""
    case = Path(case)
    if not enabled(case) or not (case/REQUEST).is_file():
        return None
    request = active_request(case)
    from _lib.runtime.fact_versions import ROOT as FACT_ROOT, current_facts
    if (case/FACT_ROOT/'active.json').is_file() and \
            ('fact_version_id' in request or 'question_authorization' in request) and \
            request.get('fact_version_id') != current_facts(case).version_id:
        raise ContractError('stale_version: Behavior closure request targets an older fact version',
                            rule_id='stale_version')
    return request


def request_path(case, request):
    origin = verify_origin(case)
    if origin and request['request_sha256'] == origin['request_sha256']:
        return Path(case)/REQUEST
    version_id = request.get('fact_version_id')
    path = _versioned_request_path(case, version_id)
    if _verified_request(path)['request_sha256'] != request['request_sha256']:
        raise InputChanged('Behavior request path and digest disagree')
    return path


def _known_requests(case):
    origin = verify_origin(case)
    if origin is None:
        return {}
    result = {origin['request_sha256']: origin}
    for path in (Path(case)/VERSIONED_REQUESTS).glob('*.json'):
        request = _verified_request(path)
        result[request['request_sha256']] = request
    return result


def verify(case):
    request = verify_origin(case)
    if request is None:
        return None
    from _lib.runtime.fact_versions import ROOT as FACT_ROOT, current_facts
    case = Path(case)
    versioned = (case/FACT_ROOT/'active.json').is_file()
    if not versioned or request.get('fact_version_id') == current_facts(case).version_id:
        body = dict(request); body.pop('request_sha256', None)
        if body != snapshot(case):
            raise InputChanged('Behavior World/Registry/Batch/frame/parameter inputs changed after freezing')
    return request


def record_validation(case, candidate_path, *, passed, error=None, source_candidate_path=None,
                      source_session=None, expected_version=None):
    """Record a current check separately from the immutable model request."""
    from _lib.runtime.fact_versions import current_facts
    known = _known_requests(case)
    if not known:
        return None
    view = current_facts(case, expected_version)
    candidate_path = Path(candidate_path).resolve()
    if not candidate_path.is_file() or not candidate_path.is_relative_to(Path(case).resolve()):
        raise InputChanged('Behavior revalidation candidate is unavailable')
    source_hash = None
    sessions_path = Path(case)/SESSIONS
    sessions = read_json(sessions_path) if sessions_path.is_file() else {}
    if source_session is not None:
        source_hash = sessions.get(source_session)
        if source_hash is None:
            raise InputChanged('Behavior revalidation session has no frozen request')
    elif source_candidate_path is not None:
        progress_path = Path(case)/'state/progress.json'
        progress = read_json(progress_path) if progress_path.is_file() else {}
        source_hash = next((sessions.get(row.get('session_id'))
            for row in reversed(progress.get('sessions', []))
            if row.get('invalid_candidate_path') == str(Path(source_candidate_path).resolve())), None)
        if (source_hash is None and Path(source_candidate_path).resolve()
                == (Path(case)/'state/behavior.candidate.json').resolve()):
            source_hash = next((sessions.get(row.get('session_id'))
                for row in reversed(progress.get('sessions', []))
                if row.get('stage') == 'behavior' and sessions.get(row.get('session_id'))), None)
    if source_hash is None:
        request = verify_origin(case)
    else:
        request = known.get(source_hash)
    if request is None:
        raise InputChanged('Behavior revalidation source request is unknown')
    record = dict(revision='behavior-revalidation.v1',
                  source_request_sha256=request['request_sha256'],
                  source_fact_version_id=request.get('fact_version_id'),
                  candidate_path=str(candidate_path), candidate_sha256=sha256(candidate_path),
                  validation_version_id=view.version_id,
                  validation_context_sha256=sha256(view.directory/'manifest.json'),
                  status='passed' if passed else 'failed',
                  error=str(error) if error is not None else None)
    from _lib.contracts.core_lane_continuity import obligations
    record['motion_obligations_sha256'] = obligations(case)['sha256']
    if timeline_enabled(case):
        record['evidence_timeline_sha256'] = digest(current_evidence_timeline(case))
    if passed:
        from _lib.contracts.core_lane_continuity import validate as validate_motion_obligations
        record['motion_dispositions'] = validate_motion_obligations(
            case, read_json(candidate_path), return_dispositions=True)
    if source_session is not None:
        record['source_session_id'] = source_session
    if source_candidate_path is not None:
        source_candidate_path = Path(source_candidate_path).resolve()
        if (not source_candidate_path.is_file()
                or not source_candidate_path.is_relative_to(Path(case).resolve())):
            raise InputChanged('Original Behavior candidate is unavailable')
        record['source_candidate_path'] = str(source_candidate_path)
        record['source_candidate_sha256'] = sha256(source_candidate_path)
    record['validation_sha256'] = digest(record)
    source_suffix = record.get('source_candidate_sha256', 'accepted')[:16]
    outcome_suffix = 'passed' if passed else 'failed-'+digest(str(error))[:8]
    path = Path(case)/'diagnostics/behavior_revalidations'/(
        f"{record['candidate_sha256']}.{view.version_id}.{source_suffix}.{outcome_suffix}.json")
    if path.is_file() and read_json(path) != record:
        raise InputChanged('Behavior revalidation result already differs')
    if not path.is_file():
        atomic_json(path, record)
    progress_path = Path(case)/'state/progress.json'
    if progress_path.is_file():
        progress = read_json(progress_path)
        progress.setdefault('diagnostics', {})['behavior_revalidation'] = dict(
            status=record['status'], validation_version_id=view.version_id,
            behavior_sha256=record['candidate_sha256'], record_path=str(path))
        atomic_json(progress_path, progress)
    return record


def accepted_validation(case, expected_version=None):
    """Require a successful full Behavior check of the exact accepted bytes."""
    from _lib.runtime.fact_versions import current_facts
    view = current_facts(case, expected_version)
    requests = _known_requests(case)
    behavior = Path(case)/'state/behavior.json'
    if not requests or not behavior.is_file():
        raise InputChanged('Delivered Behavior lacks original request provenance')
    expected_hash = sha256(behavior)
    from _lib.contracts.core_lane_continuity import obligations, validate as validate_motion_obligations
    current_obligations_sha256 = obligations(case)['sha256']
    current_timeline_sha256 = (digest(current_evidence_timeline(case))
                               if timeline_enabled(case) else None)
    directory = Path(case)/'diagnostics/behavior_revalidations'
    for path in directory.glob(f'{expected_hash}.{view.version_id}.*.passed.json'):
        row = read_json(path)
        body = dict(row); signature = body.pop('validation_sha256', None)
        if (signature == digest(body)
                and row.get('status') == 'passed'
                and row.get('source_request_sha256') in requests
                and row.get('candidate_sha256') == expected_hash
                and row.get('validation_version_id') == view.version_id
                and row.get('validation_context_sha256') == sha256(view.directory/'manifest.json')
                and row.get('motion_obligations_sha256', current_obligations_sha256)
                    == current_obligations_sha256
                and (current_timeline_sha256 is None or
                     row.get('evidence_timeline_sha256') == current_timeline_sha256)
                and (row.get('source_session_id') is None
                     or read_json(Path(case)/SESSIONS).get(row['source_session_id'])
                        == row['source_request_sha256'])
                and (row.get('source_candidate_path') is None
                     or Path(row['source_candidate_path']).is_file()
                     and sha256(row['source_candidate_path'])
                        == row['source_candidate_sha256'])):
            dispositions = validate_motion_obligations(
                case, read_json(behavior), return_dispositions=True)
            if 'motion_dispositions' in row and row['motion_dispositions'] != dispositions:
                continue
            return row
    raise InputChanged('Accepted Behavior has not passed current fact-version validation')


def freeze(case):
    if not enabled(case):
        return None
    case = Path(case)
    if (case/REQUEST).exists():
        return verify(case)
    if any((case/name).exists() for name in ('state/behavior.json', 'state/behavior.candidate.json')):
        raise InputChanged('Cannot bind an existing Behavior artifact to newly observed inputs')
    alignment_timeline_sha256 = _require_reconciliation_alignment(case)
    if _census_view_requested(case):
        from _lib.behavior import census_read_view
        census_read_view.materialize(case, evidence_timeline(case))
    request = snapshot(case)
    if alignment_timeline_sha256 is not None and digest(request['evidence_timeline']) != alignment_timeline_sha256:
        raise InputChanged('Reconciliation source timeline changed before Behavior freeze')
    request['request_sha256'] = digest(request)
    atomic_json(case/REQUEST, request)
    return request


def task_request(case, key, candidate):
    if key == 'behavior' and candidate is not None and enabled(case) and (
            Path(candidate).resolve() == (Path(case)/'state/behavior.candidate.json').resolve()):
        return active_request(case)
    return None


def bind_session(case, session, request):
    if request is None:
        return
    path = Path(case)/SESSIONS
    records = read_json(path) if path.is_file() else {}
    prior = records.get(session)
    if prior is not None and prior != request['request_sha256']:
        raise InputChanged('Behavior session already belongs to another frozen request')
    records[session] = request['request_sha256']
    atomic_json(path, records)


def require_session(case, session=None):
    origin = verify_origin(case)
    if origin is None:
        return
    path = Path(case)/SESSIONS
    records = read_json(path) if path.is_file() else {}
    if session is None:
        progress = read_json(Path(case)/'state/progress.json')
        session = next((row.get('session_id') for row in reversed(progress.get('sessions', []))
                        if row.get('stage') == 'behavior'), None)
    valid_hashes = {origin['request_sha256']}
    directory = Path(case)/VERSIONED_REQUESTS
    for path in directory.glob('*.json'):
        valid_hashes.add(_verified_request(path)['request_sha256'])
    if not session or records.get(session) not in valid_hashes:
        raise InputChanged('Behavior candidate/session does not belong to the frozen input request')


def archive_matches(case, path):
    if not enabled(case):
        return True
    request = verify_origin(case)
    sessions = read_json(Path(case)/SESSIONS) if (Path(case)/SESSIONS).is_file() else {}
    progress = read_json(Path(case)/'state/progress.json')
    valid_hashes = {request['request_sha256']}
    for frozen in (Path(case)/VERSIONED_REQUESTS).glob('*.json'):
        valid_hashes.add(_verified_request(frozen)['request_sha256'])
    return any(row.get('invalid_candidate_path') == str(path) and
               sessions.get(row.get('session_id')) in valid_hashes
               for row in progress.get('sessions', []))


def is_complete_behavior(data):
    return (isinstance(data, dict) and 'base_sha256' not in data
            and 'replacements' not in data
            and isinstance(data.get('contract_version'), str)
            and isinstance(data.get('actors'), list)
            and isinstance(data.get('events'), list)
            and isinstance(data.get('semantic_limitations'), list))


def replayable_archives(case):
    """Complete terminal Behavior answers bound to the current frozen facts."""
    case = Path(case).resolve()
    request = verify(case) if enabled(case) else None
    active = active_request(case) if request is not None else None
    from _lib.runtime.fact_versions import ROOT as FACT_ROOT, current_facts
    if (active is not None and (case/FACT_ROOT/'active.json').is_file()
            and active.get('fact_version_id') != current_facts(case).version_id):
        return []
    sessions = read_json(case/SESSIONS) if (case/SESSIONS).is_file() else {}
    progress = read_json(case/'state/progress.json')
    result = []
    for row in reversed(progress.get('sessions', [])):
        if (row.get('stage') != 'behavior' or row.get('status') not in
                {'invalid_output', 'failed', 'completed'} or not row.get('task_id')):
            continue
        source = row.get('invalid_candidate_path')
        if not isinstance(source, str):
            continue
        path = Path(source)
        if (not path.is_file() or not path.resolve().is_relative_to(case)
                or path.parent.resolve() != (case/'diagnostics/invalid_candidates').resolve()
                or path.name != f"behavior.attempt_{row.get('attempt')}.{sha256(path)[:16]}.json"
                or (active is not None and sessions.get(row.get('session_id')) != active['request_sha256'])):
            continue
        try:
            complete = is_complete_behavior(read_json(path))
        except (ValueError, UnicodeError):
            complete = False
        if not complete:
            continue
        result.append(path)
    return result


def retire(case):
    """Only the explicit World re-adjudication lifecycle may retire a request."""
    path = Path(case)/REQUEST
    if path.is_file():
        old = read_json(path)
        atomic_json(Path(case)/'checkpoints/behavior_inputs'/f"{old['request_sha256']}.json", old)
        path.unlink()
