"""Scoped post-Behavior census recovery using the existing task and commit owners."""
import copy
import math
from pathlib import Path

from _lib.contracts import census_adjudication as contract, census_history as history, census_scope as scope
from _lib.contracts.conversion_policy import digest
from _lib.contracts.v3 import ContractError
from _lib.runtime.case_core import atomic_json, read_json, sha256, now

KEY = 'census_scope_recovery'


def initial_adjudication(runner, case):
    """v4 only: unresolved census may need the existing World owner without layout repair."""
    case = Path(case)
    policy = case/'state/execution_policy.json'
    if not policy.is_file() or read_json(policy).get('world_census_adjudication') not in contract.SCOPE_REVISIONS:
        return False
    from _lib.contracts.vehicle_census import issues
    if not issues(case) or contract.proof(case):
        return False  # Partial accepted rounds still use their existing explicit continuation.
    from _lib.runtime import world_adjudication as runtime
    runtime.run(runner, case)
    return True


def _seal(value):
    return dict(value, sha256=digest(value))


def begin(runner, case, error, *, producer_stage='behavior', producer_session=None):
    """Save the whole valid Behavior before the task runner quarantines its candidate."""
    case = Path(case).resolve()
    if not hasattr(error, 'scope_review'):
        return  # Malformed or missing answers cannot authorize scope revocation.
    from _lib.contracts.execution_gaps import enabled
    previous = runner.progress(case).get('diagnostics', {}).get(KEY)
    if enabled(case) and previous and previous['status'] in {'adjudicated', 'completed'}:
        # One bounded census recovery round; repeated uncertainty is not new evidence.
        return
    saved = contract.proof(case)
    if saved['request']['revision'] not in contract.SCOPE_REVISIONS:
        raise ContractError('Scope recovery requires the frozen v4 contract')
    from _lib.runtime.fact_versions import census_path
    raw = read_json(census_path(case))
    archive = case/history.ARCHIVE/(raw['sha256']+'.json')
    if archive.is_file() and read_json(archive) != raw:
        raise ContractError('Scope recovery source archive changed')
    atomic_json(archive, raw)
    state = runner.progress(case)
    producer = next((s for s in reversed(state.get('sessions', []))
                     if s.get('stage') == producer_stage and
                     (producer_session is None or s.get('session_id') == producer_session)), None)
    if producer_session is not None and producer is None:
        raise ContractError('Scope recovery producer session is not recorded')
    value = dict(certificate=dict(path=str(archive), sha256=sha256(archive)),
        certificate_sha256=saved['sha256'], behavior=error.behavior, review=error.scope_review,
        issue_ids=sorted(error.diagnostics['issue_ids']),
        producer={k:producer.get(k) for k in ('task_id','session_id')} if producer else None)
    if enabled(case):
        from _lib.runtime.routing import unobserved_actor_lanes
        value['lane_actor_ids'] = sorted(r['actor_id'] for r in
            unobserved_actor_lanes(saved['result']['world'], error.behavior))
    if getattr(error, 'pending_census', False):
        value['pending_census'] = True
        from _lib.behavior.compile import evidence_frames
        value['frames'] = evidence_frames(case)
        if getattr(error, 'deferred_issue_ids', None):
            value['deferred_issue_ids'] = sorted(error.deferred_issue_ids)
    value = _seal(value)
    root = case/'diagnostics/census_scope_recovery'/value['sha256']
    trigger = root/'trigger.json'
    if trigger.is_file() and read_json(trigger) != value:
        raise ContractError('Immutable scope recovery trigger changed')
    atomic_json(trigger, value)
    prior = state.setdefault('diagnostics', {}).get(KEY)
    if prior and prior.get('trigger_sha256') != value['sha256'] and prior['status'] != 'completed':
        raise ContractError('Reconcile prior scope recovery before replacing it')
    if prior and prior.get('trigger_sha256') == value['sha256']:
        return
    state['diagnostics'][KEY] = dict(status='requested', trigger_path=str(trigger),
        trigger_sha256=value['sha256'], root=str(root), issue_ids=value['issue_ids'])
    runner.save_progress(case, state)


def trigger(path):
    value = read_json(path); body = dict(value); signature = body.pop('sha256', None)
    if signature != digest(body):
        raise ContractError('Scope recovery trigger changed')
    return value


def verify_trigger(packet, prior):
    link = packet.get('scope_recovery')
    if packet['revision'] not in contract.SCOPE_REVISIONS or not link:
        contract.fail('Scope revocation requires a bound v4 Behavior conflict')
    path = Path(link['path']).resolve(); case = Path(packet['case_dir']).resolve()
    if (not path.is_relative_to(case/'diagnostics/census_scope_recovery')
            or not path.is_file() or sha256(path) != link['sha256']
            or packet['input_hashes'].get(str(path)) != link['sha256']):
        contract.fail('Scope recovery trigger lost its input binding')
    value = trigger(path)
    source = next((i for i in history.rounds(prior) if i['sha256'] == value['certificate_sha256']), None)
    if source is None:
        contract.fail('Scope recovery source certificate is outside this lineage')
    original = Path(value['certificate']['path'])
    if (not original.is_file() or sha256(original) != value['certificate']['sha256']
            or read_json(original)['sha256'] != source['sha256']):
        contract.fail('Scope recovery source certificate changed')
    chain = history.rounds(prior)
    source = dict(source, rounds=chain[:chain.index(source)+1])
    if 'lane_actor_ids' in value:
        from _lib.contracts.execution_gaps import enabled
        from _lib.runtime.routing import unobserved_actor_lanes
        if not enabled(case) or value['lane_actor_ids'] != sorted(r['actor_id'] for r in
                unobserved_actor_lanes(source['result']['world'], value['behavior'])):
            contract.fail('Scope recovery lane obligations differ from the original observations')
    from _lib.world.gaps import IdentityEvidenceGap
    try:
        scope.validate_review(case, value['behavior'], value['review'], saved=source,
                              frames=value['frames'] if value.get('pending_census') else source['request']['frames'],
                              historical=True)
    except IdentityEvidenceGap as error:
        if (not value.get('pending_census') or not getattr(error, 'pending_census', False)
                or sorted(error.diagnostics['issue_ids']) != value['issue_ids']
                or packet.get('scope_revocations')):
            contract.fail('Pending census recovery lacks a complete needs-identity review')
        if sorted(getattr(error, 'deferred_issue_ids', [])) != value.get('deferred_issue_ids', []):
            contract.fail('Pending census recovery changed its deferred observations')
    except scope.ScopeReviewBlocked as error:
        if not hasattr(error, 'scope_review') or sorted(error.diagnostics['issue_ids']) != value['issue_ids']:
            contract.fail('Scope revocation lacks a valid explicit Behavior challenge')
    else:
        contract.fail('Confirmed scope review cannot revoke an observation')
    if packet.get('scope_revocations') and packet['scope_revocations'] != value['issue_ids']:
        contract.fail('Scope recovery cannot reopen unrelated observations')


def selected_records(records, link):
    """Only a fully validated mixed review may narrow this recovery's authority."""
    if not link:
        return records
    value = trigger(link['path'])
    if not value.get('deferred_issue_ids'):
        return records
    ids = set(value['issue_ids'])
    if not ids or not ids <= {r['issue_id'] for r in records}:
        raise ContractError('Pending census recovery selection is no longer unresolved')
    return [r for r in records if r['issue_id'] in ids]


def alternative_actors(case, link):
    from _lib.contracts.behavior_handoff import enabled
    if not link or not enabled(case): return []
    value = trigger(link['path'])
    # Existing structured affected IDs also schedule comparison opportunities.
    # They do not authorize a binding; the World evidence contract still does.
    return sorted({aid for row in value['review']['decisions'] if row['issue_id'] in value['issue_ids']
                   for aid in set(row['affected_actor_ids']) |
                   set((row.get('identity_assessment') or {}).get('alternative_actor_ids', []))
                   if aid != 'ego'})


def _archive_dependencies(runner, case, record):
    from _lib.runtime.identity_revision import _dependencies, _retire_progress
    from _lib.behavior import input_binding
    root = Path(record['root'])
    if 'dependencies' not in record:
        paths = _dependencies(case)
        paths += [p for p in (case/scope.REVIEW,) if p.is_file()]
        record['dependencies'] = {str(p.relative_to(case)):sha256(p) for p in paths}
        state = runner.progress(case); state['diagnostics'][KEY] = record; runner.save_progress(case, state)
    for name, signature in record['dependencies'].items():
        live, archive = case/name, root/'retired'/name
        if ((live.exists() and sha256(live) != signature)
                or (archive.exists() and sha256(archive) != signature)
                or not live.exists() and not archive.exists()):
            raise ContractError('Scope recovery dependent artifact changed: ' + name)
    input_binding.retire(case)
    for name in record['dependencies']:
        live, archive = case/name, root/'retired'/name
        if live.exists():
            archive.parent.mkdir(parents=True, exist_ok=True); live.replace(archive)
    state = runner.progress(case)
    _retire_progress(state)
    state.update(phase='aggregating', status='aggregating', error=None,
        artifacts={}, generation_status='not_generated', updated_at=now())
    runner.save_progress(case, state)


def _parent(case):
    from _lib.runtime.fact_versions import census_path
    raw = read_json(census_path(case))
    archive = case/history.ARCHIVE/(raw['sha256']+'.json')
    if archive.is_file() and read_json(archive) != raw:
        raise ContractError('Scope recovery parent archive changed')
    atomic_json(archive, raw)
    return dict(path=str(archive.resolve()), sha256=sha256(archive))


def _commit_revocation(runner, case, packet, response):
    from _lib.runtime import world_adjudication as runtime
    result = runtime.compile_response(packet, response)
    receipt = read_json(case/'state/reconciliation.json')
    receipt.update(world=result['world'], census_adjudication=packet['request_sha256'],
        remaining_census_issue_ids=result['remaining_issue_ids'])
    journal = dict(world=result['world'], registry=result['registry'], receipt=receipt,
        old_world_sha256=sha256(case/'state/world_context.json'),
        old_registry_sha256=sha256(case/'state/actor_registry.json'),
        input_hashes=packet['input_hashes'] | {str(Path(packet['output_path']).parent/'request.json'):
            sha256(Path(packet['output_path']).parent/'request.json')},
        census_certificate=runtime.certificate(packet, response, result))
    from _lib.runtime.fact_versions import ROOT as FACT_ROOT, current_facts
    if (case/FACT_ROOT/'active.json').is_file():
        parent = current_facts(case)
        journal.update(fact_parent_version=parent.version_id,
                       fact_parent_manifest_sha256=sha256(parent.directory/'manifest.json'),
                       old_world_sha256=sha256(parent.world_path),
                       old_registry_sha256=sha256(parent.registry_path))
    journal['sha256'] = digest(journal)
    old = case/'state/lane_layout.commit.json'
    if old.is_file():
        previous = read_json(old)
        if previous.get('status') != 'committed':
            raise ContractError('Recover existing commit before scope revocation')
        atomic_json(case/runtime.ROOT/'previous_commits'/f'{sha256(old)}.json', previous)
    atomic_json(old, journal)
    if not runner.commit_lane_layout(case, receipt['batch_sha256']):
        raise ContractError('Scope revocation commit is incomplete')


def recover(runner, case):
    case = Path(case).resolve(); state = runner.progress(case)
    record = state.get('diagnostics', {}).get(KEY)
    if not record or record['status'] in {'adjudicated','completed'}:
        return False
    value = trigger(record['trigger_path'])
    producer = value.get('producer')
    if producer:
        session = next((s for s in state.get('sessions', []) if s.get('task_id') == producer['task_id']), None)
        if session is None or session.get('session_id') != producer['session_id']:
            raise ContractError('Scope recovery Behavior producer binding changed')
        if (session.get('status') in {'submitted','pending','running'} or
                session.get('recovered_from_poll_failure') and not session.get('deadline_task_resolved')):
            remote = runner.backend.poll(producer['task_id'], 'global-video-understanding')
            if remote.get('status') not in {'completed','failed'} or remote.get('session_id') not in {None,producer['session_id']}:
                raise ContractError('Scope recovery Behavior producer is not terminal')
            session.update(status='invalid_output', error_code=scope.ScopeReviewBlocked.code, deadline_task_resolved=True)
            runner.save_progress(case, state)
    if state.get('diagnostics', {}).get('unresolved_submissions') or any(
            s.get('status') in {'submitted','pending','running'} or
            (s.get('error_code') in {'task_deadline_exceeded','task_status_unavailable'}
             or s.get('recovered_from_poll_failure')) and not s.get('deadline_task_resolved')
            for s in state.get('sessions', [])):
        # Existing world task recovery must reconcile its own producer first.
        if record['status'] == 'submitted':
            from _lib.runtime import world_adjudication as runtime
            runtime.resume(runner, case)
        else:
            raise ContractError('Reconcile remote tasks before scope recovery')
    if value.get('pending_census') and record['status'] == 'requested':
        from _lib.contracts.execution_gaps import enabled
        # A World adjudication requires a subsequent Behavior rebuild. Do not
        # retire dependencies if the existing budget cannot cover both tasks.
        required = 2 if enabled(case) else 1
        from _lib.runtime.bounded_recovery import require_content_slots
        require_content_slots(case, state, ['world_closure','behavior'] if required == 2 else ['world_closure'])
        from _lib.runtime.bounded_recovery import remaining
        available = remaining(case, state)
        if available is None:
            available = state.get('diagnostics', {}).get('model_recovery_budget', {}).get('remaining', 2)
        if available < required:
            raise ContractError('model_recovery_budget_exhausted')
    root = Path(record['root'])
    link = dict(path=record['trigger_path'], sha256=sha256(record['trigger_path']))
    _archive_dependencies(runner, case, record)
    from _lib.runtime import world_adjudication as runtime
    journal = case/'state/lane_layout.commit.json'
    if journal.is_file() and read_json(journal).get('status') != 'committed':
        saved_journal = read_json(journal)
        if saved_journal.get('census_certificate', {}).get('request', {}).get('scope_recovery') != link:
            raise ContractError('Scope recovery found an unrelated incomplete commit')
        if not runner.commit_lane_layout(case, {p.name:sha256(p) for p in (case/'batches').glob('batch_*.json')}):
            raise ContractError('Scope recovery journal is incomplete')
    saved = contract.proof(case)
    already_adjudicated = (saved['request'].get('scope_recovery') == link
                          and not saved['request'].get('scope_revocations'))
    if not already_adjudicated and set(value['issue_ids']) & set(scope.active(saved)):
        packet = runtime.prepare(case, root=root/'revocation', revision=saved['request']['revision'],
            parent_certificate=_parent(case), scope_revocations=value['issue_ids'], scope_recovery=link)
        response = dict(revision=packet['revision'], request_sha256=packet['request_sha256'], layout=None,
            vehicles=[], identity_groups=[], bindings=[dict(issue_id=r['issue_id'], outcome='unresolved',
                vehicle_key=None, reason='Revoked by bound Behavior scope review',
                evidence_frame_ids=[r['frame_id']], separation=None, candidate_refutations=[], scope_claim=None)
                for r in packet['records']])
        if packet['revision'] == contract.RELEVANCE_REVISION:
            from _lib.contracts.census_relevance import subjects
            response['relevance'] = [dict(issue_id=iid, assessment='unknown',
                reason='Revoked by evidence-bound Behavior conflict; no scope authorization',
                evidence_frame_ids=[row['frame_id']]) for iid,row in subjects(packet).items()]
        _commit_revocation(runner, case, packet, response)
    saved = contract.proof(case)
    # A restart after the model's atomic commit must not authorize another task.
    if saved['request'].get('scope_recovery') == link and not saved['request'].get('scope_revocations'):
        record.update(status='adjudicated', certificate_sha256=saved['sha256'])
    else:
        packet = runtime.prepare(case, root=root/'adjudication', revision=saved['request']['revision'],
            parent_certificate=_parent(case), scope_recovery=link)
        runtime.authorize_scope_recovery(runner, case, packet, record)
        record['status'] = 'submitted'
        state = runner.progress(case); state['diagnostics'][KEY] = record; runner.save_progress(case, state)
        runtime.run(runner, case, packet=packet)
        saved = contract.proof(case)
        record.update(status='adjudicated', certificate_sha256=saved['sha256'])
    state = runner.progress(case); state['diagnostics'][KEY] = record; runner.save_progress(case, state)
    return True


def repair_prompt(case):
    record = read_json(Path(case)/'state/progress.json').get('diagnostics', {}).get(KEY)
    if not record or record['status'] != 'adjudicated':
        return ''
    value = trigger(record['trigger_path'])
    lane_note = ('\n同次重建复核以下全未知车道 Actor：' + ','.join(value['lane_actor_ids']) +
        '。仅允许其原轨迹点的 road_id/ordinal_from_right/lane_id/detail 修订；'
        '保持身份、样本数、时间、速度、事件不变。仍无法确认时保留 null。'
        '若同一 Actor 已属于本轮正式身份裁定的修复范围，则按该身份修复权限处理。'
        '相关限制和 World 车道疑问只通过 lane_closures 回答，不直接删改。'
        if value.get('lane_actor_ids') else '')
    from _lib.contracts.behavior_handoff import enabled as handoff_enabled
    scope_note = ('\n本轮还可为未提供 execution_scope_evidence 的 Actor 补充该证据字段；'
                  '只补完整逐帧范围证据，不改变其他 Actor 的身份、角色、车道、轨迹或事件。'
                  if handoff_enabled(case) else '')
    from _lib.runtime.bounded_recovery import enabled as bounded_enabled
    preservation = ('\n有限修复额外约束：保留基线全部已有车辆轨迹的样本、帧/时间、位置、速度和已知车道。'
        '仅可为原未知车道补充lane_id/road_id/ordinal_from_right/detail；不追加或拼接旧车轨迹。'
        'review中已通过的irrelevant/excluded/confirmed/distinct条目逐字保留，其他条目按当前凭证复查。'
        if bounded_enabled(case) else '')
    return (preservation + lane_note + scope_note + '\n这是 census 范围撤销后的受限 Behavior 恢复。完整已保存基线在 '
        + record['trigger_path'] + ' 的 behavior；保留所有不相关内容。仅允许修正复查声明的'
        ' affected_actor_ids/affected_event_ids、补充本轮 census 对相关 issue 正式绑定的车辆，'
        '及这些对象明确关联的事件、动作和局限；不得更改其他对象、重写身份或删掉已登记车辆。'
        '更新 evidence_epoch 为当前 World。仍输出完整 candidate 及当前要求的范围复查。'
        '若相关影响范围无法确认，保持证据不足，不为放行修改原事实。')


def _affected_entities(value, record, saved):
    decisions = [r for r in value['review']['decisions'] if r['issue_id'] in value['issue_ids']]
    actors = {aid for row in decisions for aid in row['affected_actor_ids']}
    events = {eid for row in decisions for eid in row['affected_event_ids']}
    for actor_ids in _accepted_issue_actors(value, record, saved).values():
        actors.update(actor_ids)
    return actors, events


def _accepted_issue_actors(value, record, saved):
    """Return only issue-to-Actor links committed by this recovery's certificate."""
    selected = set(value['issue_ids'])
    result = {issue_id: set() for issue_id in selected}
    for item in history.rounds(saved):
        if item['request'].get('scope_recovery', {}).get('path') != record['trigger_path']:
            continue
        for binding in item['response']['bindings']:
            if binding['issue_id'] in selected and binding['outcome'] in {'existing', 'new'}:
                result[binding['issue_id']].add(item['result']['vehicle_ids'][binding['vehicle_key']])
    return result


def _accepted_fact_projection(base, world, claims, issue_actors, certificate_sha256):
    """Apply only certificate-backed Census facts to an execution copy.

    The source Behavior remains immutable. A trajectory point is admitted only
    when paired identity/lane claims establish an earlier sighting of the same
    Actor. The point has no position or speed, so appearance evidence cannot be
    promoted to a motion conclusion. Pure identity limitations are retired only
    when every cited issue has a committed Actor binding; mixed limitations are
    retained unchanged.
    """
    data = copy.deepcopy(base)
    actor_rows = data.get('actors', [])
    actors = {row.get('actor_id'): row for row in actor_rows}
    actor_indexes = {row.get('actor_id'): index for index, row in enumerate(actor_rows)}
    original_first_times = {actor_id: min(
        point.get('time_s', point.get('timestamp_s', float('inf')))
        for point in actor.get('trajectory', []))
        for actor_id, actor in actors.items() if actor.get('trajectory')}
    lanes = {row.get('lane_id'): row for row in
             world.get('world', {}).get('road', {}).get('lanes', [])}
    accepted_certificates = ({certificate_sha256} if isinstance(certificate_sha256, str)
                             else set(certificate_sha256))
    if not accepted_certificates:
        raise ScopeReviewBlocked('Accepted Census projection has no source certificate')
    selected = set(issue_actors)
    grouped = {}
    issue_identity_proofs = {}
    for claim in claims.values():
        if (not isinstance(claim, dict)
                or claim.get('source_certificate_sha256') not in accepted_certificates
                or claim.get('field') not in {'actor_identity', 'actor_lane'}):
            continue
        source_issues = set(claim.get('source_issue_ids', [])) & selected
        actor_id = claim.get('actor_id')
        if (not source_issues or actor_id not in actors
                or any(actor_id not in issue_actors.get(iid, set()) for iid in source_issues)):
            continue
        if claim['field'] == 'actor_identity' and claim.get('value') == actor_id:
            for issue_id in source_issues:
                issue_identity_proofs.setdefault(issue_id, set()).add(
                    (claim['source_certificate_sha256'], claim['claim_id'], actor_id))
        key = (claim.get('source_certificate_sha256'), claim.get('observation_anchor_id'), actor_id,
               claim.get('frame_id'), claim.get('time_s'))
        grouped.setdefault(key, {})[claim['field']] = claim

    additions = []
    for (source_certificate, anchor, actor_id, frame_id, time_s), pair in sorted(
            grouped.items(), key=lambda row: (str(row[0][2]), float(row[0][4]))):
        identity, lane_claim = pair.get('actor_identity'), pair.get('actor_lane')
        actor = actors[actor_id]
        trajectory = actor.get('trajectory', [])
        if (not identity or not lane_claim or identity.get('value') != actor_id
                or not isinstance(frame_id, str) or type(time_s) not in (int, float)
                or not trajectory or any(p.get('frame_id') == frame_id for p in trajectory)
                or time_s >= original_first_times[actor_id]):
            continue
        lane = lanes.get(lane_claim.get('value'))
        if lane is None:
            raise ScopeReviewBlocked('Accepted Census sighting has no current World lane')
        source_issues = sorted(set(identity.get('source_issue_ids', []))
                               | set(lane_claim.get('source_issue_ids', [])))
        if (not source_issues or any(actor_id not in issue_actors.get(iid, set())
                                     for iid in source_issues)):
            raise ScopeReviewBlocked('Accepted Census sighting lost its issue binding')
        point = dict(frame_id=frame_id, time_s=time_s, lane_id=lane['lane_id'],
                     road_id=lane['road_id'], ordinal_from_right=lane['ordinal_from_right'],
                     detail='Census accepted appearance at '+frame_id+
                            '; identity and lane provenance are bound in census_evidence.',
                     census_evidence=dict(observation_anchor_id=anchor,
                         source_certificate_sha256=source_certificate,
                         source_issue_ids=source_issues,
                         actor_identity_claim_id=identity['claim_id'],
                         actor_lane_claim_id=lane_claim['claim_id']))
        actor['trajectory'].append(point)
        actor['trajectory'].sort(key=lambda row: (row.get('time_s', row.get('timestamp_s')), row['frame_id']))
        actor['first_seen_frame_id'] = actor['trajectory'][0]['frame_id']
        additions.append(dict(actor_id=actor_id, frame_id=frame_id, time_s=time_s,
                              source_certificate_sha256=source_certificate,
                              source_issue_ids=source_issues,
                              claim_ids=[identity['claim_id'], lane_claim['claim_id']]))
    for addition in additions:
        actor = actors[addition['actor_id']]
        point_index = next(index for index, point in enumerate(actor['trajectory'])
            if point.get('frame_id') == addition['frame_id']
            and point.get('time_s') == addition['time_s']
            and point.get('census_evidence', {}).get('source_certificate_sha256') ==
                addition['source_certificate_sha256'])
        addition['path'] = f"/actors/{actor_indexes[addition['actor_id']]}/trajectory/{point_index}"

    retired = []
    retained = []
    for index, item in enumerate(data.get('semantic_limitations', [])):
        issues = set(item.get('census_issue_ids', []))
        actor_ids = set(item.get('actor_ids', []))
        domains = set(item.get('impact_domains', []))
        resolved_identity = (bool(issues) and bool(actor_ids)
            and issues <= selected and domains == {'actor_identity'}
            and item.get('execution_effect') == 'bounded_uncertainty'
            and not any(key in item for key in ('scope_binding', 'event_ids',
                                                 'affected_event_ids', 'world_paths'))
            and all(actor_ids <= issue_actors.get(iid, set()) for iid in issues)
            and all({proof[2] for proof in issue_identity_proofs.get(iid, set())} >= actor_ids
                    for iid in issues))
        if resolved_identity:
            retired.append(dict(limitation_id=item.get('limitation_id'), original_index=index,
                source_issue_ids=sorted(issues), actor_ids=sorted(actor_ids),
                source_proofs={iid: [dict(source_certificate_sha256=cert, claim_id=claim,
                                           actor_id=actor_id)
                                      for cert, claim, actor_id in sorted(issue_identity_proofs[iid])]
                               for iid in sorted(issues)},
                original_sha256=digest(item)))
        else:
            retained.append(item)
    data['semantic_limitations'] = retained
    if 'evidence_epoch' in world:
        data['world_evidence_epoch'] = world['evidence_epoch']
    manifest = dict(revision='accepted-census-behavior-projection.v1',
                    source_behavior_sha256=digest(base),
                    source_certificate_sha256s=sorted(accepted_certificates),
                    added_sightings=additions, retired_limitations=retired)
    return data, manifest


def accepted_fact_projection(case):
    """Build the trusted post-Census Behavior base without mutating the Case."""
    case = Path(case)
    record = read_json(case/'state/progress.json').get('diagnostics', {}).get(KEY)
    if not record or record.get('status') != 'adjudicated':
        return None, None
    value = trigger(record['trigger_path'])
    saved = contract.proof(case)
    if record.get('certificate_sha256') != saved['sha256']:
        raise ScopeReviewBlocked('Accepted Census certificate changed before Behavior projection')
    from _lib.runtime.fact_versions import current_facts
    facts = current_facts(case)
    issue_actors = _accepted_issue_actors(value, record, saved)
    accepted_certificates = {item['sha256'] for item in history.rounds(saved)
        if item['request'].get('scope_recovery', {}).get('path') == record['trigger_path']}
    data, manifest = _accepted_fact_projection(value['behavior'], facts.world, facts.claims,
        issue_actors, accepted_certificates)
    manifest['accepted_certificate_sha256'] = record['certificate_sha256']
    manifest['fact_version_id'] = facts.version_id
    manifest['source_trigger_path'] = str(Path(record['trigger_path']).resolve())
    manifest['source_trigger_sha256'] = sha256(record['trigger_path'])
    return data, manifest


def repair_scope(case):
    """Choose the next Behavior repair authority from verified old/new evidence.

    This is a read-only preflight. A submitted repair request keeps its own
    frozen authority and must never be reclassified on restart.
    """
    case = Path(case).resolve()
    record = read_json(case/'state/progress.json').get('diagnostics', {}).get(KEY)
    if not record or record['status'] != 'adjudicated':
        return None
    value = trigger(record['trigger_path'])
    from _lib.behavior.compile import evidence_frames
    from _lib.contracts.behavior_handoff import enabled as handoff_enabled
    saved = contract.proof(case)
    rounds = history.rounds(saved)
    source_index = next((i for i, row in enumerate(rounds)
                         if row['sha256'] == value['certificate_sha256']), None)
    if source_index is None or record.get('certificate_sha256') != saved['sha256']:
        raise ContractError('Scope repair source certificate is stale')
    if value.get('pending_census'):
        old_frames = value['frames']
        old_source = Path(record['trigger_path'])
    else:
        old_source = Path(record['root'])/'retired/state/behavior_frames.json'
        expected = record.get('dependencies', {}).get('state/behavior_frames.json')
        if not expected or not old_source.is_file() or sha256(old_source) != expected:
            raise ContractError('Scope repair old frame index is unavailable or changed')
        old_frames = read_json(old_source)['frames']
    current_frames = evidence_frames(case)
    current_index = case/'state/behavior_frames.json'
    if current_index.is_file() and read_json(current_index) != {'frames': current_frames}:
        raise ContractError('Scope repair current frame index differs from accepted evidence')

    def index(rows):
        result = {}
        for frame in rows:
            fid = frame.get('frame_id')
            if (not isinstance(fid, str) or not fid or fid in result
                    or type(frame.get('time_s')) not in (int, float)
                    or not math.isfinite(frame['time_s']) or frame['time_s'] < 0
                    or not isinstance(frame.get('variants', []), list)):
                raise ContractError('Scope repair frame index is ambiguous')
            for variant in [frame, *frame.get('variants', [])]:
                if not isinstance(variant, dict):
                    raise ContractError('Scope repair frame index is ambiguous')
                path, signature = variant.get('path'), variant.get('sha256')
                if (not isinstance(path, str) or not path or not isinstance(signature, str)
                        or not signature or not Path(path).is_file() or sha256(path) != signature):
                    raise ContractError('Scope repair source image changed: ' + fid)
            result[fid] = frame
        return result

    old, current = index(old_frames), index(current_frames)
    if not set(old) <= set(current):
        raise ContractError('Scope repair lost a frozen evidence frame')
    def image_bindings(frame):
        return {(row.get('path'), row.get('sha256'), row.get('source_path'))
                for row in [frame, *frame.get('variants', [])] if row.get('sha256')}
    for fid, frame in old.items():
        if (frame['time_s'] != current[fid]['time_s']
                or not image_bindings(frame) <= image_bindings(current[fid])):
            raise ContractError('Scope repair changed a frozen evidence frame: ' + fid)
    added = sorted(set(current) - set(old))
    later = [frame for row in rounds[source_index+1:] for frame in row['request']['frames']]
    for fid in added:
        if not any(frame.get('frame_id') == fid and frame.get('time_s') == current[fid]['time_s']
                   and image_bindings(frame) & image_bindings(current[fid]) for frame in later):
            raise ContractError('Scope repair new frame lacks accepted Census provenance: ' + fid)

    actors = value['behavior']['actors']
    ids = [a.get('actor_id') for a in actors]
    if any(not isinstance(aid, str) or not aid for aid in ids) or len(ids) != len(set(ids)):
        raise ContractError('Scope repair baseline Actor IDs are missing or repeated')
    affected, _ = _affected_entities(value, record, saved)
    stale_actors = []
    if added and handoff_enabled(case):
        for actor in actors:
            claim = actor.get('execution_scope_evidence')
            if claim is None or actor['actor_id'] in affected:
                continue
            checked = claim.get('checked_frame_ids') if isinstance(claim, dict) else None
            if isinstance(checked, list) and len(checked) == len(set(checked)) and set(checked) == set(old):
                stale_actors.append(actor['actor_id'])
    from _lib.behavior.bounded_repair import state_of
    passed, _, _ = state_of(case, value['behavior'], value['review'])
    stale_decisions = sorted(row['issue_id'] for row in value['review']['decisions']
        if added and row.get('outcome') in {'irrelevant', 'excluded', 'confirmed', 'distinct'}
        and row['issue_id'] not in passed and set(row.get('evidence_frame_ids', [])) == set(old))
    from _lib.runtime.fact_versions import ROOT as FACT_ROOT, current_facts
    return dict(mode='full_evidence_review' if stale_actors or stale_decisions else 'local',
        source_trigger_path=str(Path(record['trigger_path']).resolve()),
        source_trigger_sha256=sha256(record['trigger_path']),
        old_frame_source=str(old_source.resolve()), old_frame_sha256=sha256(old_source),
        current_frame_sha256=sha256(current_index) if current_index.is_file() else None,
        old_frame_ids=sorted(old), current_frame_ids=sorted(current), added_frame_ids=added,
        affected_actor_ids=sorted(affected),
        refresh_actor_ids=sorted(stale_actors), refresh_issue_ids=stale_decisions,
        fact_version_id=current_facts(case).version_id if (case/FACT_ROOT/'active.json').is_file() else None,
        census_certificate_sha256=saved['sha256'])


def validate_patch(case, data):
    record = read_json(Path(case)/'state/progress.json').get('diagnostics', {}).get(KEY)
    if not record or record['status'] != 'adjudicated':
        return
    value = trigger(record['trigger_path'])
    base, _ = accepted_fact_projection(case)
    from _lib.contracts.behavior_handoff import enabled as handoff_enabled
    from _lib.behavior.bounded_repair import request as repair_request
    packet = repair_request(case)
    refresh = set(packet.get('refresh_actor_ids', [])) if packet and packet.get('mode') == 'full_evidence_review' else set()
    if refresh:
        scope = repair_scope(case)
        if scope is None or scope['mode'] != 'full_evidence_review' or refresh != set(scope['refresh_actor_ids']):
            raise ScopeReviewBlocked('Full evidence review authority changed')
    if handoff_enabled(case):
        # The rebuild may supply previously absent scope evidence, but cannot
        # change an unrelated observation, role or already reviewed scope claim.
        base = copy.deepcopy(base)
        current = {a['actor_id']:a for a in data['actors']}
        for actor in base['actors']:
            updated = current.get(actor['actor_id'], {})
            if (('execution_scope_evidence' not in actor or actor['actor_id'] in refresh)
                    and 'execution_scope_evidence' in updated):
                actor['execution_scope_evidence'] = copy.deepcopy(updated['execution_scope_evidence'])
    saved = contract.proof(case)
    actors, events = _affected_entities(value, record, saved)
    if value.get('lane_actor_ids'):
        from _lib.contracts.lane_resolution import apply
        # Identity-owned Actors already have a broader, certificate-bound scope.
        # Extra lane authority applies only to otherwise unrelated Actors.
        lane_ids = set(value['lane_actor_ids']) - actors
        base = copy.deepcopy(base)
        current = {a['actor_id']:a for a in data['actors']}
        for actor in base['actors']:
            if actor['actor_id'] not in lane_ids:
                continue
            updated = current.get(actor['actor_id'])
            if updated is None or len(updated['trajectory']) != len(actor['trajectory']):
                raise ScopeReviewBlocked('Combined lane review changes Actor samples')
            for before, after in zip(actor['trajectory'], updated['trajectory']):
                for key in ('road_id','ordinal_from_right','lane_id','detail'):
                    if key in after:
                        before[key] = copy.deepcopy(after[key])
                if before != after:
                    raise ScopeReviewBlocked('Combined lane review changes non-lane observations')
            if actor != updated:
                raise ScopeReviewBlocked('Combined lane review changes Actor identity')
        rows = [r['lane_resolution'] for key in ('semantic_limitations','challenge_resolutions')
                for r in data.get(key, []) if r.get('lane_resolution', {}).get('actor_id') in lane_ids]
        base = apply(read_json(Path(case)/'state/world_context.json'), base, base, rows, lane_ids)
        from _lib.behavior.compile import derive_initial_states
        base = derive_initial_states(base, sync_actor_ids=lane_ids)
    def affected(row, collection):
        if collection == 'actors': return row['actor_id'] in actors
        if collection == 'events':
            return row['event_id'] in events or bool(set(row.get('involved_actor_ids', [])) & actors)
        refs = set(row.get('actor_ids', [])) | set(row.get('affected_actor_ids', []))
        return row.get('actor_id') in actors or row.get('event_id') in events or bool(refs & actors)
    for collection in ('actors','events','executor_primitives','challenge_resolutions'):
        if ([r for r in base.get(collection, []) if not affected(r, collection)]
                != [r for r in data.get(collection, []) if not affected(r, collection)]):
            raise ScopeReviewBlocked('Scope repair changes unrelated ' + collection)
    issue_actors = _accepted_issue_actors(value, record, saved)
    certified_actors = set().union(*issue_actors.values()) if issue_actors else set()
    def limitations(rows):
        indexed = {}
        for row in rows:
            key = row.get('limitation_id')
            if not isinstance(key, str) or not key or key in indexed:
                raise ScopeReviewBlocked('Scope repair has ambiguous semantic_limitations')
            indexed[key] = row
        return indexed
    old_limits = limitations(base.get('semantic_limitations', []))
    new_limits = limitations(data.get('semantic_limitations', []))
    if old_limits.keys() != new_limits.keys():
        raise ScopeReviewBlocked('Scope repair changes unrelated semantic_limitations')
    for key, old in old_limits.items():
        new = new_limits[key]
        if old == new:
            continue
        old_issues = set(old.get('census_issue_ids', []))
        new_issues = set(new.get('census_issue_ids', []))
        old_actors = set(old.get('actor_ids', []))
        new_actors = set(new.get('actor_ids', []))
        added_actors = new_actors - old_actors
        linked = set().union(*(issue_actors[issue] for issue in old_issues & issue_actors.keys()))
        if (not added_actors or not added_actors <= certified_actors
                or not old_actors <= new_actors
                or not new_issues <= old_issues
                or any(not issue_actors.get(issue, set()) & added_actors
                       for issue in old_issues - new_issues)
                or (old_issues and not added_actors <= linked)):
            raise ScopeReviewBlocked('Scope repair changes unrelated semantic_limitations')
        # A limitation already covering many Actors may add a certified Actor;
        # changing its interpretation additionally requires its old issue link.
        interpretation = {'description', 'impact_domains', 'census_issue_ids'}
        if old_issues:
            permitted = interpretation | {'actor_ids'}
            old_domains = set(old.get('impact_domains', []))
            new_domains = set(new.get('impact_domains', []))
            if (not new_domains <= old_domains
                    or old_domains - new_domains - {'actor_identity'}):
                raise ScopeReviewBlocked('Scope repair changes unrelated semantic_limitations')
        else:
            permitted = {'actor_ids'}
        if any(old.get(field) != new.get(field) for field in set(old) | set(new)
               if field not in permitted):
            raise ScopeReviewBlocked('Scope repair changes unrelated semantic_limitations')
    allowed = {'world_evidence_epoch','actors','events','executor_primitives','semantic_limitations',
               'challenge_resolutions','video_fidelity'}
    if any(base.get(k) != data.get(k) for k in set(base)|set(data) if k not in allowed):
        raise ScopeReviewBlocked('Scope repair changes unrelated Behavior metadata')
    before, after = base.get('video_fidelity'), data.get('video_fidelity')
    if before is not None or after is not None:
        def unaffected_fidelity(value):
            result = copy.deepcopy(value)
            if result:
                result['observations'] = [o for o in result['observations'] if o['actor_id'] not in actors]
            return result
        if unaffected_fidelity(before) != unaffected_fidelity(after):
            raise ScopeReviewBlocked('Scope repair changes unrelated fidelity observations')


ScopeReviewBlocked = scope.ScopeReviewBlocked


def finish(runner, case):
    state = runner.progress(case); record = state.get('diagnostics', {}).get(KEY)
    if record and record['status'] == 'adjudicated':
        record['status'] = 'completed'; runner.save_progress(case, state)
