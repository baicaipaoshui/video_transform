"""Budgeted identity review and recoverable owner commit using existing v3 stages."""
import copy
from pathlib import Path

from _lib.contracts import actor_identity_revision as contract
from _lib.contracts.conversion_policy import digest
from _lib.contracts.v3 import ContractError
from _lib.runtime.case_core import atomic_json, read_json, sha256, now, materialize_pack

ROOT = 'diagnostics/actor_identity_review'
ACCEPTED = 'state/identity_link.accepted.json'
LINEAGE = 'state/identity_link.lineage.json'


def enabled(case, mode='identity_link'):
    path = Path(case)/'state/execution_policy.json'
    if mode == 'identity_split':
        return path.is_file() and read_json(path).get('actor_identity_split') == 'actor-identity-split.v1'
    return path.is_file() and read_json(path).get('actor_identity_review') == contract.REVISION


def _seal(value):
    return dict(value, sha256=digest(value))


def _verified(path):
    value = read_json(path)
    body = dict(value); signature = body.pop('sha256', None)
    if signature != digest(body):
        raise ContractError('Identity revision journal/certificate changed: ' + str(path))
    return body


def _hashes(paths):
    return {str(Path(p).resolve()): sha256(p) for p in paths}


def _verify_hashes(hashes):
    for name, signature in hashes.items():
        if not Path(name).is_file() or sha256(name) != signature:
            raise ContractError('Identity revision input changed: ' + name)


def proof(case):
    path = Path(case)/ACCEPTED
    if not path.is_file():
        if (Path(case)/ROOT/'committed.json').exists():
            raise ContractError('Committed identity certificate is missing')
        return None
    saved = _verified(path)
    packet, response = saved['request'], saved['response']
    result = contract.materialize(packet, response)
    if result is None or digest(result) != saved['result_sha256']:
        raise ContractError('Identity certificate result differs from reviewed candidate')
    for name, key in (('world_context', 'world'), ('actor_registry', 'registry')):
        if read_json(Path(case)/'state'/f'{name}.json') != result[key]:
            raise ContractError('Identity certificate no longer matches World/Registry')
    return saved


def checkpoint_proof(case, *, previous=None):
    """Read-only identity commit/history validation for Batch replay consumers."""
    case = Path(case).resolve(); root = case/ROOT
    prepared, committed = root/'commit.json', root/'committed.json'
    receipt_path = case/'state/reconciliation.json'
    receipt = read_json(receipt_path) if receipt_path.is_file() else {}
    if prepared.is_file() and not committed.is_file():
        raise ContractError('Recover the pending identity commit before replaying Batches',
                            rule_id='identity.commit_pending')
    saved = proof(case)
    if saved is None:
        if (prepared.exists() or committed.exists() or receipt.get('identity_link_request_sha256')
                or receipt.get('status') == 'reconciled_after_identity_review'):
            raise ContractError('Committed identity certificate is missing')
        return None
    if not prepared.is_file() or not committed.is_file():
        raise ContractError('Identity certificate requires a completed commit')
    if read_json(committed).get('journal_sha256') != sha256(prepared):
        raise ContractError('Identity committed journal changed')
    journal = _verified(prepared)
    packet = saved['request']
    if packet.get('case_id') != case.name:
        raise ContractError('Identity checkpoint belongs to another Case')
    for key in ('world_path', 'registry_path', 'behavior_path'):
        path = Path(packet[key]).resolve()
        if not path.is_relative_to(root.resolve()) or str(path) not in packet['input_hashes']:
            raise ContractError('Identity baseline snapshot binding changed')
    if (journal.get('request_sha256') != packet['request_sha256']
            or journal.get('result_sha256') != saved['result_sha256']
            or journal.get('writes', {}).get(ACCEPTED) != read_json(case/ACCEPTED)):
        raise ContractError('Identity certificate differs from committed journal')
    for name, key in (('world_context', 'world_path'), ('actor_registry', 'registry_path')):
        if journal.get('old', {}).get('state/'+name+'.json') != sha256(packet[key]):
            raise ContractError('Identity committed baseline changed: '+name)
    for name in ('state/world_context.json', 'state/actor_registry.json', 'state/reconciliation.json'):
        if journal['writes'].get(name) != read_json(case/name):
            raise ContractError('Identity committed checkpoint changed: '+name)
    manifest = read_json(case/'manifest.json')
    specs = manifest.get('batches', [])
    names = [spec['batch_id']+'.json' for spec in specs]
    paths = {p.name:p for p in (case/'batches').glob('batch_*.json')}
    if len(set(names)) != len(names) or set(names) != set(paths):
        raise ContractError('Identity checkpoint requires the complete frozen Batch chain')
    hashes = {name:sha256(path) for name, path in paths.items()}
    bound = {Path(path).name:signature for path, signature in packet['input_hashes'].items()
             if Path(path).resolve().parent == (case/'batches').resolve()}
    if (bound != hashes or receipt.get('batch_sha256') != hashes
            or packet['input_hashes'].get(str(case/'manifest.json')) != sha256(case/'manifest.json')
            or receipt.get('status') != 'reconciled_after_identity_review'
            or receipt.get('identity_link_request_sha256') != packet['request_sha256']):
        raise ContractError('Identity checkpoint Batch/receipt binding changed')
    baseline = read_json(packet['registry_path'])
    if previous is not None and baseline != previous:
        raise ContractError('Identity baseline Registry differs from last accepted Batch')
    if not specs and baseline != read_json(packet['world_path'])['actor_hypotheses']:
        raise ContractError('Identity baseline Registry differs from zero-Batch World')
    return saved


def supplemental_frames(case):
    saved = proof(case)
    return saved['request']['frames'] if saved else []


def validate_behavior(case, behavior):
    saved = proof(case)
    if saved:
        contract.validate_rebuilt(saved['request'], saved['response'], behavior)


def behavior_prompt(case):
    saved = proof(case)
    if not saved:
        return ''
    if saved['request']['revision'] == 'actor-identity-split.v1':
        return (f'\n身份拆分证书：{Path(case)/ACCEPTED}。读取证书及 behavior_path 原始观察。'
                '旧 Actor 混合了两辆物理车辆；证书 actors 和 sightings 已取代其旧身份描述和混合证据，'
                '不得按旧 Batch/旧 Behavior 描述重新合并。逐车覆盖且仅覆盖证书 sightings 中该车可见的帧，'
                '逐字沿用修订后的 identity_description、actor_type、color、vehicle_class；保留已确认车道。'
                '按照 trajectory_assignments 将原轨迹每个样本分配给正确车辆，保留其中已知位置/速度等数值；'
                '新的 detail 根据对应 sightings.features 重写，描述逐帧位置和运动，不复制被推翻的混合叙述。'
                '其他 Actor、原已验收事件和未解决局限保持不变，只移除 resolved_issue_ids。'
                '没有旧 Behavior 时正常生成其他 Actor 和事件；新观察缺少的米制量保持未知，不能为了超车效果编造。')
    return (f'\n身份复核证书：{Path(case)/ACCEPTED}。读取证书和其中 behavior_path 原始 Behavior。'
            '同一物理车辆的早期关联已确认；按证书图像补充该车早期轨迹和相对位置文字。'
            '旧 Batch 原文件仅作历史证据，其该车 first_seen 和证书明确解决的身份疑问已被此证书取代。'
            '保留所有原轨迹样本、已知车道/数值、其他 Actor、事件和未解决局限；'
            '移除且仅移除 resolved_issue_ids 中的旧 semantic_limitations。'
            '身份确认本身不证明距离和速度，未知数值保持未知，禁止按预期超车效果补造运动。')


def prepare(case, actor_id, *, mode='identity_link'):
    if mode == 'identity_split':
        from _lib.runtime.identity_split import prepare as split
        return split(case, actor_id)
    case = Path(case).resolve(); root = case/ROOT
    request = root/'request.json'
    if request.exists():
        packet = read_json(request); contract.verify_request(packet)
        if packet['actor_id'] != actor_id or packet['revision'] != contract.REVISION:
            raise ContractError('One identity-link review scope per Case revision')
        return packet
    world, registry, behavior = [read_json(case/'state'/name) for name in
                                ('world_context.json', 'actor_registry.json', 'behavior.json')]
    actor = next((a for a in registry if a['actor_id'] == actor_id), None)
    assigned = [i for i in contract.issues(world, behavior) if i['actor_id'] == actor_id]
    if actor is None or actor_id == 'ego' or not assigned or any(
            set(i['impact_domains']) - {'actor_identity', 'actor_motion'} for i in assigned):
        raise ContractError('Identity link needs one existing Actor and an isolated identity issue')
    from _lib.world.actor_facts import frames_for
    frames = sorted(frames_for(case), key=lambda f: f['time_s'])
    index = next((i for i, f in enumerate(frames) if f['frame_id'] == actor['first_seen_frame_id']), None)
    # Inspect the complete early sequence, the old first sighting and two later
    # witnesses. Never silently downsample the transition that is under review.
    if index is None or index == 0 or index >= len(frames)-1 or index+3 > 12:
        raise ContractError('Identity link needs an earlier-to-later sequence within the 12-frame review budget')
    selected = frames[:index+3]
    required = {fid for item in assigned for fid in item['evidence_frame_ids']}
    assigned_ids = {item['issue_id'] for item in assigned}
    for window in world.get('critical_intervals', []):
        if assigned_ids & set(window.get('question_ids', []) + window.get('ambiguity_ids', [])):
            required.update(f['frame_id'] for f in frames if
                            window.get('start_s', window.get('start_time_s', 0)) <= f['time_s'] <=
                            window.get('end_s', window.get('end_time_s', float('inf'))))
    if not required <= {f['frame_id'] for f in selected}:
        raise ContractError('Identity issue needs evidence outside this early-link window')
    sources = [Path(f.get('source_path') or f['path']).resolve() for f in selected]
    for f, source in zip(selected, sources):
        if not source.is_file() or (source == Path(f['path']).resolve() and f.get('sha256')
                                    and sha256(source) != f['sha256']):
            raise ContractError('Identity source frame missing or changed')
    packed = materialize_pack(sources, [(12, 2560, 85), (12, 2560, 70), (12, 1920, 80)],
                              root/'frames', 'identity', protected_paths=sources, max_image_bytes=2*1024*1024)
    rows = [dict(p, frame_id=f['frame_id'], time_s=f['time_s'], source_path=str(source))
            for p, f, source in zip(packed['frames'], selected, sources)]
    snapshots = {}
    for key, name in (('world', 'world_context'), ('registry', 'actor_registry'), ('behavior', 'behavior')):
        path = root/'before'/f'{name}.json'
        atomic_json(path, {'world': world, 'registry': registry, 'behavior': behavior}[key])
        snapshots[key+'_path'] = str(path)
    stable = [case/p for p in ('case.json', 'state/execution_policy.json', 'state/probe.json',
                               'state/global_frames.json', 'manifest.json')]
    stable += list((case/'batches').glob('batch_*.json'))
    stable += [Path(p) for p in snapshots.values()] + sources + [Path(f['path']) for f in rows]
    others = [a['actor_id'] for a in registry if a['actor_id'] != actor_id]
    co_visible = {f['frame_id']: sorted(a['actor_id'] for a in behavior['actors']
                    if a['actor_id'] in others and any(p.get('frame_id') == f['frame_id']
                                                     for p in a.get('trajectory', []))) for f in rows}
    packet = dict(revision=contract.REVISION, case_id=case.name, actor_id=actor_id, actor=actor,
                  other_actor_ids=others, co_visible_actor_ids=co_visible, issues=assigned,
                  required_frame_ids=sorted(required),
                  frames=rows, input_hashes=_hashes(stable), **snapshots)
    packet['request_sha256'] = digest(packet)
    atomic_json(request, packet)
    return packet


def _dependencies(case):
    state = case/'state'
    exact = {'behavior.json', 'behavior.candidate.json', 'behavior_frames.json', 'execution_route.json',
             'execution_plan.json', 'execution_scope_projection.json', 'conversion_result.json',
             'target_checks.json', 'dense_evidence_catalog.json'}
    prefixes = ('behavior_repair', 'behavior_review', 'conversion_semantic_gap', 'preview_', 'intersection_')
    paths = [p for p in state.glob('*.json') if p.name in exact or p.name.startswith(prefixes)]
    paths += [p for p in (state/'conversion_work').rglob('*') if p.is_file()]
    paths += [p for p in (case/'final').rglob('*') if p.is_file()]
    for name in ('preview_relations.json', 'intersection_review.json', 'execution_scope.json'):
        if (case/'diagnostics'/name).is_file():
            paths.append(case/'diagnostics'/name)
    return sorted(set(paths))


def _retire_progress(state):
    diagnostics = state.setdefault('diagnostics', {})
    for name in ('preview_review', 'preview_separation', 'execution_localization', 'execution_verification',
                 'behavior_review', 'lane_review', 'execution_route', 'analysis', 'execution_repairs'):
        diagnostics.pop(name, None)
    state.update(verification_level='not_verified', source_contract_validation='not_passed',
                 render_verification='not_executed')


def stage_commit(case, packet, response):
    case = Path(case); root = case/ROOT
    result = contract.materialize(packet, response)
    if result is None:
        atomic_json(root/'outcome.json', _seal(dict(request=packet, response=response)))
        return False
    for name, key in (('world_context', 'world'), ('actor_registry', 'registry'), ('behavior', 'behavior')):
        if name == 'behavior' and packet.get('had_behavior') is False:
            if (case/'state/behavior.json').exists():
                raise ContractError('Behavior appeared during identity review')
            continue
        if sha256(case/'state'/f'{name}.json') != sha256(packet[key+'_path']):
            raise ContractError('Identity review live baseline changed')
    from _lib.contracts.v3 import validate_world
    from _lib.world.gaps import enabled as observation_contract
    validate_world(result['world'], read_json(case/'state/probe.json')['probe']['duration_s'],
                   observation_contract=observation_contract(case))
    receipt_path = case/'state/reconciliation.json'
    receipt = read_json(receipt_path)
    batches = {p.name: sha256(p) for p in (case/'batches').glob('batch_*.json')}
    if receipt.get('batch_sha256') != batches:
        raise ContractError('Identity baseline Batches differ from reconciliation')
    receipt.update(status='reconciled_after_identity_review', world=result['world'],
                   identity_link_request_sha256=packet['request_sha256'])
    resolved = set(response['resolved_issue_ids'])
    receipt['pending_verification_ids'] = [q for q in receipt.get('pending_verification_ids', []) if q not in resolved]
    # Keep raw Batch files byte-identical. This receipt explicitly supersedes
    # only the old identity binding; it does not re-accept old observations.
    proof_value = _seal(dict(request=packet, response=response, result_sha256=digest(result)))
    writes = {'state/world_context.json': result['world'], 'state/actor_registry.json': result['registry'],
              'state/reconciliation.json': receipt, ACCEPTED: proof_value}
    old = {name: sha256(case/name) if (case/name).exists() else None for name in writes}
    dependencies = {str(p.relative_to(case)): sha256(p) for p in _dependencies(case)}
    atomic_json(root/'before/progress.json', read_json(case/'state/progress.json'))
    journal = dict(writes=writes, old=old, dependencies=dependencies,
                   request_sha256=packet['request_sha256'], result_sha256=digest(result))
    atomic_json(root/'commit.json', _seal(journal))
    return True


def recover(runner, case):
    case = Path(case); root = case/ROOT; path = root/'commit.json'
    if not path.exists():
        return False
    journal = _verified(path)
    if (root/'committed.json').exists():
        if read_json(root/'committed.json')['journal_sha256'] != sha256(path):
            raise ContractError('Identity committed journal changed')
        proof(case)
        return False
    saved = journal['writes'][ACCEPTED]
    result = contract.materialize(saved['request'], saved['response'])
    if digest(result) != journal['result_sha256'] or any(
            result[key] != journal['writes']['state/'+name+'.json']
            for key, name in (('world', 'world_context'), ('registry', 'actor_registry'))):
        raise ContractError('Identity prepared result changed')
    # Validate all destinations before the first mutation, including a restart
    # after either World or Registry was written. Unrelated changes never win.
    for name, value in journal['writes'].items():
        dest = case/name
        if dest.exists():
            if sha256(dest) != journal['old'][name] and read_json(dest) != value:
                raise ContractError('Identity commit destination changed: ' + name)
        elif journal['old'][name] is not None:
            raise ContractError('Identity commit destination disappeared: ' + name)
    for name, signature in journal['dependencies'].items():
        live, archive = case/name, root/'retired'/name
        if live.exists() and sha256(live) != signature:
            raise ContractError('Identity dependent artifact changed: ' + name)
        if archive.exists() and sha256(archive) != signature:
            raise ContractError('Identity retired artifact changed: ' + name)
        if not live.exists() and not archive.exists():
            raise ContractError('Identity dependent artifact disappeared: ' + name)
    from _lib.behavior.input_binding import retire
    retire(case)
    for name in journal['dependencies']:
        live, archive = case/name, root/'retired'/name
        if live.exists():
            archive.parent.mkdir(parents=True, exist_ok=True)
            live.replace(archive)
    for name, value in journal['writes'].items():
        atomic_json(case/name, value)
    state = runner.progress(case)
    # Idempotent progress transition across a crash before the commit marker.
    if state.setdefault('diagnostics', {}).get('identity_link_commit') != journal['request_sha256']:
        _retire_progress(state)
        state['diagnostics']['identity_link_commit'] = journal['request_sha256']
        for stage in ('behavior', 'conversion_relations', 'conversion_semantic_gap', 'preview_review', 'intersection_review'):
            state.setdefault('attempts', {})[stage] = 0
            state.setdefault('retry_epochs', {})[stage] = state.get('retry_epochs', {}).get(stage, 0)+1
        state.update(phase='aggregating', substage='behavior', status='aggregating', error=None,
                     artifacts={}, generation_status='not_generated', updated_at=now())
        runner.save_progress(case, state)
    atomic_json(root/'committed.json', dict(journal_sha256=sha256(path)))
    return True


def run(runner, case, actor_id, *, mode='identity_link'):
    case = Path(case).resolve()
    if runner.progress(case).get('status') in {'done', 'done_degraded'}:
        raise ContractError('Completed identity review requires a derived Case')
    if not enabled(case, mode):
        raise ContractError('Frozen Case has no requested identity review policy; use an explicit derived revision')
    if any(s.get('stage') != 'actor_identity_review_'+actor_id and
           (s.get('status') in {'submitted', 'pending', 'running'} or
            (s.get('error_code') in {'task_deadline_exceeded', 'task_status_unavailable'}
             and not s.get('deadline_task_resolved')))
           for s in runner.progress(case).get('sessions', [])):
        raise ContractError('Reconcile active model tasks before identity review')
    recover(runner, case)
    existing = proof(case)
    if existing:
        expected_revision = 'actor-identity-split.v1' if mode == 'identity_split' else contract.REVISION
        if existing['request']['actor_id'] != actor_id or existing['request']['revision'] != expected_revision:
            raise ContractError('One identity-link review scope per Case revision')
        return existing
    root = case/ROOT; outcome = root/'outcome.json'
    from _lib.world.gaps import IdentityEvidenceGap
    if outcome.exists():
        saved = _verified(outcome); contract.materialize(saved['request'], saved['response'])
        raise IdentityEvidenceGap(saved['request']['issues'])
    packet = prepare(case, actor_id, mode=mode)
    candidate = root/'candidate.json'
    from _lib.prompts import render
    prompt = render('behavior-understanding',
                    'identity-split-prompt.md' if mode == 'identity_split' else 'identity-link-prompt.md',
                    request_path=root/'request.json', candidate_path=candidate)
    def adopt(_session=None):
        from _lib.runtime.orchestrator import candidate_snapshot
        response, _ = candidate_snapshot(case, 'actor_identity_review', candidate)
        if stage_commit(case, packet, response):
            recover(runner, case)
    runner.model_stage(case, 'actor_identity_review_'+actor_id, 'global-video-understanding',
                       prompt, adopt, precheck=adopt if candidate.exists() else None,
                       candidate_path=candidate, max_attempts=1)
    if outcome.exists():
        raise IdentityEvidenceGap(packet['issues'])
    return proof(case)


def validate_lineage(case):
    """Reuse a parent's original observation binding without relabelling it."""
    case = Path(case).resolve(); path = case/LINEAGE
    if not path.exists():
        return False
    lineage = _verified(path)
    parent = Path(lineage['parent_path']).resolve()
    if parent == case or parent.parent != case.parent or (parent/LINEAGE).exists():
        raise ContractError('Identity lineage must reference one original sibling Case')
    _verify_hashes(lineage['input_hashes'])
    from _lib.world.single_world import validate_inputs
    validate_inputs(parent)
    if read_json(parent/'case.json')['source'] != read_json(case/'case.json')['source']:
        raise ContractError('Identity lineage source mismatch')
    return True


def derive(runner, parent, video, actor_id, *, mode='identity_link'):
    """Explicit revision; no writes to the completed/frozen parent directory."""
    from _lib.runtime.case_core import Cases
    parent = Path(parent).resolve()
    if (parent/LINEAGE).exists():
        raise ContractError('Resume the existing identity revision; do not chain revisions')
    original = runner.progress(parent)
    if original.get('status') not in {'done', 'done_degraded'}:
        raise ContractError('Only completed parents can create an identity revision')
    runner.validate_completed(parent, original)
    binding = read_json(parent/'state/binding.json')
    if Path(video).resolve() != Path(binding['source_path']).resolve() or sha256(video) != binding['source_sha256']:
        raise ContractError('source_changed')
    record = read_json(parent/'case.json')
    names = ['state/world_context.json', 'state/actor_registry.json', 'state/behavior.json',
             'state/global_frames.json', 'state/behavior_frames.json', 'state/probe.json',
             'manifest.json', 'state/reconciliation.json']
    names += [str(p.relative_to(parent)) for p in sorted((parent/'batches').glob('batch_*.json'))]
    inputs = _hashes([parent/name for name in names] + [parent/'case.json', parent/'state/execution_policy.json',
                                                       parent/'final/analysis.json', parent/'state/progress.json'])
    revision_key = digest(dict(actor_id=actor_id, parent_inputs=inputs, **({'mode': mode} if mode != 'identity_link' else {})))
    for path in parent.parent.glob('*/'+LINEAGE):
        prior = _verified(path)
        if prior.get('revision_key') == revision_key:
            child = path.parent.parent
            validate_lineage(child)
            return child
    # Fail on unsupported scope before allocating a derived Case.
    world, behavior = read_json(parent/names[0]), read_json(parent/'state/behavior.json')
    from _lib.contracts.core_lane_continuity import validate as validate_motion_obligations
    validate_motion_obligations(parent, behavior)
    if mode == 'identity_split':
        if actor_id == 'ego' or actor_id not in {a['actor_id'] for a in world['actor_hypotheses']}:
            raise ContractError('Split review requires an existing non-Ego Actor')
    elif not any(i['actor_id'] == actor_id for i in contract.issues(world, behavior)):
        raise ContractError('No isolated identity issue for the requested Actor')
    child, _ = Cases(parent.parent).bind(video, record['parameters'], force_new=True)
    for name in names:
        if name == 'state/behavior.json':
            continue
        atomic_json(child/name, read_json(parent/name))
    from _lib.behavior.input_binding import write_behavior
    write_behavior(child, behavior, source_case=parent)
    # Explicit versioned child opts into text motion; original policy/request
    # remains frozen in the parent and is validated through the lineage above.
    policy = read_json(parent/'state/execution_policy.json')
    from _lib.contracts.semantic_motion import REVISION as motion_revision
    policy.update(actor_identity_review=contract.REVISION, conversion_semantic_gap=motion_revision,
                  behavior_input_binding='behavior-input.v1')
    if mode == 'identity_split':
        policy['actor_identity_split'] = 'actor-identity-split.v1'
    atomic_json(child/'state/execution_policy.json', policy)
    immutable = [child/name for name in names if name not in {
        'state/world_context.json', 'state/actor_registry.json', 'state/behavior.json',
        'state/behavior_frames.json', 'state/reconciliation.json'}]
    immutable += [child/'case.json', child/'state/execution_policy.json']
    atomic_json(child/LINEAGE, _seal(dict(parent_path=str(parent), actor_id=actor_id,
                revision_key=revision_key, input_hashes=inputs | _hashes(immutable))))
    state = runner.progress(child)
    # Preserve task history and consumed budget; a child is not a budget reset.
    for key in ('sessions', 'attempts', 'retry_epochs', 'completed_batches', 'diagnostics'):
        if key in original:
            state[key] = copy.deepcopy(original[key])
    _retire_progress(state)
    state.update(status='needs_review', phase='failed', artifacts={}, generation_status='not_generated',
                 error=dict(code='identity_evidence_insufficient', message='Explicit identity revision',
                            resume_from='aggregating'))
    state.setdefault('diagnostics', {})['identity_revision_parent'] = parent.name
    runner.save_progress(child, state)
    return child
