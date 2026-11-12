"""CaseDelivery: deterministic pipeline adapter, never a model worker."""
from __future__ import annotations

import errno
import subprocess
import sys
from contextlib import nullcontext
from pathlib import Path
from string import Template
from _lib.runtime.orchestrator import validate_analysis
from _lib.contracts.v3 import CONTRACT, ContractError, build_analysis, completion_envelope, gate_degradations, validate_capabilities
from _lib.runtime.case_core import atomic_json, now, publish, read_json, sha256
from ..paths import SKILLS as HERE, SCENARIO_ROOT as ROOT, EXTRACTOR
from ..fingerprint import pipeline_fingerprint


def _fact_version(case):
    from _lib.runtime.fact_versions import ROOT as FACT_ROOT, current_facts
    return current_facts(case).version_id if (Path(case)/FACT_ROOT/'active.json').is_file() else None


def _assert_fact_version(case, version_id):
    if version_id is not None:
        from _lib.runtime.fact_versions import current_facts
        current_facts(case, version_id)


def _assert_road_minimum_binding(case, analysis):
    """Reject a changed Case before conversion or formal publication."""
    case = Path(case)
    ruling = analysis.get('quality_report', {}).get('road_minimum_decision')
    if ruling is None:
        return
    from _lib.runtime.execution_scope import inputs
    from _lib.world.readiness import validate_noncore_road_minimum
    route_path = case/'state/execution_route.json'
    route = read_json(route_path) if route_path.is_file() else {}
    world, behavior, scope = inputs(case, persist=False)
    input_hashes = ruling.get('input_hashes', {})
    if (route.get('road_minimum_decision') != ruling
            or route.get('input_hashes') != input_hashes
            or scope is None
            or analysis['quality_report'].get('execution_actor_scope', {}).get('sha256') != scope['sha256']
            or ruling.get('fact_version_id') != _fact_version(case)
            or any(not (case/name).is_file() or sha256(case/name) != expected
                   for name, expected in input_hashes.items())):
        raise ContractError('Noncore road ruling no longer binds the current Case',
                            rule_id='stale_version')
    policy_path = case/'state/execution_policy.json'
    if (not policy_path.is_file() or read_json(policy_path).get('noncore_road_minimum')
            != 'noncore_road_minimum.v1'):
        raise ContractError('Noncore road policy is no longer adopted', rule_id='stale_version')
    validate_noncore_road_minimum(
        ruling, world, behavior, scope_sha256=scope['sha256'],
        input_hashes=input_hashes, policy_sha256=sha256(policy_path))


def record_batch_class_fallbacks(analysis, progress):
    """Carry admitted field changes into the published observation audit."""
    rows=[]
    for entry in progress.get('diagnostics', {}).get('deterministic_candidate_repairs', []):
        for repair in entry.get('repairs', []):
            if repair.get('action') == 'unbound_vehicle_class_to_unknown':
                rows.append({'stage':entry['stage'], 'candidate_sha256':entry['input_sha256'],
                             **repair})
    if rows:
        analysis.setdefault('quality_report', {})['batch_field_fallbacks']=rows


class CaseDelivery:
    def ensure_synthetic_target(self, case, progress, cause):
        """Record a usable Target separately from strict source delivery."""
        case = Path(case)
        policy_path = case/'state/execution_policy.json'
        if (not policy_path.is_file() or read_json(policy_path).get('synthetic_target')
                != 'synthetic-target.v1'):
            return progress
        from _lib.delivery.synthetic_target import ensure
        try:
            result = ensure(case, cause)
        except Exception as error:
            progress.setdefault('diagnostics', {})['synthetic_target_failure'] = {
                'error': str(error)[:500], 'source_error': str(cause)[:500]}
            return progress
        progress.setdefault('diagnostics', {})['synthetic_target'] = result
        return progress

    def reconcile_analysis(self, case, analysis, behavior):
        """One narrow text task; invalid or unavailable advice leaves unknowns."""
        from _lib.contracts import analysis_reconciliation as contract
        case = Path(case)
        policy_path = case/'state/execution_policy.json'
        if (not policy_path.is_file() or read_json(policy_path).get('analysis_reconciliation')
                != contract.REVISION):
            return analysis
        from _lib.contracts.census_adjudication import proof as census_proof
        census = census_proof(case)
        request = contract.request(analysis, behavior, census)
        if not request['issues']:
            return analysis
        request_path = case/'state/analysis_reconciliation.request.json'
        decision_path = case/'state/analysis_reconciliation.json'
        candidate_path = case/'state/analysis_reconciliation.candidate.json'
        if request_path.is_file():
            if read_json(request_path) != request:
                raise ContractError('Frozen Analysis reconciliation request differs from current facts')
        else:
            atomic_json(request_path, request)
        if decision_path.is_file():
            accepted = read_json(decision_path)
            if accepted.get('revision') != contract.REVISION or accepted.get('request_sha256') != request['request_sha256']:
                raise ContractError('Accepted Analysis reconciliation differs from frozen request')
            return contract.apply(analysis, request, accepted['response'])

        def adopt(_session=None):
            from _lib.runtime.orchestrator import candidate_snapshot
            candidate, candidate_sha = candidate_snapshot(case, contract.STAGE, candidate_path)
            checked = contract.validate(request, candidate)
            atomic_json(decision_path, dict(revision=contract.REVISION,
                request_sha256=request['request_sha256'], response=checked,
                candidate_sha256=candidate_sha, status='accepted'))
            candidate_path.unlink(missing_ok=True)

        template = ROOT/'json-coverter/skills/case-conversion/references/analysis-reconciliation-prompt.md'
        prompt = Template(template.read_text(encoding='utf-8')).substitute(
            request_path=request_path, candidate_path=candidate_path)
        try:
            self.model_stage(case, contract.STAGE, 'json-coverter', prompt, adopt,
                             precheck=adopt if candidate_path.is_file() else None,
                             candidate_path=candidate_path, max_attempts=1)
        except Exception as error:
            # The unresolved source facts already remain in Builder Analysis.
            # A failed text interpretation is not permission to replace them.
            fallback = contract.unknown_decisions(request, str(error)[:300])
            atomic_json(decision_path, dict(revision=contract.REVISION,
                request_sha256=request['request_sha256'], response=fallback,
                candidate_sha256=None, status='kept_unknown'))
        return contract.apply(analysis, request, read_json(decision_path)['response'])

    def ensure_semantic_gap(self, case):
        """One hash-bound model decision before the deterministic Converter."""
        from _lib.contracts import semantic_gap
        from _lib.behavior.compile import evidence_frames
        from _lib.runtime.orchestrator import BackendError, candidate_snapshot
        case = Path(case)
        policy_path = case/'state/execution_policy.json'
        revision = (read_json(policy_path).get('conversion_semantic_gap')
                    if policy_path.is_file() else None)
        if revision not in semantic_gap.SUPPORTED_REVISIONS:
            return
        source = case/'final/analysis.json'
        analysis = read_json(source)
        eligible = semantic_gap.eligible(analysis, revision)
        if not eligible:
            return
        frames = None
        if revision == semantic_gap.IMAGE_REVISION:
            by_id = {row['frame_id']: row for row in evidence_frames(case)}
            frames = []
            for frame_id in sorted({row['frame_id'] for row in eligible}):
                frame = by_id.get(frame_id)
                path = Path(frame.get('path', '')) if frame else None
                if frame is None or not path.is_file() or sha256(path) != frame.get('sha256'):
                    raise ContractError('Semantic gap first-frame binding is unavailable or changed')
                frames.append({'frame_id': frame_id, 'path': str(path.resolve()),
                               'sha256': frame['sha256']})
        request_path = case/'state/conversion_semantic_gap.request.json'
        from _lib.contracts import motion_witnesses, bounded_execution
        from _lib.runtime import conversion_work
        witness_revision = None
        if request_path.is_file():
            witness_revision = read_json(request_path).get('policy', {}).get(motion_witnesses.POLICY_KEY)
        elif conversion_work.enabled(case) and analysis.get('metadata', {}).get(bounded_execution.KEY):
            witness_revision = motion_witnesses.REVISION
        if witness_revision:
            request = semantic_gap.semantic_motion.request(analysis, sha256(source), witness_revision=witness_revision)
        else:
            request = semantic_gap.request(analysis, sha256(source), revision=revision, frames=frames)
        decision_path = case/'state/conversion_semantic_gap.json'
        candidate_path = case/'state/conversion_semantic_gap.candidate.json'
        if request_path.is_file():
            if read_json(request_path) != request:
                raise ContractError('Frozen semantic gap request differs from accepted analysis')
        else:
            atomic_json(request_path, request)
        from _lib.runtime import conversion_work
        if conversion_work.enabled(case):
            conversion_work.run(self, case, request)
            return
        if decision_path.is_file():
            if read_json(decision_path).get('revision') != revision:
                raise ContractError('Semantic decision revision differs from frozen Case policy')
            semantic_gap.validate(analysis, sha256(source), read_json(decision_path))
            sessions = [row for row in self.progress(case).get('sessions', [])
                        if row.get('stage') == 'conversion_semantic_gap']
            unresolved = any(row.get('status') in {'submitted', 'pending', 'running'} or
                             (row.get('error_code') in {'task_deadline_exceeded', 'task_status_unavailable'}
                              and not row.get('deadline_task_resolved')) for row in sessions)
            if not unresolved:
                return

        def adopt(_session=None):
            if decision_path.is_file():
                semantic_gap.validate(analysis, sha256(source), read_json(decision_path))
                return
            if sha256(source) != request['analysis_sha256']:
                raise ContractError('Semantic gap analysis changed while model task ran')
            candidate, candidate_sha = candidate_snapshot(case, 'conversion_semantic_gap', candidate_path)
            if candidate.get('revision') != revision or candidate.get('request_sha256') != request['request_sha256']:
                raise ContractError('Semantic candidate differs from frozen request')
            decision = semantic_gap.candidate_to_decision(
                analysis, request['analysis_sha256'], candidate, candidate_sha, frames=frames)
            atomic_json(decision_path, decision)
            candidate_path.unlink(missing_ok=True)

        prompt_name = ('semantic-motion-prompt.md' if revision == semantic_gap.semantic_motion.REVISION
                       else 'semantic-gap-image-prompt.md' if revision == semantic_gap.IMAGE_REVISION
                       else 'semantic-gap-legacy-prompt.md' if revision == semantic_gap.LEGACY_REVISION
                       else 'semantic-gap-prompt.md')
        template = ROOT/'json-coverter/skills/case-conversion/references'/prompt_name
        prompt = Template(template.read_text(encoding='utf-8')).substitute(
            request_path=request_path, analysis_path=source, candidate_path=candidate_path)
        try:
            self.model_stage(case, 'conversion_semantic_gap', 'json-coverter', prompt,
                             adopt, precheck=adopt if candidate_path.is_file() else None,
                             candidate_path=candidate_path, max_attempts=1)
        except Exception as error:
            if revision == semantic_gap.semantic_motion.REVISION:
                # Lost temporal relations must never be silently replaced by
                # equal default speeds after an unavailable/invalid model task.
                raise
            # A task with uncertain remote state must still be reconciled by the
            # normal lifecycle. Only definitive non-submission or terminal
            # invalid output may fall back to the ordinary 50 m policy.
            sessions = [row for row in self.progress(case).get('sessions', [])
                        if row.get('stage') == 'conversion_semantic_gap']
            active = any(row.get('status') in {'submitted', 'pending', 'running'} or
                         (row.get('error_code') in {'task_deadline_exceeded', 'task_status_unavailable'}
                          and not row.get('deadline_task_resolved')) for row in sessions)
            safe = (isinstance(error, BackendError) and error.code in {
                'backend_unavailable', 'service_access_denied', 'gateway_429', 'task_cancelled'})
            safe |= (str(error) == 'model_attempts_exhausted' and bool(sessions)
                     and sessions[-1].get('status') == 'invalid_output'
                     and sessions[-1].get('error_code') in {
                         'missing_output', 'invalid_json', 'schema_validation_failed'})
            if active or not safe:
                raise
            atomic_json(decision_path, {
                'revision': revision, 'request_sha256': request['request_sha256'],
                'analysis_sha256': request['analysis_sha256'], 'gap_values_m': semantic_gap.GAPS_M,
                **({'frames': frames} if revision == semantic_gap.IMAGE_REVISION else {}),
                'status': 'skipped', 'reason': getattr(error, 'code', str(error))})

    def enable_straight_preview(self, case):
        """Explicit project-authorized migration; ordinary retry never enables it."""
        import copy
        from _lib.contracts.v3 import normalize_parameters, contract_fingerprint
        case=Path(case); record=read_json(case/'case.json'); old=copy.deepcopy(record['parameters'])
        if self.progress(case).get('status') in {'done','done_degraded'}:
            raise ContractError('Do not migrate a delivered Case')
        updated=normalize_parameters(old)
        updated['fallback_policy']['straight_preview']['enabled']=True
        updated['allowed_degradations']=sorted(set(updated['allowed_degradations'])|{'straight_preview_policy_default'})
        if old==updated: return
        audit=case/'diagnostics/preview_policy_adoption.json'
        if audit.exists() and read_json(audit)['parameters_after']!=updated:
            raise ContractError('Existing preview policy adoption differs')
        if not audit.exists():
            atomic_json(audit,{'reason':'Explicit user authorization: relation-preserving preview',
                'parameters_before':old,'parameters_after':updated,'case_record_sha256_before':sha256(case/'case.json'),
                'fingerprint_before':contract_fingerprint(old),'fingerprint_after':contract_fingerprint(updated),'at':now()})
        record['parameters']=updated; atomic_json(case/'case.json',record)

    @staticmethod
    def validate_completed(case, progress):
        """A completed Case is immutable while all four published artifacts remain valid."""
        version_id = _fact_version(case)
        if version_id is not None:
            if progress.get('delivered_fact_version_id') != version_id:
                raise ContractError('Completed Target belongs to another fact version')
            from _lib.behavior.input_binding import accepted_validation
            accepted_validation(case, version_id)
        from _lib.runtime.identity_revision import validate_lineage, proof
        validate_lineage(case)
        proof(case)
        from _lib.contracts.census_adjudication import proof as census_proof, validate_behavior
        if census_proof(case):
            validate_behavior(case, read_json(case/'state/behavior.json'))
            from _lib.contracts.census_scope import verified_review
            verified_review(case, read_json(case/'state/behavior.json'))
        required={"analysis.json","report.md","report.docx","target.json"}
        artifacts=progress.get("artifacts") or {}
        if set(artifacts) != required:
            raise ContractError("completed Case artifact set is incomplete")
        for name, record in artifacts.items():
            path=Path(record.get("path", ""))
            if not path.is_file() or sha256(path) != record.get("sha256"):
                raise ContractError(f"completed Case artifact hash mismatch: {name}")
        analysis=read_json(case/"final/analysis.json")
        validate_analysis(analysis,read_json(case/"state/actor_registry.json"))
        from _lib.contracts.core_lane_continuity import validate_analysis_motion
        validate_analysis_motion(case, read_json(case/'state/behavior.json'), analysis)
        conversion=read_json(case/"state/conversion_result.json")
        if (conversion.get("status") not in {"done", "done_degraded"}
                or conversion.get("input_sha256") != sha256(case/"final/analysis.json")
                or conversion.get("target_sha256") != sha256(case/"final/target.json")):
            raise ContractError("completed Case conversion binding mismatch")
        from _lib.contracts.conversion_policy import enabled, validate, digest, default_completion
        if enabled(case):
            policy = validate(conversion.get('conversion_policy'),
                              input_sha256=sha256(case/'final/analysis.json'),
                              scene_type=analysis['scene_type'], road_source=conversion.get('road_source'))
            if (policy.get('case_id') != case.name or conversion.get('policy_sha256') != policy['policy_sha256']
                    or policy['parameters_sha256'] != digest(read_json(case/'case.json')['parameters'])
                    or read_json(case/'state/conversion_policies'/f"{policy['policy_sha256']}.json") != policy):
                raise ContractError('completed Case frozen policy binding mismatch')
            if policy.get('default_completion') != default_completion(case):
                raise ContractError('completed Case conversion default adoption binding mismatch')
        semantic_policy = case/'state/execution_policy.json'
        semantic_revision = (read_json(semantic_policy).get('conversion_semantic_gap')
                             if semantic_policy.is_file() else None)
        from _lib.contracts import semantic_gap
        if semantic_revision in semantic_gap.SUPPORTED_REVISIONS:
            decision_path = case/'state/conversion_semantic_gap.json'
            frames = (read_json(decision_path).get('frames') if decision_path.is_file()
                      and semantic_revision == semantic_gap.IMAGE_REVISION else None)
            expected = semantic_gap.request(analysis, sha256(case/'final/analysis.json'),
                                            revision=semantic_revision, frames=frames)
            if expected['eligible']:
                if not decision_path.is_file():
                    raise ContractError('completed Case semantic gap decision is missing')
                decision = semantic_gap.validate(analysis, expected['analysis_sha256'],
                                                 read_json(decision_path))
                from _lib.runtime import conversion_work
                if conversion_work.enabled(case):
                    conversion_work.validate_committed(case, expected, decision)
                if decision.get('revision') != semantic_revision:
                    raise ContractError('completed Case semantic revision differs')
                for frame in decision.get('frames', []):
                    path = Path(frame['path'])
                    if not path.is_file() or sha256(path) != frame['sha256']:
                        raise ContractError('completed Case semantic gap frame binding changed')
                if conversion.get('semantic_gap') != decision:
                    raise ContractError('completed Case semantic gap receipt differs')
        return True

    def build(self, case):
        version_id = _fact_version(case)
        progress = self.progress(case); progress["substage"] = "building"; self.save_progress(case, progress)
        parameters = read_json(case / "case.json")["parameters"]
        from _lib.behavior.compile import execution_issues, evidence_frames
        from _lib.runtime.execution_scope import inputs, registry as scope_registry, annotate
        world, behavior, audit = inputs(case)
        registry = scope_registry(case, behavior)
        issues = execution_issues(behavior, world, registry, evidence_frames(case))
        review = progress.get('diagnostics', {}).get('lane_review')
        if review and review.get('status') != 'completed':
            issues.append({'rule_id':'motion.lane_visual_review', 'actor_id':review['actor_id'],
                           'message':'Requested lane image review remains unresolved; original observations retained'})
        preview_record=progress.get('diagnostics',{}).get('preview_review')
        if preview_record and preview_record.get('status')!='completed':
            issues.append({'rule_id':'motion.preview_review_pending','actor_id':None,'message':'Preview image review has not been accepted'})
        certificate=case/'diagnostics/preview_relations.json'
        review=read_json(certificate) if certificate.exists() else None
        if review:
            for frame in review.get('review_frames',[]):
                if not Path(frame['path']).is_file() or sha256(Path(frame['path']))!=frame['sha256']:
                    raise ContractError('Preview reviewed source image changed')
        from _lib.runtime.routing import automatic, record_route, effective_parameters, CapabilityBlocked
        road_minimum_decision = None
        if automatic(case):
            route = record_route(case, world, behavior, parameters,
                                 [i for i in issues if i['rule_id'] != 'motion.preview_review_pending'])
            road_minimum_decision = route.get('road_minimum_decision')
            if road_minimum_decision is not None and (
                    audit is None or road_minimum_decision['execution_scope_sha256'] != audit['sha256']):
                raise ContractError('Noncore road decision differs from the retained execution scope',
                                    rule_id='stale_version')
            if route['route'] == 'unsupported': raise CapabilityBlocked(route['issues'])
            if route['route'] == 'intersection_preview':
                from _lib.stages.intersection import build_intersection
                analysis, status = build_intersection(case, world, behavior, parameters)
                status = annotate(case, analysis, status)
                record_batch_class_fallbacks(analysis, progress)
                analysis = self.reconcile_analysis(case, analysis, behavior)
                from _lib.contracts.core_lane_continuity import validate_analysis_motion
                validate_analysis_motion(case, read_json(case/'state/behavior.json'), analysis)
                gate_degradations(analysis['quality_report'])
                validate_analysis(analysis, registry)
                _assert_fact_version(case, version_id)
                atomic_json(case/'final/analysis.json', analysis)
                return analysis, status, None
            parameters = effective_parameters(parameters, route)
            if route['route'] != 'straight_preview':
                issues = [i for i in issues if i['rule_id'] != 'motion.preview_review_pending']
                review = None
        from _lib.world.gaps import enabled as new_facts
        probe_path = case/'state/probe.json'
        analysis, status = build_analysis(world, behavior, parameters, observation_issues=issues,preview_review=review,observation_contract=new_facts(case),
                                         media_probe=read_json(probe_path)['probe'] if probe_path.is_file() else None,
                                         road_minimum_decision=road_minimum_decision)
        status = annotate(case, analysis, status)
        record_batch_class_fallbacks(analysis, progress)
        analysis = self.reconcile_analysis(case, analysis, behavior)
        from _lib.contracts.core_lane_continuity import validate_analysis_motion
        validate_analysis_motion(case, read_json(case/'state/behavior.json'), analysis)
        if status == 'needs_review':
            if automatic(case) and route['route']=='blocked':
                root_issues=route.get('issues',[])
                analysis['quality_report']['execution_issues']=root_issues+[
                    i for i in analysis['quality_report']['execution_issues'] if i not in root_issues]
            from _lib.contracts.v3 import _schema
            _schema('analysis.schema.json', analysis)
            _assert_fact_version(case, version_id)
            atomic_json(case/'final/analysis.json', analysis)
            return analysis, status, None
        gate_degradations(analysis["quality_report"])
        if status == "placeholder_generated":
            diagnostics = case / "diagnostics"; diagnostics.mkdir(exist_ok=True)
            target = diagnostics / "placeholder_target.json"; atomic_json(target, {"publishable": False, "analysis": analysis})
            return analysis, status, target
        validate_analysis(analysis, read_json(case / "state/actor_registry.json"))
        _assert_fact_version(case, version_id)
        atomic_json(case / "final/analysis.json", analysis)
        return analysis, status, None

    def local_delivery_step(self, case, stage, action):
        """Retry a repeatable delivery action once on transient local I/O only."""
        try:
            return action()
        except OSError as error:
            if error.errno not in {errno.EAGAIN, errno.EBUSY, errno.EINTR, errno.ETIMEDOUT}:
                raise
            state = self.progress(case)
            records = state.setdefault("diagnostics", {}).setdefault("local_delivery_retries", [])
            digest = sha256(case/"final/analysis.json")
            if any(item["stage"] == stage and item["input_sha256"] == digest for item in records):
                raise
            records.append({"stage":stage, "input_sha256":digest, "errno":error.errno,
                            "message":str(error), "at":now()})
            self.save_progress(case,state)
            self.sleep(1)
            return action()

    def strict_convert(self, case):
        version_id = _fact_version(case)
        from _lib.world.actor_facts import strict_enabled
        if version_id is not None:
            from _lib.behavior.input_binding import accepted_validation
            accepted_validation(case, version_id)
        if version_id is not None or strict_enabled(case):
            from _lib.world.actor_facts import (frames_for, resolved_lane_issues,
                                                 execution_lane_comparison_issues)
            world = read_json(case/'state/world_context.json')
            frames = frames_for(case)
            behavior_path = case/'state/behavior.json'
            behavior = read_json(behavior_path) if behavior_path.is_file() else None
            issues = resolved_lane_issues(world, frames, behavior)
            if issues:
                rule = ('undetermined' if any('undetermined' in row['diagnostics']
                                              for row in issues) else 'world.question_contract')
                raise ContractError('Current World lane conclusion needs review: ' +
                                    ', '.join(row['issue_id'] for row in issues),
                                    rule_id=rule)
            if behavior is not None:
                comparisons = execution_lane_comparison_issues(world, behavior, frames)
                if comparisons:
                    code = ('fact_conflict' if any(row['verdict'] == 'fact_conflict'
                                                   for row in comparisons) else 'undetermined')
                    raise ContractError('Current World lane conclusions require review: ' +
                                        ', '.join('/'.join(row['issue_ids']) for row in comparisons),
                                        rule_id=code)
        progress = self.progress(case); progress["substage"] = "capability_gate"; self.save_progress(case, progress)
        analysis = read_json(case / "final/analysis.json"); scene = analysis["scene_type"]
        from _lib.contracts.core_lane_continuity import validate_analysis_motion
        validate_analysis_motion(case, read_json(case/'state/behavior.json'), analysis)
        _assert_road_minimum_binding(case, analysis)
        parameters = read_json(case/"case.json")["parameters"]
        validate_capabilities(parameters["road_source"], scene)
        progress["substage"] = "conversion"; self.save_progress(case, progress)
        converter = ROOT / "json-coverter/skills/case-conversion/scripts/process.py"
        from _lib.contracts.conversion_policy import freeze
        policy_path = freeze(case, sha256(case/'final/analysis.json'), scene)
        policy_args = (['--policy', str(policy_path), '--policy-sha256', read_json(policy_path)['policy_sha256']]
                       if policy_path else [])
        semantic_path = case/'state/conversion_semantic_gap.json'
        semantic_args = ['--semantic-gap', str(semantic_path)] if semantic_path.is_file() else []
        result = subprocess.run([sys.executable, str(converter), "--input", str(case/"final/analysis.json"), "--output", str(case/"final/target.json"),
            "--scene-type", scene, "--result", str(case/"state/conversion_result.json"), "--workflow-contract", CONTRACT,
            "--road-source", parameters["road_source"], *policy_args, *semantic_args], capture_output=True, text=True, timeout=120)
        _assert_fact_version(case, version_id)
        _assert_road_minimum_binding(case, analysis)
        if result.returncode:
            receipt = case/'state/conversion_result.json'
            record = read_json(receipt) if receipt.is_file() else {}
            message = record.get('message') or result.stderr[-2000:]
            diagnostics = record.get('diagnostics') or {}
            error = ContractError('conversion_failed: ' + (message or 'converter returned no diagnostic'),
                                  rule_id=diagnostics.get('rule_id'), actor_id=diagnostics.get('actor_id'))
            error.diagnostics = {key: diagnostics[key] for key in (
                'rule_id', 'path', 'related_paths', 'kind', 'owner', 'retry_kind',
                'actor_id', 'input_sha256', 'recovery_hint') if key in diagnostics}
            error.diagnostics.update(diagnostics)
            error.code = record.get('error_code') or 'conversion_failed'
            raise error
        progress = self.progress(case); progress["substage"] = "projection_gate"; self.save_progress(case, progress)
        from _lib.delivery.checks import validate_target
        receipt = read_json(case / "state/conversion_result.json")
        if semantic_path.is_file() and receipt.get('semantic_gap') != read_json(semantic_path):
            raise ContractError('Conversion receipt semantic gap decision differs')
        if policy_path and receipt.get('policy_sha256') != read_json(policy_path)['policy_sha256']:
            raise ContractError('Conversion receipt is not bound to the frozen Case policy')
        checks = validate_target(analysis, read_json(case / "final/target.json"),
                                 parameters["road_source"], receipt)
        atomic_json(case / "state/target_checks.json", checks)

    def finish_v3(self, case, status):
        version_id = _fact_version(case)
        _assert_road_minimum_binding(case, read_json(case/'final/analysis.json'))
        if version_id is not None:
            from _lib.behavior.input_binding import accepted_validation
            accepted_validation(case, version_id)
        # report generation remains deterministic and uses the accepted analysis.
        analysis = case / "final/analysis.json"
        result = subprocess.run([sys.executable, str(HERE/'case-delivery/scripts/report.py'), "--input", str(analysis), "--markdown", str(case/"final/report.md"), "--docx", str(case/"final/report.docx"),
            '--conversion', str(case/'state/conversion_result.json'), '--checks', str(case/'state/target_checks.json'),
            '--target', str(case/'final/target.json'), '--delivery-status', status], timeout=120)
        if result.returncode: raise ContractError("publish_failed")
        from _lib.runtime.fact_versions import _lock
        with _lock(case) if version_id is not None else nullcontext():
            _assert_fact_version(case, version_id)
            artifacts = publish(case, EXTRACTOR / "media/output")
            progress = self.progress(case)
            diagnostics = progress.get('diagnostics', {})
            if version_id is not None:
                diagnostics['target_delivery'] = dict(status='delivered',
                    validation_version_id=version_id,
                    target_sha256=sha256(case/'final/target.json'))
            if 'analysis' in diagnostics:
                analysis_path = case/'final/analysis.json'
                diagnostics['analysis'] = {'path':str(analysis_path), 'sha256':sha256(analysis_path),
                    'execution_issues':read_json(analysis_path)['quality_report'].get('execution_issues', [])}
            envelope = completion_envelope(status, artifacts=artifacts, diagnostics=diagnostics)
            progress.update(envelope); progress.update({"phase": "done", "error":None,
                "substage": "render_verification", "pipeline_fingerprint": pipeline_fingerprint(),
                "updated_at": now()})
            if version_id is not None:
                progress['delivered_fact_version_id'] = version_id
            self.save_progress(case, progress)
        return progress
