"""Program-owned non-adoption of late census associations, never identity proof.

An established Actor is retained unchanged. Unused sightings remain unresolved;
this policy neither certifies irrelevance nor fills lanes, motion or events.
"""
import copy
from pathlib import Path

from _lib.contracts.conversion_policy import digest
from _lib.contracts.v3 import ContractError
from _lib.runtime.case_core import atomic_json, read_json, sha256

REVISION = 'unused-association.v1'
KEY = 'unused_association'
HANDLING = 'unadopted_association'
ADOPTION = 'state/unused_association.adoption.json'


def fail(message):
    error = ContractError(message, rule_id='identity.unused_association')
    # These failures concern a program receipt/policy, never missing visual
    # content. Ineligible model answers instead retain the ordinary scope gate.
    error.code = 'internal_pipeline_error'
    error.diagnostics = dict(rule_id=error.rule_id, retry_kind='code_change', message=message)
    raise error


def enabled(case):
    case = Path(case)
    path = case/'state/execution_policy.json'
    policy = read_json(path) if path.is_file() else {}
    if policy.get(KEY) == REVISION:
        return True
    if not (case/ADOPTION).is_file():
        return False
    value = read_json(case/ADOPTION)
    body = {k: v for k, v in value.items() if k != 'sha256'}
    if (value.get('sha256') != digest(body) or value.get('revision') != REVISION
            or not isinstance(value.get('source_hashes'), dict)
            or not {'case.json', 'state/execution_policy.json', 'state/actor_registry.json',
                    'state/world_context.json', 'state/census_adjudication.accepted.json'} <= set(value['source_hashes'])
            or value.get('case_id') != read_json(case/'case.json')['case_id']):
        fail('Unused-association adoption is malformed')
    for name, expected in value['source_hashes'].items():
        if not (case/name).is_file() or sha256(case/name) != expected:
            fail('Unused-association adoption source changed: ' + name)
    return True


def adopt(case):
    """Explicit local opt-in before the first Behavior attempt; never run a task."""
    case = Path(case)
    if enabled(case):
        return read_json(case/ADOPTION) if (case/ADOPTION).exists() else None
    state = read_json(case/'state/progress.json')
    from _lib.runtime.bounded_recovery import require_terminal
    require_terminal(state)
    if (state.get('attempts', {}).get('behavior', 0)
            or any(s.get('stage') == 'behavior' for s in state.get('sessions', []))
            or any((case/name).exists() for name in (
                'state/behavior.json', 'state/behavior.candidate.json',
                'state/behavior_scope_review.accepted.json', 'final/analysis.json'))):
        fail('Unused-association adoption requires an unattempted Behavior stage')
    from _lib.contracts.census_adjudication import proof
    saved = proof(case)
    if not saved or saved['request']['revision'] != 'world-census-adjudication.v6':
        fail('Unused associations require a verified v6 census')
    names = ['case.json', 'state/execution_policy.json', 'state/actor_registry.json',
             'state/world_context.json', 'state/census_adjudication.accepted.json']
    # Execution permission is separate from the immutable visual request. When
    # one was already prepared, adoption explicitly binds it without replacing it.
    if (case/'state/behavior_input.request.json').is_file():
        from _lib.behavior.input_binding import verify
        verify(case)
        names.append('state/behavior_input.request.json')
    body = dict(revision=REVISION, case_id=read_json(case/'case.json')['case_id'],
                source_hashes={name: sha256(case/name) for name in names})
    value = dict(body, sha256=digest(body))
    atomic_json(case/ADOPTION, value)
    return value


def mentions(value, names):
    if isinstance(value, str):
        return any(name in value for name in names)
    if isinstance(value, list):
        return any(mentions(v, names) for v in value)
    if isinstance(value, dict):
        return any(mentions(v, names) for v in value.values())
    return False


def candidates(saved, pending, frames):
    """Source-only eligibility, not permission to generate a target."""
    from _lib.contracts.census_history import rounds
    registry = {a['actor_id']: a for a in saved['result']['registry']}
    bindings = {b['issue_id']: b for r in rounds(saved) for b in r['response']['bindings']}
    result = {}
    for iid, observation in pending.items():
        ids = observation.get('candidate_actor_ids', [])
        if observation.get('distinction') or len(ids) != 1:
            continue
        actor = registry.get(ids[0])
        binding = bindings.get(iid, {})
        if (not actor or actor.get('actor_type') != 'vehicle'
                or binding.get('outcome') != 'unresolved'
                or any(r['actor_id'] == ids[0] for r in binding.get('candidate_refutations', []))):
            continue
        anchors = sorted({e['frame_id'] for e in actor.get('evidence', [])
                          if e.get('status') == 'observed' and e.get('frame_id') in frames})
        fid = observation['frame_id']
        if (len(anchors) < 2 or fid not in frames
                or max(frames[f]['time_s'] for f in anchors) >= frames[fid]['time_s']):
            continue
        result[iid] = dict(issue_id=iid, actor_id=ids[0], observation_frame_id=fid,
                           object_id=observation['object_id'], anchor_frame_ids=anchors,
                           actor_registry_sha256=digest(actor))
    return result


def pure_limitation(item, records):
    """Only the precise observation association may be discharged."""
    ids = item.get('census_issue_ids', [])
    if (not isinstance(ids, list) or not ids or len(ids) != len(set(ids))
            or not set(ids) <= set(records)
            or item.get('impact_domains') != ['actor_identity']
            or item.get('execution_effect') not in {None, 'bounded_uncertainty'}
            or any(item.get(k) for k in ('actor_id', 'affected_actor_ids', 'affected_event_ids',
                'event_id', 'event_ids', 'world_paths', 'related_world_paths', 'identity_binding',
                'scope_binding', 'localization', 'involved_actor_ids'))):
        return False
    return set(item.get('actor_ids', [])) <= {records[i]['actor_id'] for i in ids}


def build(case, behavior, pending, frames, saved, review=None):
    """Derive non-adoption from source anchors and actual submitted references.

    This proves only that the execution input does not adopt these associations,
    not that the objects cannot affect the real scene. No model proof is needed.
    """
    if not enabled(case) or not isinstance(behavior, dict):
        return None
    source = candidates(saved, pending, frames)
    actors = {a['actor_id']: a for a in behavior.get('actors', [])}
    registry = {a['actor_id']: a for a in saved['result']['registry']}
    rows = {r['issue_id']: r for r in (review or {}).get('decisions', [])}
    records = {}
    for iid, record in source.items():
        aid = record['actor_id']; actor = actors.get(aid)
        anchors = set(record['anchor_frame_ids'])
        points = (actor or {}).get('trajectory', [])
        row = rows.get(iid, {})
        assessment = row.get('identity_assessment') or {}
        # A new assertion, alternative or counterevidence is not an omission.
        if (row.get('outcome') not in {None, 'unknown', 'needs_identity'}
                or row.get('affected_event_ids') or row.get('scope_proof')
                or set(row.get('affected_actor_ids', [])) - {aid}
                or assessment.get('candidate_status') == 'rejected'
                or assessment.get('alternative_actor_ids') or assessment.get('counterevidence')):
            continue
        if (not actor or any(actor.get(k) != registry[aid].get(k)
                for k in ('actor_type', 'identity_description'))
                or {p.get('frame_id') for p in points} != anchors
                or any(p.get('time_s') != frames[p['frame_id']]['time_s'] for p in points)):
            continue
        names = {iid, record['object_id']}
        if (mentions(behavior.get('actors', []), names)
                or mentions(behavior.get('challenge_resolutions', []), names | {aid})):
            continue
        # Fidelity/corridor rows and other Actors' point descriptions can also
        # adopt a late association without adding a point to this Actor itself.
        referenced_rows = [*behavior.get('video_fidelity', {}).get('observations', []),
            *(p for other in behavior.get('actors', []) if other['actor_id'] != aid
              for p in other.get('trajectory', []))]
        if any(mentions(row, names) or (mentions(row, {aid}) and row.get('frame_id') not in anchors)
               for row in referenced_rows):
            continue
        actions = behavior.get('events', []) + behavior.get('executor_primitives', [])
        dependent = False
        for action in actions:
            if mentions(action, names):
                dependent = True
            if mentions(action, {aid}):
                # Independently derived initial state is not an identity link.
                if action.get('type') == 'initial_state' and action.get('actor_id') == aid:
                    from _lib.behavior.compile import derive_initial_states
                    expected = derive_initial_states(dict(actors=[actor], executor_primitives=[]))['executor_primitives']
                    if len(expected) == 1 and action == expected[0]:
                        continue
                evidence = [e for e in action.get('evidence', []) if e.get('actor_id') == aid]
                interval = action.get('time_range_s')
                if (not evidence or any(e.get('frame_id') not in anchors for e in evidence)
                        or not isinstance(interval, list) or len(interval) != 2
                        or interval[0] < min(frames[f]['time_s'] for f in anchors)
                        or interval[1] > max(frames[f]['time_s'] for f in anchors)):
                    dependent = True
        if not dependent:
            records[iid] = record
    # Mixed or unbound Actor disputes remain blockers; they cannot be explained
    # away as this optional association. Apply this to the whole candidate group.
    limits = [*saved['result']['world'].get('questions_to_verify', []),
              *saved['result']['world'].get('ambiguities', []),
              *behavior.get('semantic_limitations', [])]
    previous = None
    from _lib.contracts import execution_assumptions
    grouped_observations = execution_assumptions.enabled(case)
    while previous != set(records):
        previous = set(records)
        for item in limits:
            if item.get('resolution') in {'resolved', 'confirmed', 'non_core'}:
                continue
            if pure_limitation(item, records):
                continue
            if grouped_observations and execution_assumptions.pure_observation(item, pending):
                continue
            for iid, record in list(records.items()):
                if (mentions(item, {iid, record['object_id']}) or (
                        'actor_identity' in item.get('impact_domains', [])
                        and mentions(item, {record['actor_id']}))):
                    records.pop(iid)
    if not records:
        return None
    body = dict(revision=REVISION, case_dir=str(Path(case).resolve()),
        certificate_sha256=saved['sha256'], behavior_sha256=digest(behavior),
        world_sha256=digest(saved['result']['world']), registry_sha256=digest(saved['result']['registry']),
        policy_sha256=sha256(Path(case)/'state/execution_policy.json'),
        adoption_sha256=sha256(Path(case)/ADOPTION) if (Path(case)/ADOPTION).exists() else None,
        records=records, review=copy.deepcopy(review),
        actors_sha256=digest(behavior['actors']), events_sha256=digest(behavior.get('events', [])),
        primitives_sha256=digest(behavior.get('executor_primitives', [])))
    return dict(body, sha256=digest(body))


def validate_projection(world, behavior, frames, receipt):
    """Recompute authority, source binding and reference non-use at every consumer."""
    program = (receipt or {}).get(KEY)
    selected = [r for r in (receipt or {}).get('choices', []) if r['handling'] == HANDLING]
    if not program and not selected:
        return None
    if not program or frames is None:
        fail('Unused associations require a complete program record')
    from _lib.contracts import census_adjudication, behavior_census_scope
    case = Path(program['case_dir'])
    saved = census_adjudication.proof(case)
    pending = behavior_census_scope.pending(case, saved)
    expected = build(case, behavior, pending, {f['frame_id']: f for f in frames}, saved, program['review'])
    expected_choices = [dict(issue_id=iid, candidate_actor_ids=[r['actor_id']],
        observation_frame_id=r['observation_frame_id'], outcome='unresolved',
        handling=HANDLING, identity_resolution='unresolved')
        for iid, r in sorted((expected or {}).get('records', {}).items())]
    if (expected is None or program != expected or digest(world) != program['world_sha256']
            or sorted(selected, key=lambda r:r['issue_id']) != expected_choices):
        fail('Unused-association source, policy, observations or actions changed')
    return program


def disposes(item, receipt):
    program = (receipt or {}).get(KEY)
    if not program or not pure_limitation(item, program['records']):
        return False
    choices = {r['issue_id']: r for r in receipt['choices']}
    return all(choices.get(i, {}).get('handling') == HANDLING for i in item['census_issue_ids'])


def preserve(program, behavior, *, opposing_scope=None):
    if not program:
        return
    fields = (('actors', 'actors_sha256'), ('events', 'events_sha256'),
              ('executor_primitives', 'primitives_sha256'))
    if opposing_scope is None:
        expected = {key: program[signature] for key, signature in fields}
    else:
        world, source, groups = opposing_scope[:3]
        inherited = None
        if len(opposing_scope) == 4:
            from _lib.contracts.inherited_opposing import replay
            inherited = replay(opposing_scope[3], world, source)
        if (digest(world) != program.get('world_sha256')
                or digest(source) != program.get('behavior_sha256')
                or any(digest(source.get(key, [])) != program[signature] for key, signature in fields)):
            fail('Opposing preservation source differs from the original program receipt')
        from _lib.runtime.execution_scope import opposing_preservation_ids
        missing = ({a['actor_id'] for a in source['actors']}
                   - {a['actor_id'] for a in behavior['actors']})
        protected = set(program.get('lanes', {})) | {
            row['actor_id'] for row in program.get('records', {}).values()}
        if missing - opposing_preservation_ids(world, source, groups, inherited=inherited) or missing & protected:
            fail('Program projection cannot omit an unproven or required vehicle')
        expected = {key: digest([a for a in source[key] if a['actor_id'] not in missing])
                    if key == 'actors' else program[signature] for key, signature in fields}
    if any(digest(behavior.get(key, [])) != expected[key] for key, _ in fields):
        fail('Unused association cannot remove or change vehicles, trajectories or actions')


def preserve_conversion(analysis, excluded_actor_ids):
    quality = analysis.get('quality_report', {})
    scope = quality.get('execution_actor_scope') or quality.get('offline_execution_actor_scope') or {}
    if any((scope.get('execution_dispositions') or {}).get(k)
           for k in (KEY, 'execution_assumptions', 'peripheral_uncertainty')):
        vehicles = {a['actor_id'] for a in [analysis.get('ego', {}), *analysis.get('actors', [])]
                    if a.get('actor_type') == 'vehicle'}
        if vehicles & set(excluded_actor_ids):
            fail('Unused association cannot authorize conversion to omit confirmed vehicles')
