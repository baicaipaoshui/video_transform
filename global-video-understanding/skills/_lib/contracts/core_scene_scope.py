"""Explicit sparse-background omission, not proof of visual independence."""
import copy

from _lib.contracts.conversion_policy import digest
from _lib.contracts.v3 import ContractError

REVISION = 'core-scene-approximation.v1'
HANDLING = 'omitted_by_execution_policy'


def fail(message):
    raise ContractError(message, rule_id='execution_scope.core_scene')


def actor_references(value, known):
    """Structured references only; prose is retained, never treated as a verdict."""
    if isinstance(value, str): return {value} & known
    if isinstance(value, dict): return actor_references(list(value.values()), known)
    if isinstance(value, list):
        return set().union(*(actor_references(v, known) for v in value)) if value else set()
    return set()


def required_actors(world, behavior, core):
    from _lib.contracts.downstream_scope import mandatory
    known = {a['actor_id'] for a in behavior['actors']}
    required = set(core) | mandatory(behavior)
    for item in behavior.get('semantic_limitations', []):
        if item.get('source_revision_qualitative'):
            required.update(actor_references(item, known))
        if (item.get('execution_effect') == 'blocks_execution'
                and set(item.get('impact_domains', [])) & {'event', 'path', 'actor_motion', 'executor_primitive'}):
            required.update(actor_references(item, known))
    # Preserve structured direct relations and constraints transitively. Generic
    # metric-scale limitations are NOT relations and stay with motion completion.
    edges = []
    for source in (world, behavior):
        for key in ('relations', 'constraints', 'interactions'):
            edges.extend(actor_references(r, known) for r in source.get(key, []))
    while True:
        expanded = required | set().union(*(e for e in edges if e & required), set())
        if expanded == required: return required
        required = expanded


def validate_review_shape(packet, response, behavior):
    """Check the archived review's binding and coverage, without changing its verdict."""
    if (not isinstance(response, dict) or set(response) != {'request_sha256', 'actors', 'observations'}
            or response['request_sha256'] != packet['request_sha256']):
        fail('Core scene requires the original bound scope response')
    times = {f['frame_id']: f['time_s'] for f in packet['frames']}
    actors = {a['actor_id']: a for a in behavior['actors']}
    for key, collection, expected in (
            ('actor_id', 'actors', packet['candidate_actor_ids']),
            ('issue_id', 'observations', packet['pending'])):
        rows = response[collection]
        if (not isinstance(rows, list) or len(rows) != len(expected)
                or any(not isinstance(r, dict) for r in rows)
                or {r.get(key) for r in rows} != set(expected)):
            fail('Core scene review coverage differs from the frozen request')
        for row in rows:
            if (set(row) != {key, 'decision', 'reason', 'identity_dependency', 'path_dependency',
                            'interaction_dependency', 'sightings'}
                    or row['decision'] not in {'exclude', 'retain', 'unknown'}
                    or not isinstance(row['reason'], str) or not row['reason'].strip()
                    or any(type(row[k]) is not bool for k in
                           ('identity_dependency', 'path_dependency', 'interaction_dependency'))):
                fail('Malformed archived execution scope verdict')
            sights = row['sightings']
            if (not isinstance(sights, list) or len(sights) != len(times)
                    or any(not isinstance(s, dict) for s in sights)
                    or {s.get('frame_id') for s in sights} != set(times)):
                fail('Archived scope review lacks complete frame coverage')
            visible = ({p['frame_id'] for p in actors[row[key]]['trajectory']} if key == 'actor_id'
                       else {packet['pending'][row[key]]['frame_id']})
            for s in sights:
                if (set(s) != {'frame_id', 'time_s', 'object_visible', 'relation_to_ego_path',
                              'location_evidence', 'interaction_evidence'}
                        or type(s['time_s']) not in (int, float) or s['time_s'] != times[s['frame_id']]
                        or type(s['object_visible']) is not bool
                        or s['frame_id'] in visible and not s['object_visible']
                        or s['relation_to_ego_path'] not in {'outside_path', 'remote_non_interacting', 'unknown'}
                        or any(not isinstance(s[k], str) or not s[k].strip()
                               for k in ('location_evidence', 'interaction_evidence'))):
                    fail('Malformed archived scope frame evidence')


def plan(world, behavior, frames, packet, core):
    from _lib.contracts.downstream_scope import candidates, mandatory
    from _lib.contracts.actors import is_vehicle
    from _lib.runtime.execution_scope import references
    actors = {a['actor_id']: a for a in behavior['actors']}
    if (not isinstance(core, list) or not core or any(not isinstance(a, str) for a in core)
            or len(set(core)) != len(core) or 'ego' not in core or set(core) - set(actors)):
        fail('Core scene requires unique existing Actor IDs including ego')
    expected = set(candidates(world, behavior, frames))
    if expected != set(packet['candidate_actor_ids']):
        fail('Core scene candidates differ from the frozen scope request')
    retained = required_actors(world, behavior, core)
    if retained - expected - mandatory(behavior):
        fail('Core scene cannot restore an Actor omitted by existing execution policy')
    omitted = expected - retained
    if not omitted:
        fail('Core scene has no eligible omitted Actors')
    for aid in omitted:
        a = actors[aid]
        if not is_vehicle(a) or not 1 <= len({p['frame_id'] for p in a['trajectory']}) <= 2:
            fail('Core scene omission is limited to sparse vehicle observations')
    covered = []
    decisions = {r['issue_id']: r for r in packet['review']['decisions']}
    for iid, obs in packet['pending'].items():
        ids = set(obs.get('candidate_actor_ids', []))
        row = decisions[iid]
        alternatives = set((row.get('identity_assessment') or {}).get('alternative_actor_ids', []))
        affected = set(row.get('affected_actor_ids', []))
        if not (ids | alternatives | affected) & omitted:
            continue  # Retained groups still need their ordinary identity evidence.
        if (not ids or obs.get('distinction') or row.get('affected_event_ids')
                or (ids | alternatives | affected) - omitted):
            fail('Core scene cannot omit a mixed/core identity group or event dependency')
        covered.append(iid)
    items = world.get('questions_to_verify', []) + world.get('ambiguities', []) + behavior.get('semantic_limitations', [])
    for item in items:
        ids = references(item)
        issues = set(item.get('census_issue_ids', []))
        if not (ids & omitted or issues & set(covered)):
            continue
        if (item.get('limitation_kind') == 'absolute_scale'
                and set(item.get('impact_domains', [])) <= {'speed', 'position'}
                and not issues and not item.get('world_paths') and not item.get('related_world_paths')
                and not item.get('affected_event_ids') and not item.get('scope_binding')):
            continue
        if (ids - omitted or issues - set(covered) or item.get('world_paths')
                or item.get('related_world_paths') or item.get('affected_event_ids')
                or (item.get('scope_binding') or {}).get('affected_event_ids')):
            fail('Core scene omission affects a retained Actor, road or event constraint')
    return dict(retained_actor_ids=sorted(retained), omitted_actor_ids=sorted(omitted),
                covered_issue_ids=sorted(covered))


def make(world, behavior, frames, packet, response, core):
    validate_review_shape(packet, response, behavior)
    planned = plan(world, behavior, frames, packet, core)
    value = dict(revision=REVISION, core_actor_ids=sorted(core),
        world_digest=digest(world), behavior_digest=digest(behavior),
        frames=[dict(frame_id=f['frame_id'], time_s=f['time_s']) for f in frames],
        request_sha256=packet['request_sha256'], candidate_actor_ids=packet['candidate_actor_ids'],
        pending=copy.deepcopy(packet['pending']), review=copy.deepcopy(packet['review']),
        archived_response=copy.deepcopy(response), identity_resolution='unresolved',
        visual_independence='not_proven', handling=HANDLING,
        **planned)
    value['sha256'] = digest(value)
    return value


def validate(world, behavior, frames, value):
    if not isinstance(value, dict): fail('Missing core scene receipt')
    packet = {k: value[k] for k in ('request_sha256', 'candidate_actor_ids', 'pending', 'review', 'frames')}
    expected = make(world, behavior, frames, packet, value['archived_response'], value['core_actor_ids'])
    if value != expected: fail('Core scene receipt or execution inputs changed')
    return set(value['omitted_actor_ids'])
