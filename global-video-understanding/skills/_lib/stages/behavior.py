"""BehaviorStage: deterministic pipeline adapter, never a model worker."""
from __future__ import annotations

from pathlib import Path
from _lib.runtime.orchestrator import candidate_snapshot, quarantine_candidate
from _lib.contracts.v3 import ContractError
from _lib.world.gaps import IdentityEvidenceGap
from _lib.runtime.case_core import atomic_json, now, read_json, sha256, retryable
from ..paths import SKILLS as HERE, SCENARIO_ROOT as ROOT, EXTRACTOR
from ..prompts import behavior_request_text


class BehaviorStage:
    def review_preview_relations(self, case):
        """One budgeted review, with a durable prepared result for interrupted commits."""
        case = Path(case)
        from _lib.runtime.execution_policy import require_active_policy
        case_record = case / "case.json"
        if case_record.is_file():
            require_active_policy(read_json(case_record).get("parameters"))
        from _lib.runtime.execution_scope import enabled as scoped, review_straight
        if scoped(case): return review_straight(self, case)
        from _lib.behavior.repair import preview_review_prompt, materialize_preview
        from _lib.behavior.compile import validate_compiled_behavior, evidence_frames, write_evidence_index
        progress=self.progress(case)
        from _lib.behavior.preview_contract import PreviewStopped
        from _lib.behavior.preview_recovery import validation_fingerprint
        progress['substage']='preview_review'; self.save_progress(case,progress)
        record=progress.setdefault('diagnostics',{}).get('preview_review')
        revalidation_source=None
        if record and record['status']=='completed':
            from _lib.contracts.core_lane_continuity import validate as validate_motion_obligations
            validate_motion_obligations(case, read_json(case/'state/behavior.json'))
            return record
        if record and record['status'] in {'failed', 'needs_review'}:
            session=next((s for s in reversed(progress.get('sessions',[]))
                          if s.get('stage')==record.get('task_stage','behavior') and
                          s.get('attempt')==record['max_attempts']), {})
            archived=Path(record.get('revalidation_source') or session.get('invalid_candidate_path') or '')
            corrected=Path(record['candidate_path'])
            if corrected.is_file() and (not archived.is_file() or sha256(corrected)!=sha256(archived)):
                archived=corrected
            if not progress.get('retry_requested') or not archived.is_file():
                return record
            if (not archived.resolve().is_relative_to((case/'diagnostics').resolve()) and
                    archived.resolve()!=corrected.resolve()):
                raise ValueError('preview archive outside diagnostics')
            fingerprint=validation_fingerprint(case,archived)
            if fingerprint==record.get('validation_fingerprint'):
                detail=record.get('failure',{})
                raise PreviewStopped(detail.get('code','preview_contract_blocked'),
                    detail.get('message','Unchanged preview validation inputs; retry has no effect.'),
                    required_change=detail.get('required_change'))
            revalidation_source=archived
            record.update(status='pending',revalidation_source=str(archived))
            self.save_progress(case,progress)
        if not record:
            if any(s.get('status') in {'submitted','pending','running'} or
                   (s.get('error_code') in {'task_deadline_exceeded','task_status_unavailable'} and not s.get('deadline_task_resolved'))
                   for s in progress.get('sessions',[])):
                raise ContractError('Reconcile existing tasks before preview review')
            write_evidence_index(case)
            for name in ('state/world_context.json','state/behavior.json','final/analysis.json','state/progress.json','case.json'):
                p=case/name
                if p.exists(): atomic_json(case/'diagnostics'/f'before_preview.{p.stem}.{sha256(p)[:16]}.json',read_json(p))
            base=case/'diagnostics'/f'preview_base.{sha256(case/"state/behavior.json")[:16]}.json'
            atomic_json(base,read_json(case/'state/behavior.json'))
            candidate=case/'state/preview_review.candidate.json'
            prompt=preview_review_prompt(case,candidate,base)
            record={'status':'pending','started_at':now(),'base_path':str(base),'candidate_path':str(candidate),
                    'prompt':prompt,'task_stage':'preview_review','max_attempts':2}
            from _lib.runtime.routing import automatic
            if automatic(case): record['max_attempts']=3
            progress['diagnostics']['preview_review']=record; self.save_progress(case,progress)
        candidate=Path(record['candidate_path']); prepared=case/'diagnostics/preview_review.prepared.json'
        def adopt(_session=None):
            if prepared.exists():
                bundle=read_json(prepared); world,data,certificate=bundle['world'],bundle['behavior'],bundle['certificate']
                # Only resume the exact old/new checkpoint pair, never overwrite unrelated edits.
                request=read_json(case/'state/preview_review.request.json')
                from _lib.behavior.compile import preview_digest
                for name,new in (('world_context.json',world),('behavior.json',data)):
                    p=case/'state'/name
                    if sha256(p)!=request['input_hashes'][str(p)] and preview_digest(read_json(p))!=preview_digest(new):
                        raise ValueError('preview prepared commit checkpoint changed')
            else:
                source=revalidation_source or candidate
                response=read_json(source) if revalidation_source else candidate_snapshot(case,record.get('task_stage','behavior'),source)[0]
                from _lib.behavior.preview_repair import materialize as materialize_evidence_patch
                response=materialize_evidence_patch(case,response)
                try:
                    world,data,certificate=materialize_preview(case,response)
                except PreviewStopped as error:
                    atomic_json(case/'diagnostics/preview_review.inconclusive.json',
                                {'response':response,'failure':error.diagnostics})
                    raise
                except Exception as error:
                    error.preview_materialized=response
                    raise
                validate_compiled_behavior(data,world,read_json(case/'state/actor_registry.json'),evidence_frames(case),execution=False)
                from _lib.contracts.core_lane_continuity import validate as validate_motion_obligations
                validate_motion_obligations(case, data, world_override=world)
                atomic_json(prepared,{'world':world,'behavior':data,'certificate':certificate})
            old=case/'state/world_context.json'
            atomic_json(case/'checkpoints/world'/f'{sha256(old)}.json',read_json(old))
            from _lib.behavior.input_binding import write_behavior
            write_behavior(case, data, world_override=world)
            atomic_json(old,world)
            atomic_json(case/'checkpoints/world'/f'{sha256(old)}.json',world)
            atomic_json(case/'diagnostics/preview_relations.json',certificate)
            current=self.progress(case)
            current['diagnostics']['preview_review'].update(status='completed',finished_at=now(),
                world_sha256=sha256(old),behavior_sha256=sha256(case/'state/behavior.json'))
            atomic_json(case/'diagnostics/preview_review.result.json',current['diagnostics']['preview_review'])
            self.save_progress(case,current)
        try:
            # Do not spend an image-review task on an immutable upstream blocker.
            # Already submitted tasks keep their frozen lifecycle and are reconciled.
            active=any(s.get('stage')==record.get('task_stage','behavior') and
                       s.get('status') in {'submitted','pending','running'}
                       for s in self.progress(case).get('sessions',[]))
            if not active and not revalidation_source and not candidate.exists() and not prepared.exists():
                from _lib.behavior.repair import preview_limitation_contract
                base=read_json(record['base_path'])
                blocked=[item['limitation_id'] for item in preview_limitation_contract(base['semantic_limitations'])
                         if item['requires_upstream_adjudication']]
                if blocked or base.get('events') or any(p.get('type')!='initial_state' for p in base.get('executor_primitives', [])):
                    raise PreviewStopped('preview_prerequisite_blocked',
                        'Preview requires upstream adjudication: '+(', '.join(blocked) if blocked else 'observed events/actions'),
                        required_change='Resolve the upstream limitation type or use an executor supporting the observed event; do not repeat image review.')
            if revalidation_source:
                adopt()
            else:
                self.model_stage(case,record.get('task_stage','behavior'),'global-video-understanding',record['prompt'],adopt,
                    precheck=(adopt if candidate.exists() or prepared.exists() else None),
                    candidate_path=candidate,max_attempts=record['max_attempts'])
        except Exception as error:
            current=self.progress(case)
            pending=bool(current.get('diagnostics',{}).get('unresolved_submissions',{}).get(record.get('task_stage','behavior'))) or any(
                s.get('status') in {'submitted','pending','running'} or
                (s.get('error_code') in {'task_deadline_exceeded','task_status_unavailable'} and not s.get('deadline_task_resolved'))
                for s in current.get('sessions',[]))
            sessions=[s for s in current.get('sessions',[]) if
                      s.get('stage')==record.get('task_stage','behavior') and
                      (record.get('task_stage') or s.get('attempt')==record['max_attempts'])]
            source=revalidation_source or next((Path(s['invalid_candidate_path']) for s in reversed(sessions)
                                               if s.get('invalid_candidate_path')),None)
            cause=error
            while cause.__cause__ is not None: cause=cause.__cause__
            message=str(cause)
            if message in {'model_attempts_exhausted','schema_validation_failed'} and sessions:
                message=sessions[-1].get('message',message)
            code=getattr(cause,'code',None) or ('preview_evidence_insufficient' if
                 str(error)=='preview_evidence_insufficient' else 'preview_contract_blocked')
            from _lib.runtime.routing import automatic
            if automatic(case) and not isinstance(cause, PreviewStopped):
                code=getattr(cause,'code',None) or (sessions[-1].get('error_code') if sessions else None) or self.candidate_error_code(cause)
            failure=getattr(cause,'diagnostics',None) or (sessions[-1].get('validation_details') if sessions else None) or {'code':code,'stage':'preview_review',
                'rule_id':code,'message':message,
                'required_change':'Provide a corrected candidate, new evidence, or a changed validator before retrying.'}
            failure.setdefault('owner','behavior')
            failure.setdefault('actor_id',getattr(cause,'actor_id',None))
            failure.setdefault('field_path','/'+ '/'.join(map(str,cause.absolute_path)) if hasattr(cause,'absolute_path') else None)
            failure.setdefault('expected',None);failure.setdefault('actual',None)
            failure.setdefault('retry_kind','content_repair' if code in {'schema_validation_failed','invalid_json'} else
                               'transport_recovery' if code in {'model_stream_interrupted','timeout','gateway_5xx','task_status_unavailable'} else 'new_evidence')
            entry=current['diagnostics']['preview_review']
            entry.update(status='pending' if pending else ('needs_review' if code in {'preview_evidence_insufficient','preview_prerequisite_blocked'} else 'failed'),
                         error=str(error),failure=failure,local_attempts=len(sessions),finished_at=now())
            if source and source.is_file():
                entry.update(revalidation_source=str(source),validation_fingerprint=validation_fingerprint(case,source))
            self.save_progress(case,current)
            if pending or self.candidate_error_code(error) in {'backend_unavailable','model_recovery_budget_exhausted','local_dependency_missing','local_io_error','internal_pipeline_error'}:
                raise
            stop=PreviewStopped(code,message,required_change=failure.get('required_change'))
            stop.diagnostics.update(failure)
            stop.diagnostics['code']=code
            raise stop from error
        current=self.progress(case)
        entry=current['diagnostics']['preview_review']
        entry['local_attempts']=sum(s.get('stage')==entry.get('task_stage','behavior') and
            (bool(entry.get('task_stage')) or s.get('attempt')==entry['max_attempts']) for s in current.get('sessions',[]))
        self.save_progress(case,current)
        self.build(case)
        return self.progress(case)['diagnostics']['preview_review']

    def behavior(self, case):
        if not (Path(case)/'state/progress.json').is_file():
            return self._behavior(case)
        from _lib.runtime import census_scope_recovery
        from _lib.runtime import core_scene_scope
        if core_scene_scope.active(case):
            core_scene_scope.request(case)
            return self._behavior(case)
        from _lib.runtime import downstream_scope_review
        if downstream_scope_review.active(case):
            downstream_scope_review.run(self, case)
            return self._behavior(case)
        from _lib.runtime import behavior_scope_review
        if behavior_scope_review.active(case):
            # Supplement the saved Behavior before any old World recovery.
            # Remaining gaps stop here; this grant never funds another stage.
            behavior_scope_review.run(self, case)
            return self._behavior(case)
        census_scope_recovery.recover(self, case)
        census_scope_recovery.initial_adjudication(self, case)
        try:
            return self._behavior(case)
        except Exception:
            from _lib.runtime import fact_revision_review
            dispute = self.progress(case).get('diagnostics', {}).get('fact_disagreement')
            if dispute and dispute.get('status') == 'pending':
                if fact_revision_review.run(self, case):
                    return self._behavior(case)
                raise
            record = self.progress(case).get('diagnostics', {}).get(census_scope_recovery.KEY)
            if not record or record['status'] != 'requested':
                raise
            census_scope_recovery.recover(self, case)
            return self._behavior(case)

    def _behavior(self, case):
        from _lib.runtime import census_scope_recovery
        from _lib.runtime.fact_versions import ROOT as FACT_ROOT, current_facts
        fact_view = current_facts(case) if (Path(case)/FACT_ROOT/'active.json').is_file() else None
        fact_version_id = fact_view.version_id if fact_view else None
        def assert_fact_version():
            if fact_version_id is not None:
                current_facts(case, fact_version_id)
        if (Path(case)/'state/progress.json').is_file():
            progress = self.progress(case); progress['substage'] = 'behavior'; self.save_progress(case, progress)
        from _lib.contracts.vehicle_census import require_resolved
        require_resolved(case, allow_scope_pending=True)
        from _lib.runtime.execution_policy import require_active_policy
        case_record = Path(case) / "case.json"
        if case_record.is_file():
            require_active_policy(read_json(case_record).get("parameters"))
        progress = self.progress(case); progress["substage"] = "behavior"; self.save_progress(case, progress)
        target = case / "state/behavior.json"; candidate = case / "state/behavior.candidate.json"
        epoch = (fact_view.world if fact_view else read_json(case / "state/world_context.json"))["evidence_epoch"]
        feedback = None
        checkpoint_archive = None
        from _lib.behavior.compile import evidence_frames, write_evidence_index
        from _lib.behavior import input_binding
        def before_new_model():
            assert_fact_version()
            if fact_view:
                input_binding.freeze_current(case)
        from _lib.behavior.repair import (scope_completion_request, scope_completion_prompt,
                                          finish_scope_completion)
        # Verify before regenerating the derived frame index; never hide drift.
        if input_binding.enabled(case) and (case/input_binding.REQUEST).exists():
            input_binding.verify(case)
        completion = scope_completion_request(case)
        write_evidence_index(case)
        input_binding.freeze(case)
        frame_records = evidence_frames(case)
        from _lib.contracts.fidelity import enabled as fidelity_enabled, frame_index
        if fidelity_enabled(case):
            frame_index(frame_records)  # Local producer preflight, before model submission.
        from _lib.behavior import bounded_repair
        repair_route = (census_scope_recovery.repair_scope(case)
                        if bounded_repair.request(case) is None else None)
        from _lib.contracts import census_scope
        scope_review = None
        accepted_scope_review = None
        scope_completion_allowed = False
        def prepare(raw, *, complete_archive=False):
            nonlocal scope_review, scope_completion_allowed
            from _lib.behavior import bounded_repair
            retained_repairs = []
            if complete_archive:
                raw, retained_repairs = bounded_repair.restore_protected_review(case, raw)
            if not complete_archive and bounded_repair.request(case) is not None:
                raw = bounded_repair.materialize(case, raw)
            raw, scope_review = census_scope.split(raw)
            is_patch = isinstance(raw, dict) and 'replacements' in raw
            scope_completion_allowed = not is_patch
            from _lib.behavior.repair import materialize
            raw = materialize(case, raw)
            if is_patch and scope_review is None:
                # Reject before rewriting a patch into a checkpoint-shaped candidate.
                # Otherwise a no-op patch could inherit the old review on the next retry.
                census_scope.validate_review(case, raw, None)
            if (not is_patch and scope_review is None and scope_completion_request(case) is None
                    and (case/census_scope.REVIEW).is_file()):
                from _lib.contracts.conversion_policy import digest
                saved_review = read_json(case/census_scope.REVIEW)
                if digest(raw) == saved_review.get('behavior_digest'):
                    census_scope.verified_review(case, raw)
                    scope_review = saved_review['review']
            from _lib.behavior.compile import prepare_behavior_candidate
            data, repairs = prepare_behavior_candidate(
                raw, frame_records,
                fact_view.registry if fact_view else read_json(case / 'state/actor_registry.json'),
                fact_view.world if fact_view else read_json(case / 'state/world_context.json'), case=case)
            scope_review, index_repairs = bounded_repair.normalize_index(case, data, scope_review, frame_records)
            return data, retained_repairs + repairs + index_repairs
        def check_raw(data):
            nonlocal accepted_scope_review
            assert_fact_version()
            input_binding.verify(case)
            from _lib.behavior.compile import validate_compiled_behavior
            current_world=fact_view.world if fact_view else read_json(case/"state/world_context.json")
            frames = evidence_frames(case)
            from _lib.contracts.behavior_census_scope import enabled as deferred_scope_enabled
            from _lib.contracts.unused_association import enabled as unused_enabled
            from _lib.contracts.execution_assumptions import enabled as assumptions_enabled
            try:
                validate_compiled_behavior(data, current_world,
                    fact_view.registry if fact_view else read_json(case/"state/actor_registry.json"),
                    frames, execution=False,
                    allow_pending_identity=(scope_review is not None or unused_enabled(case) or assumptions_enabled(case)) and deferred_scope_enabled(case))
                from _lib.contracts.core_lane_continuity import validate as validate_core_lane_continuity
                validate_core_lane_continuity(case, data)
            except ContractError as error:
                from _lib.runtime.fact_revision_review import begin
                begin(self, case, data, error)
                raise
            from _lib.runtime.identity_revision import validate_behavior
            validate_behavior(case, data)
            from _lib.contracts.census_adjudication import validate_behavior as validate_census_behavior
            try:
                validate_census_behavior(case, data)
            except ContractError as error:
                from _lib.runtime.fact_revision_review import begin
                begin(self, case, data, error)
                raise
            from _lib.runtime.execution_scope import validate_behavior_scope
            from _lib.contracts.behavior_handoff import enabled as handoff_enabled
            if not handoff_enabled(case):
                validate_behavior_scope(case, current_world, data)
            from _lib.contracts.fidelity import enabled, validate_evidence
            if enabled(case):
                if 'video_fidelity' not in data:
                    from _lib.contracts.fidelity import FidelityEvidenceError
                    raise FidelityEvidenceError('video_fidelity observations required by frozen policy', '/video_fidelity')
                validate_evidence(data['video_fidelity'], current_world, data, frames,
                                  read_json(case/'state/probe.json')['probe']['duration_s'])
            from _lib.runtime import census_scope_recovery
            census_scope_recovery.validate_patch(case, data)
            from _lib.behavior.bounded_repair import preserve_rebuild
            preserve_rebuild(case, data, scope_review)
            try:
                accepted_scope_review = census_scope.validate_review(case, data, scope_review)
            except census_scope.ScopeReviewIncomplete as error:
                # Only this owner, after the other gates, authorizes a review-only
                # response. Motion/lane writers retain their own bounded contracts.
                if scope_completion_allowed:
                    error.code = 'behavior_scope_review_incomplete'
                    error.completion_behavior = data
                raise
            except census_scope.ScopeReviewBlocked as error:
                from _lib.runtime.behavior_scope_review import active as scope_supplement
                from _lib.runtime.downstream_scope_review import active as downstream_scope
                if not scope_supplement(case) and not downstream_scope(case):
                    census_scope_recovery.begin(self, case, error)
                raise
            except IdentityEvidenceGap as error:
                from _lib.runtime.behavior_scope_review import active as scope_supplement
                from _lib.runtime.downstream_scope_review import active as downstream_scope
                if getattr(error, 'pending_census', False) and not scope_supplement(case) and not downstream_scope(case):
                    census_scope_recovery.begin(self, case, error)
                raise
            from _lib.contracts.behavior_handoff import validate_execution
            validate_execution(case, current_world, data, frames, accepted_scope_review)
        def check(data):
            try:
                check_raw(data)
            except (ContractError, IdentityEvidenceGap) as error:
                if fact_view:
                    assert_fact_version()
                    progress = self.progress(case)
                    progress.setdefault('diagnostics', {})['behavior_revalidation'] = dict(
                        status='failed', validation_version_id=fact_version_id,
                        error=str(error)[:500], rule_id=getattr(error, 'rule_id', None))
                    self.save_progress(case, progress)
                from _lib.behavior.bounded_repair import mark_failure
                mark_failure(case, data, scope_review, error)
                raise
        def adopt(_session=None):
            source = candidate
            if not source.is_file() and scope_completion_request(case) is not None:
                prior = next((s for s in reversed(self.progress(case).get('sessions', []))
                              if s.get('stage') == 'behavior' and s.get('invalid_candidate_path')
                              and (_session is None or s.get('session_id') == _session)), None)
                if prior and input_binding.archive_matches(case, Path(prior['invalid_candidate_path'])):
                    source = Path(prior['invalid_candidate_path'])
                    if not source.resolve().is_relative_to(case.resolve()):
                        raise ContractError('Scope completion archive belongs to another Case')
            raw, input_digest = candidate_snapshot(case, "behavior", source)
            input_binding.require_session(case, _session)
            data, repairs = prepare(raw)
            completion_packet = scope_completion_request(case)
            if completion_packet is not None and data != read_json(completion_packet['base_path']):
                raise input_binding.InputChanged('Scope completion normalization changed frozen Behavior')
            from _lib.behavior import bounded_repair
            if data is not raw and completion_packet is None and bounded_repair.request(case) is None:
                atomic_json(case/'diagnostics'/f'behavior_input.{input_digest[:16]}.json',raw)
                atomic_json(candidate, dict(data, census_scope_review=scope_review) if scope_review else data)
            if repairs:
                progress=self.progress(case)
                progress.setdefault("diagnostics",{}).setdefault("deterministic_candidate_repairs",[]).append({
                    "stage":"behavior","input_sha256":input_digest,
                    "source_snapshot_path":str(case/'diagnostics/candidate_snapshots'/
                                               f'behavior.{input_digest}.json'),
                    "source_session_id":_session,
                    "repairs":repairs,"at":now(),
                })
                self.save_progress(case,progress)
            check(data)
            assert_fact_version()
            census_scope.commit_review(case, accepted_scope_review)
            input_binding.write_behavior(case, data)
            if fact_view:
                input_binding.record_validation(case, target, passed=True,
                                                source_candidate_path=source,
                                                source_session=_session,
                                                expected_version=fact_version_id)
            finish_scope_completion(case)
            bounded_repair.finish(case)
            candidate.unlink(missing_ok=True)
            from _lib.runtime.census_scope_recovery import finish
            finish(self, case)
        from _lib.runtime.behavior_scope_review import completed_candidate
        supplemented = completed_candidate(case)
        if supplemented is None:
            from _lib.runtime.core_scene_scope import completed_candidate as core_candidate
            supplemented = core_candidate(case)
        if supplemented is None:
            from _lib.runtime.downstream_scope_review import completed_candidate as downstream_candidate
            supplemented = downstream_candidate(case)
        if supplemented is not None:
            data, repairs = prepare(supplemented, complete_archive=True)
            check(data)
            assert_fact_version()
            if target.is_file() and read_json(target) != data:
                raise input_binding.InputChanged('Scope supplement found a different Behavior checkpoint')
            census_scope.commit_review(case, accepted_scope_review)
            input_binding.write_behavior(case, data)
            if fact_view:
                input_binding.record_validation(case, target, passed=True,
                                                expected_version=fact_version_id)
            return
        if target.is_file():
            try:
                original = read_json(target)
                restored, repairs = prepare(original, complete_archive=True)
                check(restored)
                assert_fact_version()
                census_scope.commit_review(case, accepted_scope_review)
                if restored != original:
                    source_hash = sha256(target)
                    atomic_json(case / "diagnostics" / f"behavior_checkpoint.{source_hash[:16]}.json", original)
                    input_binding.write_behavior(case, restored)
                    progress = self.progress(case)
                    progress.setdefault("diagnostics", {}).setdefault("deterministic_candidate_recoveries", []).append({
                        "stage": "behavior", "source_sha256": source_hash,
                        "recovery": "deterministic_behavior_normalization",
                        "repairs": repairs, "at": now(),
                    })
                    self.save_progress(case, progress)
                if fact_view:
                    input_binding.record_validation(case, target, passed=True,
                                                    expected_version=fact_version_id)
                # Sync behavior session status to completed
                progress = self.progress(case)
                for session in progress.get('sessions', []):
                    if session.get('stage') == 'behavior' and session.get('status') == 'running':
                        session['status'] = 'completed'
                self.save_progress(case, progress)
                return
            except Exception as error:
                if not isinstance(error, (ContractError, IdentityEvidenceGap)):
                    raise
                if not retryable(self.candidate_error_code(error, candidate)):
                    raise
                stale=self.quarantine_checkpoint(target)
                checkpoint_archive = str(stale)
                progress=self.progress(case)
                progress.setdefault("retry_epochs",{})["behavior"]=progress.setdefault("retry_epochs",{}).get("behavior",0)+1
                progress.setdefault("attempts",{}).pop("behavior",None)
                progress.setdefault("diagnostics",{}).setdefault("invalid_checkpoints",[]).append({
                    "stage":"behavior","path":str(stale),"error":str(error)[:500],"recovery":"revalidate_or_patch_behavior"
                })
                self.save_progress(case,progress)
                feedback = str(error)[:1500]
        # A candidate rejected by an older deterministic gate may become valid
        # after a validator fix. Revalidate that immutable archive before
        # spending another Behavior model attempt.
        archives = ([Path(checkpoint_archive)] if checkpoint_archive else [])
        archives.extend(path for path in input_binding.replayable_archives(case)
                        if path not in archives)
        for archived in archives if not candidate.is_file() else []:
            if not archived.resolve().is_relative_to(case.resolve()):
                raise ContractError("Behavior archive belongs to another Case")
            try:
                archived_raw = read_json(archived)
                projection_audit = None
                if fact_view and (input_binding.verify_origin(case) or {}).get('fact_version_id') != fact_version_id:
                    from _lib.runtime.question_review import project_archived
                    archived_raw, projection_audit = project_archived(
                        case, archived, expected_version=fact_version_id)
                recovered, repairs=prepare(archived_raw, complete_archive=True)
                check(recovered)
                assert_fact_version()
                census_scope.commit_review(case, accepted_scope_review)
                input_binding.write_behavior(case, recovered)
                if fact_view:
                    input_binding.record_validation(case, target, passed=True,
                                                    source_candidate_path=archived,
                                                    expected_version=fact_version_id)
                bounded_repair.retire_after_archive(case, archived)
                census_scope_recovery.finish(self, case)
                progress=self.progress(case)
                progress.setdefault("diagnostics",{}).setdefault("deterministic_candidate_recoveries",[]).append({
                    "stage":"behavior", "source_path":str(archived), "source_sha256":sha256(archived),
                    "recovery":"revalidated_after_gate_update", "at":now(),
                    **({'question_review_projection': projection_audit} if projection_audit else {}),
                })
                if repairs:
                    progress["diagnostics"].setdefault("deterministic_candidate_repairs",[]).append({
                        "stage":"behavior","source_path":str(archived),"repairs":repairs,"at":now(),
                    })
                self.save_progress(case,progress)
                return
            except Exception as error:
                if not isinstance(error, (ContractError, IdentityEvidenceGap)):
                    raise
                if not retryable(self.candidate_error_code(error, candidate)):
                    raise
                feedback = str(error)[:1500]
        bounded_packet = bounded_repair.request(case)
        if bounded_packet is not None:
            frozen, _ = census_scope.split(read_json(bounded_packet['base_path']))
            history = self.progress(case).get('sessions', [])
            last_behavior = next((row for row in reversed(history)
                                  if row.get('stage') == 'behavior'), None)
            if last_behavior and last_behavior.get('error_code') in {
                    'bounded_recovery_no_progress', 'bounded_recovery_contract_blocked'}:
                from _lib.runtime.bounded_recovery import RecoveryStopped
                raise RecoveryStopped(last_behavior['error_code'],
                    request_sha256=bounded_packet['request_sha256'])
            failure = next((row.get('validation_details') or {'message':row.get('message', '')}
                            for row in reversed(history) if row.get('stage') == 'behavior'
                            and row.get('error_code') == 'behavior_bounded_repairable'), {})
            bounded_repair.require_actionable(case, data=frozen,
                allowed_paths=bounded_packet['allowed_paths'], issue_ids=bounded_packet['issue_ids'],
                blocked=bounded_packet['blocked'],
                lane_obligations=bounded_packet['lane_closure_obligations'], failure=failure)
            before_new_model()
            return self.model_stage(case, 'behavior', 'global-video-understanding',
                bounded_repair.prompt(case, bounded_packet), adopt, lambda: adopt(None), candidate_path=candidate)
        if completion is not None:
            # Reconcile any persisted producer before spending again.
            before_new_model()
            return self.model_stage(case, 'behavior', 'global-video-understanding',
                scope_completion_prompt(case, completion), adopt, lambda: adopt(None), candidate_path=candidate)
        if candidate.is_file():
            try: adopt(); return
            except Exception as error:
                archived = quarantine_candidate(case,"behavior",candidate,0)
                if not retryable(self.candidate_error_code(error, candidate)):
                    raise
                if archived is not None:
                    if not repair_route or repair_route['mode'] != 'full_evidence_review':
                        from _lib.behavior.repair import repair_prompt
                        before_new_model()
                        repair = repair_prompt(case, candidate, archived, error)
                        if repair:
                            self.model_stage(case, "behavior", "global-video-understanding", repair,
                                             adopt, lambda: adopt(None), candidate_path=candidate)
                            return
        if repair_route and repair_route['mode'] == 'full_evidence_review':
            before_new_model()
            prompt = bounded_repair.prepare_full(case, candidate, repair_route)
            return self.model_stage(case, 'behavior', 'global-video-understanding', prompt,
                                    adopt, lambda: adopt(None), candidate_path=candidate)
        if input_binding.timeline_enabled(case):
            before_new_model()  # The prompt must name the effective frozen request.
        prompt = behavior_request_text(case)
        from _lib.runtime.census_scope_recovery import repair_prompt as scope_repair_prompt
        prompt += scope_repair_prompt(case)
        if feedback: prompt += "\n旧检查点不能执行，定向修正：" + feedback
        before_new_model()
        self.model_stage(case, "behavior", "global-video-understanding", prompt, adopt, lambda: adopt(None), candidate_path=candidate)

    def review_actor_lane(self, case, actor_id, *, scope='lane_only'):
        """Explicit one-shot review of an accepted observation; no full rerun.

        Called by the owning Python orchestrator, never by a model worker.
        A durable request resumes the same task and never resets its budget.
        """
        from _lib.behavior.repair import lane_repair_prompt, materialize
        from _lib.behavior.compile import (derive_initial_states, evidence_frames,
                                          validate_compiled_behavior, execution_issues)
        from _lib.contracts import census_scope
        from _lib.contracts.conversion_policy import digest
        from _lib.behavior.input_binding import InputChanged
        case = Path(case).resolve()
        target = case/'state/behavior.json'
        request = case/'state/behavior_repair.request.json'
        prepared = case/'diagnostics/lane_review.prepared.json'
        progress = self.progress(case)
        record = progress.setdefault('diagnostics', {}).get('lane_review')
        if record and record['actor_id'] != actor_id:
            raise ContractError('Existing lane review belongs to another actor')
        if record and record['status'] == 'completed':
            if sha256(target) != record['behavior_sha256']:
                raise InputChanged('Reviewed lane Behavior changed')
            from _lib.contracts.core_lane_continuity import validate as validate_motion_obligations
            validate_motion_obligations(case, read_json(target))
            census_scope.verified_review(case, read_json(target))
        if record and record['status'] == 'failed' and not prepared.is_file():
            return record
        if not record:
            if progress.get('diagnostics', {}).get('unresolved_submissions') or any(s.get('status') in {'submitted','pending','running'} or
                   (s.get('error_code') in {'task_deadline_exceeded','task_status_unavailable'} and
                    not s.get('deadline_task_resolved')) for s in progress.get('sessions', [])):
                raise ContractError('Reconcile existing tasks before requesting lane review')
            base = case/'state/behavior.json'
            original = read_json(base)
            archive = case/'diagnostics'/f'behavior_before_lane_review.{sha256(base)[:16]}.json'
            atomic_json(archive, original)
            candidate = case/'state/behavior.lane_review.candidate.json'
            prompt = lane_repair_prompt(case, candidate, archive, actor_id, scope=scope)
            record = {'actor_id': actor_id, 'status': 'pending', 'started_at': now(),
                      'base_path': str(archive), 'candidate_path':str(candidate), 'prompt':prompt,
                      'request_sha256':sha256(request),
                      'max_attempts':progress.get('attempts', {}).get('behavior', 0) + 1}
            progress['diagnostics']['lane_review'] = record
            self.save_progress(case, progress)
        packet = read_json(request)
        request_sha256 = sha256(request)
        if record.get('request_sha256', request_sha256) != request_sha256:
            raise InputChanged('Lane review frozen request changed')
        census_scope.verify_request(case, packet)
        candidate = Path(record['candidate_path'])
        def rebuild():
            analysis, status, _ = self.build(case)
            current = self.progress(case)
            current.setdefault('diagnostics', {})['analysis'] = {
                'path':str(case/'final/analysis.json'), 'sha256':sha256(case/'final/analysis.json'),
                'execution_issues':analysis['quality_report'].get('execution_issues', [])}
            current.update(generation_status='not_generated', updated_at=now())
            if status == 'needs_review':
                from _lib.delivery.diagnostics import execution_blocked_message
                current.update(phase='failed', status='needs_review', error={
                    'code':'analysis_execution_blocked',
                    'message':execution_blocked_message(analysis['quality_report'].get('execution_issues', []))})
            else:
                current.update(phase='aggregating', status='aggregating', error=None)
            self.save_progress(case, current)
        if record['status'] == 'completed':
            rebuild()
            return self.progress(case)['diagnostics']['lane_review']
        if not prepared.is_file():
            rebuild()  # A partial commit must be recovered before consuming its scope proof.

        def verify_inputs(data=None):
            if sha256(request) != request_sha256:
                raise InputChanged('Lane review request changed during its task')
            for name, expected in packet['input_hashes'].items():
                path = Path(name)
                if path.is_file() and sha256(path) == expected:
                    continue
                if data is not None and path == target and path.is_file() and digest(read_json(path)) == digest(data):
                    continue
                raise InputChanged('Lane review inputs changed: ' + name)
            if sha256(packet['base_path']) != packet['base_sha256']:
                raise InputChanged('Lane review base changed')
            census_scope.verify_request(case, packet)

        def validate(data):
            world = read_json(case/'state/world_context.json')
            registry = read_json(case/'state/actor_registry.json')
            frames = evidence_frames(case)
            validate_compiled_behavior(data, world, registry, frames, execution=False)
            from _lib.contracts.core_lane_continuity import validate as validate_motion_obligations
            validate_motion_obligations(case, data)
            old_issues = execution_issues(read_json(record['base_path']), world, registry, frames)
            new_issues = execution_issues(data, world, registry, frames)
            missing_initial = f"participant actors missing initial_state primitive: {[actor_id]}"
            def permitted_gap(issue):
                return (issue['actor_id'] == actor_id and issue['rule_id'] in {
                    'execution.lane_unknown', 'execution.lateral_position_unknown'}) or (
                    issue['rule_id'] == 'execution.contract' and issue['message'] == missing_initial and
                    (next(a for a in data['actors'] if a['actor_id'] == actor_id)['trajectory'][0].get('lane_id') is None or
                     'y' not in next(a for a in data['actors'] if a['actor_id'] == actor_id)['trajectory'][0].get('position_ego_m', {})))
            if any(i not in old_issues and not permitted_gap(i) for i in new_issues):
                raise ValueError('lane review introduces an execution conflict')
            from _lib.runtime.identity_revision import validate_behavior
            from _lib.runtime.execution_scope import validate_behavior_scope
            validate_behavior(case, data)
            validate_behavior_scope(case, world, data)

        def commit():
            bundle = read_json(prepared); body = dict(bundle); signature = body.pop('sha256', None)
            if (signature != digest(body) or bundle.get('revision') != 'lane-review-commit.v1'
                    or bundle['request_sha256'] != request_sha256 or sha256(request) != request_sha256):
                raise InputChanged('Lane review prepared commit changed')
            data, review = bundle['behavior'], bundle['scope_review']
            verify_inputs(data)
            review_path = case/census_scope.REVIEW
            old_hash = sha256(review_path) if review_path.is_file() else None
            if old_hash != packet.get('scope_review_sha256') and not (
                    review is not None and review_path.is_file() and read_json(review_path) == review):
                raise InputChanged('Lane review scope checkpoint changed')
            validate(data)
            if census_scope.validate_review(case, data, review['review'] if review else None) != review:
                raise InputChanged('Lane review prepared scope proof changed')
            from _lib.behavior.input_binding import write_behavior
            write_behavior(case, data)
            census_scope.commit_review(case, review)
            census_scope.verified_review(case, data)
            current = self.progress(case)
            current['diagnostics']['lane_review'].update(status='completed', finished_at=now(),
                patch_sha256=bundle['patch_sha256'], behavior_sha256=sha256(target))
            self.save_progress(case, current)

        def adopt(_session=None):
            if prepared.is_file():
                commit(); return
            verify_inputs()
            patch, review = census_scope.split(read_json(candidate))
            if not isinstance(patch, dict) or 'replacements' not in patch:
                raise ValueError('lane review requires a scoped replacement patch')
            data = materialize(case, patch)
            data = derive_initial_states(data, sync_actor_ids={actor_id})
            validate(data)
            try:
                checked = census_scope.validate_review(case, data, review)
            except census_scope.ScopeReviewBlocked as error:
                from _lib.runtime.census_scope_recovery import begin
                begin(self, case, error, producer_session=_session)
                raise
            body = dict(revision='lane-review-commit.v1', request_sha256=request_sha256,
                        behavior=data, scope_review=checked, patch_sha256=sha256(candidate))
            atomic_json(prepared, dict(body, sha256=digest(body)))
            commit()
        try:
            if not prepared.is_file():
                verify_inputs()
            self.model_stage(case, 'behavior', 'global-video-understanding', record['prompt'],
                             adopt, precheck=adopt if prepared.is_file() or candidate.is_file() else None,
                             candidate_path=candidate, max_attempts=record['max_attempts'])
        except Exception as error:
            current = self.progress(case)
            # Lost/running tasks retain the same request for reconciliation.
            unresolved = bool(current.get('diagnostics', {}).get('unresolved_submissions', {}).get('behavior')) or any(s.get('status') in {'submitted','pending','running'} or
                (s.get('error_code') in {'task_deadline_exceeded','task_status_unavailable'} and
                 not s.get('deadline_task_resolved')) for s in current.get('sessions', []))
            last = next((s for s in reversed(current.get('sessions', []))
                         if s.get('stage') == 'behavior'), {})
            current['diagnostics']['lane_review']['validation_error'] = last.get('message', str(error))
            current['diagnostics']['lane_review'].update(
                status='pending' if unresolved or prepared.is_file() else 'failed', error=str(error), finished_at=now())
            self.save_progress(case, current)
            raise
        rebuild()
        return self.progress(case)['diagnostics']['lane_review']
