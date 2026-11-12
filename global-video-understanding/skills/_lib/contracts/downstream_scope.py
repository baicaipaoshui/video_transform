"""Execution-only scope proof. Never an identity or observation amendment."""
from _lib.contracts.conversion_policy import digest
from _lib.contracts.v3 import ContractError

REVISION = 'downstream-execution-scope.v1'
HANDLING = 'downstream_scope_excluded'


def fail(message):
    raise ContractError(message, rule_id='execution_scope.review')


def mandatory(behavior):
    from _lib.runtime.execution_scope import references
    ids = {'ego'}
    known = {a['actor_id'] for a in behavior['actors']}
    def nested_ids(value):
        if isinstance(value, str): return {value} & known
        if isinstance(value, dict): return nested_ids(list(value.values()))
        if isinstance(value, list): return set().union(*(nested_ids(v) for v in value)) if value else set()
        return set()
    for item in behavior.get('events', []) + behavior.get('executor_primitives', []):
        ids.update(references(item))
        ids.update(item.get('involved_actor_ids', []))
        ids.update(nested_ids(item))
    # A sustained, independently localized track remains part of the scene even
    # if the event summary omitted it. Scope review cannot shorten that track.
    ids.update(a['actor_id'] for a in behavior['actors'] if len({
        p['frame_id'] for p in a.get('trajectory', []) if p.get('lane_id') is not None}) >= 3)
    return ids


def candidates(world, behavior, frames):
    from _lib.runtime.execution_scope import project, EGO_POLICY
    _, projected, _ = project(world, behavior, policy_id=EGO_POLICY, handoff_frames=frames)
    return sorted({a['actor_id'] for a in projected['actors']} - mandatory(behavior))


def validate(world, behavior, frames, proof):
    """Replay the embedded proof at every projection, independent of live state."""
    if (not isinstance(proof, dict) or proof.get('revision') != REVISION
            or proof.get('sha256') != digest({k:v for k,v in proof.items() if k != 'sha256'})
            or proof.get('world_digest') != digest(world)
            or proof.get('behavior_digest') != digest(behavior)
            or proof.get('frames') != [dict(frame_id=f['frame_id'], time_s=f['time_s']) for f in frames]):
        fail('Downstream scope proof changed or belongs to different execution inputs')
    expected = set(candidates(world, behavior, frames))
    if set(proof.get('candidate_actor_ids', [])) != expected or not expected:
        fail('Downstream scope candidate set changed')
    response = proof.get('response')
    if (not isinstance(response, dict) or set(response) != {'request_sha256', 'actors', 'observations'}
            or response.get('request_sha256') != proof.get('request_sha256')):
        fail('Downstream scope response lacks its frozen request binding')
    def indexed(rows, key, ids):
        if (not isinstance(rows, list) or any(not isinstance(r, dict) or not isinstance(r.get(key), str) for r in rows)
                or len(rows) != len(ids) or {r[key] for r in rows} != set(ids)):
            fail('Downstream scope response must cover each selected object exactly once')
        return {r[key]:r for r in rows}
    actors = {a['actor_id']:a for a in behavior['actors']}
    actor_rows = indexed(response['actors'], 'actor_id', expected)
    pending = proof['pending']
    obs_rows = indexed(response['observations'], 'issue_id', pending)
    for row in proof['review']['decisions']:
        assessment = row.get('identity_assessment') or {}
        if (row.get('affected_event_ids') or set(row.get('affected_actor_ids', [])) - expected
                or set(assessment.get('alternative_actor_ids', [])) - expected):
            fail('Scope review cannot discharge a retained identity alternative or event')
    times = {f['frame_id']:f['time_s'] for f in frames}
    def evidence(row, key, visible):
        fields = {key, 'decision', 'reason', 'identity_dependency', 'path_dependency',
                  'interaction_dependency', 'sightings'}
        if (set(row) != fields or row.get('decision') != 'exclude'
                or any(row.get(k) is not False for k in
                       ('identity_dependency', 'path_dependency', 'interaction_dependency'))
                or not isinstance(row.get('reason'), str) or not row['reason'].strip()):
            fail('Execution scope remains relevant/unknown or lacks explicit independence evidence')
        sights = indexed(row['sightings'], 'frame_id', times)
        for fid, sight in sights.items():
            if (set(sight) != {'frame_id', 'time_s', 'object_visible', 'relation_to_ego_path',
                             'location_evidence', 'interaction_evidence'}
                    or type(sight['time_s']) not in (int, float) or sight['time_s'] != times[fid]
                    or type(sight['object_visible']) is not bool
                    or fid in visible and not sight['object_visible']
                    or sight['relation_to_ego_path'] not in {'outside_path', 'remote_non_interacting'}
                    or any(not isinstance(sight[k], str) or not sight[k].strip()
                           for k in ('location_evidence', 'interaction_evidence'))):
                fail('Downstream scope lacks complete frame-bound non-interaction evidence')
    for aid, row in actor_rows.items():
        evidence(row, 'actor_id', {p['frame_id'] for p in actors[aid].get('trajectory', [])})
    for iid, row in obs_rows.items():
        obs = pending[iid]
        if (obs.get('distinction') or not obs.get('candidate_actor_ids')
                or not set(obs['candidate_actor_ids']) <= expected):
            fail('Observation has a retained/unknown candidate or a formal identity distinction')
        evidence(row, 'issue_id', {obs['frame_id']})
    # All alternatives in a group are scoped together. An actor-only claim
    # cannot erase a mixed identity, road, motion or event obligation.
    from _lib.runtime.execution_scope import references
    items = world.get('questions_to_verify', []) + world.get('ambiguities', []) + behavior.get('semantic_limitations', [])
    for item in items:
        ids = references(item)
        issue_ids = set(item.get('census_issue_ids', []))
        if not (ids & expected or issue_ids & set(pending)):
            continue
        # A global metric-scale limitation is retained by project(), then owned
        # by normal semantic motion completion. It is not an identity dependency.
        if (item.get('limitation_kind') == 'absolute_scale'
                and set(item.get('impact_domains', [])) <= {'speed', 'position'}
                and not issue_ids and not item.get('world_paths') and not item.get('related_world_paths')
                and not item.get('affected_event_ids') and not item.get('scope_binding')):
            continue
        binding = item.get('scope_binding') or {}
        if (ids - expected or item.get('world_paths') or item.get('related_world_paths')
                or item.get('affected_event_ids') or binding.get('affected_event_ids')
                or issue_ids - set(pending)):
            fail('Execution exclusion conflicts with a retained identity, road or event dependency')
    return expected


def make(world, behavior, frames, packet, response):
    value = dict(revision=REVISION, request_sha256=packet['request_sha256'],
        world_digest=digest(world), behavior_digest=digest(behavior),
        frames=[dict(frame_id=f['frame_id'], time_s=f['time_s']) for f in frames],
        candidate_actor_ids=packet['candidate_actor_ids'], pending=packet['pending'],
        review=packet['review'], response=response)
    value['sha256'] = digest(value)
    validate(world, behavior, frames, value)
    return value
