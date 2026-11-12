"""Evidence-only Behavior patches with durable, monotonically shrinking scope."""
import copy
from pathlib import Path

from _lib.contracts.conversion_policy import digest
from _lib.contracts.v3 import ContractError
from _lib.runtime.case_core import atomic_json, now, read_json, sha256
from _lib.runtime.bounded_recovery import RecoveryStopped, enabled

REVISION = 'bounded-behavior-repair.v1'
REQUEST = 'state/behavior_repair.request.json'


def normalize_index(case, behavior, review, frames):
    """Derive a redundant index only when the complete original proof validates."""
    if not enabled(case) or not isinstance(review, dict):
        return review, []
    from _lib.contracts import census_adjudication, behavior_census_scope
    saved = census_adjudication.proof(case)
    pending = behavior_census_scope.pending(case, saved)
    mapped = {f['frame_id']:f for f in frames}
    result = copy.deepcopy(review); repairs = []
    for row in result.get('decisions', []):
        if not isinstance(row, dict) or row.get('issue_id') not in pending:
            continue
        proof = row.get('scope_proof')
        sights = proof.get('sightings') if isinstance(proof, dict) else None
        old = row.get('evidence_frame_ids')
        if (row.get('outcome') != 'irrelevant' or not isinstance(sights, list)
                or not all(isinstance(s, dict) and isinstance(s.get('frame_id'), str) for s in sights)
                or not isinstance(old, list) or not all(isinstance(fid, str) for fid in old)):
            continue
        ids = [s['frame_id'] for s in sights]
        if (len(ids) != len(set(ids)) or set(ids) != set(mapped)
                or len(old) != len(set(old)) or not set(old) < set(ids)
                or pending[row['issue_id']]['frame_id'] not in old):
            continue
        trial = dict(row, evidence_frame_ids=ids)
        try:
            behavior_census_scope.validate_decision(case, behavior, trial, pending[row['issue_id']],
                mapped, world=saved['result']['world'], registry=saved['result']['registry'])
        except ContractError:
            continue  # Leave invalid proof untouched so the original gate reports it.
        row['evidence_frame_ids'] = ids
        repairs.append(dict(rule_id='scope.index_from_validated_sightings', issue_id=row['issue_id'],
                            before=old, after=ids, proof_sha256=digest(proof)))
    return result, repairs


def restore_protected_review(case, raw):
    """Keep previously passed scope decisions when replaying a full archive."""
    from _lib.contracts import census_scope
    from _lib.runtime.census_scope_recovery import KEY, trigger
    record = read_json(Path(case)/'state/progress.json').get('diagnostics', {}).get(KEY, {})
    if record.get('status') != 'adjudicated' or not isinstance(raw, dict):
        return raw, []
    value = trigger(record['trigger_path'])
    behavior, review = census_scope.split(raw)
    if review is None:
        return raw, []
    protected = {row['issue_id']:row for row in value['review']['decisions']
                 if row.get('outcome') in {'irrelevant', 'excluded', 'confirmed', 'distinct'}
                 and row['issue_id'] not in value['issue_ids']}
    if not protected:
        return raw, []
    passed, _, _ = state_of(case, behavior, dict(review, decisions=list(protected.values())))
    result = copy.deepcopy(raw); repairs = []
    for index, row in enumerate(result['census_scope_review']['decisions']):
        old = protected.get(row['issue_id'])
        if old is not None and old != row and passed.get(row['issue_id']) == old:
            result['census_scope_review']['decisions'][index] = copy.deepcopy(old)
            repairs.append(dict(rule_id='scope.preserve_validated_decision', issue_id=row['issue_id'],
                                archived_sha256=digest(row), retained_sha256=digest(old)))
    return result, repairs


def request(case):
    path = Path(case)/REQUEST
    if not path.is_file(): return None
    value = read_json(path)
    if value.get('repair_contract') != REVISION: return None
    body = dict(value); signature = body.pop('request_sha256', None)
    if signature != digest(body):
        raise RecoveryStopped('bounded_recovery_input_changed')
    for name, expected in value['input_hashes'].items():
        if not Path(name).is_file() or sha256(name) != expected:
            raise RecoveryStopped('bounded_recovery_input_changed', path=name)
    from _lib.contracts import census_scope
    census_scope.verify_request(case, value)
    if (not Path(value['base_path']).resolve().is_relative_to((Path(case)/'diagnostics').resolve())
            or sha256(value['base_path']) != value['base_sha256']):
        raise RecoveryStopped('bounded_recovery_input_changed')
    if value.get('mode') == 'full_evidence_review':
        from _lib.runtime.census_scope_recovery import repair_scope
        if value.get('repair_scope') != repair_scope(case):
            raise RecoveryStopped('bounded_recovery_input_changed')
    return value


def prepare_full(case, candidate, scope):
    """Freeze a full frame-evidence review before spending its Behavior task."""
    case = Path(case).resolve()
    if (case/REQUEST).is_file():
        raise RecoveryStopped('bounded_recovery_input_changed')
    from _lib.runtime.census_scope_recovery import accepted_fact_projection, repair_scope, trigger
    if scope.get('mode') != 'full_evidence_review' or scope != repair_scope(case):
        raise RecoveryStopped('bounded_recovery_input_changed')
    from _lib.runtime.bounded_recovery import require_content_slots
    require_content_slots(case, read_json(case/'state/progress.json'), ['behavior'])
    from _lib.behavior import input_binding
    binding = input_binding.closure_request(case)
    if binding is None:
        raise RecoveryStopped('bounded_recovery_input_changed')
    from _lib.contracts import census_scope
    value = trigger(scope['source_trigger_path'])
    data, fact_projection = accepted_fact_projection(case)
    from _lib.runtime.fact_versions import ROOT as FACT_ROOT, current_facts
    world = (current_facts(case).world if (case/FACT_ROOT/'active.json').is_file()
             else read_json(case/'state/world_context.json'))
    old_epoch = value['behavior']['world_evidence_epoch']
    data['world_evidence_epoch'] = world['evidence_epoch']
    context = census_scope.context(case)
    current_ids = {row['issue_id'] for row in context['observations']}
    original_rows = [copy.deepcopy(row) for row in value['review']['decisions']
                     if row['issue_id'] in current_ids]
    if len({row['issue_id'] for row in original_rows}) != len(original_rows):
        raise RecoveryStopped('bounded_recovery_input_changed')
    original_review = dict(certificate_sha256=context['certificate_sha256'], decisions=original_rows)
    passed, _, _ = state_of(case, data, original_review)
    protected = {iid:row for iid,row in passed.items() if iid in current_ids}
    assigned = sorted(current_ids - set(protected))
    paths = []
    refresh = set(scope['refresh_actor_ids'])
    for i, actor in enumerate(data['actors']):
        if actor['actor_id'] in refresh or ('execution_scope_evidence' not in actor and actor['actor_id'] != 'ego'):
            paths.append(f'/actors/{i}/execution_scope_evidence')
    for i, item in enumerate(data.get('semantic_limitations', [])):
        if not set(item.get('actor_ids', [])) & set(scope['affected_actor_ids']):
            continue
        for key in ('census_issue_ids', 'scope_binding'):
            if key not in item:
                paths.append(f'/semantic_limitations/{i}/{key}')
    base = dict(data, census_scope_review=original_review)
    base_path = case/'diagnostics/bounded_recovery'/f'{digest(base)}.json'
    if base_path.is_file():
        if read_json(base_path) != base:
            raise RecoveryStopped('bounded_recovery_input_changed')
    else:
        atomic_json(base_path, base)
    packet = dict(repair_contract=REVISION, mode='full_evidence_review',
        repair_scope=scope, refresh_actor_ids=sorted(refresh),
        accepted_fact_projection=fact_projection,
        old_evidence_epoch=old_epoch, current_evidence_epoch=data['world_evidence_epoch'],
        old_scope_evidence_hashes={actor['actor_id']:digest(actor['execution_scope_evidence'])
            for actor in value['behavior']['actors'] if actor['actor_id'] in refresh},
        base_path=str(base_path), base_sha256=sha256(base_path),
        output_path=str(candidate), created_at=now(), group='behavior',
        blocked=['scope:'+iid for iid in assigned] + ['scope_evidence:'+aid for aid in sorted(refresh)],
        previous_request_sha256=None, protected_decisions=protected,
        issue_ids=assigned, allowed_paths=paths, lane_actor_ids=[],
        lane_closure_obligations=[],
        behavior_request_sha256=binding['request_sha256'],
        fact_version_id=binding.get('fact_version_id'),
        input_hashes=dict(binding['input_hashes']),
        output_contract=dict(base_sha256=sha256(base_path), replacements=[],
            census_scope_review=dict(certificate_sha256=context['certificate_sha256'],
                decisions='ONLY assigned issue_ids, exactly once'), lane_closures=[]))
    for source in (base_path, scope['source_trigger_path'], scope['old_frame_source']):
        packet['input_hashes'][str(source)] = sha256(source)
    census_scope.bind_request(case, packet)
    packet['request_sha256'] = digest(packet)
    atomic_json(case/REQUEST, packet)
    return prompt(case, packet)


def mark_failure(case, data, review, error):
    """Only semantic scope failures get this evidence-only repair authority."""
    if not enabled(case) or not isinstance(review, dict): return
    from _lib.contracts.census_scope import ScopeReviewBlocked
    from _lib.world.gaps import IdentityEvidenceGap
    rule = getattr(error, 'rule_id', '') or ''
    if not isinstance(error, (ScopeReviewBlocked, IdentityEvidenceGap)) and rule not in {
            'scope.single_frame_relevance','scope.transient_evidence','behavior.handoff.identity_dependencies'}:
        return
    recovery = read_json(Path(case)/'state/progress.json').get('diagnostics', {}).get('census_scope_recovery', {})
    if recovery.get('status') == 'requested':
        return  # Explicit identity conflicts go through the existing World owner first.
    error.code = 'behavior_bounded_repairable'
    error.bounded_behavior = copy.deepcopy(data)
    error.bounded_review = copy.deepcopy(review)


def state_of(case, data, review):
    if (not isinstance(review,dict) or not isinstance(review.get('decisions'),list)
            or any(not isinstance(r,dict) or not isinstance(r.get('issue_id'),str) for r in review['decisions'])
            or len({r['issue_id'] for r in review['decisions']}) != len(review['decisions'])):
        raise RecoveryStopped('bounded_recovery_requires_scoped_repair')
    from _lib.behavior.compile import evidence_frames
    from _lib.contracts import census_adjudication, behavior_census_scope, execution_gaps
    saved = census_adjudication.proof(case)
    pending = behavior_census_scope.pending(case, saved)
    from _lib.contracts.census_scope import active, validate_confirmation_row
    claims = active(saved)
    frames = {f['frame_id']:f for f in evidence_frames(case)}
    passed = {}; blocked = set()
    for row in review['decisions']:
        iid = row['issue_id']
        if iid not in pending:
            # Only affirmative existing exclusions can be frozen. Unknown or
            # challenged rows remain repair targets, never implicitly accepted.
            try:
                checked = iid in claims and validate_confirmation_row(case, data, row, claims[iid], frames)
            except ContractError:
                checked = False
            if checked:
                passed[iid] = row
            else:
                blocked.add('scope:'+iid)
            continue
        try:
            checked = behavior_census_scope.validate_decision(case, data, row, pending[iid], frames,
                world=saved['result']['world'], registry=saved['result']['registry'])
            if checked: passed[iid] = row
            else: blocked.add('scope:'+iid)
        except ContractError:
            blocked.add('scope:'+iid)
    blocked.update('scope:'+iid for iid in (set(pending)|set(claims))-{r['issue_id'] for r in review['decisions']})
    issues = execution_gaps.diagnostics(case, saved['result']['world'], data, review['decisions'])
    for item in issues:
        if item['rule_id'] != 'identity.census.scope_pending':
            blocked.add(item['rule_id']+':'+str(item.get('issue_id') or item.get('actor_id')))
    return passed, sorted(blocked), frames


def require_actionable(case, *, data, allowed_paths, issue_ids, blocked,
                       lane_obligations, failure):
    """Stop a bounded task whose authority cannot touch its recorded failure."""
    details = (getattr(failure, 'diagnostics', {}) if not isinstance(failure, dict)
               else failure)
    actor_paths = {row.get('actor_id'): f'/actors/{index}/'
                   for index, row in enumerate(data['actors'])}
    limitation_paths = {row.get('limitation_id'): f'/semantic_limitations/{index}/'
                        for index, row in enumerate(data.get('semantic_limitations', []))}
    allowed = set(allowed_paths)
    unresolved = []
    for blocker in blocked:
        rule, _, target = blocker.partition(':')
        actionable = rule == 'scope' and target in issue_ids
        if rule == 'scope_evidence':
            prefix = actor_paths.get(target)
            actionable = bool(prefix and prefix+'execution_scope_evidence' in allowed)
        if target in actor_paths and any(path.startswith(actor_paths[target]) for path in allowed):
            actionable = True
        # Limitation failures need explicit resolution/deletion authority. Adding
        # a Census ID or scope prose cannot close identity/interaction semantics.
        if target in limitation_paths:
            prefix = limitation_paths[target]
            actionable = any(path in {prefix+'resolution', prefix.rstrip('/')}
                             for path in allowed)
        if not actionable:
            unresolved.append(blocker)
    if unresolved:
        raise RecoveryStopped('bounded_recovery_contract_blocked',
            required_paths=[], allowed_paths=sorted(allowed_paths),
            remaining_issues=blocked, issue_ids=issue_ids,
            lane_obligations=lane_obligations,
            unactionable_issues=unresolved,
            primary_failure=details)


def prepare(case, candidate, error):
    if not enabled(case) or not hasattr(error, 'bounded_behavior'): return None
    from _lib.contracts import census_scope, lane_resolution
    from _lib.behavior import input_binding
    case = Path(case)
    invalid, review = error.bounded_behavior, error.bounded_review
    from _lib.runtime.census_scope_recovery import accepted_fact_projection
    data, fact_projection = accepted_fact_projection(case)
    if data is None:
        data = copy.deepcopy(invalid); fact_projection = None
    passed, blocked, frames = state_of(case, data, review)
    previous = request(case)
    trusted = dict(data, census_scope_review=review)
    if not blocked and previous is None:
        # Accepted facts can fully repair a stale candidate without another
        # model task. Run the same gates before publishing only the candidate;
        # the normal stage adopt path performs final commit and archival.
        from _lib.runtime.census_scope_recovery import validate_patch
        from _lib.contracts.census_adjudication import validate_behavior
        validate_patch(case, data)
        preserve_rebuild(case, data, review)
        validate_behavior(case, data)
        census_scope.validate_review(case, data, review)
        candidate = Path(candidate).resolve()
        if not candidate.is_relative_to(case.resolve()):
            raise RecoveryStopped('bounded_recovery_input_changed')
        audit = dict(revision='bounded-accepted-fact-projection.v1',
                     source_invalid_candidate_sha256=digest(invalid),
                     projected_candidate_sha256=digest(trusted),
                     accepted_fact_projection=fact_projection)
        atomic_json(case/'diagnostics/bounded_recovery'/f'projection.{digest(audit)}.json', audit)
        atomic_json(candidate, trusted)
        return None
    if previous and previous.get('mode') == 'full_evidence_review':
        raise RecoveryStopped('bounded_recovery_no_progress',
                              primary_failure=getattr(error, 'diagnostics', {}))
    if previous and read_json(previous['base_path']) == trusted:
        # New prose/IDs cannot reset the group. No validated reduction -> stop.
        if not set(blocked) < set(previous['blocked']):
            raise RecoveryStopped('bounded_recovery_no_progress', remaining_issues=blocked,
                                  primary_failure=getattr(error, 'diagnostics', {}))
    from _lib.runtime.routing import unobserved_actor_lanes
    world = read_json(case/'state/world_context.json')
    lane_ids = {r['actor_id'] for r in unobserved_actor_lanes(world, data)}
    paths = []
    for i, actor in enumerate(data['actors']):
        if actor['actor_id'] in lane_ids:
            for j, point in enumerate(actor['trajectory']):
                for key in ('lane_id','road_id','ordinal_from_right'):
                    if point.get(key) is None: paths.append(f'/actors/{i}/trajectory/{j}/{key}')
                paths.append(f'/actors/{i}/trajectory/{j}/detail')
        if 'execution_scope_evidence' not in actor and actor['actor_id'] != 'ego':
            paths.append(f'/actors/{i}/execution_scope_evidence')
    projected_actors = {row['actor_id'] for row in
                        (fact_projection or {}).get('added_sightings', [])}
    for i, item in enumerate(data.get('semantic_limitations', [])):
        # Mixed identity/motion/interaction limitations need new semantic
        # evidence and cannot gain authority from bookkeeping fields alone.
        if (set(item.get('impact_domains', [])) <= {'actor_identity', 'actor_lane'}
                and set(item.get('actor_ids', [])) & projected_actors
                and 'census_issue_ids' not in item):
            paths.append(f'/semantic_limitations/{i}/census_issue_ids')
    from _lib.world.actor_facts import strict_enabled
    binding = input_binding.closure_request(case)
    strict = strict_enabled(case)
    authorization = (binding.get('question_authorization', []) if binding else []
                     ) if strict else (binding.get('question_authorization') if binding else None)
    lane_obligations = (lane_resolution.obligations(world, data, lane_ids, authorization)
                        if binding or not strict else [])
    issue_ids = sorted(x[6:] for x in blocked if x.startswith('scope:'))
    require_actionable(case, data=data, allowed_paths=paths, issue_ids=issue_ids,
                       blocked=blocked, lane_obligations=lane_obligations, failure=error)
    base = trusted
    base_path = case/'diagnostics/bounded_recovery'/f'{digest(base)}.json'
    if not base_path.exists(): atomic_json(base_path, base)
    elif read_json(base_path) != base: raise RecoveryStopped('bounded_recovery_input_changed')
    packet = dict(repair_contract=REVISION, base_path=str(base_path), base_sha256=sha256(base_path),
        output_path=str(candidate), created_at=now(), blocked=blocked,
        group='behavior', previous_request_sha256=previous['request_sha256'] if previous else None,
        protected_decisions=passed, issue_ids=issue_ids,
        accepted_fact_projection=fact_projection,
        allowed_paths=paths, lane_actor_ids=sorted(lane_ids),
        lane_closure_obligations=lane_obligations,
        **({'question_authorization': authorization} if authorization is not None else {}),
        **({'behavior_request_sha256': binding['request_sha256'],
            'fact_version_id': binding.get('fact_version_id')} if binding else {}),
        input_hashes=dict((binding or {}).get('input_hashes', {})),
        output_contract=dict(base_sha256=sha256(base_path), replacements=[],
            census_scope_review={'certificate_sha256':review['certificate_sha256'],
                                'decisions':'ONLY assigned issue_ids, exactly once'}, lane_closures=[]))
    packet['input_hashes'][str(base_path)] = sha256(base_path)
    census_scope.bind_request(case, packet)
    packet['request_sha256'] = digest(packet)
    if previous:
        atomic_json(case/'diagnostics/bounded_recovery'/f"request.{previous['request_sha256']}.json", previous)
    atomic_json(case/REQUEST, packet)
    return prompt(case, packet)


def prompt(case, packet=None):
    packet = packet or request(case)
    if packet is None: return None
    from _lib.contracts import census_scope
    selected = copy.deepcopy(packet['scope_context'])
    selected['observations'] = [r for r in selected['observations'] if r['issue_id'] in packet['issue_ids']]
    if packet.get('mode') == 'full_evidence_review':
        return ('Skill: behavior-understanding\n完整证据重审；只读任务包 '+str(Path(case)/REQUEST)+
            '、base_path原始Behavior、frames中当前全部原图。只写output_contract到output_path，一次后停止。'
            'replacements仅可修改allowed_paths中列出的范围证据字段；逐一核对当前全部冻结帧，'
            '不得根据旧checked_frame_ids补造新帧的无交互结论。'
            'census_scope_review.decisions只回答issue_ids；protected_decisions由Python原样继承。'
            '不改身份、轨迹、车道、角色、事件、限制项Actor关联和描述。证据不足时保留unknown或needs_identity。'
            + census_scope.prompt(case, frozen_context=selected, frames=packet['frames']))
    return ('Skill: behavior-understanding\n有限修复；只读任务包 '+str(Path(case)/REQUEST)+
        '、base_path完整基线与frames证据。只写 output_contract 到 output_path，一次后停止。'
        'census_scope_review.decisions 只回答 issue_ids；其他通过项由Python原样继承，不能重答。'
        'replacements 只可修改 allowed_paths；可为空。只补未知车道/范围证据，'
        '不改身份、轨迹样本、时间、位置、速度、已知车道、角色、事件或限制域。'
        '车道修改必须同时给同一点detail，包含frame_id和车道边界证据；不确定保留null。'
        'lane_closures仅回答给定obligations；身份无法确认保留unknown/needs_identity，不能猜测。'
        '路径/执行依赖不明须保留，不得为消除错误伪造无关性。'
        + census_scope.prompt(case, frozen_context=selected, frames=packet['frames']))


def materialize(case, raw):
    packet = request(case)
    if packet is None: return raw
    from _lib.behavior import input_binding
    from _lib.world.actor_facts import strict_enabled
    if isinstance(raw, dict) and raw.get('lane_closures'):
        binding = input_binding.closure_request(case)
        if strict_enabled(case) and (binding is None or
                packet.get('behavior_request_sha256') != binding['request_sha256'] or
                packet.get('question_authorization') != binding.get('question_authorization', [])):
            raise ContractError('model_out_of_scope: lane closure lacks frozen Behavior authority',
                                rule_id='model_out_of_scope')
        if packet.get('behavior_request_sha256') and (binding is None or
                packet['behavior_request_sha256'] != binding['request_sha256']):
            raise ContractError('stale_version: repair and Behavior requests differ',
                                rule_id='stale_version')
    if (not isinstance(raw, dict) or set(raw) != {'base_sha256','replacements','census_scope_review','lane_closures'}
            or raw['base_sha256'] != packet['base_sha256']):
        raise RecoveryStopped('bounded_recovery_patch_out_of_scope')
    review = raw['census_scope_review']
    if (not isinstance(review, dict) or set(review) != {'certificate_sha256','decisions'}
            or review['certificate_sha256'] != packet['scope_context']['certificate_sha256']
            or not isinstance(review['decisions'], list)
            or any(not isinstance(r,dict) or not isinstance(r.get('issue_id'),str) for r in review['decisions'])
            or len(review['decisions']) != len(packet['issue_ids'])
            or {r['issue_id'] for r in review['decisions']} != set(packet['issue_ids'])):
        raise RecoveryStopped('bounded_recovery_patch_out_of_scope')
    from _lib.contracts import census_scope, lane_resolution
    base, original_review = census_scope.split(read_json(packet['base_path']))
    data = copy.deepcopy(base)
    ops = raw['replacements']
    if not isinstance(ops,list) or len(ops) > len(packet['allowed_paths']):
        raise RecoveryStopped('bounded_recovery_patch_out_of_scope')
    submitted = set()
    for op in ops:
        if (not isinstance(op,dict) or set(op) != {'path','value'}
                or not isinstance(op['path'],str)
                or op['path'] not in packet['allowed_paths'] or op['path'] in submitted):
            raise RecoveryStopped('bounded_recovery_patch_out_of_scope')
        submitted.add(op['path']); parts = op['path'][1:].split('/')
        node = data
        for part in parts[:-1]: node = node[int(part)] if isinstance(node,list) else node[part]
        node[parts[-1]] = copy.deepcopy(op['value'])
    lanes = {l['lane_id'] for l in read_json(Path(case)/'state/world_context.json')['world']['road']['lanes']}
    for i, actor in enumerate(data['actors']):
        for j, point in enumerate(actor['trajectory']):
            prefix = f'/actors/{i}/trajectory/{j}/'
            if any(p.startswith(prefix) for p in submitted):
                if (prefix+'detail' not in submitted or not isinstance(point.get('detail'),str)
                        or point['frame_id'] not in point['detail']
                        or point.get('lane_id') is not None and point['lane_id'] not in lanes):
                    raise RecoveryStopped('bounded_recovery_patch_out_of_scope')
    from _lib.runtime.fact_versions import ROOT as FACT_ROOT, current_facts
    world = current_facts(case).world if ('question_authorization' in packet and
        (Path(case)/FACT_ROOT/'active.json').is_file()) else read_json(
        Path(case)/'state/world_context.json')
    data = lane_resolution.apply(world, base, data, raw['lane_closures'],
        set(packet['lane_actor_ids']), authorization=packet.get('question_authorization'),
        frozen_obligations=packet['lane_closure_obligations'])
    incoming = {r['issue_id']:r for r in review['decisions']}
    decisions = [incoming.pop(r['issue_id'], r) for r in original_review['decisions']]
    decisions.extend(incoming.values())
    data['census_scope_review'] = dict(certificate_sha256=review['certificate_sha256'], decisions=decisions)
    if (data == dict(base, census_scope_review=data['census_scope_review'])
            and {r['issue_id']:r for r in decisions} ==
                {r['issue_id']:r for r in original_review['decisions']}):
        raise RecoveryStopped('bounded_recovery_no_progress', reason='patch_changes_no_validated_state')
    return data


def finish(case):
    packet = request(case)
    if packet:
        accepted = packet
        if packet.get('mode') == 'full_evidence_review':
            target = Path(case)/'state/behavior.json'
            candidate = Path(packet['output_path'])
            behavior = read_json(target)
            accepted = dict(packet, accepted_behavior_sha256=sha256(target),
                candidate_sha256=sha256(candidate) if candidate.is_file() else None,
                new_scope_evidence_hashes={actor['actor_id']:digest(actor['execution_scope_evidence'])
                    for actor in behavior['actors'] if actor['actor_id'] in packet['refresh_actor_ids']})
        atomic_json(Path(case)/'diagnostics/bounded_recovery'/f"accepted.{packet['request_sha256']}.json", accepted)
        (Path(case)/REQUEST).unlink()


def retire_after_archive(case, source):
    """Archive an unused patch request after a complete answer passes all gates."""
    packet = request(case)
    if packet is None:
        return
    source = Path(source).resolve()
    if not source.is_file() or not source.is_relative_to(Path(case).resolve()):
        raise RecoveryStopped('bounded_recovery_input_changed')
    record = dict(request=packet, replay_source_path=str(source),
                  replay_source_sha256=sha256(source), reason='complete_behavior_revalidated')
    path = Path(case)/'diagnostics/bounded_recovery'/f"retired.{packet['request_sha256']}.json"
    if path.is_file() and read_json(path) != record:
        raise RecoveryStopped('bounded_recovery_input_changed')
    if not path.is_file():
        atomic_json(path, record)
    (Path(case)/REQUEST).unlink()


def preserve_rebuild(case, data, review):
    """Identity-owned rebuilds retain original motion and passed scope answers."""
    if not enabled(case): return
    state = read_json(Path(case)/'state/progress.json')
    record = state.get('diagnostics', {}).get('census_scope_recovery', {})
    if record.get('status') != 'adjudicated': return
    from _lib.runtime.census_scope_recovery import accepted_fact_projection, trigger
    value = trigger(record['trigger_path'])
    projected, _ = accepted_fact_projection(case)
    current = {a['actor_id']:a for a in data['actors']}
    packet = request(case)
    allowed = set(packet.get('allowed_paths', [])) if packet else set()
    lane_actor_ids = set(value.get('lane_actor_ids', []))
    for actor_index, actor in enumerate(projected['actors']):
        updated = current.get(actor['actor_id'])
        if updated is None or len(updated['trajectory']) != len(actor['trajectory']):
            raise RecoveryStopped('bounded_recovery_patch_out_of_scope')
        for point_index, (old, new) in enumerate(zip(actor['trajectory'], updated['trajectory'])):
            prefix = f'/actors/{actor_index}/trajectory/{point_index}/'
            changed = {key for key in set(old)|set(new) if old.get(key) != new.get(key)}
            local_allowed = set(allowed)
            if actor['actor_id'] in lane_actor_ids and old.get('lane_id') is None:
                local_allowed.update(prefix+key for key in
                                     ('lane_id', 'road_id', 'ordinal_from_right', 'detail'))
            if any(prefix+key not in local_allowed for key in changed):
                raise RecoveryStopped('bounded_recovery_patch_out_of_scope')
        old_meta = {key:value for key,value in actor.items() if key != 'trajectory'}
        new_meta = {key:value for key,value in updated.items() if key != 'trajectory'}
        changed = {key for key in set(old_meta)|set(new_meta)
                   if old_meta.get(key) != new_meta.get(key)}
        local_allowed = set(allowed)
        if ('execution_scope_evidence' not in old_meta
                and 'execution_scope_evidence' in new_meta):
            local_allowed.add(f'/actors/{actor_index}/execution_scope_evidence')
        if any(f'/actors/{actor_index}/{key}' not in local_allowed for key in changed):
            raise RecoveryStopped('bounded_recovery_patch_out_of_scope')
    rows = {r['issue_id']:r for r in (review or {}).get('decisions', [])}
    if packet and packet.get('mode') == 'full_evidence_review':
        for iid, row in packet['protected_decisions'].items():
            if rows.get(iid) != row:
                raise RecoveryStopped('bounded_recovery_patch_out_of_scope')
        return
    for row in value['review']['decisions']:
        if row.get('outcome') in {'irrelevant','excluded','confirmed','distinct'}:
            if rows.get(row['issue_id']) != row:
                raise RecoveryStopped('bounded_recovery_patch_out_of_scope')
