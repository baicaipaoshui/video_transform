#!/usr/bin/env python3
"""Public v3 lifecycle facade: routing, recovery and task budget ownership.

Domain adapters live in _lib/stages; worker instructions live in Skill references.
Historical imports and the process.py command remain compatible.
"""
from __future__ import annotations

import copy
import subprocess
import sys
from pathlib import Path
from _lib.runtime.orchestrator import Orchestrator, validate_analysis
from _lib.contracts.v3 import CONTRACT, ContractError, completion_envelope, normalize_parameters, validate_capabilities
from _lib.runtime.case_core import Cases, now, read_json, sha256
from _lib.paths import SKILLS as HERE, SCENARIO_ROOT as ROOT, EXTRACTOR
from _lib.fingerprint import pipeline_fingerprint
from _lib.prompts import (world_request_text, batch_request_text_v3,
                          behavior_request_text, readjudication_request_text)
from _lib.stages.world import WorldStage
from _lib.stages.batch import BatchLifecycle, freeze_v3
from _lib.stages.reconciliation import WorldReconciliation
from _lib.stages.behavior import BehaviorStage
from _lib.stages.delivery import CaseDelivery


class V3Orchestrator(WorldStage, BatchLifecycle, WorldReconciliation, BehaviorStage, CaseDelivery, Orchestrator):
    TRANSPORT_RECOVERY_CODES = {
        "timeout", "model_stream_interrupted", "gateway_429", "gateway_5xx",
        "task_status_unavailable", "task_deadline_exceeded", "empty_response",
    }

    def _conversion_retry_binding(self, case, progress, *, reconcile=False):
        """Bind an extra conversion attempt to frozen input and terminal timeouts."""
        stage = "conversion_semantic_gap"
        sessions = [row for row in progress.get("sessions", []) if row.get("stage") == stage]
        if not sessions or progress["sessions"][-1].get("stage") != stage:
            raise ContractError("conversion recovery requires the latest failed model stage")
        if progress.get("diagnostics", {}).get("unresolved_submissions"):
            raise ContractError("conversion recovery refused while a submission is unresolved")
        if any(row.get("status") in {"submitted", "pending", "running"} for row in progress.get("sessions", [])):
            raise ContractError("conversion recovery refused while a task may still be active")
        if any(row.get("status") != "failed" or row.get("error_code") != "task_deadline_exceeded"
               for row in sessions):
            raise ContractError("conversion recovery requires terminal deadline failures only")
        case = Path(case)
        request = case/"state/conversion_semantic_gap.request.json"
        analysis = case/"final/analysis.json"
        if (case/"state/conversion_semantic_gap.json").exists():
            raise ContractError("conversion recovery decision already exists; resume validation instead")
        if not request.is_file() or not analysis.is_file():
            raise ContractError("conversion recovery requires frozen request and analysis")
        binding = {"request_file_sha256": sha256(request), "analysis_sha256": sha256(analysis),
                   "prior_task_ids": [row.get("task_id") for row in sessions]}
        if not all(binding["prior_task_ids"]) or read_json(request).get("analysis_sha256") != binding["analysis_sha256"]:
            raise ContractError("conversion recovery input or task binding is invalid")
        for row in sessions:
            if row.get("deadline_task_resolved") is True:
                continue
            if not reconcile:
                raise ContractError("conversion recovery requires resolved tasks")
            remote = self.backend.poll(row["task_id"], "json-coverter")
            if remote.get("status") == "completed":
                raise ContractError("conversion task completed remotely; resume validation without authorization")
            if remote.get("status") != "failed":
                raise ContractError("conversion recovery refused while a task may still be active")
            if remote.get("error") not in {"timeout", "task_deadline_exceeded"}:
                raise ContractError("conversion recovery requires a confirmed remote timeout")
            row["deadline_task_resolved"] = True
            self.save_progress(case, progress)
        return binding

    def authorize_transport_retry(self, case, stage, *, resume_session_id=None):
        """Persist one user-authorized transport retry without resetting the Case budget."""
        conversion = stage == "conversion_semantic_gap"
        from _lib.runtime.conversion_work import enabled as continuation_enabled
        if conversion and continuation_enabled(case):
            raise ContractError('Checkpoint conversion uses its bounded continuation budget; legacy session grants do not apply')
        if resume_session_id is not None and not conversion:
            raise ContractError("session continuation only supports conversion_semantic_gap")
        if not conversion and (not isinstance(stage, str) or not stage.startswith("batch_") or not stage[6:].isdigit()):
            raise ContractError("transport recovery only supports batch_NNNN or conversion_semantic_gap")
        progress = self.progress(case)
        if progress.get("status") != "failed":
            raise ContractError("transport recovery requires a failed Case")
        if stage in progress.get("completed_batches", []):
            raise ContractError("transport recovery stage is already completed")
        error = progress.get("error") or {}
        binding = None
        if conversion:
            if (error.get("stage") not in {stage, "building", "aggregating", "converting", "conversion"}
                    or error.get("code") not in {"task_deadline_exceeded", "model_attempts_exhausted", "model_recovery_budget_exhausted"}):
                raise ContractError("conversion recovery does not match the current failure")
            binding = self._conversion_retry_binding(case, progress, reconcile=True)
        elif error.get("stage") != stage:
            raise ContractError("transport recovery stage does not match the current failure")
        sessions = [item for item in progress.get("sessions", []) if item.get("stage") == stage]
        if not sessions:
            raise ContractError("transport recovery requires prior task history")
        if resume_session_id is not None and (not resume_session_id or resume_session_id != sessions[-1].get("session_id")):
            raise ContractError("conversion continuation must use the latest failed session")
        if any(item.get("status") in {"submitted", "pending", "running"} for item in sessions):
            raise ContractError("transport recovery refused while a task may still be active")
        failures = [item.get("error_code") for item in sessions]
        if any(not code or code not in self.TRANSPORT_RECOVERY_CODES for code in failures):
            raise ContractError("transport recovery requires transport-only failures for the selected stage")
        diagnostics = progress.setdefault("diagnostics", {})
        authorizations = diagnostics.setdefault("transport_recovery_authorizations", [])
        existing = [item for item in authorizations if item.get("stage") == stage]
        latest = existing[-1] if existing else None
        if latest and latest.get("status") == "authorized":
            if latest.get("resume_session_id") != resume_session_id:
                raise ContractError("transport recovery authorization session choice changed")
            if conversion and latest.get("conversion_binding") != binding:
                raise ContractError("conversion recovery authorization binding changed")
            return latest
        if latest:
            prior_count = latest.get("prior_session_count", len(latest.get("prior_failure_codes", [])))
            if len(sessions) <= prior_count:
                raise ContractError("transport recovery reauthorization requires a new failed transport task")
            new_failures = [item.get("error_code") for item in sessions[prior_count:]]
            if any(not code or code not in self.TRANSPORT_RECOVERY_CODES for code in new_failures):
                raise ContractError("transport recovery reauthorization requires a new transport-only failure")
        sequence = len(existing) + 1
        authorization = {
            "authorization_id": f"transport-retry:{stage}:{sequence}",
            "sequence": sequence,
            "stage": stage,
            "status": "authorized",
            "authorized_at": now(),
            "reason": "explicit_user_authorization_after_transport_only_failures",
            "prior_failure_codes": failures,
            "prior_session_count": len(sessions),
            "preserved_completed_batches": list(progress.get("completed_batches", [])),
        }
        if conversion:
            authorization["conversion_binding"] = binding
        if resume_session_id:
            authorization["resume_session_id"] = resume_session_id
        authorizations.append(authorization)
        self.save_progress(case, progress)
        return authorization

    def submit_model_task(self, case, key, agent, prompt):
        from _lib.runtime import behavior_scope_review
        if key in behavior_scope_review.STAGES:
            return behavior_scope_review.submit(self, case,
                lambda: super(V3Orchestrator, self).submit_model_task(case, key, agent, prompt))
        from _lib.runtime.world_adjudication import submit_continuation
        continued = submit_continuation(self, case, key, agent, prompt)
        if continued is not None:
            return continued
        progress = self.progress(case)
        diagnostics = progress.get("diagnostics", {})
        reservations = diagnostics.get("model_task_reservations", [])
        reservation = reservations[-1] if reservations else {}
        grant = next((row for row in diagnostics.get("transport_recovery_authorizations", [])
                      if row.get("authorization_id") == reservation.get("authorization_id")), None)
        if key != "conversion_semantic_gap" or not grant or not grant.get("resume_session_id"):
            from _lib.runtime.census_task_accounting import submit
            return submit(self, case, key,
                lambda: super(V3Orchestrator, self).submit_model_task(case, key, agent, prompt))
        from _lib.runtime.orchestrator import BackendError
        try:
            binding = self._conversion_retry_binding(case, progress)
        except ContractError as error:
            raise BackendError("source_changed", str(error), submission_not_sent=True) from error
        if (agent != "json-coverter" or grant.get("stage") != key or grant.get("status") != "consumed"
                or reservation.get("stage") != key or reservation.get("status")
                or grant.get("conversion_binding") != binding):
            raise BackendError("source_changed", "Conversion continuation authorization changed", submission_not_sent=True)
        latest = progress["sessions"][-1]
        if latest.get("session_id") != grant["resume_session_id"]:
            raise BackendError("source_changed", "Conversion continuation session changed", submission_not_sent=True)
        continuation = ("继续本会话此前未完成的同一 Case 转换任务。上次已超时结束，本次获得一次显式恢复授权。"
                        "保留已有上下文；无需从头重复推导已确定的结论。以以下冻结请求为准完成 candidate，"
                        "不得修改 analysis、请求或 Case 状态。写入后停止，交由编排器验收。\n\n")
        return self.backend.submit(agent, continuation + prompt, session_id=grant["resume_session_id"])

    def before_model_attempt(self, case, key):
        """Two additional model tasks across the existing standard v3 Case.

        Reconstruct usage from durable sessions, including prior retry epochs.
        Normal World/Behavior/Batch tasks remain available after repairs elsewhere.
        Standard v3 reserves at most two extra model tasks beyond initial stages.
        """
        from _lib.contracts.fidelity import enabled, preflight
        from _lib.world.semantic import legacy_topology_enabled
        topology_planned = legacy_topology_enabled(case)
        if enabled(case): preflight()
        from _lib.runtime.downstream_scope_review import KEY as scope_key, before_attempt
        if key == scope_key:
            before_attempt(case, self.progress(case))
        from _lib.runtime.bounded_recovery import preflight_total
        preflight_total(case, self.progress(case), key)
        from _lib.runtime.behavior_scope_review import reserve as reserve_scope_review
        if reserve_scope_review(self, case, key):
            return
        from _lib.runtime.routing import automatic
        preview_planned = automatic(case) and (Path(case)/'state/execution_route.json').is_file() and read_json(Path(case)/'state/execution_route.json').get('route') == 'straight_preview'
        planned_preview = preview_planned and key == 'preview_review'
        from _lib.stages.intersection import enabled as intersection_enabled
        intersection_planned = intersection_enabled(case) and (Path(case)/'state/execution_route.json').is_file() and read_json(Path(case)/'state/execution_route.json').get('route') == 'intersection_preview'
        semantic_policy = Path(case)/'state/execution_policy.json'
        from _lib.contracts.semantic_gap import SUPPORTED_REVISIONS as semantic_revisions
        semantic_planned = (semantic_policy.is_file() and
                            read_json(semantic_policy).get('conversion_semantic_gap')
                            in semantic_revisions)
        reconciliation_planned = (semantic_policy.is_file() and
            read_json(semantic_policy).get('analysis_reconciliation') == 'analysis-reconciliation.v1')
        from _lib.runtime.conversion_work import enabled as continuation_enabled, STAGES as conversion_stages
        continuation = continuation_enabled(case)
        from _lib.runtime.world_adjudication import reserve_contract_repair
        if reserve_contract_repair(self,case,key):
            return
        from _lib.contracts.census_adjudication import MERGE_REVISIONS
        closure_planned = (semantic_policy.is_file() and
            read_json(semantic_policy).get('world_census_adjudication') in MERGE_REVISIONS
            and (Path(case)/'diagnostics/world_census_adjudication/initial/request.json').is_file())
        progress = self.progress(case)
        diagnostics = progress.setdefault("diagnostics", {})
        structured_stage={
            'post_behavior_world_readjudication':'world_readjudication',
            'behavior':'behavior_rebuild',
            'intersection_review':'review_rebuild',
        }.get(key)
        structured_active=(key=='post_behavior_world_readjudication' or
                           diagnostics.get('intersection_structured_closure_active') is True)
        if structured_stage and structured_active:
            reservations=diagnostics.setdefault('intersection_structured_closure_reservations',[])
            free={'abandoned_before_task_id','credited_pipeline_contract_fault'}
            used=sum(item.get('stage')==structured_stage and item.get('status') not in free for item in reservations)
            diagnostics['intersection_structured_closure_budget']={
                'limits':{'world_readjudication':1,'behavior_rebuild':1,'review_rebuild':1},
                'used':{name:sum(item.get('stage')==name and item.get('status') not in free for item in reservations)
                        for name in ('world_readjudication','behavior_rebuild','review_rebuild')},
                'next_stage':structured_stage,
            }
            if used >= 1:
                self.save_progress(case,progress)
                raise ContractError('intersection_structured_closure_budget_exhausted')
            reservations.append({'stage':structured_stage,'model_stage':key,'at':now(),
                                 'origin':'reserved_before_submission'})
            diagnostics['intersection_structured_closure_budget']['used'][structured_stage]=used+1
            self.save_progress(case,progress)
            return
        reservations = diagnostics.get("model_task_reservations")
        if reservations is None:
            reservations = []
            seen = set()
            for session in progress.get("sessions", []):
                stage = session.get("stage", "")
                if stage in {'behavior_scope_review', 'behavior_scope_quota_retry', 'behavior_scope_quota_retry_2'}:
                    continue  # Separate explicit one-task observation supplement.
                normal = stage in {"world", "behavior"} or (stage.startswith("batch_") and stage[6:].isdigit())
                normal |= topology_planned and stage == 'world_topology'
                normal |= preview_planned and stage == 'preview_review'
                normal |= intersection_planned and stage == 'intersection_review'
                normal |= semantic_planned and stage == 'conversion_semantic_gap'
                normal |= continuation and stage == 'conversion_relations'
                normal |= reconciliation_planned and stage == 'analysis_reconciliation'
                normal |= closure_planned and stage == 'world_closure'
                reservations.append({"stage": stage, "additional": not normal or stage in seen, "origin": "session_history"})
                seen.add(stage)
            diagnostics["model_task_reservations"] = reservations
        from _lib.runtime.world_adjudication import continuation_binding, reusable_reservation
        census_binding = continuation_binding(case, key)
        if reusable_reservation(case, progress, census_binding):
            return
        from _lib.runtime import census_task_accounting
        census_task_binding = census_task_accounting.binding(self, case, key, progress)
        if census_task_accounting.reuse(progress, census_task_binding):
            return
        # Migrate an older definitive submit failure: three recorded submit
        # errors and no session/task for that planned attempt means no durable
        # model task exists and the reservation must not consume recovery budget.
        planned_attempt=progress.setdefault("attempts",{}).get(key,0)+1
        submit_errors=[item for item in diagnostics.get("submission_errors",[])
                       if item.get("stage")==key and item.get("planned_attempt")==planned_attempt]
        session_exists=any(item.get("stage")==key and item.get("attempt")==planned_attempt
                           for item in progress.get("sessions",[]))
        if (not census_binding and len({item.get("submission_try") for item in submit_errors}) >= 3 and not session_exists
                and all(item.get("error_code") == "gateway_429" for item in submit_errors)):
            for item in reversed(reservations):
                if item.get("stage")==key and item.get("origin")=="reserved_before_submission" and not item.get("status"):
                    item.update(status="abandoned_before_task_id", reason=submit_errors[-1].get("error_code"))
                    break
        from _lib.world.compilation_repair import task_binding, require_task_available
        frame_repair_binding = task_binding(case, key)
        require_task_available(case, frame_repair_binding, reservations)
        repair_reservation = ({'initial_frame_repair_binding': frame_repair_binding}
                              if frame_repair_binding else {})
        if census_task_binding is not None:
            repair_reservation.update(census_task_binding=census_task_binding, status='reserved')
        if census_binding:
            repair_reservation['world_continuation_binding'] = census_binding
        free_status={'abandoned_before_task_id','credited_pipeline_contract_fault'}
        seen = {item["stage"] for item in reservations if item.get('status') != 'abandoned_before_task_id'}
        additional = sum(item["additional"] for item in reservations
                         if item.get("status") not in free_status
                         and item.get("budget_scope") != "authorized_transport_recovery")
        normal = key in {"world", "behavior"} or (key.startswith("batch_") and key[6:].isdigit())
        normal |= topology_planned and key == 'world_topology'
        normal |= planned_preview
        normal |= intersection_planned and key == 'intersection_review'
        normal |= semantic_planned and key == 'conversion_semantic_gap'
        normal |= continuation and key == 'conversion_relations'
        normal |= reconciliation_planned and key == 'analysis_reconciliation'
        normal |= closure_planned and key == 'world_closure'
        needs_additional = not normal or key in seen or bool(frame_repair_binding)
        if continuation and key in conversion_stages and needs_additional:
            conversion_extra = sum(item.get('additional', False) for item in reservations
                                   if item.get('stage') in conversion_stages
                                   and item.get('status') not in free_status)
            if conversion_extra >= 1:
                raise ContractError('conversion_continuation_limit_exhausted')
        from _lib.runtime import bounded_recovery
        if bounded_recovery.enabled(case):
            return bounded_recovery.reserve(self, case, key, reservations, needs_additional, repair_reservation)
        diagnostics["model_recovery_budget"] = {
            "limit": 2, "used": additional, "remaining": max(0, 2-additional),
            "next_stage": key, "next_is_additional": needs_additional,
        }
        authorization = next((item for item in diagnostics.get("transport_recovery_authorizations", [])
                              if item.get("stage") == key and item.get("status") == "authorized"), None)
        # Consume conversion grants on the first next attempt, even if ordinary
        # budget remains; a grant must never linger for a later model task.
        if needs_additional and (additional >= 2 or (key == "conversion_semantic_gap" and authorization)):
            if authorization is None:
                self.save_progress(case, progress)
                raise ContractError("model_recovery_budget_exhausted")
            if key == "conversion_semantic_gap":
                if authorization.get("conversion_binding") != self._conversion_retry_binding(case, progress):
                    raise ContractError("conversion recovery authorization binding changed")
            authorization.update(status="consumed", consumed_at=now())
            reservations.append({"stage": key, "additional": True, "at": now(),
                                 "origin": "reserved_before_submission",
                                 "budget_scope": "authorized_transport_recovery",
                                 "authorization_id": authorization["authorization_id"], **repair_reservation})
            diagnostics["model_recovery_budget"]["authorized_transport_retry"] = {
                "stage": key, "authorization_id": authorization["authorization_id"], "status": "consumed"
            }
            self.save_progress(case, progress)
            return
        reservations.append({"stage": key, "additional": needs_additional, "at": now(),
                             "origin": "reserved_before_submission", **repair_reservation})
        used = additional + int(needs_additional)
        diagnostics["model_recovery_budget"].update(used=used, remaining=max(0, 2-used))
        self.save_progress(case, progress)

    def abandon_model_reservation(self, case, key, reason):
        progress=self.progress(case)
        from _lib.runtime.census_task_accounting import abandon
        if abandon(progress, key, reason):
            self.save_progress(case, progress)
            return
        from _lib.runtime.world_adjudication import abandon_continuation_reservation
        if abandon_continuation_reservation(case, progress, key, reason):
            self.save_progress(case, progress)
            return
        structured=progress.setdefault('diagnostics',{}).get('intersection_structured_closure_reservations',[])
        for item in reversed(structured):
            if item.get('model_stage')==key and item.get('origin')=='reserved_before_submission' and not item.get('status'):
                item.update(status='abandoned_before_task_id',reason=str(reason)[:200],at_abandoned=now())
                self.save_progress(case,progress)
                return
        reservations=progress.setdefault("diagnostics",{}).setdefault("model_task_reservations",[])
        for item in reversed(reservations):
            if item.get("stage")==key and item.get("origin")=="reserved_before_submission" and not item.get("status"):
                item.update(status="abandoned_before_task_id",reason=str(reason)[:200],at_abandoned=now())
                break
        self.save_progress(case,progress)

    def credit_model_reservation(self,case,key,attempt,reason,evidence_sha256):
        """Credit a task whose useful answer was rejected by a local wire-contract fault."""
        progress=self.progress(case);diagnostics=progress.setdefault('diagnostics',{})
        sessions=[s for s in progress.get('sessions',[]) if s.get('stage')==key]
        target=next((s for s in sessions if s.get('attempt')==attempt),None)
        if not target or not target.get('invalid_candidate_path') or not evidence_sha256:return False
        reservations=[r for r in diagnostics.setdefault('model_task_reservations',[]) if r.get('stage')==key]
        if attempt<1 or attempt>len(reservations):return False
        item=reservations[attempt-1]
        if item.get('status')=='credited_pipeline_contract_fault':return True
        if not item.get('additional') or item.get('status'):return False
        item.update(status='credited_pipeline_contract_fault',reason=str(reason)[:200],
                    evidence_sha256=evidence_sha256,at_credited=now())
        free={'abandoned_before_task_id','credited_pipeline_contract_fault'}
        used=sum(r.get('additional',False) for r in diagnostics['model_task_reservations'] if r.get('status') not in free)
        diagnostics['model_recovery_budget']={'limit':2,'used':used,'remaining':max(0,2-used),
            'next_stage':key,'next_is_additional':True}
        self.save_progress(case,progress);return True

    @staticmethod
    def revoke_artifact_claims(progress, reason):
        """Never let a non-terminal run continue claiming an older publication."""
        old=progress.get("artifacts") or {}
        if old:
            history=progress.setdefault("diagnostics",{}).setdefault("superseded_artifact_claims",[])
            history.append({"reason":reason,"artifacts":copy.deepcopy(old),"at":now()})
        progress["artifacts"]={}
        progress["generation_status"]="not_generated"
        progress["verification_level"]="not_verified"
        progress["source_contract_validation"]="not_passed"
        progress["render_verification"]="not_executed"

    @staticmethod
    def quarantine_checkpoint(path):
        """Preserve an invalid committed file for audit without overwriting history."""
        path=Path(path)
        digest=sha256(path)[:16]
        sequence=0
        while True:
            suffix=f".invalid.{digest}" if sequence==0 else f".invalid.{digest}.{sequence}"
            destination=path.with_name(f"{path.stem}{suffix}{path.suffix}")
            if not destination.exists():
                path.rename(destination)
                return destination
            sequence+=1

    @staticmethod
    def reopen_failed_attempts(progress):
        """An explicit retry renews exhausted failed stages, never active leases."""
        sessions = progress.get("sessions", [])
        for stage, attempts in list(progress.get("attempts", {}).items()):
            records = [item for item in sessions if item.get("stage") == stage]
            threshold = 1 if stage == "conversion_semantic_gap" else 3
            if attempts < threshold or not records:
                continue
            if stage == "conversion_semantic_gap":
                if (records[-1].get("error_code") != "task_deadline_exceeded"
                        or any(row.get("error_code") == "task_deadline_exceeded"
                               and row.get("deadline_task_resolved") is not True for row in records)):
                    continue
            if any(item.get("status") in {"submitted", "pending", "running"} for item in records):
                continue
            if records[-1].get("status") not in {"failed", "invalid_output"}:
                continue
            progress.setdefault("retry_epochs", {})[stage] = progress.get("retry_epochs", {}).get(stage, 0) + 1
            progress["attempts"].pop(stage)

    @staticmethod
    def recovery_phase(case):
        """Infer the next phase from committed artifacts, not a missing later file."""
        from _lib.contracts.conversion_policy import default_completion
        if default_completion(case) is not None:
            # The adoption binds the already-accepted analysis bytes. Recovery
            # changes only conversion permission, not the model input contract.
            return 'converting'
        if (case/"state/behavior.json").is_file():
            # Rebuilding analysis is deterministic and prevents a failed
            # conversion from reusing output produced by an older Builder.
            return "aggregating"
        if (case/"final/analysis.json").is_file():
            return "converting"
        reconciliation=case/"state/reconciliation.json"
        if reconciliation.is_file():
            status=read_json(reconciliation).get("status","")
            if status in {"reconciled","reconciled_after_geometry_readjudication","reconciled_after_world_closure"}:
                return "aggregating"
        if (case/"state/world_context.json").is_file() and (case/"manifest.json").is_file():
            return "batching"
        return "planning"

    def run_v3(self, video, force_new=False, case_id=None, parameters=None, retry_failed=False, *, enable_straight_preview=False, enable_conversion_defaults=False, enable_behavior_census_scope=False, execution_scope=None, execution_scope_bindings=None, execution_planning=None, execution_evidence=None, authorize_transport_retry=None, resume_conversion_session=None, resume_world_closure_session=None, review_behavior_scope=False, reauthorize_behavior_scope_after_quota=None, scope_quota_retry_generation=1, accept_early_association_independence=False, reauthorize_behavior_after_timeout=None, review_execution_scope=False, core_scene_actors=None, stop_after_behavior_candidate=False):
        if core_scene_actors is not None and (not case_id or not retry_failed or force_new
                or review_execution_scope or reauthorize_behavior_after_timeout
                or accept_early_association_independence
                or review_behavior_scope or reauthorize_behavior_scope_after_quota
                or enable_straight_preview or enable_conversion_defaults or enable_behavior_census_scope
                or execution_scope or execution_scope_bindings or execution_planning or execution_evidence
                or authorize_transport_retry or resume_conversion_session or resume_world_closure_session):
            raise ContractError('Core scene adoption requires ordinary saved-Case retry without other recovery flags')
        if review_execution_scope and (not case_id or not retry_failed or force_new
                or reauthorize_behavior_after_timeout
                or accept_early_association_independence
                or review_behavior_scope or reauthorize_behavior_scope_after_quota
                or enable_straight_preview or enable_conversion_defaults or enable_behavior_census_scope
                or execution_scope or execution_scope_bindings or execution_planning or execution_evidence
                or authorize_transport_retry or resume_conversion_session or resume_world_closure_session):
            raise ContractError('Downstream scope review requires ordinary saved-Case retry without other recovery flags')
        if reauthorize_behavior_after_timeout and (not case_id or not retry_failed or force_new
                or accept_early_association_independence
                or review_behavior_scope or reauthorize_behavior_scope_after_quota
                or enable_straight_preview or enable_conversion_defaults or enable_behavior_census_scope
                or execution_scope or execution_scope_bindings or execution_planning or execution_evidence
                or authorize_transport_retry or resume_conversion_session or resume_world_closure_session):
            raise ContractError('Behavior timeout retry requires ordinary saved v3 retry')
        if accept_early_association_independence and (not case_id or not retry_failed or force_new
                or review_behavior_scope or reauthorize_behavior_scope_after_quota
                or enable_straight_preview or enable_conversion_defaults or enable_behavior_census_scope
                or execution_scope or execution_scope_bindings or execution_planning or execution_evidence
                or authorize_transport_retry or resume_conversion_session or resume_world_closure_session):
            raise ContractError('Early association adoption requires ordinary saved-Case retry')
        if scope_quota_retry_generation not in (1, 2) or (scope_quota_retry_generation != 1 and not reauthorize_behavior_scope_after_quota):
            raise ContractError('Quota retry generation 2 requires explicit quota reauthorization')
        if (review_behavior_scope or reauthorize_behavior_scope_after_quota) and (not case_id or not retry_failed or force_new
                or review_behavior_scope and reauthorize_behavior_scope_after_quota
                or enable_straight_preview or enable_conversion_defaults or enable_behavior_census_scope
                or execution_scope or execution_scope_bindings or execution_planning or execution_evidence
                or authorize_transport_retry or resume_conversion_session or resume_world_closure_session):
            raise ContractError('Observation scope review requires saved-Case retry without other recovery flags')
        if enable_behavior_census_scope and (not case_id or not retry_failed or force_new
                or resume_world_closure_session or resume_conversion_session or authorize_transport_retry
                or enable_straight_preview or enable_conversion_defaults or execution_scope or execution_planning):
            raise ContractError('Behavior census scope adoption requires ordinary saved-Case retry')
        if resume_world_closure_session and (not case_id or not retry_failed or force_new
                or resume_conversion_session or authorize_transport_retry or enable_straight_preview
                or enable_conversion_defaults or execution_scope or execution_planning):
            raise ContractError('World closure continuation requires explicit saved-Case retry without migration')
        if resume_conversion_session and (authorize_transport_retry != "conversion_semantic_gap"
                or not case_id or not retry_failed or force_new or enable_conversion_defaults
                or enable_straight_preview or execution_scope or execution_planning):
            raise ContractError("conversion session continuation requires explicit recovery without policy migration")
        from _lib.runtime.execution_policy import require_active_policy
        require_active_policy(parameters)
        parameters = normalize_parameters({"workflow_version": "v3", **(parameters or {})})
        if case_id:
            case = (EXTRACTOR / "cases" / case_id).resolve()
            if case.parent != (EXTRACTOR/"cases").resolve() or not (case/"case.json").is_file(): raise ContractError("unknown case_id")
            case_record = read_json(case/"case.json")
            require_active_policy(case_record.get('parameters'))
            if case_record.get("workflow_contract") != CONTRACT: raise ContractError("v2 Case cannot be used by v3")
            if normalize_parameters(case_record.get("parameters")) != parameters:
                raise ContractError("case parameters mismatch")
            binding=read_json(case/"state/binding.json")
            if Path(video).resolve()!=Path(binding["source_path"]).resolve() or sha256(video)!=binding["source_sha256"]: raise ContractError("source_changed")
        else: case, _ = Cases(EXTRACTOR/"cases").bind(video, parameters, force_new)
        if stop_after_behavior_candidate:
            mode = case/'state/core_approximate_mode.json'
            if (not mode.is_file() or read_json(mode).get('revision') != 'core-scene-approximate-mode.v1'):
                raise ContractError('Behavior candidate stop requires an explicit core approximation Case')
        from _lib.runtime import core_scene_scope
        if core_scene_scope.active(case):
            core_scene_scope.request(case)
            if any((review_execution_scope, review_behavior_scope, reauthorize_behavior_scope_after_quota,
                    reauthorize_behavior_after_timeout,
                    accept_early_association_independence, enable_straight_preview, enable_conversion_defaults,
                    enable_behavior_census_scope, execution_scope, execution_scope_bindings,
                    execution_planning, execution_evidence, resume_world_closure_session)):
                raise ContractError('Accepted core scene mode cannot combine a different scope or source recovery')
        if core_scene_actors is not None:
            core_scene_scope.authorize(self, case, core_scene_actors)
        if reauthorize_behavior_after_timeout:
            from _lib.runtime.behavior_timeout_retry import authorize
            authorize(self, case, reauthorize_behavior_after_timeout)
        if enable_behavior_census_scope:
            from _lib.contracts.behavior_census_scope import adopt
            adopt(case)
        if enable_conversion_defaults:
            if not (case_id and retry_failed):
                raise ContractError('Conversion default adoption requires --case-id --retry-failed')
            from _lib.contracts.conversion_policy import adopt_defaults
            adopt_defaults(case)
        if execution_planning:
            from _lib.runtime.execution_plan import activate as activate_plan
            activate_plan(case,execution_planning,execution_evidence)
        if execution_scope:
            from _lib.runtime.execution_scope import activate
            activate(case, execution_scope, execution_scope_bindings)
        progress = self.progress(case)
        if accept_early_association_independence:
            from _lib.contracts.early_association import adopt
            adopt(case)
        if review_execution_scope:
            from _lib.runtime.downstream_scope_review import authorize
            authorize(self, case)
            progress = self.progress(case)
        if review_behavior_scope:
            from _lib.runtime.behavior_scope_review import authorize
            authorize(self, case)
            progress = self.progress(case)
        if reauthorize_behavior_scope_after_quota:
            from _lib.runtime.behavior_scope_review import authorize_quota_retry
            authorize_quota_retry(self, case, reauthorize_behavior_scope_after_quota, generation=scope_quota_retry_generation)
            progress = self.progress(case)
        if resume_world_closure_session:
            from _lib.runtime.world_adjudication import authorize_continuation
            authorize_continuation(self, case, resume_world_closure_session)
            progress = self.progress(case)
        if authorize_transport_retry:
            if not (case_id and retry_failed):
                raise ContractError("transport recovery authorization requires --case-id --retry-failed")
            self.authorize_transport_retry(case, authorize_transport_retry, resume_session_id=resume_conversion_session)
            progress = self.progress(case)
        if retry_failed and progress.get("status") in {"failed", "needs_review", "placeholder_generated"}:
            self.reopen_failed_attempts(progress)
            self.save_progress(case, progress)
        if progress.get("status") in {"done", "done_degraded"}:
            try:
                self.validate_completed(case,progress)
                return progress
            except Exception as error:
                # Corruption recovery is deterministic and does not reinterpret a
                # valid completion merely because source code later changed.
                self.revoke_artifact_claims(progress,str(error)[:500])
                phase = self.recovery_phase(case)
                progress.update({"phase":phase,"status":phase,"error":None,"updated_at":now()}); self.save_progress(case,progress)
        if progress.get("status") == "placeholder_generated" and retry_failed:
            phase=self.recovery_phase(case)
            progress.update({"phase":phase,"status":phase,"error":None,"retry_requested":True,"updated_at":now()})
            self.save_progress(case,progress)
        if progress.get("status") == "needs_review":
            if not retry_failed:
                self.ensure_synthetic_target(case, progress,
                    (progress.get('error') or {}).get('message', 'Case needs review'))
                self.save_progress(case, progress)
                return progress
            recovery_phase = self.recovery_phase(case)
            behavior_review=(progress.get('error') or {}).get('resume_from')=='behavior' or recovery_phase=='aggregating'
            phase = 'converting' if recovery_phase == 'converting' else ('aggregating' if behavior_review else 'batching')
            substage = 'conversion' if phase == 'converting' else ('behavior' if behavior_review else 'reconciling')
            progress.update({"phase":phase,"substage":substage,"status":phase,"error":None,
                             "retry_requested":True,"updated_at":now()})
            self.save_progress(case,progress)
        if progress.get("phase") == "failed" and not retry_failed:
            policy_path = Path(case)/'state/execution_policy.json'
            if (not policy_path.is_file() or read_json(policy_path).get('synthetic_target')
                    != 'synthetic-target.v1'):
                raise ContractError("retry_required")
            self.ensure_synthetic_target(case, progress,
                (progress.get('error') or {}).get('message', 'Case failed'))
            self.save_progress(case, progress)
            return progress
        if progress.get("phase") == "failed":
            phase=self.recovery_phase(case)
            progress.update({"phase":phase,"status":phase,"error":None,"retry_requested":True,"updated_at":now()}); self.save_progress(case,progress)
        try:
            from _lib.runtime.identity_revision import recover as recover_identity
            if recover_identity(self, case):
                progress = self.progress(case)
            from _lib.runtime import world_adjudication
            world_adjudication.recover_checkpoint(self, case)
            progress = self.progress(case)
            from _lib.contracts.fidelity import enabled, preflight
            if enabled(case):
                from _lib.runtime.case_core import atomic_json
                atomic_json(case/'diagnostics/fidelity_contract.json', {'fingerprint':preflight()})
                parent_id=parameters.get('parent_case_id')
                if parent_id:
                    parent=EXTRACTOR/'cases'/parent_id
                    record=read_json(parent/'case.json')
                    if record['source']['sha256']!=read_json(case/'case.json')['source']['sha256']:
                        raise ContractError('Parent case source hash differs')
                    atomic_json(case/'diagnostics/lineage.json',{'parent_case_id':parent_id,
                        'parent_record_sha256':sha256(parent/'case.json'),'reason':'Explicit new video-fidelity understanding run'})
            if enable_straight_preview:
                self.enable_straight_preview(case)
            if progress["phase"] == "planning":
                subprocess.run([sys.executable, str(HERE/'video-case-orchestrator/scripts/case_tools.py'), "prepare", "--case-dir", str(case)], check=True)
                self.world_understanding(case)
                # A failed initial World may be recovered by revalidating its
                # immutable archived candidate after a compiler/validator fix.
                # Once the formal checkpoint exists, the old stop outcome is
                # no longer active and must not keep advertising stale paths.
                world_progress = self.progress(case)
                if world_progress.setdefault('diagnostics', {}).pop('world_repair_outcome', None) is not None:
                    self.save_progress(case, world_progress)
                from _lib.runtime.routing import check_world_capabilities
                check_world_capabilities(case)
                freeze_v3(case)
            if self.progress(case)["phase"] == "batching":
                self.v3_batches(case)
                if not self.reconcile(case): return self.progress(case)
                progress = self.progress(case); progress.update({"phase": "aggregating", "status": "aggregating", "updated_at": now()}); self.save_progress(case, progress)
            if self.progress(case)["phase"] == "aggregating":
                from _lib.stages.preview_separation import ensure_preview_separation
                if self.progress(case).get('diagnostics',{}).get('preview_separation'):
                    ensure_preview_separation(self,case)
                if self.progress(case).get('diagnostics',{}).get('execution_localization'):
                    ensure_preview_separation(self,case,localization=True)
                if self.progress(case).get('diagnostics',{}).get('execution_verification'):
                    ensure_preview_separation(self,case,verification=True)
                from _lib.world.gaps import enabled as new_facts, record_preplan
                if new_facts(case): record_preplan(case,read_json(case/'state/world_context.json'))
                self.ensure_world_ready(case, execution=False)
                from _lib.runtime.fact_versions import enabled as fact_versions_enabled, establish_baseline
                if fact_versions_enabled(case):
                    establish_baseline(case)
                from _lib.runtime.reconciliation_review import run as review_reconciliation
                review_reconciliation(self, case)
                from _lib.runtime.routing import automatic, check_world_capabilities, record_route, CapabilityBlocked, require_lane_evidence
                check_world_capabilities(case)
                self.behavior(case)
                if stop_after_behavior_candidate:
                    return self.progress(case)
                preview_enabled=read_json(case/'case.json')['parameters']['fallback_policy']['straight_preview']['enabled']
                if automatic(case):
                    from _lib.behavior.compile import execution_issues, evidence_frames
                    from _lib.runtime.execution_scope import inputs, registry as scope_registry
                    world, behavior, _ = inputs(case)
                    route=record_route(case,world,behavior,read_json(case/'case.json')['parameters'],
                        execution_issues(behavior,world,scope_registry(case,behavior),evidence_frames(case)))
                    if route['route']=='unsupported': raise CapabilityBlocked(route['issues'])
                    from _lib.contracts.execution_gaps import enabled as gap_recovery
                    require_lane_evidence(route, allow_review=gap_recovery(case))
                    if any(i.get('rule_id') == 'identity_uncertain' for i in route['issues']):
                        from _lib.world.gaps import IdentityEvidenceGap
                        raise IdentityEvidenceGap(route['issues'])
                    from _lib.runtime.routing import repair_known_gap
                    for _ in range(2):
                        if not repair_known_gap(self,case,route): break
                        from _lib.runtime.execution_scope import inputs, registry as scope_registry
                        world, behavior, _ = inputs(case)
                        route=record_route(case,world,behavior,read_json(case/'case.json')['parameters'],
                            execution_issues(behavior,world,scope_registry(case,behavior),evidence_frames(case)))
                        if route['route']=='unsupported': raise CapabilityBlocked(route['issues'])
                        require_lane_evidence(route, allow_review=gap_recovery(case))
                    require_lane_evidence(route)
                    if any(i.get('rule_id') in {'identity_uncertain', 'identity.temporal_binding'} for i in route['issues']):
                        from _lib.world.gaps import IdentityEvidenceGap
                        raise IdentityEvidenceGap(route['issues'])
                    preview_enabled=route['route']=='straight_preview'
                    if route['route']=='intersection_preview':
                        from _lib.stages.intersection import review_intersection
                        if review_intersection(self,case) is None:
                            # A negative review may challenge World exactly once. The
                            # owner commit invalidates Behavior and routing; rebuild
                            # both from the new epoch before a second review.
                            self.behavior(case)
                            from _lib.runtime.execution_scope import inputs, registry as scope_registry
                            world, behavior, _ = inputs(case)
                            route=record_route(case,world,behavior,read_json(case/'case.json')['parameters'],
                                execution_issues(behavior,world,scope_registry(case,behavior),evidence_frames(case)))
                            if route['route']=='unsupported': raise CapabilityBlocked(route['issues'])
                            require_lane_evidence(route)
                            preview_enabled=route['route']=='straight_preview'
                            if route['route']=='intersection_preview' and review_intersection(self,case) is None:
                                raise ContractError('post_behavior_world_readjudication_exhausted')
                if preview_enabled and not (case/'diagnostics/preview_relations.json').exists():
                    self.review_preview_relations(case)
                if preview_enabled:
                    ensure_preview_separation(self,case)
                    ensure_preview_separation(self,case,localization=True)
                    ensure_preview_separation(self,case,verification=True)
                analysis, status, placeholder = self.build(case)
                if status == 'needs_review':
                    from _lib.delivery.diagnostics import execution_blocked_message
                    progress = self.progress(case)
                    progress.update(phase='failed', status='needs_review', generation_status='not_generated',
                        error={'code':'analysis_execution_blocked','message':execution_blocked_message(analysis['quality_report']['execution_issues'])},
                        updated_at=now())
                    progress.setdefault('diagnostics',{})['analysis'] = {
                        'path':str(case/'final/analysis.json'), 'sha256':sha256(case/'final/analysis.json'),
                        'execution_issues':analysis['quality_report']['execution_issues']}
                    route_path=case/'state/execution_route.json'
                    if route_path.is_file():
                        blocked_route=read_json(route_path)
                        if blocked_route.get('route')=='blocked' and blocked_route.get('issues'):
                            primary=blocked_route['issues'][0]
                            progress['diagnostics']['execution_route']=blocked_route
                            progress['error'].update(rule_id=primary.get('rule_id'),owner=primary.get('owner'),
                                retry_kind=primary.get('retry_kind'),resume_from='aggregating',
                                message='执行路由阻塞：'+primary['message']+'；观察分析已保存，其他执行缺口见 diagnostics.analysis。')
                    self.ensure_synthetic_target(case, progress, progress['error']['message'])
                    self.save_progress(case,progress)
                    return progress
                if status == "placeholder_generated":
                    validate_capabilities()
                    envelope = completion_envelope(status, diagnostics={"placeholder_target": str(placeholder), "reasons": analysis["quality_report"]["placeholder_reasons"]})
                    progress = self.progress(case); progress.update(envelope); progress.update({"phase": "done", "updated_at": now()})
                    self.ensure_synthetic_target(case, progress, 'Source analysis produced only a placeholder')
                    self.save_progress(case, progress); return progress
                progress = self.progress(case); progress.update({"phase": "converting", "status": "converting", "updated_at": now()}); self.save_progress(case, progress)
            if self.progress(case)["phase"] == "converting":
                quality = read_json(case/"final/analysis.json")["quality_report"]
                status = "done_degraded" if quality["required_degradations"] or quality.get("bounded_uncertainty_reasons") else "done"
                self.ensure_semantic_gap(case)
                self.local_delivery_step(case, "conversion", lambda: self.strict_convert(case))
                receipt = case/'state/conversion_result.json'
                if receipt.is_file() and read_json(receipt).get('status') == 'done_degraded':
                    status = 'done_degraded'
                return self.local_delivery_step(case, "publication", lambda: self.finish_v3(case, status))
            return self.progress(case)
        except Exception as error:
            try:
                analysis_path=case/"final/analysis.json"
                if not analysis_path.is_file(): raise ContractError("analysis_not_committed")
                accepted=read_json(analysis_path)
                validate_analysis(accepted,read_json(case/"state/actor_registry.json"))
                if accepted.get("quality_report",{}).get("world_evidence_epoch") != read_json(case/"state/world_context.json").get("evidence_epoch"):
                    raise ContractError("analysis belongs to an older World epoch")
                source_status="passed"; verification="source_contract_validated"
            except Exception:
                source_status="not_passed"; verification="not_verified"
            message=str(error)
            progress = self.progress(case)
            diagnostics = progress.setdefault("diagnostics", {})
            diagnostics["failure_chain"] = [
                {"stage": item.get("stage"), "code": item.get("error_code"), "message": item.get("message")}
                for item in progress.get("sessions", []) if item.get("error_code")]
            if getattr(error, 'code', None) == 'identity_evidence_insufficient':
                diagnostics['failure_chain'].append(dict(stage='census_gate', code=error.code, message=message))
            self.save_progress(case, progress)
            stable_codes={"conversion_failed","publish_failed","model_attempts_exhausted","model_recovery_budget_exhausted","world_contract_repair_budget_exhausted","missing_output","task_cancelled","task_deadline_exceeded","behavior_semantic_blocked","world_evidence_insufficient","world_patch_contract_blocked","world_closure_no_progress","internal_pipeline_error","local_dependency_missing","local_io_error","census_scope_review_required"}
            code=getattr(error,'code',None) or (message if message in stable_codes else self.candidate_error_code(error))
            progress = self.progress(case)
            latest_session=next(iter(reversed(progress.get("sessions",[]))),{})
            initial_task = (diagnostics.get('initial_world_task')
                            if progress.get('phase') == 'planning' else None)
            error_stage=progress.get("substage") or initial_task or latest_session.get("stage") or progress.get("phase")
            self.revoke_artifact_claims(progress,message[:500]); progress.update({"phase": "failed", "status": "failed", "generation_status": "not_generated",
                "verification_level":verification,"source_contract_validation":source_status,"render_verification":"not_executed",
                "error": {"code":code,"message":message,"stage":error_stage,"exception_type":type(error).__name__,
                          "resume_from":"behavior" if progress.get("substage") in {"behavior","preview_review","building"} else progress.get("phase")},
                "updated_at": now()})
            if code in {'preview_evidence_insufficient','preview_contract_blocked','preview_prerequisite_blocked'}:
                progress['error'].update(getattr(error,'diagnostics',{}))
                progress['error'].update(stage='preview_review',resume_from='behavior')
                if code in {'preview_evidence_insufficient','preview_prerequisite_blocked'}: progress['status']='needs_review'
            if code in {'census_scope_review_required', 'behavior_scope_review_incomplete'}:
                progress['status'] = 'needs_review'
                progress['error'].update(stage='behavior', resume_from='aggregating')
            if isinstance(getattr(error, 'diagnostics', None), dict):
                progress['error'].update(error.diagnostics)
            if code.startswith('bounded_recovery_'):
                progress['status'] = 'needs_review'
            if code == 'model_recovery_budget_exhausted':
                local_failures = diagnostics.get('batch_revalidation_failures', [])
                local_failure = next((row for row in reversed(local_failures)
                                      if row['stage'] == error_stage), None)
                if local_failure:
                    progress['error']['local_revalidation_failure'] = local_failure
                closure_failures=progress.get('diagnostics',{}).get('world_closure_revalidation_failures',[])
                if error_stage=='world_closure' and closure_failures:
                    progress['error']['local_revalidation_failure']=closure_failures[-1]
            if error_stage=='world_closure' and latest_session.get('status')=='invalid_output':
                progress['error']['primary_validation_failure']={k:latest_session[k] for k in
                    ('error_code','rule_id','message','validation_details','invalid_candidate_path') if k in latest_session}
            if code == 'intersection_execution_blocked':
                progress['status'] = 'needs_review'
                progress['error']['resume_from'] = 'aggregating'
            if code == 'conversion_policy_blocked':
                progress['status'] = 'needs_review'
                progress['error'].update(resume_from='conversion', automatic_retry=False)
            if code in {'identity_evidence_insufficient', 'lane_evidence_insufficient'}:
                progress.update(status='needs_review',phase='aggregating')
                progress['error']['resume_from']='aggregating'
            if code in {'identity_evidence_insufficient', 'behavior_semantic_blocked'}:
                from _lib.runtime.question_review import prepare_unregistered
                try:
                    requests = prepare_unregistered(case)
                except ContractError as source_error:
                    diagnostics['unregistered_observation_review'] = dict(
                        status='source_blocked', reason=str(source_error))
                else:
                    if requests:
                        diagnostics['unregistered_observation_review'] = dict(
                            status='awaiting_world_visual_review',
                            request_paths=[str(path) for path in requests])
            if code == 'behavior_semantic_blocked':
                from _lib.behavior.repair import write_review_request
                progress.update({'phase':'aggregating','status':'needs_review'})
                progress.setdefault('diagnostics',{})['behavior_review']={
                    'reason':message,'recovery':'resolve core limitations using current Case evidence before retry',
                    'candidate_path':latest_session.get('invalid_candidate_path'),
                    'request_path':write_review_request(case,latest_session.get('invalid_candidate_path'),message)}
            if code in {'world_evidence_insufficient','world_patch_contract_blocked','world_closure_no_progress'}:
                from _lib.world.admission import enabled as admission_enabled
                reported_stage = getattr(error, 'diagnostics', {}).get('stage')
                world_stage = (reported_stage if reported_stage in {'world', 'world_closure'} else
                               'world' if admission_enabled(case) and
                               latest_session.get('stage') == 'world' else 'world_closure')
                progress['error'].update(stage=world_stage, resume_from='planning' if world_stage=='world' else 'aggregating')
                is_initial = world_stage == 'world'
                request_path = case/('state/world_field_repair.active.json' if is_initial else 'state/world_closure.request.json')
                diagnostic_path = case/('diagnostics/world_repair_outcomes.json' if is_initial else 'diagnostics/world_closure_outcome.json')
                outcome = {'code':code, 'reason':message, 'automatic_retry':False}
                # A semantic compilation stop can happen before a bounded
                # repair packet or ledger exists.  Never advertise imaginary
                # recovery files; retain the exact archived candidate instead.
                if request_path.is_file():
                    outcome['request_path'] = str(request_path)
                if diagnostic_path.is_file():
                    outcome['diagnostic_path'] = str(diagnostic_path)
                archived = latest_session.get('invalid_candidate_path')
                if archived and Path(archived).is_file():
                    outcome['candidate_path'] = archived
                outcome['recovery'] = ('Resume the bounded World repair request.' if request_path.is_file()
                    else 'Retry only after the reported compiler/validator contract is corrected; the immutable candidate will be revalidated first.')
                progress.setdefault('diagnostics',{})['world_repair_outcome' if is_initial else 'world_closure_outcome'] = outcome
                if code == 'world_evidence_insufficient':
                    progress.update(phase='planning' if world_stage=='world' else 'aggregating',status='needs_review')
            from _lib.delivery.diagnostics import failure_explanation
            explanation = failure_explanation(code)
            if explanation:
                progress['error']['explanation'] = explanation
            self.ensure_synthetic_target(case, progress, error)
            self.save_progress(case, progress); return progress
