"""Explicit core approximation from a source-bound raw Behavior answer.

The ordinary Case and formal publication remain untouched. Conversion work
uses the original owner, Agent, checkpoints and deterministic Converter in a
per-Case isolated copy. The existing independent acceptance CLI owns delivery.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from _lib.behavior.compile import (evidence_frames, execution_issues,
                                   prepare_behavior_candidate, validate_compiled_behavior)
from _lib.behavior.lane_change import from_analysis
from _lib.behavior.repair import materialize
from _lib.contracts import (behavior_census_scope, bounded_execution,
                            census_adjudication, census_execution, core_identity_hypothesis,
                            motion_witnesses, core_projection_sources,
                            retained_motion, semantic_motion)
from _lib.contracts.census_scope import split
from _lib.contracts.conversion_policy import digest, freeze
from _lib.contracts.core_scene_scope import actor_references, required_actors
from _lib.contracts.v3 import ContractError, build_analysis, gate_degradations
from _lib.delivery.converter import strict_v3_preflight
from _lib.paths import SCENARIO_ROOT, SKILLS
from _lib.runtime.case_core import Cases, atomic_json, read_json, sha256
from _lib.runtime.conversion_work import run as run_conversion
from _lib.runtime.fact_versions import current_facts
from _lib.runtime.orchestrator import candidate_snapshot, quarantine_candidate, validate_analysis
from _lib.runtime.v3 import V3Orchestrator

REVISION = 'core-scene-approximate-mode.v1'
ROOT_NAME = 'core_approximate_deliveries'
RAW_CANDIDATE = 'core-approximate-behavior-candidate.v1'
PROJECTION_REVISION = 'core-approximate-probe.v3'

_TERMINAL_PROVIDER_QUOTA = re.compile(
    r"Quota exceeded for model '[^'\n]+'\. Reason: Your token-plan [^\n.]+ "
    r"quota has been exhausted\. The quota will reset at \d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC\."
    r"(?: \[dump: [^\n\]]+\])?")


class NoncoreScopeEvidenceIncomplete(ContractError):
    code = 'core_noncore_scope_evidence_insufficient'


def _noncore_scope_coverage(error, pending, review, core_ids):
    """Only incomplete proof for unrelated exclusions may authorize B."""
    issues = error.diagnostics.get('scope_validation_issues', [])
    gaps = error.diagnostics.get('execution_gap_issues', [])
    rows = {row['issue_id']: row for row in (review or {}).get('decisions', [])}
    if not issues:
        return False
    for gap in gaps:
        domains = set(gap.get('impact_domains') or [])
        actors = set(gap.get('actor_ids') or [])
        if (domains & {'event', 'event_type', 'event_order', 'interaction',
                      'actor_motion', 'executor_primitive'}
                or 'conflict' in str(gap.get('rule_id', ''))
                or 'actor_lane' in domains and actors & core_ids):
            return False
    for issue in issues:
        iid = issue.get('issue_id')
        details = issue.get('details') or {}
        row, observation = rows.get(iid), pending.get(iid)
        if (details.get('rule_id') not in {
                'identity.census.scope_proof_frames', 'identity.census.scope_evidence_frames'}
                or not row or not observation or row.get('outcome') != 'irrelevant'
                or row.get('affected_actor_ids') or row.get('affected_event_ids')
                or set(observation.get('candidate_actor_ids') or []) & core_ids
                or set((row.get('identity_assessment') or {}).get('alternative_actor_ids') or []) & core_ids
                or observation.get('actor_id') in core_ids
                or (observation.get('preliminary_relevance') or {}).get('assessment') == 'retain'
                or (row.get('scope_proof') or {}).get('rule_id') != 'isolated_opposing'):
            return False
    return True


def validated_dispositions(case, behavior, pending, review, frames):
    """Run formal row/proof validation; unresolved issues remain explicit.

    This does not commit a strict scope review or grant strict admission.
    """
    from _lib.contracts import census_scope
    checked = census_scope.validate_review(case, behavior, review, frames=frames,
                                           allow_pending=True)
    dispositions = (checked or {}).get('execution_dispositions')
    if dispositions is None:
        dispositions = census_execution.decisions(case, behavior, pending, review, frames=frames)
    choices = (dispositions or {}).get('choices', [])
    if (dispositions is None or len(choices) != len(pending)
            or {r['issue_id'] for r in choices} != set(pending)):
        raise ContractError('Core execution dispositions lack complete verified observation coverage')
    return dispositions


def disposition_deferrals(full, dispositions):
    """Use the formal projection predicate for the whole source limitation."""
    from _lib.contracts.census_relevance import observation_issue_disposed, EXECUTION
    from _lib.contracts.behavior_handoff import census_limitation_disposed
    choices = dispositions['choices']
    rows = {r['issue_id']: r for r in choices}
    result = []
    for index, item in enumerate(full['limitations']):
        ids = item.get('census_issue_ids', [])
        if (not ids or len(ids) != len(set(ids)) or any(
                iid not in rows or rows[iid]['handling'] == 'needs_evidence' for iid in ids)):
            continue
        disposed = (observation_issue_disposed(item, dispositions)
                    if dispositions['revision'] == EXECUTION
                    else census_limitation_disposed(item, choices))
        if disposed:
            result.append(dict(source_limitation_index=index,
                source_limitation_sha256=digest(item), issue_ids=sorted(ids),
                execution_dispositions_sha256=dispositions['sha256']))
    return result


def disposition_risks(pending, review, dispositions):
    rows = {r['issue_id']: r for r in (review or {}).get('decisions', [])}
    return [dict(issue_id=r['issue_id'], handling=r['handling'],
                 observation=pending[r['issue_id']], review=rows.get(r['issue_id']))
            for r in dispositions['choices']]


def representative_projection(case, world, behavior, frames, pending, review, dispositions):
    """Reuse formal proxy construction without importing strict scope admission."""
    from _lib.contracts import peripheral_uncertainty
    from _lib.runtime.execution_scope import append_representatives

    if validated_dispositions(case, behavior, pending, review, frames) != dispositions:
        raise ContractError('Census execution dispositions changed or lack source binding')
    choices = (dispositions or {}).get('choices', [])
    if not any(r['handling'] == 'single_representative' for r in choices):
        return world, behavior, []
    peripheral = peripheral_uncertainty.validate_projection(world, behavior, frames, dispositions)
    w, b = copy.deepcopy(world), copy.deepcopy(behavior)
    b, representatives = append_representatives(w, b, choices, peripheral)
    return w, b, representatives


def _source_candidate_parts(case, raw):
    """Rebuild an assigned patch against its archived answer and review."""
    from _lib.behavior import source_revision
    if isinstance(raw, dict) and 'replacements' not in raw and source_revision.reserved(raw):
        raise ContractError('Source revision records require their immutable candidate and assigned patch')
    if isinstance(raw, dict) and 'replacements' in raw:
        request = read_json(case/'state/behavior_repair.request.json')
        if request.get('repair_contract') != 'behavior-source-coverage.v1':
            raise ContractError('Core source repair lacks its assigned Behavior contract')
        patch, review = split(raw)
        if request.get('scope_context') is not None and review is None:
            raise ContractError('Core source repair lacks the required scope review')
        if review is None:
            _, review = split(read_json(request['base_path']))
        return materialize(case, patch), review
    return split(raw)


def _check_source_candidate(case, raw):
    from _lib.contracts.core_lane_continuity import validate_source_coverage
    behavior, _ = _source_candidate_parts(case, raw)
    validate_source_coverage(case, behavior)


def _resumable_source_repair_quota_failure(case, prior):
    """Recognize a terminal provider quota stop without trusting its output."""
    case = Path(case).resolve()
    if (prior.get('status') != 'failed'
            or prior.get('error_code') != 'backend_protocol_error'
            or not _TERMINAL_PROVIDER_QUOTA.fullmatch(prior.get('message', ''))
            or not prior.get('task_id') or not prior.get('session_id')
            or (case/'state/behavior.candidate.json').exists()):
        return False
    invalid = prior.get('invalid_candidate_path')
    if invalid:
        archived = Path(invalid).resolve()
        return (archived.is_file()
                and archived.is_relative_to(case/'diagnostics/invalid_candidates')
                and prior.get('candidate_validation_error') ==
                    'Behavior candidate has no terminal producer'
                and isinstance(prior.get('output_file_size_bytes'), int)
                and prior['output_file_size_bytes'] > 0)
    return (not prior.get('candidate_validation_error')
            and prior.get('output_file_size_bytes') in {None, 0})


class CoreApproximateOrchestrator(V3Orchestrator):
    """Use the normal Behavior producer, retaining its first raw answer."""

    def capture_behavior_candidate(self, case, candidate_path, session=None):
        from _lib.behavior import input_binding

        progress = self.progress(case)
        if session is None:
            session = next((row.get('session_id') for row in reversed(progress.get('sessions', []))
                            if row.get('stage') == 'behavior'
                            and row.get('status') == 'completed'), None)
        input_binding.require_session(case, session)
        raw, fingerprint = candidate_snapshot(case, 'behavior', candidate_path)
        if not isinstance(raw, dict):
            raise ContractError('Behavior answer is not a JSON object')
        repair_path = case/'state/behavior_repair.request.json'
        if (repair_path.is_file()
                and read_json(repair_path).get('repair_contract') == 'behavior-source-coverage.v1'
                and 'replacements' not in raw):
            raise ContractError('Core source coverage repair requires the assigned patch')
        _check_source_candidate(case, raw)
        producer = next((row for row in reversed(progress.get('sessions', []))
                         if row.get('stage') == 'behavior' and row.get('session_id') == session), None)
        if producer is None or producer.get('status') not in {'completed', 'returned'}:
            raise ContractError('Behavior candidate has no terminal producer')
        receipt = {'revision': RAW_CANDIDATE, 'sha256': fingerprint,
                   'session_id': session,
                   'source_request_sha256': input_binding.active_request(case)['request_sha256'],
                   'fact_version': current_facts(case).version_id}
        if 'replacements' in raw:
            receipt['source_repair_request_sha256'] = sha256(case/'state/behavior_repair.request.json')
            from _lib.behavior import source_revision
            revised, _ = _source_candidate_parts(case, raw)
            revision_receipt = source_revision.archive(case, raw, revised)
            if revision_receipt:
                receipt['source_revision_receipt'] = str(Path(revision_receipt).relative_to(case))
                receipt['source_revision_receipt_sha256'] = sha256(revision_receipt)
        prior = progress.setdefault('diagnostics', {}).get('core_approximate_candidate')
        if prior is not None and prior != receipt:
            raise ContractError('Core approximation Behavior candidate changed')
        progress['diagnostics']['core_approximate_candidate'] = receipt
        self.save_progress(case, progress)

    def before_model_attempt(self, case, key):
        if getattr(self, '_core_transport_retry', False):
            from _lib.behavior import input_binding
            if key != 'behavior':
                raise ContractError('Core transport recovery cannot submit an upstream task')
            if input_binding.replayable_archives(case):
                raise ContractError('Core Behavior already has a complete source-bound answer')
            candidate = case/'state/behavior.candidate.json'
            if candidate.is_file():
                try:
                    raw = read_json(candidate)
                except (ValueError, UnicodeError):
                    raw = None
                if input_binding.is_complete_behavior(raw):
                    raise ContractError('Core Behavior already has a complete candidate')
        if (key == 'behavior' and not getattr(self, '_core_transport_retry', False)
                and self.progress(case).get('attempts', {}).get('behavior', 0)):
            progress = self.progress(case)
            prior = next((row for row in reversed(progress.get('sessions', []))
                          if row.get('stage') == 'behavior'), {})
            request_path = case/'state/behavior_repair.request.json'
            request = read_json(request_path) if request_path.is_file() else {}
            origin = next((row for row in progress.get('sessions', [])
                           if row.get('stage') == 'behavior'
                           and row.get('rule_id') == 'core_scene.source_coverage'
                           and row.get('actor_id') == request.get('actor_id')
                           and row.get('invalid_candidate_path') == request.get('base_path')), None)
            prior_is_assigned_failure = (prior.get('status') == 'invalid_output'
                or _resumable_source_repair_quota_failure(case, prior))
            if (not prior_is_assigned_failure
                    or origin is None
                    or request.get('repair_contract') != 'behavior-source-coverage.v1'
                    or not any(path != '/semantic_limitations'
                               for path in request.get('allowed_paths', []))):
                raise ContractError('Core Behavior retry lacks an assigned source coverage repair')
        return super().before_model_attempt(case, key)

    def submit_model_task(self, case, key, agent, prompt):
        """Continue the explicitly renewed timeout session instead of replaying it."""
        if key != 'behavior' or not getattr(self, '_core_transport_retry', False):
            return super().submit_model_task(case, key, agent, prompt)
        from _lib.runtime.behavior_timeout_retry import checked
        progress = self.progress(case)
        grant = checked(progress)
        binding = (grant or {}).get('binding', {})
        resume_session = binding.get('resume_session_id')
        if not resume_session:
            return super().submit_model_task(case, key, agent, prompt)
        reservation = next((row for row in reversed(
                progress.get('diagnostics', {}).get('model_task_reservations', []))
            if row.get('stage') == 'behavior'), {})
        if (grant.get('status') != 'consumed'
                or reservation.get('behavior_timeout_retry_binding') != grant['binding_sha256']
                or binding.get('failed_session', {}).get('session_id') != resume_session):
            raise ContractError('Core Behavior continuation authorization changed')
        continuation = ('继续本会话此前因运行时上限中断的同一冻结 Behavior 请求。'
                        '保留已经完成的证据读取和判断，不要从头重复；完成并写入原 candidate 路径后停止。'
                        '不得修改请求、Case 事实或证据要求。\n\n')
        return self.backend.submit(agent, continuation + prompt, session_id=resume_session)

    def _behavior(self, case):
        from _lib.behavior import input_binding
        from _lib.behavior.compile import evidence_frames, write_evidence_index
        from _lib.contracts.fidelity import enabled as fidelity_enabled, frame_index
        from _lib.contracts.vehicle_census import require_resolved
        from _lib.prompts import behavior_request_text
        from _lib.runtime import census_scope_recovery
        from _lib.runtime.execution_policy import require_active_policy

        _mode(case)
        if (case/'state/behavior.json').is_file():
            raise ContractError('Direct core approximation cannot use a strict Behavior checkpoint')
        progress = self.progress(case)
        progress['substage'] = 'behavior'
        self.save_progress(case, progress)
        require_resolved(case, allow_scope_pending=True)
        require_active_policy(read_json(case/'case.json')['parameters'])
        if input_binding.enabled(case) and (case/input_binding.REQUEST).exists():
            input_binding.verify(case)
        write_evidence_index(case)
        input_binding.freeze(case)
        frames = evidence_frames(case)
        if fidelity_enabled(case):
            frame_index(frames)
        input_binding.freeze_current(case)
        prompt = behavior_request_text(case) + census_scope_recovery.repair_prompt(case)
        candidate = case/'state/behavior.candidate.json'
        request_path = case/'state/behavior_repair.request.json'
        request = read_json(request_path) if request_path.is_file() else {}
        sessions = self.progress(case).get('sessions', [])
        origin = next((row for row in sessions if row.get('stage') == 'behavior'
                       and row.get('rule_id') == 'core_scene.source_coverage'
                       and row.get('actor_id') == request.get('actor_id')
                       and row.get('invalid_candidate_path') == request.get('base_path')), None)
        prior = next((row for row in reversed(sessions)
                      if row.get('stage') == 'behavior'), {})
        if (request.get('repair_contract') == 'behavior-source-coverage.v1'
                or (prior.get('status') == 'invalid_output'
                    and prior.get('rule_id') == 'core_scene.source_coverage')):
            from _lib.behavior.repair import repair_prompt
            source_failure = origin or prior
            error = ContractError(source_failure['message'],
                                  rule_id='core_scene.source_coverage',
                                  actor_id=source_failure.get('actor_id'))
            prompt = repair_prompt(case, candidate,
                                   source_failure['invalid_candidate_path'], error)
            if not prompt:
                raise ContractError('Core source coverage repair is not authorized')
        self.model_stage(case, 'behavior', 'global-video-understanding', prompt,
                         lambda _session=None: None, candidate_path=candidate)

    def model_stage(self, case, key, agent, prompt, validate_output,
                    precheck=None, candidate_path=None, *, max_attempts=None):
        if key != 'behavior':
            if getattr(self, '_core_transport_retry', False):
                raise ContractError('Core transport recovery cannot submit an upstream task')
            return super().model_stage(case, key, agent, prompt, validate_output,
                                       precheck, candidate_path, max_attempts=max_attempts)
        def capture(session=None):
            self.capture_behavior_candidate(case, candidate_path, session)

        from _lib.runtime.bounded_recovery import policy as recovery_policy
        attempts = self.progress(case).get('attempts', {}).get('behavior', 0)
        limit = (attempts + 1 if getattr(self, '_core_transport_retry', False)
                 else 1 + (recovery_policy(case) or {}).get('content_repairs', 0))
        return super().model_stage(case, key, agent, prompt, capture,
                                   precheck=capture if Path(candidate_path).is_file() else None,
                                   candidate_path=candidate_path, max_attempts=limit)


def _inventory(root):
    rows = []
    for path in sorted(Path(root).rglob('*')):
        name = path.relative_to(root).as_posix()
        if path.is_symlink():
            rows.append((name, 'link', os.readlink(path)))
        elif path.is_file():
            rows.append((name, 'file', sha256(path)))
    return hashlib.sha256(json.dumps(rows, separators=(',', ':')).encode()).hexdigest()


def _verified_synthetic_fallback(source):
    """Accept only the complete, source-independent fallback written by ensure()."""
    from _lib.delivery import synthetic_target

    target_path = source/'synthetic_delivery/target.json'
    receipt_path = source/'synthetic_delivery/manifest.json'
    if (target_path.parent.is_symlink() or target_path.is_symlink() or receipt_path.is_symlink()
            or not target_path.is_file() or not receipt_path.is_file()):
        raise ContractError('Incomplete core projection has an incomplete synthetic fallback')
    try:
        target, receipt = read_json(target_path), read_json(receipt_path)
    except (ValueError, UnicodeError) as exc:
        raise ContractError('Incomplete core projection has an invalid synthetic fallback') from exc
    required = {
        'revision': synthetic_target.REVISION, 'case_id': source.name,
        'delivery_level': 'synthetic_unverified', 'generation_status': 'generated',
        'target_generated': True, 'source_fidelity': 'not_established',
        'render_verification': 'not_executed', 'strict_validate_completed': 'not_claimed',
        'target_sha256': sha256(target_path),
    }
    analysis = source/'final/analysis.json'
    analysis_data = None
    if analysis.is_file():
        try:
            analysis_data = read_json(analysis)
        except (ValueError, UnicodeError):
            pass  # ensure() also records unknown actors for a broken Analysis.
    expected_ids = {
        'omitted_actor_ids': [row.get('actor_id') for row in analysis_data.get('actors', [])]
                             if isinstance(analysis_data, dict) else 'unknown',
        'omitted_event_ids': [row.get('event_id') for row in analysis_data.get('events', [])]
                             if isinstance(analysis_data, dict) else 'unknown',
    }
    required.update(expected_ids)
    required['synthetic_content'] = ['straight one-lane highway', 'one ego sedan',
                                     'ten-second straight motion', 'clear daytime environment']
    if (not isinstance(receipt, dict) or any(receipt.get(key) != value
            for key, value in required.items()) or receipt.get('analysis_sha256') != (
                sha256(analysis) if analysis.is_file() else None)
            or set(receipt) != set(required) | {'analysis_sha256', 'source_case_error'}
            or not isinstance(receipt.get('source_case_error'), str)
            or len(receipt['source_case_error']) > 1000
            or target != synthetic_target.build(source.name)):
        raise ContractError('Incomplete core projection has an invalid synthetic fallback')


def _archive_pre_repair_copy(source, root, isolated, candidate):
    """Keep an incomplete raw-answer copy before replaying its assigned patch."""
    if 'replacements' not in read_json(candidate):
        return
    request = read_json(source/'state/behavior_repair.request.json')
    base = Path(request['base_path'])
    old = isolated/'state/behavior.candidate.json'
    if old.is_file() and sha256(old) == sha256(candidate):
        return  # The isolated copy already contains this repaired answer.
    if (request.get('repair_contract') != 'behavior-source-coverage.v1'
            or not base.resolve().is_relative_to((source/'diagnostics').resolve())
            or not old.is_file() or sha256(old) != request['base_sha256']
            or (isolated/'final/analysis.json').exists()
            or (root/'approximate_output').exists()):
        raise ContractError('Incomplete core copy cannot be replayed after source repair')
    for name in ('case.json', 'manifest.json', 'state/binding.json',
                 'state/execution_policy.json', 'state/world_context.json',
                 'state/actor_registry.json', 'state/behavior_input.request.json',
                 'state/behavior_frames.json'):
        if sha256(source/name) != sha256(isolated/name):
            raise ContractError('Incomplete core copy changed a frozen source: ' + name)
    for path in (source/'batches').glob('batch_*.json'):
        if sha256(path) != sha256(isolated/'batches'/path.name):
            raise ContractError('Incomplete core copy changed a frozen Batch')
    archived = root/'diagnostics'/f'pre_source_repair.{request["base_sha256"][:16]}'
    archived.parent.mkdir(parents=True, exist_ok=True)
    if archived.exists():
        raise ContractError('Incomplete core copy was already archived')
    isolated.rename(archived)


def _incomplete_copy(source, root, isolated):
    """Recognize an interrupted pre-conversion projection without erasing it."""
    if (root/'approximate_output').exists():
        raise ContractError('Incomplete core projection has conversion output; manual review required')
    inputs = root/'inputs'
    if inputs.exists() and {p.relative_to(inputs).as_posix() for p in inputs.rglob('*')
                            if p.is_file() or p.is_symlink()} - {'full_observation_analysis.json'}:
        raise ContractError('Incomplete core projection has unexpected inputs')
    original = {p.relative_to(source).as_posix(): p for p in source.rglob('*')
                if p.is_file() or p.is_symlink()}
    copied = {p.relative_to(isolated).as_posix(): p for p in isolated.rglob('*')
              if p.is_file() or p.is_symlink()}
    added = set(copied) - set(original)
    permitted = {'final/analysis.json', 'state/analysis_reconciliation.request.json',
                 'state/analysis_reconciliation.json', 'state/analysis_reconciliation.candidate.json'}
    # ensure() may write this independent output after the Case was copied.
    # Check both files and their binding before excluding them from inputs.
    absent = set(original) - set(copied)
    fallback = {'synthetic_delivery/target.json', 'synthetic_delivery/manifest.json'}
    if absent & fallback:
        if absent & fallback != fallback:
            raise ContractError('Incomplete core projection has an incomplete synthetic fallback')
        _verified_synthetic_fallback(source)
    missing = absent - fallback
    if (missing or any(name not in permitted and not name.startswith(
            'diagnostics/invalid_candidates/analysis_reconciliation.') for name in added)):
        raise ContractError('Incomplete core projection is not an intact Case copy')
    if any(copied[name].is_symlink() for name in added):
        raise ContractError('Incomplete core projection contains a linked artifact')
    for name, path in original.items():
        clone = copied.get(name)
        if clone is None:
            # The verified fallback pair is the only permitted source-only output.
            continue
        if name == 'state/progress.json':
            before, after = read_json(path), read_json(clone)
            if (after.get('sessions', [])[:len(before.get('sessions', []))]
                    != before.get('sessions', []) or any(
                        row.get('stage') != 'analysis_reconciliation'
                        for row in after.get('sessions', [])[len(before.get('sessions', [])):])):
                raise ContractError('Incomplete core projection has unrelated model sessions')
            before.pop('sessions', None); after.pop('sessions', None)
            before.pop('updated_at', None); after.pop('updated_at', None)
            before_attempts = before.pop('attempts', {})
            after_attempts = after.pop('attempts', {})
            after_attempts.pop('analysis_reconciliation', None)
            if before_attempts != after_attempts:
                raise ContractError('Incomplete core projection changed unrelated attempts')
            before.pop('diagnostics', None); after.pop('diagnostics', None)
            if before != after:
                raise ContractError('Incomplete core projection changed Case progress')
            continue
        if (path.is_symlink() != clone.is_symlink()
                or (os.readlink(path) != os.readlink(clone) if path.is_symlink()
                    else sha256(path) != sha256(clone))):
            raise ContractError('Incomplete core projection differs from original Case: ' + name)
    analysis = isolated/'final/analysis.json'
    if analysis.is_symlink():
        raise ContractError('Incomplete core projection has linked Analysis checkpoint')
    return sha256(analysis) if analysis.is_file() else None


def _source_hashes(case, view, candidate, frames, *, adoption_source=None):
    paths = [case/'case.json', case/'state/binding.json',
             case/'state/execution_policy.json', case/'state/probe.json',
             case/'manifest.json', view.directory/'manifest.json',
             view.world_path, view.registry_path, candidate]
    mode = case/'state/core_approximate_mode.json'
    if mode.is_file():
        paths.append(mode)
        if 'replacements' in read_json(candidate):
            repair = case/'state/behavior_repair.request.json'
            request = read_json(repair)
            if request.get('repair_contract') != 'behavior-source-coverage.v1':
                raise ContractError('Core source repair request changed')
            paths.extend((repair, Path(request['base_path'])))
            if read_json(candidate).get('source_dispositions'):
                from _lib.behavior.source_revision import archived_receipt
                paths.append(archived_receipt(case, read_json(candidate)))
    elif adoption_source is not None:
        from _lib.behavior import input_binding
        paths.extend(adoption_source/name for name in
                     ('state/progress.json', input_binding.REQUEST, input_binding.SESSIONS))
        paths.extend(sorted((adoption_source/input_binding.VERSIONED_REQUESTS).glob('*.json')))
        repair = adoption_source/'state/behavior_repair.request.json'
        if repair.is_file():
            paths.append(repair)
    binding = read_json(case/'state/binding.json')
    source = Path(binding['source_path'])
    if sha256(source) != binding['source_sha256']:
        raise ContractError('Frozen source video changed')
    paths.append(source)
    paths.extend(case/'batches'/f"{row['batch_id']}.json"
                 for row in read_json(case/'manifest.json')['batches'])
    census = view.directory/'census_adjudication.accepted.json'
    if census.is_file():
        paths.append(census)
    paths.extend(sorted((case/'diagnostics/question_review').glob('observation-*.json')))
    for frame in frames:
        for variant in frame.get('variants', []):
            path = Path(variant['path'])
            if sha256(path) != variant['sha256']:
                raise ContractError('Frozen frame variant changed: ' + str(path))
            paths.append(path)
    return {str(path): sha256(path) for path in sorted(set(paths))}


def _projection(full, required, pending, identity_hypotheses=(), observation_deferrals=(),
                *, dispositions=None):
    core = copy.deepcopy(full)
    all_ids = {a['actor_id'] for a in full['actors']}
    omitted = all_ids - required
    core['actors'] = [a for a in core['actors'] if a['actor_id'] in required]
    retained, deferred = [], []
    exemptions = [*identity_hypotheses, *observation_deferrals]
    choices = {}
    if dispositions is not None:
        choices = {r['issue_id']: r for r in dispositions['choices']}
        exemptions.extend(disposition_deferrals(full, dispositions))
        for row in exemptions:
            index = row['source_limitation_index']
            bound_issue_ids = (set(row['issue_ids'])
                               | set(row.get('disposed_issue_ids', [])))
            if (type(index) is not int or not 0 <= index < len(full['limitations'])
                    or row['source_limitation_sha256'] != digest(full['limitations'][index])
                    or bound_issue_ids != set(full['limitations'][index].get('census_issue_ids', []))
                    or any(choices.get(iid, {}).get('handling') == 'needs_evidence'
                           for iid in row.get('disposed_issue_ids', []))):
                raise ContractError('Core limitation disposition source binding changed')
    assumed = {row['source_limitation_index'] for row in exemptions}
    for index, item in enumerate(core['limitations']):
        if index in assumed:
            continue  # Source limitation remains in full Analysis and receipt.
        ids = actor_references(item, all_ids)
        issues = set(item.get('census_issue_ids', []))
        if any(choices.get(iid, {}).get('handling', 'needs_evidence') != 'needs_evidence'
               for iid in issues):
            # A signed issue cannot authorize other issues, Actor disputes or
            # dependencies in this same source limitation. Whole-item formal
            # authorization was checked above, before Actor scope heuristics.
            retained.append(item)
            continue
        event_or_road = any(item.get(k) for k in
                            ('affected_event_ids', 'event_ids', 'event_id',
                             'world_paths', 'related_world_paths', 'scope_binding'))
        peripheral_actor = bool(ids) and ids <= omitted
        unresolved_observation = bool(issues) and issues <= set(pending) and not (ids & required)
        if (peripheral_actor or unresolved_observation) and not event_or_road:
            deferred.append({'source_index': index, 'limitation': item})
        else:
            retained.append(item)
    core['limitations'] = retained
    quality = core['quality_report']
    execution = quality.get('execution_issues', [])
    quality['execution_issues'] = [i for i in execution if i.get('actor_id') not in omitted]
    deferred_issues = [i for i in execution if i.get('actor_id') in omitted]
    return core, deferred, deferred_issues


def _candidate(case):
    progress = read_json(case/'state/progress.json')
    direct = progress.get('diagnostics', {}).get('core_approximate_candidate')
    if direct is not None:
        from _lib.behavior import input_binding
        fingerprint = direct.get('sha256')
        path = case/'diagnostics/candidate_snapshots'/f'behavior.{fingerprint}.json'
        behavior = [row for row in progress.get('sessions', []) if row.get('stage') == 'behavior']
        if (direct.get('revision') != RAW_CANDIDATE or not re.fullmatch(r'[0-9a-f]{64}', fingerprint or '')
                or progress.get('phase') != 'aggregating' or (case/'state/behavior.json').exists()
                or not behavior or behavior[-1].get('status') != 'completed'
                or behavior[-1].get('session_id') != direct.get('session_id')
                or sha256(path) != fingerprint
                or current_facts(case).version_id != direct.get('fact_version')
                or input_binding.active_request(case)['request_sha256'] != direct.get('source_request_sha256')
                or (direct.get('source_repair_request_sha256') is not None and
                    sha256(case/'state/behavior_repair.request.json') !=
                    direct['source_repair_request_sha256'])):
            raise ContractError('Direct core approximation Behavior candidate changed')
        input_binding.require_session(case, direct['session_id'])
        if direct.get('source_revision_receipt'):
            from _lib.behavior.source_revision import verify_archive
            verify_archive(case, direct['source_revision_receipt'], direct['source_revision_receipt_sha256'])
        return path
    return _failed_behavior_archive(case, progress)


def _queue_source_repair(runner, case):
    """Reclassify a previously captured raw answer by its frozen source gate."""
    progress = runner.progress(case)
    if not progress.get('diagnostics', {}).get('core_approximate_candidate'):
        return False
    archive = _candidate(case)
    try:
        _check_source_candidate(case, read_json(archive))
        return False
    except ContractError as error:
        if error.rule_id != 'core_scene.source_coverage' or not error.actor_id:
            raise
        fault = error
    current = case/'state/behavior.candidate.json'
    if not current.is_file() or sha256(current) != sha256(archive):
        raise ContractError('Core source repair requires the unchanged original candidate')
    producer = next(row for row in reversed(progress['sessions'])
                    if row.get('stage') == 'behavior')
    invalid = quarantine_candidate(case, 'behavior', current, producer['attempt'])
    producer.update(status='invalid_output', error_code='schema_validation_failed',
                    message=str(fault)[:500], candidate_validation_error=str(fault)[:4000],
                    rule_id=fault.rule_id, actor_id=fault.actor_id,
                    invalid_candidate_path=str(invalid))
    progress['diagnostics'].pop('core_approximate_candidate')
    runner.save_progress(case, progress)
    return True


def _failed_behavior_archive(case, progress=None):
    """Reuse the existing terminal, request-bound full-answer archive selector."""
    from _lib.behavior import input_binding
    progress = progress or read_json(case/'state/progress.json')
    failure = progress.get('error') or {}
    if (progress.get('phase') != 'failed' or failure.get('stage') != 'behavior'
            or (case/'state/behavior.json').is_file()
            or not input_binding.enabled(case)
            or any(row.get('status') in {'submitted', 'pending', 'running'}
                   for row in progress.get('sessions', []))):
        raise ContractError('Core approximation requires terminal failed Behavior and no accepted checkpoint')
    archives = input_binding.replayable_archives(case)
    if not archives:
        raise ContractError('Core approximation has no complete source-bound Behavior answer')
    return archives[0]


def _reconcile_deadline_candidate(runner, case, progress):
    """Resolve the original producer before choosing its candidate or a retry."""
    from _lib.behavior import input_binding
    from _lib.runtime import bounded_recovery as budget

    sessions = progress.get('sessions', [])
    last = sessions[-1] if sessions else {}
    if (progress.get('phase') != 'failed' or (progress.get('error') or {}).get('stage') != 'behavior'
            or last.get('stage') != 'behavior' or last.get('status') != 'failed'
            or last.get('error_code') != 'task_deadline_exceeded'
            or last.get('deadline_task_resolved')):
        return None
    _mode(case)
    input_binding.verify(case)
    input_binding.require_session(case, last.get('session_id'))
    session_requests = read_json(case/input_binding.SESSIONS)
    if session_requests.get(last.get('session_id')) != input_binding.active_request(case)['request_sha256']:
        raise ContractError('Core Behavior candidate belongs to an older frozen request')
    reservation = next((row for row in reversed(progress.get('diagnostics', {}).get('model_task_reservations', []))
                        if row.get('stage') == 'behavior'), None)
    if (not last.get('task_id') or reservation is None
            or reservation.get('task_id') != last['task_id']
            or reservation.get('recovery_request') != budget.request_digest(case, 'behavior')):
        raise ContractError('Core Behavior reservation/request binding changed')
    remote = runner.backend.poll(last['task_id'], 'global-video-understanding')
    if remote.get('status') not in {'completed', 'failed'}:
        raise ContractError('Core Behavior original task is still active')
    if remote.get('session_id') and remote['session_id'] != last['session_id']:
        raise ContractError('Core Behavior original task returned another session')
    candidate = case/'state/behavior.candidate.json'
    if remote['status'] == 'completed':
        if not candidate.is_file():
            raise ContractError('Completed Core Behavior task has no candidate')
        last['status'] = 'completed'
        last['deadline_task_resolved'] = True
        progress.update(phase='aggregating', status='aggregating', error=None)
        runner.save_progress(case, progress)
        runner.capture_behavior_candidate(case, candidate, last['session_id'])
        return 'completed'
    last['deadline_task_resolved'] = True
    last['remote_error_code'] = remote.get('error') or 'unknown'
    if last['remote_error_code'] not in {'timeout', 'task_deadline_exceeded'}:
        runner.save_progress(case, progress)
        raise ContractError('Core Behavior original task failed for a non-transport reason')
    if candidate.is_file():
        fingerprint = sha256(candidate)
        archived = case/'diagnostics/invalid_candidates'/f"behavior.attempt_{last['attempt']}.{fingerprint[:16]}.json"
        archived.parent.mkdir(parents=True, exist_ok=True)
        if archived.is_file():
            if sha256(archived) != fingerprint:
                raise ContractError('Core Behavior candidate archive changed')
        else:
            with tempfile.NamedTemporaryFile(dir=archived.parent, delete=False) as stream:
                temporary = Path(stream.name)
                try:
                    with candidate.open('rb') as source:
                        shutil.copyfileobj(source, stream)
                    stream.flush()
                    os.fsync(stream.fileno())
                    if sha256(temporary) != fingerprint:
                        raise ContractError('Core Behavior candidate changed during archive')
                    temporary.replace(archived)
                finally:
                    temporary.unlink(missing_ok=True)
        last['invalid_candidate_path'] = str(archived)
    runner.save_progress(case, progress)
    return 'failed'


def _require_transport_retry(runner, case, progress):
    """Admit one explicit Behavior retry; the model loop reconciles its old task."""
    from _lib.behavior import input_binding
    from _lib.runtime import bounded_recovery as budget

    _mode(case)
    sessions = progress.get('sessions', [])
    last = sessions[-1] if sessions else {}
    failure = progress.get('error') or {}
    if (progress.get('phase') != 'failed' or progress.get('status') != 'failed'
            or failure.get('stage') != 'behavior' or last.get('stage') != 'behavior'
            or last.get('status') != 'failed' or not last.get('task_id')
            or not last.get('session_id') or last.get('error_code') not in budget.TRANSPORT
            or last.get('remote_error_code', 'timeout') not in {'timeout', 'task_deadline_exceeded'}
            or budget.failure_kind(last) != 'transport'
            or last.get('invalid_candidate_path') or last.get('candidate_validation_error')
            or progress.get('artifacts') or (case/'state/behavior.json').exists()
            or (case/'final/analysis.json').exists()
            or runner.recovery_phase(case) != 'aggregating'):
        raise ContractError('Core Behavior transport retry requires a failed frozen Behavior task')
    if (progress.get('diagnostics', {}).get('unresolved_submissions')
            or any(row.get('status') in {'submitted', 'pending', 'running', 'returned'}
                   for row in sessions)
            or any(row.get('error_code') in {'task_deadline_exceeded', 'task_status_unavailable'}
                   and not row.get('deadline_task_resolved') for row in sessions[:-1])):
        raise ContractError('Core Behavior task status must be reconciled before retry')
    request = input_binding.verify(case)
    if request is None:
        raise ContractError('Core Behavior has no frozen request')
    input_binding.require_session(case, last['session_id'])
    reservation = next((row for row in reversed(progress.get('diagnostics', {}).get('model_task_reservations', []))
                        if row.get('stage') == 'behavior'), None)
    if (reservation is None or reservation.get('task_id') != last['task_id']
            or reservation.get('recovery_request') != budget.request_digest(case, 'behavior')):
        raise ContractError('Core Behavior reservation/request binding changed')
    if input_binding.replayable_archives(case):
        raise ContractError('Core Behavior already has a complete source-bound answer')
    candidate = case/'state/behavior.candidate.json'
    if candidate.is_file():
        try:
            raw = read_json(candidate)
        except (ValueError, UnicodeError):
            raw = None
        if input_binding.is_complete_behavior(raw):
            raise ContractError('Core Behavior already has a complete candidate')
    limits = budget.policy(case)
    used = sum(row['recovery_group'] == 'behavior' and row['recovery_kind'] == 'transport'
               for row in budget.entries(progress))
    from _lib.runtime.behavior_timeout_retry import checked as timeout_grant
    grant = timeout_grant(progress)
    if grant and grant.get('status') == 'consumed':
        grant = None
    if (limits is None or budget.remaining(case, progress) < 1
            or used >= limits['transport_retries'] and not grant):
        if limits is not None and budget.remaining(case, progress) >= 1:
            progress['error'] = dict(
                stage='behavior', code='bounded_recovery_transport_exhausted',
                message='bounded_recovery_transport_exhausted', resume_from='behavior',
                original_error=copy.deepcopy(failure))
            runner.save_progress(case, progress)
        raise ContractError('Core Behavior transport recovery budget exhausted')


def _abandoned_first_behavior_submission(case, progress):
    """A proved pre-dispatch rejection can retry the same frozen first request."""
    from _lib.behavior import input_binding
    from _lib.runtime import bounded_recovery as budget

    failure = progress.get('error') or {}
    rows = [row for row in progress.get('diagnostics', {}).get('model_task_reservations', [])
            if row.get('stage') == 'behavior']
    if (progress.get('phase') != 'failed' or failure.get('stage') != 'behavior'
            or failure.get('code') != 'backend_unavailable'
            or progress.get('attempts', {}).get('behavior', 0) != 0
            or any(row.get('stage') == 'behavior' for row in progress.get('sessions', []))
            or progress.get('diagnostics', {}).get('unresolved_submissions')
            or len(rows) != 1 or rows[0].get('status') != 'abandoned_before_task_id'
            or rows[0].get('task_id') or rows[0].get('session_id')
            or rows[0].get('recovery_request') != budget.request_digest(case, 'behavior')
            or (case/'state/behavior.candidate.json').exists()
            or (case/'state/behavior.json').exists()
            or (case/'final/analysis.json').exists()
            or input_binding.replayable_archives(case)):
        return False
    return input_binding.verify(case) is not None


def _mode(case):
    path = case/'state/core_approximate_mode.json'
    mode = read_json(path)
    binding = read_json(case/'state/binding.json')
    if (mode.get('revision') != REVISION or mode.get('case_id') != case.name
            or mode.get('source_sha256') != binding['source_sha256']
            or mode.get('parameters_sha256') != digest(read_json(case/'case.json')['parameters'])):
        raise ContractError('Core approximation mode binding changed')
    return mode


def select_case(video, parameters, *, new=False, case_id=None):
    from _lib.contracts.v3 import normalize_parameters
    parameters = normalize_parameters(parameters)
    cases = SCENARIO_ROOT/'video_extractor/cases'
    if new == bool(case_id):
        raise ContractError('Core approximation requires --new or an existing --case-id')
    if new:
        case, created = Cases(cases).bind(video, parameters, force_new=True)
        if not created:
            raise ContractError('Core approximation expected a fresh Case')
        binding = read_json(case/'state/binding.json')
        atomic_json(case/'state/core_approximate_mode.json', {
            'revision': REVISION, 'case_id': case.name,
            'source_sha256': binding['source_sha256'],
            'parameters_sha256': digest(read_json(case/'case.json')['parameters'])})
    else:
        case = (cases/case_id).resolve(strict=True)
        if case.parent != cases.resolve() or not (case/'case.json').is_file():
            raise ContractError('Unknown core approximation Case')
    if (case/'state/core_approximate_mode.json').is_file():
        _mode(case)
    else:
        # Explicit --case-id --core-scene-approximate may read a failed strict
        # Behavior answer; no mode, fact, certificate or progress is rewritten.
        _failed_behavior_archive(case)
    binding = read_json(case/'state/binding.json')
    if Path(video).resolve() != Path(binding['source_path']).resolve() or sha256(video) != binding['source_sha256']:
        raise ContractError('Core approximation source video changed')
    if digest(parameters) != digest(read_json(case/'case.json')['parameters']):
        raise ContractError('Core approximation Case parameters changed')
    return case


def prepare_source_behavior(source, view, raw, frames):
    """Replay normalization against the owner of frozen supplemental receipts.

    A copied Case retains absolute request paths. Its source reviews must be
    verified by their original owner, just like a bounded repair request.
    Shared preparation applies those reviews exactly once and keeps the audit.
    """
    return prepare_behavior_candidate(materialize(source, raw), frames,
                                      view.registry, view.world, case=source)


def _project(source, root, runner=None):
    """Project a bound raw Behavior answer without accepting strict Behavior."""
    receipt_path = root/'inputs/projection_receipt.json'
    isolated = root/'cases'/source.name
    if receipt_path.is_file():
        receipt = read_json(receipt_path)
        if receipt.get('revision') not in {'core-approximate-probe.v1',
                                           'core-approximate-probe.v2', PROJECTION_REVISION}:
            raise ContractError('Core projection receipt changed')
        for name, expected in receipt['source_hashes'].items():
            if sha256(name) != expected:
                raise ContractError('Core projection source changed: ' + name)
            path = Path(name)
            if path.is_relative_to(isolated):
                original = source/path.relative_to(isolated)
                if sha256(original) != expected:
                    raise ContractError('Original Case source changed after projection: ' + str(original))
        if sha256(isolated/'final/analysis.json') != receipt['core_analysis_sha256']:
            raise ContractError('Core Analysis checkpoint changed')
        view = current_facts(isolated, receipt['fact_version'])
        raw = read_json(_candidate(source))
        _, review = _source_candidate_parts(source, raw)
        behavior, _ = prepare_source_behavior(source, view, split(raw)[0], evidence_frames(isolated))
        from _lib.contracts.core_lane_continuity import validate_analysis_motion
        projection_source = None
        if receipt.get('source_projection_disposition') is not None:
            full_path = root/'inputs/full_observation_analysis.json'
            if sha256(full_path) != receipt['full_analysis_sha256']:
                raise ContractError('Full observation Analysis changed after core projection')
            projection_source = dict(full_analysis=read_json(full_path),
                disposition=receipt['source_projection_disposition'], review=review,
                representatives=receipt.get('representative_actors', []))
        validate_analysis_motion(isolated, behavior,
                                 read_json(isolated/'final/analysis.json'),
                                 projection_source=projection_source)
        return isolated
    candidate = _candidate(source)
    if isolated.exists():
        _archive_pre_repair_copy(source, root, isolated, candidate)
    prior_incomplete_analysis = (_incomplete_copy(source, root, isolated)
                                 if isolated.exists() else None)
    adopted = not (source/'state/core_approximate_mode.json').is_file()
    protected = (_inventory(source), _inventory(SCENARIO_ROOT/'video_extractor/media/output'))
    isolated.parent.mkdir(parents=True, exist_ok=True)
    if not isolated.exists():
        shutil.copytree(source, isolated)
    copied = isolated/candidate.relative_to(source)
    view = current_facts(isolated)
    world, registry = copy.deepcopy(view.world), view.registry
    frames = evidence_frames(isolated)
    from _lib.behavior.input_binding import current_evidence_timeline, timeline_enabled
    evidence_timeline = current_evidence_timeline(isolated) if timeline_enabled(isolated) else None
    raw = read_json(copied)
    _, review = _source_candidate_parts(source, raw)
    behavior, normalizations = prepare_source_behavior(source, view, split(raw)[0], frames)
    validate_compiled_behavior(behavior, world, registry, frames,
                               execution=False, allow_pending_identity=True)
    from _lib.contracts.core_lane_continuity import validate as validate_core_lane_continuity
    validate_core_lane_continuity(isolated, behavior)
    saved = census_adjudication.proof(isolated)
    pending = behavior_census_scope.pending(isolated, saved)
    core_ids = required_actors(world, behavior, ['ego'])
    if 'ego' not in core_ids:
        raise ContractError('No evidence-backed ego core')
    from _lib.contracts.census_scope import ScopeReviewCoverageError
    try:
        dispositions = validated_dispositions(isolated, behavior, pending, review, frames)
    except ScopeReviewCoverageError as error:
        if not _noncore_scope_coverage(error, pending, review, core_ids):
            raise
        raise NoncoreScopeEvidenceIncomplete(str(error), rule_id=error.rule_id) from error
    params = read_json(isolated/'case.json')['parameters']
    execution_world, execution_behavior, representatives = representative_projection(
        isolated, world, behavior, frames, pending, review, dispositions)
    reconstruction = bounded_execution.build(
        isolated, saved, behavior, pending=pending,
        frames={frame['frame_id']: frame for frame in frames}, review=review)
    if reconstruction is not None:
        # representative_projection intentionally returns its inputs unchanged
        # when it has no representatives.  Execution metadata belongs only to
        # the private Builder input; never mutate the frozen source World used
        # by the projection receipt and replay validator.
        execution_world = copy.deepcopy(execution_world)
        execution_world['world'].setdefault('metadata', {})[
            bounded_execution.KEY] = reconstruction
    representative_ids = {row['actor_id'] for row in representatives}
    core_ids.update(representative_ids)
    core_ids.update(core_projection_sources.required_background_actors(
        execution_world, execution_behavior, frames, evidence_timeline))
    execution_registry = [*registry, *(row for row in execution_world.get('actor_hypotheses', [])
                                     if row['actor_id'] in representative_ids)]
    full, builder_status = build_analysis(
        execution_world, execution_behavior, params,
        observation_issues=execution_issues(execution_behavior, execution_world, execution_registry, frames),
        observation_contract=True,
        media_probe=read_json(isolated/'state/probe.json')['probe'])
    if runner is not None:
        # The identity question belongs to the accepted raw Behavior. Keep
        # that source binding stable for an independent description fallback.
        full = runner.reconcile_analysis(isolated, full, behavior)
    from _lib.contracts.core_lane_continuity import validate_analysis_motion
    validate_analysis_motion(isolated, behavior, full)
    gate_degradations(full['quality_report'])
    validate_analysis(full, execution_registry)
    retained_scope_ids = (core_projection_sources.execution_required_actors(
        world, behavior, full, evidence_timeline) if evidence_timeline is not None else set())
    core_ids.update(retained_scope_ids)
    inputs = root/'inputs'
    inputs.mkdir(parents=True, exist_ok=True)
    full_path = inputs/'full_observation_analysis.json'
    atomic_json(full_path, full)
    risks = disposition_risks(pending, review, dispositions)
    identity_hypotheses = core_identity_hypothesis.select(
        isolated, world, registry, full, risks, saved, core_ids)
    observation_deferrals = core_identity_hypothesis.observation_only_deferrals(full, risks)
    source_disposition = (core_projection_sources.make(
        isolated, world, behavior, full, core_ids, frames,
        evidence_timeline, review, representatives)
        if evidence_timeline is not None else None)
    core, deferred, deferred_issues = _projection(
        full, core_ids, pending, identity_hypotheses, observation_deferrals,
        dispositions=dispositions)
    identity_context = core_identity_hypothesis.execution_context(
        identity_hypotheses, observation_deferrals)
    if identity_context is not None:
        core.setdefault('metadata', {})['core_identity_execution_context'] = identity_context
    source_hashes = _source_hashes(
        isolated, view, copied, frames, adoption_source=source if adopted else None)
    if source_disposition is not None and reconstruction is not None:
        core.setdefault('metadata', {})[
            core_projection_sources.ANALYSIS_BINDING_KEY] = core_projection_sources.bind_analysis(
                isolated, source, copied, view.version_id, source_hashes,
                behavior, normalizations, full_path, source_disposition, representatives)
    validate_analysis(core)
    validate_analysis_motion(isolated, behavior, core,
        projection_source=(dict(full_analysis=full, disposition=source_disposition,
                                review=review, representatives=representatives)
                           if source_disposition is not None else None))
    lane_change = from_analysis(core)
    if lane_change is None or lane_change.actor_id not in core_ids:
        raise ContractError('Core action lacks an evidence-bound lane change')
    core_path = isolated/'final/analysis.json'
    if core_path.is_file() and prior_incomplete_analysis is None and read_json(core_path) != core:
        raise ContractError('Existing strict Analysis differs from core projection')
    atomic_json(core_path, core)
    strict_v3_preflight(core, params['road_source'])
    request = semantic_motion.request(core, sha256(core_path))
    coverage, relations = bounded_execution.complete_relations(request, [], [])
    coverage, relations = retained_motion.complete_relations(request, coverage, relations)
    coverage, relations = motion_witnesses.complete(request, coverage, relations)
    uncovered = sorted({r['source'] for r in request['evidence']}
                       - {r['source'] for r in coverage})
    plan = bounded_execution.plan(request, relations) if not uncovered else None
    receipt = {
        'revision': PROJECTION_REVISION, 'fact_version': view.version_id,
        'source_hashes': source_hashes,
        'raw_candidate_sha256': sha256(copied),
        'normalized_behavior_digest': digest(behavior), 'normalizations': normalizations,
        'census_certificate_digest': saved['sha256'],
        'census_dispositions_digest': dispositions['sha256'],
        **({'source_projection_disposition': source_disposition}
           if source_disposition is not None else {}),
        'core_actor_ids': sorted(core_ids),
        'retained_execution_relevant_actor_ids': sorted(retained_scope_ids),
        'omitted_actor_ids': sorted({a['actor_id'] for a in behavior['actors']} - core_ids),
        'observation_risks': risks,
        'identity_hypotheses': identity_hypotheses,
        'observation_only_identity_deferrals': observation_deferrals,
        'disposition_limitations': disposition_deferrals(full, dispositions),
        **({'representative_actors': representatives} if representatives else {}),
        **({'identity_execution_context_sha256': digest(identity_context)}
           if identity_context is not None else {}),
        'unresolved_issue_ids': [r['issue_id'] for r in dispositions['choices']
                                 if r['handling'] == 'needs_evidence'],
        'deferred_limitations': deferred, 'deferred_execution_issues': deferred_issues,
        'fidelity_loss': 'Only selected core Actors appear in executable Analysis; source observations remain frozen and bound above.',
        'simulation_assumptions': {'builder_defaults': core['quality_report'].get('applied_defaults', []),
                                   'converter_defaults': [], 'motion_plans': []},
        'use_restrictions': ['Not a complete video reconstruction',
                             'Not a proof of target-lane clearance or collision safety against omitted vehicles']
                            + (['Core vehicle identity is a reversible execution hypothesis, '
                                'not a formal identity finding'] if identity_hypotheses else [])
                            + (['Observation-only horizon sightings have no chosen Actor identity or lane']
                               if observation_deferrals else []),
        'builder_status': builder_status, 'full_analysis_sha256': sha256(full_path),
        'core_analysis_sha256': sha256(core_path), 'lane_change': vars(lane_change),
        'converter_preflight': 'passed', 'semantic_request_sha256': request['request_sha256'],
        'semantic_eligible_actor_ids': [a['actor_id'] for a in request['eligible']],
        'semantic_evidence_count': len(request['evidence']),
        'uncovered_relations': uncovered,
        'bounded_plan': 'not_available_without_bound_request' if plan is None else 'available',
        'approximate_target_generated': False, 'approximate_acceptance': 'not_run',
        'strict_completion': 'not_attempted_or_modified',
    }
    if prior_incomplete_analysis is not None:
        receipt['replaced_incomplete_analysis_sha256'] = prior_incomplete_analysis
    if adopted:
        from _lib.behavior.input_binding import active_request
        producer = next(row for row in read_json(source/'state/progress.json')['sessions']
                        if row.get('invalid_candidate_path') == str(candidate))
        receipt['behavior_archive_adoption'] = {
            'case_id': source.name, 'task_id': producer['task_id'],
            'session_id': producer['session_id'],
            'request_sha256': active_request(source)['request_sha256'],
            'candidate_sha256': sha256(candidate),
            'original_case_progress': 'unchanged',
        }
    if (_inventory(source), _inventory(SCENARIO_ROOT/'video_extractor/media/output')) != protected:
        raise ContractError('Strict Case or formal output changed during core projection')
    receipt['protected_inputs_unchanged'] = True
    atomic_json(receipt_path, receipt)
    return isolated


def _convert(case, root):
    analysis = case/'final/analysis.json'
    request_path = case/'state/conversion_semantic_gap.request.json'
    data = read_json(analysis)
    witness_revision = (read_json(request_path).get('policy', {}).get(motion_witnesses.POLICY_KEY)
                        if request_path.is_file() else
                        motion_witnesses.REVISION if data.get('metadata', {}).get(bounded_execution.KEY)
                        else None)
    request = semantic_motion.request(data, sha256(analysis), witness_revision=witness_revision)
    if request_path.is_file() and read_json(request_path) != request:
        raise ContractError('Core conversion frozen request changed')
    if not request_path.is_file():
        atomic_json(request_path, request)
    return request


def run(runner, video, parameters, *, new=False, case_id=None, retry_failed=False,
        reauthorize_behavior_after_timeout=None):
    """Run normal visual stages only through the first Behavior candidate."""
    case = select_case(video, parameters, new=new, case_id=case_id)
    from _lib.contracts.v3 import normalize_parameters
    parameters = normalize_parameters(parameters)
    try:
        if reauthorize_behavior_after_timeout:
            from _lib.runtime.behavior_timeout_retry import authorize
            authorize(runner, case, reauthorize_behavior_after_timeout)
        result = _run_selected(runner, video, parameters, case, retry_failed=retry_failed)
        if (result.get('generation_status') == 'not_generated'
                and (result.get('phase') == 'failed' or result.get('status') in {'failed','needs_review'})):
            from _lib.delivery.synthetic_target import ensure
            policy = case/'state/execution_policy.json'
            if policy.is_file() and read_json(policy).get('synthetic_target') == 'synthetic-target.v1':
                fallback = ensure(case, (result.get('error') or {}).get('message', 'Core scene failed'))
                from _lib.delivery.synthetic_target import public_result
                return public_result(result, fallback)
        from _lib.delivery.synthetic_target import public_result
        return public_result(result)
    except Exception as error:
        result = dict(case_id=case.name, status='failed',
                    delivery_level='core_scene_approximate',
                    generation_status='not_generated',
                    error={'code': getattr(error, 'code', type(error).__name__),
                           'message': str(error)[:1800],
                           'rule_id': getattr(error, 'rule_id', None)},
                    strict_validate_completed='not_claimed')
        policy = case/'state/execution_policy.json'
        if policy.is_file() and read_json(policy).get('synthetic_target') == 'synthetic-target.v1':
            try:
                from _lib.delivery.synthetic_target import ensure
                fallback = ensure(case, error)
                from _lib.delivery.synthetic_target import public_result
                result = public_result(result, fallback)
            except Exception as fallback_error:
                result['synthetic_fallback_error'] = str(fallback_error)[:500]
        return result


def _run_selected(runner, video, parameters, case, *, retry_failed=False):
    root = SCENARIO_ROOT/'video_extractor'/ROOT_NAME/case.name
    manifest = root/'approximate_output/manifest.json'
    progress = runner.progress(case)
    if progress.get('status') in {'done', 'done_degraded'}:
        raise ContractError('Strictly completed Case cannot become a core approximation')
    failed_behavior = (progress.get('phase') == 'failed'
                       and (progress.get('error') or {}).get('stage') == 'behavior')
    if not manifest.is_file() and not (root/'inputs/projection_receipt.json').is_file():
        from _lib.behavior import input_binding
        reconciled = (_reconcile_deadline_candidate(runner, case, progress)
                      if (case/'state/core_approximate_mode.json').is_file() else None)
        if reconciled is not None:
            progress = runner.progress(case)
        if reconciled == 'completed' or (progress['phase'] == 'failed'
                                         and failed_behavior
                                         and input_binding.replayable_archives(case)):
            retry_failed = False
    if retry_failed:
        if manifest.is_file() or (root/'inputs/projection_receipt.json').is_file():
            raise ContractError('Core transport retry cannot reopen a projected delivery')
        if not (case/'state/core_approximate_mode.json').is_file():
            raise ContractError('Core transport retry requires a frozen core approximation Case')
        if failed_behavior:
            if not _abandoned_first_behavior_submission(case, progress):
                _require_transport_retry(runner, case, progress)
                runner._core_transport_retry = True
        try:
            result = runner.run_v3(video, case_id=case.name, parameters=parameters,
                                   retry_failed=True, stop_after_behavior_candidate=True)
        finally:
            if failed_behavior:
                runner._core_transport_retry = False
        if result.get('phase') == 'failed':
            return dict(result, delivery_level='core_scene_approximate',
                        generation_status='not_generated', strict_validate_completed='not_claimed')
        progress = runner.progress(case)
    if not manifest.is_file() and not (root/'inputs/projection_receipt.json').is_file():
        result = progress
        if not progress.get('diagnostics', {}).get('core_approximate_candidate') and progress['phase'] != 'failed':
            request_path = case/'state/behavior_repair.request.json'
            request = read_json(request_path) if request_path.is_file() else {}
            last_behavior = next((row for row in reversed(progress.get('sessions', []))
                                  if row.get('stage') == 'behavior'), {})
            source_pending = (last_behavior.get('status') == 'invalid_output'
                              and last_behavior.get('rule_id') == 'core_scene.source_coverage'
                              and last_behavior.get('actor_id')
                              and last_behavior.get('invalid_candidate_path'))
            if request.get('repair_contract') == 'behavior-source-coverage.v1' or source_pending:
                runner._behavior(case)
                result = runner.progress(case)
            else:
                result = runner.run_v3(video, case_id=case.name, parameters=parameters,
                                       stop_after_behavior_candidate=True)
            if result.get('phase') not in {'aggregating', 'failed'}:
                return dict(result, delivery_level='core_scene_approximate',
                            approximate_status='waiting_for_behavior_candidate')
        progress = runner.progress(case)
        if (progress['phase'] == 'failed'
                and not progress.get('diagnostics', {}).get('core_approximate_candidate')
                and (progress.get('error') or {}).get('stage') != 'behavior'):
            # A failed World/Batch producer has no Behavior answer to adopt.
            # Preserve its failure envelope instead of masking it with an
            # unrelated archive-selection error, including on resume.
            return dict(result, delivery_level='core_scene_approximate',
                        generation_status='not_generated', strict_validate_completed='not_claimed')
        if progress['phase'] not in {'aggregating', 'failed'} or any(
                row.get('status') in {'submitted', 'pending', 'running'}
                for row in progress.get('sessions', [])):
            raise ContractError('Core approximation requires a terminal Behavior task')
        _candidate(case)  # A failed World/Batch/transport Case cannot enter this mode.
        if _queue_source_repair(runner, case):
            runner._behavior(case)
    isolated = _project(case, root, runner)
    if manifest.is_file():
        action = 'check'
    else:
        request = _convert(isolated, root)
        run_conversion(runner, isolated, request)
        from _lib.runtime.conversion_work import validate_committed
        decision = read_json(isolated/'state/conversion_semantic_gap.json')
        validate_committed(isolated, request, decision)
        output = root/'approximate_output'
        output.mkdir(parents=True, exist_ok=True)
        target, result_path = output/'target.json', output/'conversion_result.json'
        policy = freeze(isolated, sha256(isolated/'final/analysis.json'),
                        read_json(isolated/'final/analysis.json')['scene_type'])
        params = read_json(isolated/'case.json')['parameters']
        command = [sys.executable,
                   str(SCENARIO_ROOT/'json-coverter/skills/case-conversion/scripts/process.py'),
                   '--input', str(isolated/'final/analysis.json'), '--output', str(target),
                   '--scene-type', read_json(isolated/'final/analysis.json')['scene_type'],
                   '--result', str(result_path), '--workflow-contract', 'video-case-world-behavior',
                   '--road-source', params['road_source'], '--policy', str(policy),
                   '--policy-sha256', read_json(policy)['policy_sha256'],
                   '--semantic-gap', str(isolated/'state/conversion_semantic_gap.json')]
        completed = subprocess.run(command, capture_output=True, text=True, timeout=120)
        if completed.returncode:
            raise ContractError('Core approximation original Converter failed: ' +
                                (completed.stderr or completed.stdout)[-1500:])
        action = 'accept'
    accepted = subprocess.run([
        sys.executable, str(SKILLS/'video-case-orchestrator/scripts/accept_core_approximate_target.py'),
        action, '--isolation-root', str(root), '--case-dir', str(isolated)],
        capture_output=True, text=True, timeout=120)
    if accepted.returncode:
        raise ContractError('Core approximation independent acceptance failed: ' +
                            (accepted.stderr or accepted.stdout)[-1500:])
    outcome = json.loads(accepted.stdout)
    if outcome.get('status') != 'accepted':
        raise ContractError('Core approximation acceptance did not finish')
    return dict(case_id=case.name, status='accepted', delivery_level='core_scene_approximate',
                target_path=str(root/'approximate_output/target.json'),
                manifest=outcome['manifest'], report=outcome['report'],
                target_sha256=outcome['target_sha256'], unresolved=outcome['unresolved'],
                strict_validate_completed='not_claimed')
