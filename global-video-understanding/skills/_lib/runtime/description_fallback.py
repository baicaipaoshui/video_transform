"""Independent unverified delivery using archived text and the original Converter.

prepare/check never submit or poll a model. Only explicit resume (or a user-run
new pipeline's auto route) may invoke the original json-coverter worker.
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from _lib.contracts import description_draft as contract, semantic_motion
from _lib.contracts.conversion_policy import digest, freeze
from _lib.runtime.case_core import atomic_json, read_json, sha256
from _lib.paths import SCENARIO_ROOT, SKILLS
from _lib.runtime.v3 import V3Orchestrator
from _lib.runtime.conversion_work import ConversionWork, STAGES, run as convert_work, validate_committed
from _lib.runtime import core_motion_recovery as motion_recovery

REVISION = contract.REVISION
ROOT = SCENARIO_ROOT/'video_extractor/description_fallback_deliveries'
DRAFT_STAGE = 'conversion_description_draft'
MINIMUM_ROOT = 'minimum_scene'
MOTION_ROOT = 'core_motion_revision'
SEMANTIC_ROOT = 'semantic_revision_v1'


def _semantic_revision(root):
    return root.name == SEMANTIC_ROOT or (root.name == MINIMUM_ROOT and root.parent.name == SEMANTIC_ROOT)


def _minimum_allowed(error):
    # Backend/transport failures must reconcile their original task. They do
    # not authorize another model task in a smaller scene.
    return (isinstance(error, (ValueError, KeyError, TypeError))
            and not isinstance(error, contract.SourceChanged))


def _project(root, bundle, full):
    from _lib.contracts.core_lane_continuity import validate_analysis_motion
    receipt = read_json(root/'inputs/source_receipt.json')
    source = Path(receipt['source_case'])
    source_behavior = bundle['behavior']
    full_omissions = validate_analysis_motion(source, source_behavior, full,
                                              allow_omission=root.name == MINIMUM_ROOT)
    result = (contract.minimum_scene(bundle, full) if root.name == MINIMUM_ROOT
              else contract.project(bundle, full))
    if full_omissions:
        # Preserve the failed diagnostic candidate. Preparation/publication must
        # reject this receipt; an audit view cannot qualify missing core motion.
        first = full_omissions[0]
        result[1]['core_action_omission'] = dict(
            rule_id=first.get('rule_id', 'core_scene.motion_retention'),
            source=first.get('source', dict(missing_motion=full_omissions)),
            missing_motion=full_omissions, status='unverified_missing_core_action')
        return result
    # Published/frozen projections without this receipt revision keep their
    # original replay. New B inputs account for C only after full source replay.
    saved = root/'inputs/projection_receipt.json'
    coverage = (read_json(saved).get('source_coverage_revision') if saved.is_file()
                else contract.SOURCE_COVERAGE)
    if coverage not in {None, contract.SOURCE_COVERAGE}:
        raise contract.SourceChanged('Unknown Description source coverage revision')
    audit = result[0]
    if coverage == contract.SOURCE_COVERAGE:
        from _lib.behavior.input_binding import timeline_enabled, current_evidence_timeline
        timeline = current_evidence_timeline(source) if timeline_enabled(source) else None
        audit = contract.diagnostic_source_view(source, bundle, full, *result, timeline)
    projected_omissions = validate_analysis_motion(source, source_behavior, audit,
                                                   allow_omission=root.name == MINIMUM_ROOT)
    omissions = full_omissions + [row for row in projected_omissions if row not in full_omissions]
    if omissions:
        first = omissions[0]
        result[1]['core_action_omission'] = dict(
            rule_id=first.get('rule_id', 'core_scene.motion_retention'),
            source=first.get('source', dict(missing_motion=omissions)),
            missing_motion=omissions, status='unverified_missing_core_action')
    return result


def source_case(case_id):
    base = (SCENARIO_ROOT/'video_extractor/cases').resolve()
    case = (base/case_id).resolve(strict=True)
    if case.parent != base:
        raise contract.SourceChanged('Case path leaves the Case directory')
    return case


def eligible_failure(result):
    """Fail closed: transport, corruption and unclassified exceptions never route B."""
    if result.get('status') in {'accepted', 'done', 'done_degraded', 'unverified'}:
        return False
    from _lib.legacy.v2.minimum_fallback import eligible as v2_eligible
    if v2_eligible(result):
        return True
    error = result.get('error') or {}
    if error.get('retry_kind') in {'transport', 'code_change', 'input_changed'}:
        return False
    if result.get('delivery_level') == 'core_scene_approximate':
        return error.get('code') in {'core_noncore_scope_evidence_insufficient',
                                     'identity_evidence_insufficient'}
    if error.get('code') == 'core_noncore_scope_evidence_insufficient':
        return True
    if (error.get('rule_id') == 'core_scene.motion_continuity' or
            error.get('code') == 'core_motion_semantic_recovery_required'):
        return True
    code = error.get('code')
    if code in {'identity_evidence_insufficient', 'behavior_observation_blocked',
                'behavior_semantic_blocked', 'behavior_scope_review_incomplete'}:
        return True
    if code == 'schema_validation_failed' and error.get('stage') == 'behavior':
        return True
    return False


def _root(source, root):
    result = Path(root).absolute() if root is not None else ROOT/source.name
    # Reject existing symlink components before resolving; do not follow an
    # output link into another Case or publication directory.
    if any(p.is_symlink() for p in (result, *result.parents)):
        raise contract.SourceChanged('Fallback output path contains a symlink')
    result = result.resolve()
    forbidden = [SCENARIO_ROOT/'video_extractor/cases', SCENARIO_ROOT/'video_extractor/media/output',
                 SCENARIO_ROOT/'video_extractor/core_approximate_deliveries']
    if any(result == p.resolve() or result.is_relative_to(p.resolve()) for p in forbidden):
        raise contract.SourceChanged('Fallback must use an independent output directory')
    if result.exists() and any(p.is_symlink() for p in result.rglob('*')):
        raise contract.SourceChanged('Fallback directory contains a symlink')
    return result


def _archive(source):
    from _lib.runtime.core_approximate_delivery import _candidate
    from _lib.behavior import input_binding
    accepted = source/'state/behavior.json'
    if accepted.is_file():
        input_binding.accepted_validation(source)
        return accepted
    try:
        return _candidate(source)
    except ValueError as error:
        if str(error) != 'Core approximation has no complete source-bound Behavior answer':
            raise
    # Reuse the original archive/session binding for a partial representation.
    # Malformed JSON, old facts and patch-only answers cannot become a draft.
    input_binding.verify(source)
    active = input_binding.active_request(source)
    from _lib.runtime.fact_versions import current_facts
    if active.get('fact_version_id') != current_facts(source).version_id:
        raise contract.SourceChanged('Archived Behavior belongs to a different fact version')
    sessions = read_json(source/input_binding.SESSIONS)
    for row in reversed(read_json(source/'state/progress.json').get('sessions', [])):
        if (row.get('stage') != 'behavior' or row.get('status') not in {'failed', 'invalid_output', 'completed'}
                or not row.get('task_id') or sessions.get(row.get('session_id')) != active['request_sha256']):
            continue
        path = Path(row.get('invalid_candidate_path') or '')
        if (not path.is_file() or path.parent.resolve() != (source/'diagnostics/invalid_candidates').resolve()
                or path.name != f"behavior.attempt_{row.get('attempt')}.{sha256(path)[:16]}.json"):
            continue
        raw = read_json(path)
        if isinstance(raw, dict) and not {'base_sha256', 'replacements'} & set(raw):
            return path
    raise contract.DraftBlocked('No source-bound archived Behavior representation is available')


def _bundle(source):
    from _lib.runtime.core_approximate_delivery import _source_hashes
    from _lib.runtime.fact_versions import current_facts
    from _lib.behavior.compile import evidence_frames
    from _lib.contracts import census_adjudication, behavior_census_scope
    from _lib.contracts.census_scope import split
    from _lib.behavior.repair import materialize
    if any(row.get('status') in {'submitted', 'pending', 'running', 'submission_unresolved'}
           for row in read_json(source/'state/progress.json').get('sessions', [])):
        raise contract.SourceChanged('Reconcile active original tasks before freezing fallback inputs')
    view = current_facts(source)
    candidate = _archive(source)
    frames = evidence_frames(source)
    raw = read_json(candidate)
    behavior, review = split(raw)
    behavior = materialize(source, behavior)
    census = census_adjudication.proof(source)
    reconciliation = None
    reconciliation_request = source/'state/analysis_reconciliation.request.json'
    reconciliation_decision = source/'state/analysis_reconciliation.json'
    if not (reconciliation_request.is_file() and reconciliation_decision.is_file()):
        isolated = (SCENARIO_ROOT/'video_extractor/core_approximate_deliveries'/
                    source.name/'cases'/source.name)
        if (isolated/'case.json').is_file() and (isolated/'state/binding.json').is_file():
            if (sha256(isolated/'case.json') == sha256(source/'case.json')
                    and sha256(isolated/'state/binding.json') == sha256(source/'state/binding.json')):
                reconciliation_request = isolated/'state/analysis_reconciliation.request.json'
                reconciliation_decision = isolated/'state/analysis_reconciliation.json'
    if reconciliation_request.is_file() and reconciliation_decision.is_file():
        from _lib.contracts import analysis_reconciliation as analysis_contract
        packet = read_json(reconciliation_request)
        accepted = read_json(reconciliation_decision)
        if (accepted.get('revision') == analysis_contract.REVISION
                and accepted.get('status') == 'accepted'
                and accepted.get('request_sha256') == packet.get('request_sha256')
                and packet.get('behavior_sha256') == digest(behavior)
                and packet.get('census_sha256') == digest(census)):
            analysis_contract.validate(packet, accepted['response'])
            reconciliation = dict(request=packet, response=accepted['response'])
    pending = behavior_census_scope.pending(source, census) if census else {}
    execution_dispositions = None
    if pending and review:
        from _lib.runtime.core_approximate_delivery import validated_dispositions
        try:
            execution_dispositions = validated_dispositions(
                source, behavior, pending, review, frames)
        except (ValueError, KeyError, TypeError):
            # A bad optional review cannot become exclusion authority. The
            # original observation and the ordinary draft gates still apply.
            pass
    hashes = _source_hashes(source, view, candidate, frames)
    # Bind the selected task, input request and every source state JSON used by
    # historical certificate replay. No mutable task record is copied back.
    for path in sorted((source/'state').rglob('*.json')):
        hashes[str(path)] = sha256(path)
    if reconciliation is not None:
        hashes[str(reconciliation_request)] = sha256(reconciliation_request)
        hashes[str(reconciliation_decision)] = sha256(reconciliation_decision)
    hashes[str(source/'state/progress.json')] = sha256(source/'state/progress.json')
    existing_analysis = source/'final/analysis.json'
    if existing_analysis.is_file():
        hashes[str(existing_analysis)] = sha256(existing_analysis)
    separately_bound = {}
    for iid, row in pending.items():
        if row.get('distinction') is True:
            # Census-generated pair obligations have a certificate field_path,
            # not a Batch vehicle census slot.
            continue
        if not row.get('batch_id'):
            raise contract.DraftBlocked('Batch Census observation lacks a source batch_id: '+iid)
        batch = read_json(source/'batches'/f"{row['batch_id']}.json")
        separately_bound[iid] = [v['actor_id'] for f in batch.get('vehicle_census', [])
            if f['frame_id'] == row['frame_id'] for v in f['vehicles']
            if v['object_id'] != row['object_id'] and v.get('binding') in {'existing', 'new'}
            and v.get('actor_id')]
    bundle = dict(world=view.world, registry=view.registry, census=census,
        behavior=behavior, review=review or {}, pending=pending, separately_bound=separately_bound,
        frames=[dict(frame_id=f['frame_id'], time_s=f['time_s']) for f in frames],
        parameters=read_json(source/'case.json')['parameters'],
        probe=read_json(source/'state/probe.json')['probe'])
    if execution_dispositions is not None:
        bundle['validated_execution_dispositions'] = execution_dispositions
    if reconciliation is not None:
        bundle['analysis_reconciliation'] = reconciliation
    receipt = dict(revision=REVISION, source_case=str(source), case_id=source.name,
        fact_version=view.version_id, archive_path=str(candidate), archive_sha256=sha256(candidate),
        source_hashes=hashes, bundle_sha256=digest(bundle))
    receipt['receipt_sha256'] = digest(receipt)
    return bundle, receipt


def _bound(root):
    receipt = read_json(root/'inputs/source_receipt.json')
    if (receipt.get('revision') != REVISION or receipt.get('receipt_sha256') !=
            digest({k: v for k, v in receipt.items() if k != 'receipt_sha256'})):
        raise contract.SourceChanged('Unsupported fallback source receipt')
    for name, expected in receipt['source_hashes'].items():
        if not Path(name).is_file() or sha256(name) != expected:
            raise contract.SourceChanged('Frozen source changed: '+name)
    bundle = read_json(root/'inputs/source_bundle.json')
    if digest(bundle) != receipt['bundle_sha256']:
        raise contract.SourceChanged('Frozen text bundle changed')
    case = root/'cases'/receipt['case_id']
    for name in ('case.json', 'state/execution_policy.json'):
        if (case/name).is_file() and sha256(case/name) != receipt['source_hashes'].get(
                str(Path(receipt['source_case'])/name)):
            raise contract.SourceChanged('Fallback Case policy/parameters changed: '+name)
    return bundle, receipt


def _write_once(path, value):
    if path.exists():
        if read_json(path) != value:
            raise contract.SourceChanged('Frozen fallback input changed: '+str(path))
    else:
        atomic_json(path, value)


def _assemble(source, bundle, root=None):
    from _lib.behavior.compile import prepare_behavior_candidate, execution_issues, evidence_frames
    from _lib.contracts.v3 import build_analysis
    behavior, normalizations = prepare_behavior_candidate(copy.deepcopy(bundle['behavior']),
        evidence_frames(source), bundle['registry'], copy.deepcopy(bundle['world']), case=source)
    from _lib.contracts.core_lane_continuity import validate as validate_core_lane_continuity
    ego_correction = None
    motion_root = root.parent if root is not None and root.name == MINIMUM_ROOT else root
    recovered_parent = (motion_root is not None and root is not None and root.name == MINIMUM_ROOT
        and ((motion_root/'inputs/core_motion.accepted.json').is_file() or
             (motion_root/'inputs/core_motion.fallback.json').is_file()))
    try:
        validate_core_lane_continuity(source, behavior)
    except ValueError as error:
        if getattr(error, 'rule_id', None) != 'core_scene.motion_continuity' or root is None:
            raise
        if root.name != MINIMUM_ROOT or recovered_parent:
            request = motion_recovery.request(source, bundle, behavior)
            answer_path = motion_root/'inputs/core_motion.accepted.json'
            answer = read_json(answer_path) if answer_path.is_file() else None
            fallback_path = motion_root/'inputs/core_motion.fallback.json'
            fallback = read_json(fallback_path) if fallback_path.is_file() else None
            if fallback and (answer is not None or fallback.get('request_sha256') != request['request_sha256']
                             or fallback.get('bundle_sha256') != request['bundle_sha256']):
                raise contract.SourceChanged('Core motion fallback differs from frozen request')
            behavior = motion_recovery.recover(source, bundle, behavior, request, answer,
                                               allow_fallback=fallback is not None)
            behavior, ego_correction = motion_recovery.restore_ego_execution(source, bundle, behavior)
            if ego_correction:
                normalizations.append(ego_correction)
            normalizations.append(dict(action='recover_core_motion_in_isolated_analysis',
                request_sha256=request['request_sha256'],
                decision_sha256=digest(answer) if answer is not None else None,
                source_witness=request['witness'],
                fixed_source_frames=request['fixed_source_frames'],
                fixed_source_observations=request['fixed_source_observations'],
                field_dispositions=motion_recovery.decision_audit(request, answer),
                fallback=fallback,
                provenance='frozen_frames_and_archived_text',
                semantic_decision='json-coverter_candidate_with_python_field_validation'
                    if answer is not None else 'deterministic_source_bound_fallback'))
    world = copy.deepcopy(bundle['world'])
    full, status = build_analysis(world, behavior, bundle['parameters'],
        observation_issues=execution_issues(behavior, world, bundle['registry'], evidence_frames(source)),
        observation_contract=True, media_probe=bundle['probe'])
    recovered = [row for row in normalizations
                 if row.get('action') == 'recover_core_motion_in_isolated_analysis']
    if recovered:
        full.setdefault('metadata', {})['core_motion_unknown_samples'] = [dict(
            actor_id=row['source_witness']['actor_id'], frame_id=field['frame_id'],
            time_s=field['time_s'], lane_status='unknown',
            execution_placement=field['execution_placement'],
            observation_ids=[item['observation_id'] for item in field['observations']])
            for row in recovered for field in row['field_dispositions']
            if field['lane_id'] is None]
        full['metadata']['core_motion_timing'] = [dict(
            actor_id=row['source_witness']['actor_id'],
            observation_envelope_s=row['source_witness']['observation_envelope_s'],
            exact_crossing_time='unknown',
            execution_interval_policy='source_bound_observation_envelope',
            status='unverified_simulation_assumption') for row in recovered]
    if ego_correction:
        full.setdefault('metadata', {})['ego_lane_execution_correction'] = ego_correction
    # A pre-existing full Analysis is reusable only if source replay agrees.
    # A stale or different execution projection is not silently adopted.
    existing = source/'final/analysis.json'
    if existing.is_file() and read_json(existing) == full:
        full = read_json(existing)
    return full, normalizations


def _work_case(source, root):
    case = root/'cases'/source.name
    if not case.exists():
        if any(p.is_symlink() for p in (source/'state').rglob('*')):
            raise contract.SourceChanged('Source state contains a symlink')
        case.mkdir(parents=True)
        # Copy state only. No original final Target or publication can become a
        # fallback artifact, and all new model writes have a different owner.
        shutil.copy2(source/'case.json', case/'case.json')
        shutil.copytree(source/'state', case/'state', symlinks=False,
                        ignore=shutil.ignore_patterns('conversion_work', 'conversion_*.json',
                                                      'conversion_policies'))
        # conversion policy is a frozen source permission, not a completed task.
        if not (case/'state/execution_policy.json').is_file():
            raise contract.DraftBlocked('Source Case has no conversion execution policy')
    return case


def _draft(root, receipt, **fields):
    path = root/'draft.json'
    old = read_json(path) if path.is_file() else {}
    value = dict(old, revision=REVISION, case_id=receipt['case_id'], delivery_level='unverified',
        generation_status='not_generated', target_path=None,
        source_receipt='inputs/source_receipt.json', **fields)
    atomic_json(path, value)
    return value


def _prepare_work(case, root, analysis, projection):
    _write_once(case/'final/analysis.json', analysis)
    from _lib.contracts import motion_witnesses
    saved_analysis = read_json(case/'final/analysis.json')
    request = semantic_motion.request(saved_analysis, sha256(case/'final/analysis.json'))
    if motion_witnesses.qualitative_body_constraints(saved_analysis, request)[0]:
        request = semantic_motion.request(saved_analysis, request['analysis_sha256'],
                                          witness_revision=motion_witnesses.REVISION)
    _write_once(case/'state/conversion_semantic_gap.request.json', request)
    work = ConversionWork(_OfflineOwner(), case, request)
    entry = work.state['stages'].setdefault(STAGES[0], {'runs': []})
    if not entry.get('complete') and not entry['runs']:
        work.new_run(STAGES[0], entry)
    _write_once(root/'inputs/projection_receipt.json', projection)
    if root.name == MINIMUM_ROOT:
        _reuse_relations(root, work)
    return request


def _reuse_relations(root, work):
    """Replay one finished B answer against the smaller frozen request."""
    primary = root.parent
    old_case = primary/'cases'/work.case.name
    old_request_path = old_case/'state/conversion_semantic_gap.request.json'
    if not old_request_path.is_file() or not (old_case/'state/conversion_work/workflow.json').is_file():
        return
    if read_json(primary/'inputs/source_receipt.json') != read_json(root/'inputs/source_receipt.json'):
        raise contract.SourceChanged('Minimum scene and B have different source receipts')
    old_bundle, _ = _bound(primary)
    _validate_projection(primary, old_bundle)
    progress = read_json(old_case/'state/progress.json')
    if any(s.get('stage') in STAGES and s.get('status') in
           {'submitted', 'pending', 'running', 'submission_unresolved'} for s in progress.get('sessions', [])):
        raise contract.DraftBlocked('Original conversion task remains active')
    old_request = read_json(old_request_path)
    previous = ConversionWork(None, old_case, old_request).checkpoint(STAGES[0])
    if previous is None or not previous['complete']:
        return
    old_evidence = old_request['evidence']
    new_evidence = work.request['evidence']
    old_by_source = {r['source']: r for r in old_evidence}
    old_analysis = read_json(old_case/'final/analysis.json')
    new_analysis = read_json(work.case/'final/analysis.json')
    old_limits = {r['limitation_id']: i for i, r in enumerate(old_analysis.get('limitations', []))}
    mapping = {}
    for new in new_evidence:
        source = new['source']
        previous_source = source
        if source.startswith('/limitations/'):
            parts = source.split('/')
            try:
                old_index = old_limits[new_analysis['limitations'][int(parts[2])]['limitation_id']]
            except (KeyError, IndexError, ValueError):
                return
            parts[2] = str(old_index)
            previous_source = '/'.join(parts)
        old = old_by_source.get(previous_source)
        if old is None or {k: v for k, v in old.items() if k != 'source'} != {
                k: v for k, v in new.items() if k != 'source'}:
            return  # No complete safe reuse; ordinary bounded stage remains available.
        mapping[previous_source] = source
    if len(mapping) != len(new_evidence):
        return
    coverage = [dict(row, source=mapping[row['source']]) for row in previous['coverage']
                if row['source'] in mapping]
    relations = []
    eligible = {a['actor_id'] for a in work.request['eligible']}
    for row in previous['relations']:
        if row['actor_id'] not in eligible or row['reference_actor_id'] not in eligible:
            continue
        if any(c['source'] not in mapping for c in row['evidence']):
            return
        updated = copy.deepcopy(row)
        for citation in updated['evidence']:
            citation['source'] = mapping[citation['source']]
        relations.append(updated)
    from _lib.contracts import bounded_execution, retained_motion
    coverage, relations = bounded_execution.complete_relations(work.request, coverage, relations)
    coverage, relations = retained_motion.complete_relations(work.request, coverage, relations)
    from _lib.contracts.semantic_motion import validate_relations
    try:
        validate_relations(work.request, coverage, relations)
    except ValueError:
        return
    entry = work.state['stages'][STAGES[0]]
    value = dict(request_sha256=entry['runs'][0]['request']['request_sha256'],
                 complete=True, coverage=coverage, relations=relations)
    from _lib.runtime.conversion_work import validate_save
    validate_save(work.request, STAGES[0], value, value['request_sha256'])
    signature = digest(value)
    accepted = work.root/'accepted'/f'{signature}.json'
    _write_once(accepted, value)
    entry.update(complete=True, checkpoint=dict(sha256=sha256(accepted),
        content_sha256=signature, request_sha256=value['request_sha256']))
    work.persist()
    _write_once(root/'inputs/reused_relations.json', dict(
        source_checkpoint_sha256=sha256(old_case/'state/conversion_work/workflow.json'),
        source_request_sha256=old_request['request_sha256'],
        target_request_sha256=work.request['request_sha256'],
        source_mapping=mapping, coverage_count=len(coverage), relation_count=len(relations)))


class _OfflineOwner:
    def progress(self, case):
        return read_json(case/'state/progress.json')


class FallbackOwner(V3Orchestrator):
    """Original task lifecycle, restricted to text-only json-coverter tasks."""
    def __init__(self, backend, root):
        super().__init__(backend)
        self.fallback_root = root

    def model_stage(self, case, key, agent, *args, **kwargs):
        _bound(self.fallback_root)
        if agent != 'json-coverter' or key not in {DRAFT_STAGE, motion_recovery.STAGE, *STAGES}:
            raise contract.DraftBlocked('Fallback cannot run video understanding tasks')
        return super().model_stage(case, key, agent, *args, **kwargs)


def prepare(source, root=None):
    source = Path(source).resolve(strict=True)
    root = _root(source, root)
    if (root/'inputs/source_receipt.json').exists():
        bundle, receipt = _bound(root)
        if receipt['source_case'] != str(source):
            raise contract.SourceChanged('Fallback belongs to another Case')
    else:
        try:
            bundle, receipt = _bundle(source)
        except (ValueError, KeyError, TypeError) as error:
            atomic_json(root/'draft.json', dict(revision=REVISION, case_id=source.name,
                status='blocked', delivery_level='unverified', generation_status='not_generated',
                target_path=None, source_case=str(source), source_receipt=None,
                failure=dict(code=getattr(error, 'code', type(error).__name__), message=str(error))))
            return envelope(root, {'case_id': source.name})
        _write_once(root/'inputs/source_bundle.json', bundle)
        _write_once(root/'inputs/source_receipt.json', receipt)
    case = _work_case(source, root)
    if (root/'output').exists():
        return check(source, root)
    if not (root/'draft.json').exists():
        _draft(root, receipt, status='preparing', failure=None)
    if (root/'inputs/projection_receipt.json').exists():
        _, analysis, request, projection = _validate_projection(root, bundle)
        if read_json(root/'draft.json')['status'] == 'preparing':
            _draft(root, receipt, status='conversion_prepared', analysis=analysis,
               projection=projection, failure=None,
               core_action_omission=projection.get('core_action_omission'),
               conversion_request_sha256=request['request_sha256'])
        return envelope(root, receipt)
    try:
        full, normalizations = _assemble(source, bundle, root)
    except motion_recovery.NeedsSemanticMotion as error:
        _write_once(root/'inputs/core_motion.request.json', error.request)
        _draft(root, receipt, status='needs_core_motion',
               core_action_omission=dict(rule_id='core_scene.motion_continuity',
                   witness=error.request['witness'], status='recovery_pending'),
               failure=dict(code=error.code, message=str(error)))
        return envelope(root, receipt)
    except (ValueError, KeyError, TypeError) as error:
        # A draft task may add missing representation fields, never repair a
        # corrupted source file, change a confirmed value or add observations.
        base = contract.skeleton(bundle)
        if (root/'inputs/core_motion.request.json').is_file():
            _draft(root, receipt, status='blocked', analysis=base,
                   core_action_omission=dict(rule_id='core_scene.motion_continuity',
                       witness=read_json(root/'inputs/core_motion.request.json')['witness'],
                       status='recovery_failed'),
                   failure=dict(code=getattr(error, 'code', type(error).__name__), message=str(error)))
            return envelope(root, receipt)
        if root.name == MINIMUM_ROOT:
            _draft(root, receipt, status='blocked', analysis=base,
                   source_text=contract.text_inventory(bundle),
                   failure=dict(code='description_draft_blocked',
                       message='Minimum scene cannot compile source representation: '+str(error)))
            return envelope(root, receipt)
        request = dict(revision=REVISION, bundle_sha256=receipt['bundle_sha256'],
            bundle_path=str(root/'inputs/source_bundle.json'), base=base,
            error=dict(type=type(error).__name__, message=str(error)))
        request['request_sha256'] = digest(request)
        _write_once(root/'inputs/text_draft.request.json', request)
        _draft(root, receipt, status='needs_text_draft', analysis=base,
               source_text=contract.text_inventory(bundle), failure=request['error'])
        return envelope(root, receipt)
    _write_once(root/'inputs/full_analysis.json', full)
    projection = {}
    try:
        analysis, projection = _project(root, bundle, full)
        projection.update(full_analysis_sha256=sha256(root/'inputs/full_analysis.json'),
                          normalizations=normalizations, origin='original_builder')
        contract.require_core_scene(bundle, full, analysis, projection)
        request = _prepare_work(case, root, analysis, projection)
    except (ValueError, KeyError, TypeError) as error:
        _draft(root, receipt, status='blocked', analysis=full,
               core_action_omission=projection.get('core_action_omission'),
               source_text=contract.text_inventory(bundle),
               failure=dict(code=getattr(error, 'code', type(error).__name__), message=str(error)))
        return envelope(root, receipt)
    _draft(root, receipt, status='conversion_prepared', analysis=analysis,
           projection=projection, failure=None,
           core_action_omission=projection.get('core_action_omission'),
           conversion_request_sha256=request['request_sha256'])
    _bound(root)
    return envelope(root, receipt)


def _text_request(root, bundle):
    request = read_json(root/'inputs/text_draft.request.json')
    if (request['bundle_sha256'] != digest(bundle) or request['request_sha256'] != digest(
            {k: v for k, v in request.items() if k != 'request_sha256'})
            or request['base'] != contract.skeleton(bundle)
            or request['bundle_path'] != str(root/'inputs/source_bundle.json')):
        raise contract.SourceChanged('Text draft request changed')
    return request


def _validate_projection(root, bundle):
    receipt = read_json(root/'inputs/source_receipt.json')
    source = Path(receipt['source_case'])
    case = root/'cases'/source.name
    projection = read_json(root/'inputs/projection_receipt.json')
    if projection['origin'] == 'original_builder':
        full, normalizations = _assemble(source, bundle, root)
    elif projection['origin'] == 'text_additions':
        req = _text_request(root, bundle)
        full, normalizations = contract.validate_text_candidate(bundle, req['base'],
            read_json(root/'inputs/text_draft.accepted.json'), req['request_sha256'])
    else:
        raise contract.SourceChanged('Unknown draft producer')
    if read_json(root/'inputs/full_analysis.json') != full:
        raise contract.SourceChanged('Full draft differs from source replay')
    analysis, expected = _project(root, bundle, full)
    expected.update(full_analysis_sha256=sha256(root/'inputs/full_analysis.json'),
                    normalizations=normalizations, origin=projection['origin'])
    if expected != projection or read_json(case/'final/analysis.json') != analysis:
        raise contract.SourceChanged('Execution draft/projection receipt changed')
    contract.require_core_scene(bundle, full, analysis, projection)
    analysis = read_json(case/'final/analysis.json')
    from _lib.contracts.motion_witnesses import POLICY_KEY
    frozen = read_json(case/'state/conversion_semantic_gap.request.json')
    request = semantic_motion.request(analysis, sha256(case/'final/analysis.json'),
                                      witness_revision=frozen.get('policy', {}).get(POLICY_KEY))
    if read_json(case/'state/conversion_semantic_gap.request.json') != request:
        raise contract.SourceChanged('Frozen conversion request changed')
    return case, analysis, request, projection


def _text_task(owner, case, root, bundle, receipt):
    request = _text_request(root, bundle)
    candidate = root/'inputs/text_draft.candidate.json'
    accepted = root/'inputs/text_draft.accepted.json'
    def adopt(_session=None):
        value = read_json(candidate)
        full, changes = contract.validate_text_candidate(bundle, request['base'], value,
                                                         request['request_sha256'])
        _write_once(accepted, value)
        return full, changes
    if accepted.exists():
        full, changes = contract.validate_text_candidate(bundle, request['base'], read_json(accepted),
                                                         request['request_sha256'])
    else:
        prompt_path = SCENARIO_ROOT/'json-coverter/skills/case-conversion/references/description-draft-prompt.md'
        prompt = prompt_path.read_text().replace('$request_path', str(root/'inputs/text_draft.request.json'))
        prompt = prompt.replace('$output_path', str(candidate))
        owner.model_stage(case, DRAFT_STAGE, 'json-coverter', prompt, adopt,
                          candidate_path=candidate, max_attempts=1)
        full, changes = adopt()
    _write_once(root/'inputs/full_analysis.json', full)
    analysis, projection = _project(root, bundle, full)
    projection.update(full_analysis_sha256=sha256(root/'inputs/full_analysis.json'),
                      normalizations=changes, origin='text_additions')
    contract.require_core_scene(bundle, full, analysis, projection)
    _prepare_work(case, root, analysis, projection)


def _motion_task(owner, case, root, bundle):
    request_path = root/'inputs/core_motion.request.json'
    request = read_json(request_path)
    source = Path(read_json(root/'inputs/source_receipt.json')['source_case'])
    from _lib.behavior.compile import prepare_behavior_candidate, evidence_frames
    prepared, _ = prepare_behavior_candidate(copy.deepcopy(bundle['behavior']),
        evidence_frames(source), bundle['registry'], copy.deepcopy(bundle['world']), case=source)
    if request != motion_recovery.request(source, bundle, prepared):
        raise contract.SourceChanged('Core motion request differs from frozen evidence')
    candidate = root/'inputs/core_motion.candidate.json'
    accepted = root/'inputs/core_motion.accepted.json'

    def adopt(_session=None):
        value = read_json(candidate)
        motion_recovery.recover(source, bundle, prepared, request, value)
        _write_once(accepted, value)

    if accepted.is_file():
        motion_recovery.recover(source, bundle, prepared, request, read_json(accepted))
        return
    prompt_path = SCENARIO_ROOT/'json-coverter/skills/case-conversion/references/core-motion-recovery-prompt.md'
    prompt = prompt_path.read_text().replace('$request_path', str(request_path))
    prompt = prompt.replace('$output_path', str(candidate))
    owner.model_stage(case, motion_recovery.STAGE, 'json-coverter', prompt, adopt,
                      candidate_path=candidate, max_attempts=1)
    adopt()


def _motion_failure_fallback(case, root, request, error):
    """Continue only after a terminal text failure; preserve its candidate."""
    if (isinstance(error, contract.SourceChanged) or
            getattr(error, 'code', str(error)) in
            {'submission_unresolved', 'task_deadline_exceeded', 'task_status_unavailable'}):
        return False
    progress = read_json(case/'state/progress.json')
    if (progress.get('diagnostics', {}).get('unresolved_submissions') or any(
            row.get('stage') == motion_recovery.STAGE and row.get('status') in
            {'submitted', 'pending', 'running', 'submission_unresolved'}
            for row in progress.get('sessions', []))):
        return False
    failed = [row for row in progress.get('sessions', [])
              if row.get('stage') == motion_recovery.STAGE]
    invalid = []
    for row in failed:
        path = row.get('invalid_candidate_path')
        if path:
            candidate = Path(path).resolve()
            if not candidate.is_relative_to(root.resolve()) or not candidate.is_file():
                raise contract.SourceChanged('Core motion rejected candidate moved outside isolated Case')
            invalid.append(dict(path=str(candidate), sha256=sha256(candidate)))
    _write_once(root/'inputs/core_motion.fallback.json', dict(
        request_sha256=request['request_sha256'], bundle_sha256=request['bundle_sha256'],
        reason=dict(code=getattr(error, 'code', type(error).__name__), message=str(error)),
        rejected_candidates=invalid, status='source_bound_unknown_intermediates'))
    return True


def _minimum_plan(case, request, relations):
    from dataclasses import asdict
    from _lib.behavior.lane_change import from_analysis
    from _lib.contracts import bounded_execution
    from _lib.contracts.conversion_policy import build
    analysis = read_json(case/'final/analysis.json')
    parameters = read_json(case/'case.json')['parameters']
    allowed = set(build(parameters, request['analysis_sha256'], analysis['scene_type'])[
        'allowed_degradations'])
    if not bounded_execution.enabled(case) or not bounded_execution.PERMISSIONS <= allowed:
        return None
    if not request['eligible'] and not analysis.get('actors'):
        return []  # Original Converter applies the permitted ego execution defaults.
    lane_change = from_analysis(analysis)
    virtual = dict(request, bounded_execution=dict(revision=bounded_execution.REVISION,
        source_record=dict(preferences={}),
        lane_change=asdict(lane_change) if lane_change else None))
    return bounded_execution.plan(virtual, relations)


def _minimum_observation_relations(case, root, request):
    """Finish an archived ego-only answer when unresolved text is audit-only.

    The original model save is never edited. This is limited to a scene with no
    executable pair or interaction; all other scenes retain normal relation work.
    """
    if (root.name != MINIMUM_ROOT or request['eligible'] or request['events']
            or read_json(case/'final/analysis.json').get('simulation_scenarios')):
        return None
    analysis = read_json(case/'final/analysis.json')
    if analysis.get('actors') or analysis.get('interactions'):
        return None
    ego_lanes = {p.get('lane_id') for p in analysis['ego']['trajectory']}
    if len(ego_lanes) != 1 or None in ego_lanes:
        return None
    projection = read_json(root/'inputs/projection_receipt.json')
    choices = {p['issue_id']: p for p in projection['identity_choices']}
    work = ConversionWork(None, case, request)
    entry = work.state['stages'].get(STAGES[0], {})
    if entry.get('complete') or len(entry.get('runs', [])) != 1:
        return None
    run = entry['runs'][0]
    folder = work.root/STAGES[0]/'run_0001'
    saves = sorted(folder.glob('save_*.json'))
    if not saves:
        return None
    candidate_path = saves[-1]
    candidate = read_json(candidate_path)
    sources = {p['source']: p for p in request['evidence']}
    coverage = candidate.get('coverage')
    if (candidate.get('request_sha256') != run['request']['request_sha256']
            or candidate.get('complete') is not True or candidate.get('relations') != []
            or not isinstance(coverage, list)
            or len(coverage) != len(sources)
            or {p.get('source') for p in coverage if isinstance(p, dict)} != set(sources)):
        return None
    corrected, dispositions = [], []
    for row in coverage:
        source = sources[row['source']]
        status = row.get('status')
        if status in {'no_motion', 'unobserved'}:
            corrected.append(row)
            continue
        parts = row['source'].split('/')
        if len(parts) < 4 or parts[1] != 'limitations' or not parts[2].isdigit():
            return None
        limitation = analysis['limitations'][int(parts[2])]
        issues = limitation.get('census_issue_ids', [])
        peripheral = (status == 'identity_unresolved' and bool(issues)
            and not limitation.get('actor_ids') and not limitation.get('affected_event_ids')
            and not limitation.get('world_paths') and not limitation.get('related_world_paths')
            and not limitation.get('scope_binding')
            and all(choices.get(iid, {}).get('handling') == 'unmodeled_observation'
                    for iid in issues)
            and set(limitation.get('impact_domains', [])) <= {'actor_identity', 'actor_lane', 'actor_motion'})
        scale = (status == 'motion_unresolved'
            and limitation.get('limitation_kind') == 'absolute_scale'
            and set(limitation.get('impact_domains', [])) <= {'speed', 'position'}
            and not issues and not limitation.get('affected_event_ids')
            and not limitation.get('world_paths') and not limitation.get('related_world_paths'))
        if not (peripheral or scale):
            return None
        reason = ('Unmodeled observation has no executable Actor or relation; identity remains unknown.'
                  if peripheral else
                  'Absolute scale is unobserved; numeric execution is a disclosed simulation assumption.')
        corrected.append(dict(source=row['source'], status='no_motion' if peripheral else 'unobserved',
                              reason=reason))
        dispositions.append(dict(source=row['source'], original_status=status,
                                 handling='unmodeled_observation' if peripheral else 'unobserved_scale',
                                 original_text=source['text'], reason=reason))
    from _lib.contracts.semantic_motion import validate_relations
    validate_relations(request, corrected, [])
    return candidate_path, corrected, dispositions


def _program_observation_relations(case, root, request):
    if root.name != MINIMUM_ROOT:
        return
    work = ConversionWork(_OfflineOwner(), case, request)
    if work.state['stages'].get(STAGES[0], {}).get('complete'):
        _check_program_observation_relations(case, root, request)
        return
    result = _minimum_observation_relations(case, root, request)
    if result is None:
        return
    source, coverage, dispositions = result
    entry = work.state['stages'][STAGES[0]]
    value = dict(request_sha256=entry['runs'][0]['request']['request_sha256'],
                 complete=True, coverage=coverage, relations=[])
    from _lib.runtime.conversion_work import validate_save
    validate_save(request, STAGES[0], value, value['request_sha256'])
    signature = digest(value)
    accepted = work.root/'accepted'/f'{signature}.json'
    _write_once(accepted, value)
    entry.update(complete=True, checkpoint=dict(sha256=sha256(accepted),
        content_sha256=signature, request_sha256=value['request_sha256']))
    work.persist()
    _write_once(root/'inputs/program_observation_relations.json', dict(
        revision='minimum-observation-relations.v1', request_sha256=request['request_sha256'],
        archived_save=str(source.relative_to(root)), archived_save_sha256=sha256(source),
        coverage_sha256=digest(coverage), dispositions=dispositions))


def _check_program_observation_relations(case, root, request):
    path = root/'inputs/program_observation_relations.json'
    if not path.is_file():
        return
    receipt = read_json(path)
    source = root/receipt['archived_save']
    work = ConversionWork(None, case, request)
    checkpoint = work.checkpoint(STAGES[0])
    if (receipt.get('revision') != 'minimum-observation-relations.v1'
            or receipt.get('request_sha256') != request['request_sha256']
            or not source.is_relative_to(root) or sha256(source) != receipt['archived_save_sha256']
            or checkpoint is None or digest(checkpoint['coverage']) != receipt['coverage_sha256']
            or checkpoint['relations'] != []):
        raise contract.SourceChanged('Minimum observation-only relation receipt changed')


def _program_minimum(case, root, request):
    if root.name != MINIMUM_ROOT:
        return
    work = ConversionWork(_OfflineOwner(), case, request)
    if not work.state['stages'].get(STAGES[0], {}).get('complete'):
        return
    entry = work.state['stages'].setdefault(STAGES[1], {'runs': []})
    if entry.get('runs') or entry.get('complete'):
        return
    relations = work.finish_stage(STAGES[0])['relations']
    from _lib.contracts.bounded_execution import PlanningIncomplete
    try:
        plans = _minimum_plan(case, request, relations)
    except PlanningIncomplete:
        return  # One ordinary numeric session remains available to the user.
    if plans is None:
        return
    run = work.new_run(STAGES[1], entry)
    value = dict(request_sha256=run['request']['request_sha256'], complete=True, plans=plans)
    from _lib.runtime.conversion_work import validate_save
    validate_save(request, STAGES[1], value, value['request_sha256'])
    signature = digest(value)
    path = work.root/'accepted'/f'{signature}.json'
    _write_once(path, value)
    entry.update(complete=True, checkpoint=dict(sha256=sha256(path),
        content_sha256=signature, request_sha256=value['request_sha256']))
    work.persist()
    _write_once(root/'inputs/program_plan.json', dict(
        revision='minimum-program-plan.v1', request_sha256=request['request_sha256'],
        relations_sha256=digest(relations), plans=plans,
        provenance='Existing bounded-execution.v1 finite search; simulated values are not video measurements'))


def _check_program_minimum(case, root, request):
    path = root/'inputs/program_plan.json'
    if not path.is_file():
        return
    work = ConversionWork(None, case, request)
    relations = work.finish_stage(STAGES[0])['relations']
    receipt = read_json(path)
    if (receipt.get('revision') != 'minimum-program-plan.v1'
            or receipt.get('request_sha256') != request['request_sha256']
            or receipt.get('relations_sha256') != digest(relations)
            or receipt.get('plans') != _minimum_plan(case, request, relations)
            or receipt['plans'] != work.checkpoint(STAGES[1])['plans']):
        raise contract.SourceChanged('Minimum deterministic numeric plan changed')


def _inspect_target(root, output):
    from _lib.delivery.converter import mela_valid, converter_fingerprint
    from _lib.delivery.checks import validate_target
    from _lib.contracts.semantic_gap import validate
    bundle, receipt = _bound(root)
    case, analysis, request, projection = _validate_projection(root, bundle)
    target_path, result_path = output/'target.json', output/'conversion_result.json'
    result = read_json(result_path)
    decision = read_json(case/'state/conversion_semantic_gap.json')
    _check_program_minimum(case, root, request)
    validate(analysis, request['analysis_sha256'], decision)
    validate_committed(case, request, decision)
    script = SCENARIO_ROOT/'json-coverter/skills/case-conversion/scripts/scenes'/analysis['scene_type']/'convert.py'
    if (result.get('status') not in {'done', 'done_degraded'}
            or result.get('input_sha256') != request['analysis_sha256']
            or result.get('target_sha256') != sha256(target_path)
            or result.get('output_path') != str(root/'staging/target.json')
            or result.get('converter_sha256') != converter_fingerprint(script)
            or result.get('semantic_gap') != decision):
        raise contract.SourceChanged('Original Converter receipt binding changed')
    policy = result.get('conversion_policy') or {}
    if (policy.get('case_id') != case.name
            or policy.get('parameters_sha256') != digest(bundle['parameters'])
            or policy != read_json(case/'state/conversion_policy.json')):
        raise contract.SourceChanged('Original Converter policy differs from frozen Case')
    if not mela_valid(target_path):
        raise contract.DraftBlocked('Original Target Schema validation failed')
    checks = validate_target(analysis, read_json(target_path), bundle['parameters']['road_source'], result)
    if checks != result.get('delivery_checks'):
        raise contract.SourceChanged('Original Converter checks differ on replay')
    motion = contract.require_motion(analysis, read_json(target_path), checks, decision)
    manifest = dict(revision=REVISION, case_id=receipt['case_id'], status='unverified',
        delivery_level='unverified', generation_status='generated',
        strict_validate_completed='not_claimed', core_approximate_acceptance='not_claimed',
        render_verification='not_executed', target_sha256=sha256(target_path),
        analysis_sha256=request['analysis_sha256'], source_receipt_sha256=sha256(root/'inputs/source_receipt.json'),
        projection_sha256=sha256(root/'inputs/projection_receipt.json'),
        converter_sha256=result['converter_sha256'], checks=dict(target_schema='passed', **motion),
        original_converter_checks=checks, identity_choices=projection['identity_choices'],
        unmodeled_observations=projection['unmodeled_observations'],
        unverified_evidence=projection['limitation_dispositions'],
        numeric_assumptions=dict(status='simulated', plans=decision['plans'], fallbacks=result.get('fallbacks', [])),
        use_restrictions=['Execution assumptions are not confirmed vehicle identities or video measurements.',
                          'No collision-safety claim is made against unmodeled observations.'])
    if projection.get('source_coverage_revision') == contract.SOURCE_COVERAGE:
        manifest['collision_scope'] = 'modeled_vehicles_only'
        manifest['use_restrictions'].append(
            'Unmodeled Ego interactions are not simulated; the surrounding traffic is not fully reconstructed.')
    if _semantic_revision(root):
        from _lib.contracts.core_lane_continuity import witnesses
        known_core = witnesses(Path(receipt['source_case']))
        manifest['target_generated'] = True
        manifest['core_event_fidelity'] = (
            'failed' if projection.get('core_action_omission') else
            'retained_unverified' if known_core else 'not_applicable')
    if projection.get('simplification'):
        manifest.update(simplification=projection['simplification'],
            failed_evidence_checks=['Unmodeled vehicle identity, motion and interactions are not verified',
                                    'No collision claim is made against omitted observations'])
    if projection.get('core_action_omission'):
        manifest['core_action_omission'] = projection['core_action_omission']
        manifest['core_event_fidelity'] = 'failed'
        rule = projection['core_action_omission']['rule_id']
        manifest.setdefault('failed_evidence_checks', []).append(
            ('core_scene.motion_continuity: confirmed core lane change is absent from this minimum scene'
             if rule == 'core_scene.motion_continuity' else
             'core_scene.motion_retention: accepted core event is absent from this minimum scene'))
        manifest['use_restrictions'].append(
            'This minimum scene omits a confirmed core action and is not a correct video reconstruction.')
    recovered = [row for row in projection.get('normalizations', [])
                 if row.get('action') == 'recover_core_motion_in_isolated_analysis']
    if recovered:
        manifest['core_action_recovery'] = dict(status=(
            'unverified_semantic_reconstruction' if any(row['decision_sha256'] for row in recovered)
            else 'deterministic_source_bound_reconstruction'),
                                                receipts=recovered)
    return manifest


def check(source, root=None):
    source = Path(source).resolve(strict=True)
    root = _root(source, root)
    if not (root/'inputs/source_receipt.json').is_file() and (root/'draft.json').is_file():
        return envelope(root, {'case_id': source.name})
    bundle, receipt = _bound(root)
    if receipt['source_case'] != str(source):
        raise contract.SourceChanged('Fallback belongs to another Case')
    if (root/'output').exists():
        expected = _inspect_target(root, root/'output')
        if read_json(root/'output/manifest.json') != expected:
            raise contract.SourceChanged('Unverified manifest differs from replay')
    elif (root/'inputs/projection_receipt.json').exists():
        _validate_projection(root, bundle)
    return envelope(root, receipt)


def resume(source, root=None, *, backend=None):
    from _lib.runtime.orchestrator import QwenPawBackend
    source = Path(source).resolve(strict=True)
    root = _root(source, root)
    prepared = prepare(source, root)
    if not (root/'inputs/source_receipt.json').is_file():
        return prepared
    if (root.name != MINIMUM_ROOT and prepared['status'] == 'blocked'
            and (prepared.get('error') or {}).get('code') not in
            {'description_source_changed', 'backend_unavailable'}):
        return resume(source, root/MINIMUM_ROOT, backend=backend)
    bundle, receipt = _bound(root)
    if (root/'output').exists():
        return check(source, root)
    owner = FallbackOwner(backend if backend is not None else QwenPawBackend(), root)
    case = root/'cases'/source.name
    try:
        if prepared['status'] == 'needs_core_motion':
            motion_request = read_json(root/'inputs/core_motion.request.json')
            try:
                _motion_task(owner, case, root, bundle)
            except Exception as error:
                if not _motion_failure_fallback(case, root, motion_request, error):
                    raise
            prepared = prepare(source, root)
            if prepared['status'] != 'conversion_prepared':
                raise contract.DraftBlocked('Core motion recovery did not prepare conversion work')
        if not (root/'inputs/projection_receipt.json').exists():
            if not (root/'inputs/text_draft.request.json').is_file():
                return envelope(root, receipt)
            _text_task(owner, case, root, bundle, receipt)
        case, analysis, request, projection = _validate_projection(root, bundle)
        _program_minimum(case, root, request)
        convert_work(owner, case, request)
        _bound(root)
        staging = root/'staging'
        staging.mkdir(exist_ok=True)
        policy = freeze(case, request['analysis_sha256'], analysis['scene_type'])
        command = [sys.executable, str(SCENARIO_ROOT/'json-coverter/skills/case-conversion/scripts/process.py'),
            '--input', str(case/'final/analysis.json'), '--output', str(staging/'target.json'),
            '--scene-type', analysis['scene_type'], '--result', str(staging/'conversion_result.json'),
            '--workflow-contract', 'video-case-world-behavior', '--road-source', bundle['parameters']['road_source'],
            '--policy', str(policy), '--policy-sha256', read_json(policy)['policy_sha256'],
            '--semantic-gap', str(case/'state/conversion_semantic_gap.json')]
        completed = subprocess.run(command, capture_output=True, text=True, timeout=120)
        if completed.returncode:
            raise contract.DraftBlocked('Original Converter failed: '+(completed.stderr or completed.stdout)[-2000:])
        manifest = _inspect_target(root, staging)
        atomic_json(staging/'manifest.json', manifest)
        _bound(root)
        os.rename(staging, root/'output')
        return check(source, root)
    except contract.SourceChanged:
        raise
    except Exception as error:
        # Preserve partial ConversionWork saves, but no failed attempt is a
        # public Target. A prior staging candidate cannot survive as target.json.
        target = root/'staging/target.json'
        if target.exists():
            os.replace(target, target.with_name('rejected_candidate.json'))
        _draft(root, receipt, status='blocked',
            failure=dict(code=getattr(error, 'code', type(error).__name__), message=str(error)),
            partial_conversion_work=str(case/'state/conversion_work/workflow.json'))
        progress = read_json(case/'state/progress.json')
        motion_request = (root/'inputs/core_motion.request.json').is_file()
        motion_terminal = (motion_request and not isinstance(error, contract.SourceChanged)
            and not any(row.get('status') in {'submitted', 'pending', 'running', 'submission_unresolved'}
                        for row in progress.get('sessions', []))
            and not progress.get('diagnostics', {}).get('unresolved_submissions')
            and getattr(error, 'code', str(error)) not in
                {'submission_unresolved', 'task_deadline_exceeded', 'task_status_unavailable'})
        if (root.name != MINIMUM_ROOT
                and not (root/'inputs/projection_receipt.json').is_file()
                and (motion_terminal if motion_request else _minimum_allowed(error))):
            return resume(source, root/MINIMUM_ROOT, backend=backend)
        return envelope(root, receipt)


def envelope(root, receipt):
    output = root/'output'
    if output.is_dir():
        # Callers only use this branch after check/inspection, never existence alone.
        manifest = read_json(output/'manifest.json')
        return dict(case_id=receipt['case_id'], status='unverified', delivery_level='unverified',
            generation_status='generated', target_path=str(output/'target.json'),
            manifest=str(output/'manifest.json'), strict_validate_completed='not_claimed',
            core_approximate_acceptance='not_claimed',
            **({'target_generated': manifest['target_generated'],
                'core_event_fidelity': manifest['core_event_fidelity']}
               if _semantic_revision(root) else {}))
    draft = read_json(root/'draft.json')
    return dict(case_id=receipt['case_id'], status=draft['status'], delivery_level='unverified',
        generation_status='not_generated', target_path=None, draft_path=str(root/'draft.json'),
        error=draft.get('failure'), conversion_work_prepared=(root/'cases'/receipt['case_id']/
            'state/conversion_work/workflow.json').is_file(),
        next_action='resume' if draft['status'] in {'conversion_prepared', 'needs_text_draft', 'needs_core_motion'} else 'inspect_draft',
        **({'core_action_omission': draft['core_action_omission']}
           if draft.get('core_action_omission') else {}),
        **({'target_generated': False, 'core_event_fidelity': (
            'failed' if (draft.get('core_action_omission') or {}).get('status') in
            {'recovery_failed', 'unverified_missing_core_action'} else 'pending')}
           if _semantic_revision(root) else {}),
        strict_validate_completed='not_claimed', core_approximate_acceptance='not_claimed')


def run(action, case_id, *, root=None):
    source = source_case(case_id)
    if read_json(source/'case.json').get('workflow_contract') == 'video-case-v2-short-aware-packed':
        from _lib.legacy.v2.minimum_fallback import run as run_v2_minimum
        return run_v2_minimum(action, source, root=root)
    if root is None and action in {'prepare', 'resume'}:
        primary = ROOT/case_id
        if (primary/'output/manifest.json').is_file():
            try:
                return check(source, primary)
            except motion_recovery.NeedsSemanticMotion:
                # Keep the prior immutable output as history. A revised
                # Analysis gets its own ConversionWork and Converter output.
                return {'prepare': prepare, 'resume': resume}[action](source, primary/MOTION_ROOT)
    if root is None and action in {'prepare', 'check'}:
        primary = ROOT/case_id
        revised = primary/MOTION_ROOT
        if (revised/'output/manifest.json').is_file():
            return check(source, revised)
        minimum = primary/MINIMUM_ROOT
        if not (primary/'output/manifest.json').is_file() and (minimum/'output/manifest.json').is_file():
            return check(source, minimum)
    return {'prepare': prepare, 'resume': resume, 'check': check}[action](source, root)
