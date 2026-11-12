"""Bounded World review of archived cross-stage observation disputes."""
from __future__ import annotations

from pathlib import Path

from _lib.contracts.conversion_policy import digest
from _lib.contracts.fact_disagreement import (census_identity_disagreements,
    census_lane_disagreements)
from _lib.contracts.v3 import ContractError
from _lib.runtime.case_core import atomic_json, now, read_json, sha256
from _lib.runtime.fact_versions import ROOT as FACT_ROOT, commit_revisions, current_facts

ROOT = Path('diagnostics/fact_revision_review')
REVISION = 'fact-review.v1'
MAX_REVIEWS = 4


class FactDisagreement(ContractError):
    code = 'fact_disagreement'

    def __init__(self, disagreements):
        super().__init__('Behavior challenges current Census sightings with concrete frames',
                         rule_id='facts.disagreement')
        self.diagnostics = {'rule_id': self.rule_id, 'owner': 'world',
                            'retry_kind': 'new_evidence',
                            'disagreements': disagreements}


def begin(runner, case, behavior, error):
    """Store every disagreement before the Behavior candidate is archived."""
    case = Path(case)
    if not (case/FACT_ROOT/'active.json').is_file():
        return False
    from _lib.contracts.census_adjudication import proof
    saved = proof(case)
    disagreements = (census_identity_disagreements(saved, behavior, case=case)
                     + census_lane_disagreements(saved, behavior, case=case))
    if not disagreements:
        return False
    if (getattr(error, 'rule_id', None) != 'identity.census.adjudication'
            and not any(row['property'] == 'actor_identity' for row in disagreements)):
        return False
    progress = runner.progress(case)
    progress.setdefault('diagnostics', {})['fact_disagreement'] = {
        'status': 'pending', 'version_id': current_facts(case).version_id,
        'disagreements': disagreements, 'recorded_at': now()}
    runner.save_progress(case, progress)
    raise FactDisagreement(disagreements) from error


def _archive(case, progress):
    for row in reversed(progress.get('sessions', [])):
        if row.get('stage') == 'behavior' and row.get('invalid_candidate_path'):
            path = Path(row['invalid_candidate_path']).resolve()
            if path.is_file() and path.is_relative_to(Path(case).resolve()):
                return path
    return None


def _previous_decisions(case, candidate_hash):
    values = {}
    for path in sorted((Path(case)/ROOT).glob('*.result.json')):
        result = read_json(path)
        if result.get('candidate_sha256') != candidate_hash:
            continue
        for row in result.get('decisions', []):
            if row.get('decision') in {'upheld', 'inconclusive'}:
                values[(row['fact_key'], row['superseded_claim_id'])] = row['decision']
    return values


def _recover_committed(case, archive, view):
    """Finish a receipt if the pointer switched before the task journal did."""
    directory = Path(case)/ROOT
    for request_path in sorted(directory.glob('*.request.json')):
        request = read_json(request_path)
        if (request.get('candidate_sha256') != sha256(archive)
                or request.get('parent_version') == view.version_id):
            continue
        result_path = request_path.with_suffix('.result.json')
        if result_path.is_file():
            result = read_json(result_path)
            if result.get('status') == 'committed' and result.get('effective_version') == view.version_id:
                return result
            continue
        candidate_path = request_path.with_name(
            request_path.name.replace('.request.json', '.candidate.json'))
        if not candidate_path.is_file():
            continue
        response = read_json(candidate_path)
        if (response.get('request_sha256') != request.get('request_sha256')
                or digest({k: v for k, v in request.items() if k != 'request_sha256'})
                   != request['request_sha256']):
            raise ContractError('Interrupted fact review request or answer changed')
        by_key = {row['fact_key']: row for row in request['items']}
        decisions = response.get('decisions', [])
        if len(decisions) != len(by_key) or {row.get('fact_key') for row in decisions} != set(by_key):
            raise ContractError('Interrupted fact review answer is incomplete')
        revised = [row for row in decisions if row.get('decision') == 'superseded']
        if not revised:
            continue
        matched = []
        for row in revised:
            item = by_key[row['fact_key']]
            proof = next((entry for entry in view.revisions
                          if entry.get('kind') != 'world-question-review.v1'
                          and entry['parent_version'] == request['parent_version']
                          and entry['fact_key'] == row['fact_key']
                          and entry['superseded_claim_id'] == item['superseded_claim_id']
                          and entry['new_claim']['value'] == row.get('value')
                          and entry['adjudication']['reason'] == row.get('reason')
                          and entry['behavior_binding']['candidate_sha256']
                              == request['candidate_sha256']), None)
            if proof is None:
                matched = []
                break
            matched.append(proof)
        if len(matched) != len(revised):
            continue
        result = dict(revision=REVISION, request_sha256=request['request_sha256'],
                      candidate_sha256=request['candidate_sha256'],
                      response_sha256=sha256(candidate_path),
                      parent_version=request['parent_version'],
                      effective_version=view.version_id, status='committed',
                      decisions=[dict(fact_key=row['fact_key'],
                                      superseded_claim_id=by_key[row['fact_key']]['superseded_claim_id'],
                                      decision=row['decision'], reason=row['reason'])
                                 for row in decisions])
        atomic_json(result_path, result)
        return result
    return None


def _request(case, view, archive, disagreements):
    frames = {row['frame_id']: row for row in
              read_json(Path(case)/'state/behavior_frames.json')['frames']}
    items = []
    previous = _previous_decisions(case, sha256(archive))
    for row in disagreements:
        claim = view.claims.get(row['fact_key'])
        if claim is None or claim['claim_id'] != row['claim_id']:
            raise ContractError('Disagreement does not reference the active fact claim')
        if (row['fact_key'], claim['claim_id']) in previous:
            continue
        frame = frames.get(row['frame_id'])
        if frame is None or frame['time_s'] != row['time_s']:
            raise ContractError('Disagreement frame differs from frozen evidence')
        image = {'frame_id': row['frame_id'], 'path': frame['path'],
                 'sha256': frame['sha256']}
        if not Path(image['path']).is_file() or sha256(image['path']) != image['sha256']:
            raise ContractError('Disagreement source image changed')
        item = dict(fact_key=row['fact_key'],
                          observation_anchor_id=row['observation_anchor_id'],
                          superseded_claim_id=claim['claim_id'], field=row['property'],
                          actor_id=row['actor_id'], frame_id=row['frame_id'],
                          time_s=row['time_s'],
                          census_features=row['reviewed_features'],
                          image=image)
        if row['property'] == 'actor_lane':
            item.update(current_lane_id=row['reviewed_lane_id'],
                        behavior_lane_id=row['behavior_lane_id'],
                        behavior_detail=row['behavior_detail'],
                        census_geometry=row['reviewed_lane_geometry'],
                        behavior_geometry=row['behavior_lane_geometry'])
        else:
            item.update(candidate_lanes=row['candidate_lanes'],
                        distinct_from_actor_ids=row['distinct_from_actor_ids'])
        items.append(item)
    if not items:
        return None
    from _lib.runtime.fact_versions import census_path
    certificate = census_path(case)
    body = dict(revision=REVISION, case_id=Path(case).name,
                parent_version=view.version_id, candidate_path=str(archive),
                candidate_sha256=sha256(archive),
                census_certificate_path=str(certificate),
                census_certificate_sha256=sha256(certificate),
                world_path=str(view.world_path), world_sha256=sha256(view.world_path),
                registry_path=str(view.registry_path), registry_sha256=sha256(view.registry_path),
                items=items)
    return dict(body, request_sha256=digest(body))


def _prompt(case, request_path, candidate_path):
    request = read_json(request_path)
    images = '\n'.join(f"- {row['frame_id']}: {row['image']['path']}"
                       for row in request['items'])
    return (f'你是本 Case 的 World 事实复裁者。只裁决请求中列出的观察级车道或身份冲突，不重建整个场景。'
            f'只读取冻结请求 {request_path}；其中已包含原 Census 目击、Behavior 轨迹差异、'
            '候选身份、两种车道的边界以及原文件哈希。逐张查看这些原帧图像：\n'
            f'{images}\n'
            '对每个 items 条目输出一次决定：superseded、upheld 或 inconclusive。'
            '只有可见车道边界、同一物体外观连续性和同帧其他车辆排除证据足够时才能 superseded。'
            '证据不足选 inconclusive；不能因 Behavior 与 Census 标签不同就断定旧事实错误。'
            '每条决定需包含 fact_key、superseded_claim_id、decision、reason、'
            'appearance、continuity、co_visibility_exclusion；superseded 另含 value：'
            'actor_lane 填新 lane_id，actor_identity 填经同物证据确认的候选 actor_id。'
            '所有说明引用请求对应帧的可见特征，不使用同名 Actor 或同一帧作为唯一身份证明。'
            f'只写 JSON 到 {candidate_path}，结构为 '
            '{"request_sha256":"...","decisions":[{...}]}。写完停止。'
            f'请求哈希：{request["request_sha256"]}。')


def _commit_candidate(runner, case, request_path, candidate_path):
    case = Path(case)
    request = read_json(request_path)
    if digest({k: v for k, v in request.items() if k != 'request_sha256'}) != request['request_sha256']:
        raise ContractError('Fact review request changed')
    view = current_facts(case)
    if view.version_id != request['parent_version']:
        raise ContractError('Fact review parent version is stale; recalculate against current facts')
    for name in ('candidate', 'census_certificate', 'world', 'registry'):
        path = Path(request[name+'_path'])
        if not path.is_file() or sha256(path) != request[name+'_sha256']:
            raise ContractError('Fact review frozen input changed: ' + name)
    response = read_json(candidate_path)
    if response.get('request_sha256') != request['request_sha256']:
        raise ContractError('World fact review answer is bound to another request')
    expected = {row['fact_key']: row for row in request['items']}
    decisions = response.get('decisions')
    if (not isinstance(decisions, list) or len(decisions) != len(expected)
            or {row.get('fact_key') for row in decisions if isinstance(row, dict)} != set(expected)):
        raise ContractError('World fact review must answer each disputed claim exactly once')
    replacements = []
    diagnostics = []
    for row in decisions:
        item = expected[row['fact_key']]
        if row.get('superseded_claim_id') != item['superseded_claim_id']:
            raise ContractError('World fact review replaced a different claim')
        decision = row.get('decision')
        if decision not in {'superseded', 'upheld', 'inconclusive'}:
            raise ContractError('World fact review decision is invalid')
        if not isinstance(row.get('reason'), str) or len(row['reason'].strip()) < 12:
            raise ContractError('World fact review needs a concrete visual reason')
        diagnostics.append(dict(fact_key=item['fact_key'],
                                superseded_claim_id=item['superseded_claim_id'],
                                decision=decision, reason=row['reason']))
        if decision != 'superseded':
            continue
        if item['field'] == 'actor_identity':
            lane_id = item['candidate_lanes'].get(row.get('value'))
            if row.get('value') not in item['candidate_lanes']:
                raise ContractError('Identity review chose an Actor without matching frame evidence')
            binding_actor = row['value']
        else:
            lane_id = item['behavior_lane_id']
            binding_actor = item['actor_id']
        replacements.append(dict(parent_version=view.version_id, decision='superseded',
            fact_key=item['fact_key'], observation_anchor_id=item['observation_anchor_id'],
            field=item['field'], superseded_claim_id=item['superseded_claim_id'],
            value=row.get('value'), evidence=[item['image']],
            behavior_binding=dict(candidate_path=request['candidate_path'],
                candidate_sha256=request['candidate_sha256'], actor_id=binding_actor,
                lane_id=lane_id, appearance=row.get('appearance'),
                continuity=row.get('continuity'),
                co_visibility_exclusion=row.get('co_visibility_exclusion')),
            adjudication=dict(owner='world', reason=row['reason'])))
    new_view = commit_revisions(case, view.version_id, replacements) if replacements else view
    result = dict(revision=REVISION, request_sha256=request['request_sha256'],
                  candidate_sha256=request['candidate_sha256'],
                  response_sha256=sha256(candidate_path), parent_version=view.version_id,
                  effective_version=new_view.version_id,
                  status='committed' if replacements else 'no_fact_change',
                  decisions=diagnostics)
    result_path = request_path.with_suffix('.result.json')
    if result_path.is_file() and read_json(result_path) != result:
        raise ContractError('Fact review result changed after commit')
    if not result_path.is_file():
        atomic_json(result_path, result)
    progress = runner.progress(case)
    progress.setdefault('diagnostics', {})['fact_disagreement'] = dict(
        status=result['status'], parent_version=view.version_id,
        effective_version=new_view.version_id,
        result_path=str(result_path), decisions=diagnostics)
    runner.save_progress(case, progress)
    return result


def run(runner, case):
    """Reuse the normal model task lifecycle once per frozen disagreement set."""
    case = Path(case)
    if not (case/FACT_ROOT/'active.json').is_file():
        return False
    progress = runner.progress(case)
    pending = progress.get('diagnostics', {}).get('fact_disagreement')
    if not pending or pending.get('status') != 'pending':
        return False
    archive = _archive(case, progress)
    if archive is None:
        raise ContractError('Fact disagreement has no preserved Behavior candidate')
    view = current_facts(case)
    recovered = _recover_committed(case, archive, view)
    if recovered is not None:
        return recovered['status'] == 'committed'
    from _lib.contracts.census_adjudication import proof
    saved = proof(case)
    candidate = read_json(archive)
    disagreements = (census_identity_disagreements(saved, candidate, case=case)
                     + census_lane_disagreements(saved, candidate, case=case))
    request = _request(case, view, archive, disagreements)
    if request is None:
        return False
    directory = case/ROOT
    completed = list(directory.glob('*.result.json'))
    if len(completed) >= MAX_REVIEWS:
        raise ContractError('Fact review limit reached; preserve unresolved claims')
    request_path = directory/(request['request_sha256']+'.request.json')
    candidate_path = directory/(request['request_sha256']+'.candidate.json')
    if request_path.is_file() and read_json(request_path) != request:
        raise ContractError('Frozen fact review request changed')
    if not request_path.is_file():
        atomic_json(request_path, request)
    result_path = request_path.with_suffix('.result.json')
    if result_path.is_file():
        return read_json(result_path)['status'] == 'committed'
    stage = 'fact_revision_review_'+request['request_sha256'][:12]
    def adopt(_session=None):
        from _lib.runtime.orchestrator import candidate_snapshot
        candidate_snapshot(case, stage, candidate_path)
        return _commit_candidate(runner, case, request_path, candidate_path)
    runner.model_stage(case, stage, 'global-video-understanding',
                       _prompt(case, request_path, candidate_path), adopt,
                       adopt if candidate_path.is_file() else None,
                       candidate_path=candidate_path, max_attempts=2)
    return read_json(result_path)['status'] == 'committed'
