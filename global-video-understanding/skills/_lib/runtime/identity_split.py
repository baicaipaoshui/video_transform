"""Bound a split review to existing evidence; reuse the identity commit journal."""
from pathlib import Path

from _lib.contracts.actor_identity_split import REVISION
from _lib.contracts.conversion_policy import digest
from _lib.contracts.v3 import ContractError
from _lib.runtime.case_core import atomic_json, read_json, materialize_pack, sha256


def prepare(case, actor_id):
    from _lib.runtime.identity_revision import ROOT, _hashes
    from _lib.contracts.actor_identity_revision import issues, verify_request
    from _lib.contracts.vehicle_census import issues as census_issues
    from _lib.world.actor_facts import frames_for
    case = Path(case).resolve(); root = case/ROOT; request = root/'request.json'
    if request.exists():
        packet = read_json(request); verify_request(packet)
        if packet['revision'] != REVISION or packet['actor_id'] != actor_id:
            raise ContractError('One identity review scope and mode per Case revision')
        return packet
    world, registry = [read_json(case/'state'/n) for n in ('world_context.json', 'actor_registry.json')]
    had_behavior = (case/'state/behavior.json').is_file()
    behavior = read_json(case/'state/behavior.json') if had_behavior else dict(actors=[], events=[], semantic_limitations=[])
    actor = next((a for a in registry if a['actor_id'] == actor_id), None)
    if actor is None or actor_id == 'ego' or actor_id not in {a['actor_id'] for a in world['actor_hypotheses']}:
        raise ContractError('Identity split requires an existing non-Ego World Actor')
    supported = {'actor_id', 'actor_type', 'identity_description', 'color', 'vehicle_class',
                 'first_seen_frame_id', 'first_seen_time_s', 'last_seen_frame_id', 'last_seen_time_s',
                 'hypothesized_lane', 'hypothesized_lane_reason', 'evidence', 'confidence'}
    for prior in [actor] + [a for a in world['actor_hypotheses'] if a['actor_id'] == actor_id]:
        if set(prior) - supported:
            raise ContractError('Split has additional Actor facts requiring owner review: ' +
                                ', '.join(sorted(set(prior) - supported)))
    if any(actor_id in e.get('involved_actor_ids', []) for e in behavior.get('events', [])):
        raise ContractError('Identity split involving accepted events requires event-owner review')
    assigned = [i for i in issues(world, behavior) + census_issues(case) if actor_id in i['actor_ids']]
    if any(i['actor_ids'] != [actor_id] or set(i['impact_domains']) - {'actor_identity', 'actor_motion'} for i in assigned):
        raise ContractError('Identity split requires one isolated Actor scope')
    frames = sorted(frames_for(case), key=lambda f: f['time_s'])
    by_id = {f['frame_id']: f for f in frames}
    required = {actor['first_seen_frame_id']}
    for item in assigned:
        required.update(item['evidence_frame_ids'])
    old_actor = next((a for a in behavior.get('actors', []) if a['actor_id'] == actor_id), {})
    required.update(p.get('frame_id') for p in old_actor.get('trajectory', []))
    for a in [actor] + [a for a in world['actor_hypotheses'] if a['actor_id'] == actor_id]:
        for evidence in a.get('evidence', []):
            if evidence.get('frame_id'):
                required.add(evidence['frame_id'])
            elif type(evidence.get('time_s')) in (int, float):
                match = [f['frame_id'] for f in frames if f['time_s'] == evidence['time_s']]
                if len(match) != 1:
                    raise ContractError('Split evidence time does not identify one decoded frame')
                required.update(match)
    assigned_ids = {i['issue_id'] for i in assigned}
    for window in world.get('critical_intervals', []):
        if assigned_ids & set(window.get('question_ids', []) + window.get('ambiguity_ids', [])):
            required.update(f['frame_id'] for f in frames if window['start_s'] <= f['time_s'] <= window['end_s'])
    if not required <= set(by_id):
        raise ContractError('Split baseline references unavailable source frames')
    # Cover the complete opening transition plus all old identity witnesses.
    # No extra decoding and no silent thinning when the scope exceeds budget.
    required.update(f['frame_id'] for f in frames[:4])
    selected = [f for f in frames if f['frame_id'] in required]
    if not 3 <= len(selected) <= 12:
        raise ContractError('Identity split needs 3..12 source frames; wider scope needs separate review')
    sources = [Path(f.get('source_path') or f['path']).resolve() for f in selected]
    for f, source in zip(selected, sources):
        if not source.is_file() or (source == Path(f['path']).resolve() and f.get('sha256') and sha256(source) != f['sha256']):
            raise ContractError('Split source frame missing or changed')
    packed = materialize_pack(sources, [(12, 2560, 85), (12, 2560, 70), (12, 1920, 80)],
                              root/'frames', 'identity', protected_paths=sources, max_image_bytes=2*1024*1024)
    if len(packed['frames']) != len(selected):
        raise ContractError('Split packing omitted required evidence')
    rows = [dict(p, frame_id=f['frame_id'], time_s=f['time_s'], source_path=str(source))
            for p, f, source in zip(packed['frames'], selected, sources)]
    snapshots = {}
    for key, value in (('world', world), ('registry', registry), ('behavior', behavior)):
        path = root/'before'/f'{key}.json'; atomic_json(path, value); snapshots[key+'_path'] = str(path)
    assigned = assigned or [dict(rule_id='identity.split.requested', actor_id=actor_id,
        actor_ids=[actor_id], issue_id='explicit_split:'+actor_id, evidence_frame_ids=sorted(required),
        impact_domains=['actor_identity'], message='Explicit review request; a split is not pre-confirmed')]
    existing = {a['actor_id'] for a in registry + world['actor_hypotheses']}
    index = 1
    while f'actor_{index:03d}' in existing:
        index += 1
    others = sorted(existing - {actor_id, 'ego'})
    co_visible = {f['frame_id']: sorted(a['actor_id'] for a in behavior.get('actors', [])
        if a['actor_id'] in others and any(p.get('frame_id') == f['frame_id'] for p in a.get('trajectory', []))) for f in rows}
    stable = [case/p for p in ('case.json', 'state/execution_policy.json', 'state/probe.json',
                               'state/global_frames.json', 'manifest.json')]
    stable += list((case/'batches').glob('batch_*.json'))
    stable += [Path(p) for p in snapshots.values()] + sources + [Path(f['path']) for f in rows]
    packet = dict(revision=REVISION, case_id=case.name, actor_id=actor_id, actor=actor,
        new_actor_id=f'actor_{index:03d}', other_actor_ids=others, co_visible_actor_ids=co_visible,
        had_behavior=had_behavior, issues=assigned, frames=rows, required_frame_ids=sorted(required),
        input_hashes=_hashes(stable), **snapshots)
    packet['request_sha256'] = digest(packet)
    atomic_json(request, packet)
    return packet
