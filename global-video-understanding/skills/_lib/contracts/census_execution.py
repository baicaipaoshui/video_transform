"""Execution choices for unresolved census observations, separate from facts."""
from pathlib import Path

from _lib.contracts.conversion_policy import digest
from _lib.contracts.v3 import ContractError
from _lib.runtime.case_core import read_json, sha256

REVISION = 'census-execution.v1'
HINTS = 'state/census_execution_hints.json'


def _identity_assumptions_active(program):
    """Only identity/lane choices conflict with early-association proof.

    The same receipt may carry bounded motion reconstruction metadata without
    selecting any observation or lane.  That metadata must not suppress an
    otherwise independent early prefix.
    """
    return bool(program and (program.get('observations') or program.get('lanes')
                             or program.get('inherited_opposing')))


def enabled(case):
    policy = Path(case)/'state/execution_policy.json'
    from _lib.contracts.census_relevance import enabled as relevance_enabled
    if relevance_enabled(case): return True
    return (policy.is_file() and
            read_json(policy).get('behavior_census_scope') == 'behavior-census-scope.v2' and
            read_json(policy).get('execution_scope') == 'ego_vehicle_scene.v4')


def hints(case, pending):
    """Validate optional frozen Case overrides; normal execution needs none."""
    case = Path(case)
    path = case/HINTS
    if not path.is_file():
        return {}, None
    value = read_json(path)
    from _lib.runtime.fact_versions import source_path
    source_hashes = {name: sha256(source_path(case, Path(name).name)) for name in
                     ('state/census_adjudication.accepted.json', 'state/actor_registry.json')}
    if (not isinstance(value, dict) or set(value) != {'revision', 'case_id', 'source_hashes', 'decisions'}
            or value['revision'] != REVISION or value['case_id'] != case.name
            or value['source_hashes'] != source_hashes or not isinstance(value['decisions'], list)):
        raise ContractError('Census execution hints are stale or malformed')
    rows = {}
    for row in value['decisions']:
        if (not isinstance(row, dict) or not isinstance(row.get('issue_id'), str)
                or row['issue_id'] not in pending
                or row['issue_id'] in rows or row.get('handling') not in {'omit', 'representative'}
                or (row.get('handling') == 'representative'
                    and (not isinstance(row.get('lane_id'), str) or not row['lane_id']))
                or set(row) != ({'issue_id', 'handling', 'lane_id'} if row.get('handling') == 'representative'
                                else {'issue_id', 'handling'})):
            raise ContractError('Census execution hint refers to an invalid or duplicate observation')
        rows[row['issue_id']] = row
    return rows, sha256(path)


def _certificate_hash(case, snapshot):
    if snapshot is None:
        from _lib.runtime.fact_versions import census_path
        return sha256(census_path(case))
    # Historical validation binds the already replayed ancestor, never the new
    # certificate currently being validated. The caller verified this archive.
    from _lib.contracts.census_history import ARCHIVE
    return sha256(case/ARCHIVE/(snapshot['sha256']+'.json'))


def decisions(case, behavior, pending, review, *, snapshot=None, frames=None, program=None, assumptions=None):
    """Conservatively classify each issue without changing the model contract."""
    if not enabled(case):
        return None
    case = Path(case)
    from _lib.contracts import census_relevance
    if census_relevance.enabled(case):
        from _lib.behavior.compile import evidence_frames
        if snapshot is None:
            frames = {f['frame_id']:f for f in evidence_frames(case)}
        elif frames is None:
            raise ContractError('Historical census execution requires frozen frames')
        world = snapshot['result']['world'] if snapshot is not None else read_json(case/'state/world_context.json')
        rows = {r['issue_id']:r for r in (review or {}).get('decisions', [])}
        grouped = census_relevance.grouped_views(behavior, pending, rows)
        choices = []
        for iid, observation in sorted(pending.items()):
            if iid in (assumptions or {}).get('observations', {}):
                from _lib.contracts.execution_assumptions import choice
                choices.append(choice(iid, observation))
                continue
            if iid in (program or {}).get('records', {}):
                from _lib.contracts.unused_association import HANDLING
                choices.append(dict(issue_id=iid, candidate_actor_ids=observation['candidate_actor_ids'],
                    observation_frame_id=observation['frame_id'], outcome='unresolved',
                    handling=HANDLING, identity_resolution='unresolved'))
                continue
            checked = census_relevance.validate(case, grouped.get(iid, behavior), rows[iid], observation, frames, world,
                registry=snapshot['result']['registry'] if snapshot is not None else None)
            choices.append(dict(issue_id=iid, candidate_actor_ids=observation['candidate_actor_ids'],
                observation_frame_id=observation['frame_id'], outcome=rows[iid]['outcome'],
                **{k:v for k,v in (checked or {'handling':'needs_evidence'}).items() if k != 'issue_id'}))
        retained = [(r['actor_id'], r['observation_frame_id']) for r in choices
                    if r['handling'] == 'retained_unresolved']
        if len(retained) != len(set(retained)):
            raise ContractError('Distinct unresolved objects cannot bind the same physical Actor and frame',
                                rule_id='identity.census.duplicate_execution_binding')
        receipt = dict(revision=census_relevance.EXECUTION,
            source_certificate_sha256=_certificate_hash(case, snapshot),
            behavior_digest=digest(behavior), choices=choices)
        if program:
            receipt['unused_association'] = program
        if assumptions:
            receipt['execution_assumptions'] = assumptions
        from _lib.contracts import early_association
        proof = None if program or _identity_assumptions_active(assumptions) else (early_association.automatic(case, behavior, pending, review, frames, world,
                     **({'snapshot':snapshot} if snapshot is not None else {}))
                 if early_association.automatic_enabled(case)
                 else early_association.load(case, behavior, review,
                     **({'snapshot':snapshot} if snapshot is not None else {})))
        if proof:
            covered = {iid for g in proof['groups'] for iid in g['issue_ids']}
            for choice in choices:
                if choice['issue_id'] in covered:
                    if choice['handling'] != 'needs_evidence':
                        raise ContractError('Early association cannot replace an accepted scope decision')
                    choice.update(handling=early_association.HANDLING, identity_resolution='unresolved',
                                  independence_sha256=proof['sha256'])
            receipt['early_association_proof'] = proof
        if snapshot is None:
            from _lib.contracts import peripheral_uncertainty
            from _lib.contracts.census_adjudication import proof as census_proof
            peripheral = peripheral_uncertainty.build(case, behavior, pending, frames,
                census_proof(case), review, choices)
            if peripheral:
                for item in choices:
                    record = peripheral['records'].get(item['issue_id'])
                    if record:
                        if item['handling'] != 'needs_evidence':
                            raise ContractError('Peripheral assumption replaces an accepted disposition')
                        item.update(peripheral_uncertainty.choice(item['issue_id'],
                            pending[item['issue_id']], record, peripheral['proxies']))
                receipt[peripheral_uncertainty.KEY] = peripheral
        if snapshot is None:
            # Historical certificate replay must never consume a newer live
            # execution review. The new receipt is bound to this exact Behavior.
            from _lib.runtime.downstream_scope_review import load
            from _lib.contracts.downstream_scope import HANDLING
            scope = load(case, behavior, review)
            if scope:
                if scope['pending'] != pending:
                    raise ContractError('Downstream scope observation set changed')
                for choice in choices:
                    if choice['handling'] != 'needs_evidence':
                        raise ContractError('Downstream scope cannot replace an accepted execution decision')
                    choice.update(handling=HANDLING, identity_resolution='unresolved',
                                  scope_proof_sha256=scope['sha256'])
                receipt['downstream_scope_proof'] = scope
            from _lib.runtime.core_scene_scope import load as load_core_scene
            from _lib.contracts.core_scene_scope import HANDLING as CORE_HANDLING
            core = load_core_scene(case, behavior, review)
            if core:
                if core['pending'] != pending or scope:
                    raise ContractError('Core scene observation set or execution mode changed')
                for choice in choices:
                    if choice['issue_id'] not in core['covered_issue_ids']:
                        continue
                    if choice['handling'] != 'needs_evidence':
                        raise ContractError('Core scene cannot replace an accepted execution decision')
                    choice.update(handling=CORE_HANDLING, identity_resolution='unresolved',
                                  core_scene_sha256=core['sha256'])
                receipt['core_scene_receipt'] = core
        return dict(receipt, sha256=digest(receipt))
    supplied, hint_hash = hints(case, pending)
    rows = {r['issue_id']:r for r in review['decisions']}
    actors = {a['actor_id']:a for a in behavior['actors']}
    world = snapshot['result']['world'] if snapshot is not None else read_json(case/'state/world_context.json')
    lanes = {l['lane_id']:l for l in world['world']['road'].get('lanes', [])}
    times = {f['frame_id']:f['time_s'] for f in read_json(case/'state/probe.json')['decoded_frames']}
    from _lib.contracts.execution_gaps import dependencies
    def mentions(node, values):
        if isinstance(node, str): return node in values
        if isinstance(node, (list, tuple)): return any(mentions(v, values) for v in node)
        if isinstance(node, dict): return any(mentions(v, values) for v in node.values())
        return False
    dependencies_tree = dependencies(case, behavior)
    ego = actors.get('ego', {})
    def right_neighbor(frame_id):
        point = next((p for p in ego.get('trajectory', []) if p.get('frame_id') == frame_id), {})
        ego_lane = lanes.get(point.get('lane_id'))
        if not ego_lane or type(ego_lane.get('ordinal_from_right')) is not int:
            return None
        adjacent = [lane for lane in lanes.values()
                    if lane.get('road_id') == ego_lane.get('road_id')
                    and lane.get('travel_direction', 'forward') in {'same_as_ego', 'forward'}
                    and type(lane.get('ordinal_from_right')) is int
                    and lane['ordinal_from_right'] == ego_lane['ordinal_from_right'] - 1]
        return adjacent[0] if len(adjacent) == 1 else None
    def co_visible_conflict(observation, actor_id):
        batch = read_json(case/'batches'/f"{observation['batch_id']}.json")
        return any(frame['frame_id'] == observation['frame_id']
                   and any(vehicle['object_id'] != observation['object_id']
                           and vehicle.get('actor_id') == actor_id
                           and vehicle.get('binding') in {'existing', 'new'}
                           for vehicle in frame['vehicles'])
                   for frame in batch.get('vehicle_census', []))
    choices = []
    for iid, observation in sorted(pending.items()):
        row, hint = rows[iid], supplied.get(iid)
        candidates = observation['candidate_actor_ids']
        ids = {iid, *candidates}
        independent = (not any(aid not in actors or any(p.get('lane_id') is not None
                         for p in actors[aid].get('trajectory', [])) for aid in candidates)
            and not set(row['affected_actor_ids']) - set(candidates)
            and not row['affected_event_ids']
            and not mentions(dependencies_tree[:2], ids)
            and not any(iid in q.get('census_issue_ids', []) and q.get('world_paths')
                        for collection in ('questions_to_verify', 'ambiguities')
                        for q in world.get(collection, [])))
        value = dict(issue_id=iid, observation_frame_id=observation['frame_id'],
                     candidate_actor_ids=candidates, outcome=row['outcome'], handling='needs_evidence')
        if row['outcome'] == 'excluded':
            # A pre-Behavior choice is unnecessary once the model supplies a
            # complete physical-isolation proof for the same observation.
            value['handling'] = 'isolated_opposing'
        elif (row['outcome'] == 'needs_identity' and len(candidates) == 1 and not hint
              and candidates[0] in actors):
            # This is an execution assumption. The frozen first_seen and source
            # trajectory are untouched; a separately recorded co-visible split
            # remains a conflict and is not overridden here.
            candidate = actors[candidates[0]]
            from _lib.contracts import behavior_handoff
            compatible = (not behavior_handoff.enabled(case)
                          or behavior_handoff.permits_assumption(row, candidates, behavior))
            if (compatible and observation['frame_id'] not in {p.get('frame_id') for p in candidate['trajectory']}
                    and not co_visible_conflict(observation, candidates[0])):
                value.update(handling='assumed_existing', actor_id=candidates[0])
        elif hint and hint['handling'] == 'representative' and row['outcome'] in {'needs_identity','unknown'}:
            lane = lanes.get(hint['lane_id'])
            ego = actors.get('ego', {})
            ego_point = next((p for p in ego.get('trajectory', [])
                              if p.get('frame_id') == observation['frame_id']), {})
            ego_lane = lanes.get(ego_point.get('lane_id'))
            adjacent = (hint['lane_id'] == ego_point.get('lane_id') or
                        (lane is not None and ego_lane is not None
                         and type(lane.get('ordinal_from_right')) is int
                         and type(ego_lane.get('ordinal_from_right')) is int
                         and abs(lane['ordinal_from_right']-ego_lane['ordinal_from_right']) <= 1))
            if (lane is None or lane.get('travel_direction', 'forward') not in {'same_as_ego','forward'}
                    or ego_lane is None or lane.get('road_id') != ego_lane.get('road_id')
                    or not adjacent
                    or observation['frame_id'] not in times or row['affected_event_ids']):
                raise ContractError('Representative requires the ego lane or an adjacent same-direction lane and no event conflict')
            actor_id = 'execution_proxy_' + digest(iid)[:12]
            if actor_id in actors:
                raise ContractError('Representative Actor ID collides with an observed Actor')
            value.update(handling='single_representative', actor_id=actor_id,
                         lane_id=hint['lane_id'], time_s=times[observation['frame_id']])
        elif hint and hint['handling'] == 'omit' and row['outcome'] in {'needs_identity','unknown'}:
            value['handling'] = 'observation_only_unknown'
        elif not hint and independent and row['outcome'] == 'needs_identity' and len(candidates) > 1:
            wording = observation.get('description', '') + ' ' + row['reason']
            if any(cue in wording for cue in ('右邻走廊', '右邻车道', '右侧相邻车道', '相邻右车道')):
                lane = right_neighbor(observation['frame_id'])
                if lane and observation['frame_id'] in times:
                    actor_id = 'execution_proxy_' + digest(iid)[:12]
                    if actor_id in actors:
                        raise ContractError('Representative Actor ID collides with an observed Actor')
                    value.update(handling='single_representative', actor_id=actor_id,
                                 lane_id=lane['lane_id'], time_s=times[observation['frame_id']])
        elif not hint and independent and row['outcome'] == 'unknown':
            wording = observation.get('description', '') + ' ' + row['reason']
            if (any(cue in wording for cue in ('远', '消失点', 'distant', 'far'))
                    and any(cue in wording for cue in ('小', '点状', 'tiny', 'small'))):
                value['handling'] = 'observation_only_unknown'
        if hint and value['handling'] in {'observation_only_unknown','single_representative'}:
            if not independent:
                raise ContractError('Census execution choice conflicts with an ego action or road dependency')
        choices.append(value)
    receipt = dict(revision=REVISION, source_certificate_sha256=_certificate_hash(case, snapshot), hint_file_sha256=hint_hash,
        behavior_digest=digest(behavior), choices=choices)
    return dict(receipt, sha256=digest(receipt))


def check(case, behavior, pending, review, saved):
    actual = saved.get('execution_dispositions')
    expected = decisions(case, behavior, pending, review)
    if actual != expected:
        raise ContractError('Census execution dispositions changed or lack source binding')
    return expected
