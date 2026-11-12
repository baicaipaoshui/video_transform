"""One bounded World-owner re-adjudication triggered by intersection review."""
from __future__ import annotations

import copy
import re
from pathlib import Path

from _lib.behavior.compile import preview_digest
from _lib.contracts.v3 import ContractError, validate_world
from _lib.runtime.case_core import atomic_json, now, read_json, raw_read_json, sha256, validate_evidence
from _lib.world.closure import (CONTRACT, ClosureStopped, materialize,
                                normalize_patch_evidence, request_binding)


STAGE = "post_behavior_world_readjudication"
REVISION = 'post-behavior-world-commit.v1'
PREPARED = 'state/post_behavior_world_readjudication.prepared.json'
JOURNAL = 'state/lane_layout.commit.json'
RECEIPT_KEY = 'post_behavior_world_revision'


def _immutable(path, value):
    from _lib.runtime.world_adjudication import _immutable as save
    save(path, value)
    return Path(path)


def _snapshot(case, path):
    return _immutable(case/'checkpoints/post_behavior_world'/f'{sha256(path)}.json', read_json(path))


def _dependencies(case):
    from _lib.runtime.identity_revision import _dependencies as existing
    from _lib.contracts.census_scope import REVIEW
    paths = existing(case)
    paths += [case/REVIEW] if (case/REVIEW).is_file() else []
    paths += list((case/'state').glob('behavior.lane_review*.json'))
    for prefix in ('lane_review', 'preview_review', 'preview_separation',
                   'execution_localization', 'execution_verification', 'actor_motion_review_'):
        for path in (case/'diagnostics').glob(prefix+'*'):
            paths.extend([path] if path.is_file() else [p for p in path.rglob('*') if p.is_file()])
    return sorted(set(paths))


def _require_supported_parent(case):
    from _lib.runtime.identity_revision import ACCEPTED
    if (case/ACCEPTED).is_file():
        raise ClosureStopped('world_identity_readjudication_required',
            'Post-Behavior World carry does not supersede an independent Actor identity certificate')


def _escape(value):
    return str(value).replace("~", "~0").replace("/", "~1")


def pointer(world_path):
    tokens = re.findall(r"[^.\[\]]+", world_path)
    if not tokens or tokens[0] not in {"world", "actor_hypotheses", "questions_to_verify", "ambiguities", "field_evidence"}:
        raise ContractError("intersection World challenge has unsupported path: " + str(world_path))
    return "/" + "/".join(_escape(token) for token in tokens)


def _read(document, json_pointer):
    value = document
    for token in json_pointer[1:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        value = value[int(token)] if isinstance(value, list) else value[token]
    return value


def write_request(case, review_path, review, catalog):
    case = Path(case).resolve()
    from _lib.runtime.fact_versions import source_path
    live_base = source_path(case, 'world_context.json')
    base = _snapshot(case, live_base)
    output = case / "state/post_behavior_world_readjudication.candidate.json"
    request_path = case / "state/post_behavior_world_readjudication.request.json"
    world = read_json(base)
    challenged = sorted({path for item in review["world_challenges"] for path in item["world_paths"]})
    primary = [pointer(path) for path in challenged]
    evidence = ["/field_evidence/" + _escape(path) for path in challenged]
    allowed = sorted(set(primary + evidence))
    current = {}
    for path, ptr in zip(challenged, primary):
        try:
            current[path] = copy.deepcopy(_read(world, ptr))
        except (KeyError, IndexError, TypeError, ValueError):
            current[path] = None
    inputs = [base, _snapshot(case, Path(review_path)),
              _snapshot(case, case / "state/dense_evidence_catalog.json")]
    baseline = {}
    for name in ('world_context.json', 'actor_registry.json', 'reconciliation.json',
                 'census_adjudication.accepted.json', 'behavior_input.request.json'):
        live = source_path(case, name) if name != 'behavior_input.request.json' else case/'state'/name
        if live.is_file():
            frozen = _snapshot(case, live)
            inputs.append(frozen)
            baseline[name] = dict(path=str(frozen), sha256=sha256(live))
    input_hashes = {str(path.resolve()): sha256(path) for path in inputs}
    evidence_rows = []
    used = {frame for item in review["world_challenges"] for frame in item["evidence_frame_ids"]}
    for row in catalog["frames"]:
        if row["frame_id"] not in used:
            continue
        frame = copy.deepcopy(row["frame"])
        source = Path(frame.get("source_path") or frame.get("path", ""))
        packed = Path(frame.get("path", ""))
        image = source if source.is_file() else packed
        if not image.is_file() or (frame.get("sha256") and packed.is_file() and sha256(packed) != frame["sha256"]):
            raise ContractError("post-Behavior World evidence image missing or changed")
        frame["review_path"] = str(image.resolve())
        frame["review_sha256"] = sha256(image)
        input_hashes[frame["review_path"]] = frame["review_sha256"]
        evidence_rows.append(frame)
    append_path = "/world/road/roadside_areas/-"
    append_actor_ids = sorted({actor for item in review["world_challenges"]
                               if pointer(item["world_paths"][0]) == append_path
                               for actor in item["actor_ids"]})
    payload = {
        "contract_version": CONTRACT,
        "case_id": case.name,
        "baseline": baseline,
        "base_path": str(base.resolve()),
        "base_sha256": sha256(base),
        "expected_epoch": world["evidence_epoch"] + 1,
        "issues": [item["challenge_id"] for item in review["world_challenges"]],
        "allowed_paths": allowed,
        "array_append_contracts": ({append_path: {
            "contract": "roadside_parking_area.v1", "actor_ids": append_actor_ids}}
                                   if append_path in allowed else {}),
        "challenged_paths": primary,
        "resolution_values": ["unresolved", "resolved", "non_core"],
        "focused_context": {
            "world_challenges": copy.deepcopy(review["world_challenges"]),
            "current_values": current,
            "evidence_frames": evidence_rows,
            "scope_rule": ("Re-adjudicate only challenged World facts. A missing roadside parking area may only "
                           "append at /world/road/roadside_areas/- with area_id, road_id, side, area_type, "
                           "occupied_by and evidence_frame_ids; a parked actor alone does not prove a marked "
                           "parking strip. A lane connection is observed only with "
                           "same-actor incoming, junction and post-outgoing-boundary witnesses. Being before or "
                           "on the far crosswalk is not an outgoing witness. Preserve unknown when unsupported."),
        },
        "input_hashes": input_hashes,
        "output_path": str(output.resolve()),
        "output_contract": {
            "contract_version": CONTRACT,
            "request_sha256": "copy exact request hash",
            "base_sha256": "copy exact base hash",
            "operations": [{"op": "add|replace", "path": "one exact allowed JSON Pointer",
                            "value": "replacement value", "evidence_frame_ids": ["frame_NNNN"]}],
        },
        "stop_contract": {"status": "needs_evidence|blocked_by_patch_contract", "operations": [],
                          "blocking_issues": "nonempty subset of issues", "reason": "specific reason",
                          "required_paths": "blocked_by_patch_contract only"},
    }
    payload["request_sha256"] = request_binding(payload)
    if request_path.is_file() and read_json(request_path) != payload:
        raise ContractError("post-Behavior World request changed; create a new Case")
    if not request_path.is_file():
        atomic_json(request_path, payload)
    return request_path, output


def _compile(case, request_path, response, census=None):
    request = read_json(request_path)
    old = read_json(request['base_path'])
    revised, repairs = normalize_patch_evidence(materialize(case, response, request_path=request_path))
    validate_world(revised, observation_contract=True)
    validate_evidence(revised)
    from _lib.world import admission
    if admission.enabled(case):
        admission.validate(revised, admission.duration_for(case), case=case)
    for collection, key in (('questions_to_verify', 'question_id'), ('ambiguities', 'ambiguity_id')):
        if [x[key] for x in old[collection]] != [x[key] for x in revised[collection]]:
            raise ContractError('post-Behavior World re-adjudication changed verification IDs')
    changed = []
    for path in request['challenged_paths']:
        ptr = path[:-2] if path.endswith('/-') else path
        if _read(old, ptr) != _read(revised, ptr):
            changed.append(path)
    if not changed:
        raise ClosureStopped('world_closure_no_progress', 'World re-adjudication changed no challenged fact')
    if census:
        from _lib.contracts.census_adjudication import preserve_world_revision
        preserve_world_revision(census, old, revised)
    return dict(world=revised, repairs=repairs, changed_paths=changed)


def replay(case, link, *, census=None):
    """Verify immutable post-Behavior evidence, without reading live World."""
    case = Path(case).resolve()
    _require_supported_parent(case)
    path = case/PREPARED
    if link != dict(path=str(path), sha256=sha256(path)):
        raise ContractError('Post-Behavior prepared binding changed')
    saved = read_json(path)
    body = dict(saved); signature = body.pop('sha256', None)
    if signature != preview_digest(body) or saved.get('revision') != REVISION or saved.get('case_dir') != str(case):
        raise ContractError('Post-Behavior prepared contract changed')
    request_path = case/'state/post_behavior_world_readjudication.request.json'
    if sha256(request_path) != saved['request_sha256']:
        raise ContractError('Post-Behavior request changed')
    request = read_json(request_path)
    before = request['baseline']
    registry = raw_read_json(before['actor_registry.json']['path'])
    receipt = raw_read_json(before['reconciliation.json']['path'])
    batch_hashes = {p.name:sha256(p) for p in (case/'batches').glob('batch_*.json')}
    if receipt.get('world') != raw_read_json(request['base_path']) or receipt.get('batch_sha256') != batch_hashes:
        raise ContractError('Post-Behavior baseline receipt changed')
    from _lib.contracts import census_adjudication as contract, census_history
    from _lib.runtime.fact_versions import census_path
    if 'census_adjudication.accepted.json' in before:
        frozen = read_json(before['census_adjudication.accepted.json']['path'])
        if (not census_path(case).is_file()
                or sha256(census_path(case)) != before['census_adjudication.accepted.json']['sha256']
                or read_json(census_path(case)) != frozen):
            raise ContractError('Post-Behavior census parent changed')
        census = census or census_history.replay(frozen)
        if (census['sha256'] != frozen['sha256'] or registry != census['result']['registry']
                or receipt.get('census_adjudication') != census['request']['request_sha256']):
            raise ContractError('Post-Behavior census baseline changed')
    elif census or census_path(case).exists() or receipt.get('census_adjudication'):
        raise ContractError('Post-Behavior census parent is missing')
    result = _compile(case, request_path, saved['response'], census)
    if any(saved[key] != result[key] for key in result):
        raise ContractError('Post-Behavior prepared result differs from reviewed response')
    parent = saved.get('previous_commit')
    if parent:
        parent_path = Path(parent['path'])
        from _lib.runtime.world_adjudication import ROOT, validate_journal
        if (parent_path.parent != case/ROOT/'previous_commits' or sha256(parent_path) != parent['sha256']):
            raise ContractError('Post-Behavior previous journal binding changed')
        old = read_json(parent_path); body = dict(old); signature = body.pop('sha256', None)
        if (old.get('status') != 'committed' or old.get('post_behavior_revision')
                or signature != preview_digest(body) or old['world'] != receipt['world']
                or old['registry'] != registry or old['receipt'] != receipt):
            raise ContractError('Post-Behavior previous journal differs from baseline')
        validate_journal(old)
    expected_receipt = dict(receipt, world=result['world'], **{RECEIPT_KEY:link})
    return dict(saved, registry=registry, receipt=expected_receipt)


def proof(case, *, census=None):
    case = Path(case).resolve()
    journal = read_json(case/JOURNAL) if (case/JOURNAL).is_file() else {}
    from _lib.runtime.fact_versions import source_path
    receipt_path = source_path(case, 'reconciliation.json')
    receipt = raw_read_json(receipt_path) if receipt_path.is_file() else {}
    link = receipt.get(RECEIPT_KEY)
    if journal.get('post_behavior_revision'):
        body = dict(journal); signature = body.pop('sha256', None)
        if signature != preview_digest(body):
            raise ContractError('Post-Behavior journal binding changed')
        if journal.get('status') != 'committed':
            raise ContractError('Recover pending post-Behavior World commit first')
        if link != journal['post_behavior_revision']:
            raise ContractError('Post-Behavior receipt binding is missing or changed')
    if not link:
        if (case/PREPARED).is_file() and read_json(case/PREPARED).get('revision') == REVISION:
            raise ContractError('Recover prepared post-Behavior World commit first')
        return None
    saved = replay(case, link, census=census)
    from _lib.runtime.fact_versions import ROOT as FACT_ROOT, current_facts, reviewed_world
    view = current_facts(case) if (case/FACT_ROOT/'active.json').is_file() else None
    effective_world = raw_read_json(source_path(case, 'world_context.json'))
    if (not journal.get('post_behavior_revision') or receipt != saved['receipt']
            or any(journal[key] != saved[key] for key in ('world', 'registry', 'receipt'))
            or effective_world != saved['world'] and not (view and reviewed_world(case, view))
            or raw_read_json(source_path(case, 'actor_registry.json')) != saved['registry']):
        raise ContractError('Post-Behavior proof differs from current checkpoint')
    return saved


def stage_commit(case, candidate):
    case = Path(case).resolve()
    _require_supported_parent(case)
    request_path = case/'state/post_behavior_world_readjudication.request.json'
    request = read_json(request_path)
    from _lib.contracts import census_adjudication as contract
    from _lib.runtime.world_adjudication import ROOT
    census = contract.proof(case)
    from _lib.runtime.fact_versions import source_path
    for name, frozen in request.get('baseline', {}).items():
        live = source_path(case, name) if name != 'behavior_input.request.json' else case/'state'/name
        if sha256(live) != frozen['sha256']:
            raise ContractError('Post-Behavior live baseline changed: ' + name)
    if not {'world_context.json', 'actor_registry.json', 'reconciliation.json'} <= set(request.get('baseline', {})):
        raise ContractError('Legacy post-Behavior request lacks a recoverable baseline')
    response = read_json(candidate)
    result = _compile(case, request_path, response, census)
    parent = None
    if (case/JOURNAL).is_file():
        old = read_json(case/JOURNAL)
        if old.get('status') != 'committed' or old.get('post_behavior_revision'):
            raise ContractError('Recover existing World commit before post-Behavior adjudication')
        target = case/ROOT/'previous_commits'/f'{sha256(case/JOURNAL)}.json'
        _immutable(target, old)
        parent = dict(path=str(target), sha256=sha256(target))
    dependencies = _dependencies(case)
    saved = dict(revision=REVISION, case_dir=str(case), request_sha256=sha256(request_path),
                 response=response, previous_commit=parent,
                 dependencies={str(p.relative_to(case)):sha256(p) for p in dependencies}, **result)
    saved['sha256'] = preview_digest(saved)
    _immutable(case/PREPARED, saved)
    install_journal(case)


def install_journal(case):
    """Prepared evidence can survive a crash before publishing the journal."""
    case = Path(case).resolve()
    link = dict(path=str(case/PREPARED), sha256=sha256(case/PREPARED))
    saved = replay(case, link)
    request = read_json(case/'state/post_behavior_world_readjudication.request.json')
    if (case/JOURNAL).is_file():
        existing = read_json(case/JOURNAL)
        if existing.get('post_behavior_revision') == link:
            return
        if not saved['previous_commit'] or sha256(case/JOURNAL) != saved['previous_commit']['sha256']:
            raise ContractError('Post-Behavior journal predecessor changed')
    elif saved['previous_commit']:
        raise ContractError('Post-Behavior journal predecessor disappeared')
    from _lib.runtime.fact_versions import source_path
    for name, frozen in request['baseline'].items():
        live = source_path(case, name) if name != 'behavior_input.request.json' else case/'state'/name
        if sha256(live) != frozen['sha256']:
            raise ContractError('Post-Behavior baseline changed before journal')
    journal = dict(world=saved['world'], registry=saved['registry'], receipt=saved['receipt'],
        old_world_sha256=request['baseline']['world_context.json']['sha256'],
        old_registry_sha256=request['baseline']['actor_registry.json']['sha256'],
        old_receipt_sha256=request['baseline']['reconciliation.json']['sha256'],
        input_hashes=request['input_hashes'] | {str(case/PREPARED):sha256(case/PREPARED),
            str(case/'state/post_behavior_world_readjudication.request.json'):saved['request_sha256']},
        dependencies=saved['dependencies'],
        post_behavior_revision=link)
    from _lib.runtime.fact_versions import ROOT as FACT_ROOT, current_facts
    if (case/FACT_ROOT/'active.json').is_file():
        parent = current_facts(case)
        journal.update(fact_parent_version=parent.version_id,
                       fact_parent_manifest_sha256=sha256(parent.directory/'manifest.json'))
    journal['sha256'] = preview_digest(journal)
    atomic_json(case/JOURNAL, journal)


def validate_commit(journal):
    link = journal['post_behavior_revision']
    case = Path(link['path']).parent.parent
    saved = replay(case, link)
    if journal.get('census_certificate') or any(journal[key] != saved[key] for key in ('world', 'registry', 'receipt', 'dependencies')):
        raise ContractError('Post-Behavior journal differs from prepared proof')
    baseline = read_json(case/'state/post_behavior_world_readjudication.request.json')['baseline']
    for key, name in (('old_world_sha256', 'world_context.json'),
                      ('old_registry_sha256', 'actor_registry.json'),
                      ('old_receipt_sha256', 'reconciliation.json')):
        if journal[key] != baseline[name]['sha256']:
            raise ContractError('Post-Behavior journal changed its frozen predecessor')


def retire_dependencies(case, journal):
    root = case/'checkpoints/post_behavior_world/retired'
    request = read_json(case/'state/post_behavior_world_readjudication.request.json')
    old_input = request['baseline'].get('behavior_input.request.json')
    from _lib.behavior import input_binding
    live_input = case/input_binding.REQUEST
    if old_input:
        frozen = read_json(old_input['path'])
        archive = case/'checkpoints/behavior_inputs'/f"{frozen['request_sha256']}.json"
        if (live_input.is_file() and sha256(live_input) != old_input['sha256']
                or not live_input.is_file() and (not archive.is_file() or read_json(archive) != frozen)):
            raise ContractError('Post-Behavior frozen Behavior input changed')
    elif live_input.exists():
        raise ContractError('Behavior input appeared during post-Behavior commit')
    for name, signature in journal['dependencies'].items():
        live, archive = case/name, root/name
        if (not live.exists() and not archive.exists() or
                live.exists() and sha256(live) != signature or archive.exists() and sha256(archive) != signature):
            raise ContractError('Post-Behavior dependent artifact changed: ' + name)
    input_binding.retire(case)
    for name in journal['dependencies']:
        live, archive = case/name, root/name
        if live.exists():
            archive.parent.mkdir(parents=True, exist_ok=True)
            live.replace(archive)


def finish_progress(runner, case, journal):
    from _lib.runtime.identity_revision import _retire_progress
    state = runner.progress(case)
    _retire_progress(state)
    state['post_behavior_world_readjudications'] = 1
    state.setdefault('diagnostics', {})[STAGE] = dict(status='completed',
        prepared_sha256=journal['post_behavior_revision']['sha256'], at=now())
    state['diagnostics']['intersection_structured_closure_active'] = True
    state.update(phase='aggregating', substage='behavior', status='aggregating', error=None,
                 artifacts={}, generation_status='not_generated', updated_at=now())
    runner.save_progress(case, state)


def recover(runner, case, *, reconcile_task=True):
    case = Path(case).resolve()
    if not (case/PREPARED).is_file():
        return False
    if read_json(case/PREPARED).get('revision') != REVISION:
        raise ContractError('Legacy post-Behavior prepared result lacks replay evidence')
    install_journal(case)
    runner.commit_lane_layout(case, {p.name:sha256(p) for p in (case/'batches').glob('batch_*.json')})
    journal = read_json(case/JOURNAL)
    state = runner.progress(case)
    if (state.get('post_behavior_world_readjudications') != 1
            or state.get('diagnostics', {}).get(STAGE, {}).get('prepared_sha256')
            != journal['post_behavior_revision']['sha256']):
        # Legacy task reconciliation may persist its pre-adoption progress after
        # the callback. The committed journal owns this idempotent transition.
        finish_progress(runner, case, journal)
    if reconcile_task:
        state = runner.progress(case)
        pending = any(s.get('stage') == STAGE and (s.get('status') in {'submitted', 'pending', 'running'}
            or (s.get('error_code') in {'task_deadline_exceeded', 'task_status_unavailable'}
                or s.get('recovered_from_poll_failure')) and not s.get('deadline_task_resolved'))
            for s in state.get('sessions', []))
        if pending or state.get('diagnostics', {}).get('unresolved_submissions', {}).get(STAGE):
            def accepted(_session=None):
                proof(case)
            runner.model_stage(case, STAGE, 'global-video-understanding', '', accepted,
                precheck=accepted, candidate_path=case/'state/post_behavior_world_readjudication.candidate.json',
                max_attempts=1)
            # A local output accepted after poll failure still needs terminal proof.
            for session in runner.progress(case).get('sessions', []):
                if session.get('stage') == STAGE and session.get('recovered_from_poll_failure') and not session.get('deadline_task_resolved'):
                    from _lib.runtime.orchestrator import BackendError
                    try:
                        remote = runner.backend.poll(session['task_id'], 'global-video-understanding')
                    except Exception as error:
                        raise BackendError('task_status_unavailable', 'Post-Behavior producer terminal state is unconfirmed') from error
                    if remote.get('status') not in {'completed', 'failed'}:
                        raise BackendError('task_status_unavailable', 'Post-Behavior producer may still be active')
                    current = runner.progress(case)
                    next(s for s in current['sessions'] if s.get('task_id') == session['task_id'])['deadline_task_resolved'] = True
                    runner.save_progress(case, current)
    return True


def run(runner, case, review_path, review, catalog):
    case = Path(case).resolve()
    if recover(runner, case):
        return True
    progress = runner.progress(case)
    if progress.get('post_behavior_world_readjudications', 0) >= 1:
        raise ContractError('post_behavior_world_readjudication_exhausted')
    _require_supported_parent(case)
    from _lib.contracts.census_adjudication import proof as census_proof
    census_proof(case)
    request_path, candidate = write_request(case, review_path, review, catalog)
    progress.setdefault('diagnostics', {})['intersection_structured_closure_active'] = True
    runner.save_progress(case, progress)

    def adopt(_session=None):
        if not (case/PREPARED).is_file():
            stage_commit(case, candidate)
        recover(runner, case, reconcile_task=False)

    template = (Path(__file__).resolve().parents[2] / 'world-understanding/references/post-behavior-readjudication.md').read_text()
    prompt = template.replace('$request_path', str(request_path)).replace('$candidate_path', str(candidate))
    runner.model_stage(case, STAGE, 'global-video-understanding', prompt, adopt,
                       precheck=adopt if candidate.is_file() else None,
                       candidate_path=candidate, max_attempts=1)
    recover(runner, case)
    return True
