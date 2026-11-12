"""WorldReconciliation: deterministic pipeline adapter, never a model worker."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from _lib.runtime.orchestrator import candidate_snapshot
from _lib.world.readiness import normalize_world, readiness_issues
from _lib.contracts.v3 import ContractError, completion_envelope, reconcile_world, validate_capabilities, validate_world, world_conflict_value
from _lib.runtime.case_core import atomic_json, now, read_json, raw_read_json, sha256, validate_evidence, validate_registry
from ..paths import SKILLS as HERE, SCENARIO_ROOT as ROOT, EXTRACTOR
from ..prompts import readjudication_request_text
from _lib.world.gaps import enabled


def reconciliation_result(case, paths):
    """Compute the pending World result without publishing a success receipt."""
    from _lib.world import admission, actor_facts
    from _lib.world.lane_layout import ROADSIDE_DECISION_REVISIONS
    policy_path = case/'state/execution_policy.json'
    policy = read_json(policy_path) if policy_path.is_file() else {}
    result = reconcile_world(read_json(case/'state/world_context.json'),
        [read_json(path) for path in paths], observation_contract=enabled(case),
        duration_s=admission.duration_for(case), actor_fact_review=actor_facts.enabled(case),
        strict_question_contract=actor_facts.strict_enabled(case),
        evidence_frames=actor_facts.frames_for(case),
        roadside_geometry=policy.get('lane_layout_closure') in ROADSIDE_DECISION_REVISIONS)
    result['batch_sha256'] = {path.name:sha256(path) for path in paths}
    return result


class WorldReconciliation:
    def reconcile(self, case):
        from _lib.world import admission, field_repair
        progress = self.progress(case); progress["substage"] = "reconciling"; self.save_progress(case, progress)
        paths = sorted((case / "batches").glob("batch_*.json"))
        batch_hashes = {path.name: sha256(path) for path in paths}
        receipt = case / 'state/reconciliation.json'
        current = read_json(case / 'state/world_context.json')
        from _lib.runtime.identity_revision import recover, proof
        if recover(self, case):
            return True
        if receipt.is_file() and read_json(receipt).get('status') == 'reconciled_after_identity_review':
            saved = proof(case)
            if (not saved or read_json(receipt).get('batch_sha256') != batch_hashes
                    or read_json(receipt).get('world') != read_json(case/'state/world_context.json')):
                raise ContractError('Identity reconciliation certificate or Batch binding changed')
            return True
        # Finish an interrupted, already validated local commit before attempting
        # to compare old frozen Batches with a newer World epoch.
        if self.commit_lane_layout(case, batch_hashes):
            return True
        if receipt.is_file():
            prior = read_json(receipt)
            if (prior.get('status') in {'reconciled', 'reconciled_after_geometry_readjudication'}
                    and prior.get('batch_sha256') == batch_hashes and prior.get('world') == current):
                if admission.enabled(case): admission.validate(current, admission.duration_for(case), case=case)
                return True
        batches = [read_json(path) for path in paths]
        from _lib.world.lane_layout import handles_conflicts
        result = reconciliation_result(case, paths)
        layout_issues = [row for row in result.get('layout_reviews', []) if row['rule_ids']]
        if layout_issues and (result['status'] != 'needs_review' or handles_conflicts(result, batches)):
            return self.close_lane_layout(case, result, paths)
        if admission.enabled(case) and result['status'] != 'needs_review':
            baseline = field_repair.snapshot(case, result['world'])
            pending = case/'state/world_reconciliation.pending.json'
            self.admitted_world(case,source=baseline,stage='world_closure',destination=pending)
            result['world'] = read_json(pending)
            # Validate before publishing either the receipt or the formal World.
            admission.validate(result['world'], admission.duration_for(case), case=case)
        atomic_json(case / "state/reconciliation.json", result)
        if result["status"] == "needs_review":
            unresolved=set(result["unresolved_conflict_ids"])
            geometry_domains={"road_topology","lane_count","actor_lane"}
            relevant=[conflict for batch in batches for conflict in batch["semantic_conflicts"] if conflict["conflict_id"] in unresolved and geometry_domains.intersection(conflict["impact_domains"])]
            progress=self.progress(case)
            if relevant and progress.get("geometry_readjudications",0)<1 and not admission.enabled(case):
                old=read_json(case/"state/world_context.json"); candidate=case/"state/world_context.readjudicated.candidate.json"; expected_epoch=old["evidence_epoch"]+1
                def adopt(_session=None):
                    revised=read_json(candidate); validate_world(revised, admission.duration_for(case), observation_contract=enabled(case))
                    if revised["evidence_epoch"]!=expected_epoch: raise ContractError("geometry re-adjudication epoch mismatch")
                    validate_registry(old["actor_hypotheses"],revised["actor_hypotheses"])
                    for collection,key in (("questions_to_verify","question_id"),("ambiguities","ambiguity_id")):
                        if {item[key] for item in old[collection]} != {item[key] for item in revised[collection]}:
                            raise ContractError("geometry re-adjudication cannot add or remove verification IDs")
                    validate_evidence(revised)
                    for path in {item["world_path"] for item in relevant}:
                        accepted={json.dumps(item["proposed_value"],sort_keys=True,ensure_ascii=False)
                                  for item in relevant if item["world_path"]==path}
                        actual=json.dumps(world_conflict_value(revised,path),sort_keys=True,ensure_ascii=False)
                        if actual not in accepted:
                            raise ContractError(f"geometry re-adjudication did not choose an evidenced proposal: {path}")
                    atomic_json(case/"state/world_context.json",revised); candidate.unlink(missing_ok=True)
                self.model_stage(case,"geometry_readjudication","global-video-understanding",
                    readjudication_request_text(case,sorted(unresolved),expected_epoch),adopt,lambda:adopt(None),candidate_path=candidate)
                progress=self.progress(case); progress["geometry_readjudications"]=1; self.save_progress(case,progress)
                atomic_json(case/"state/reconciliation.json",{"status":"reconciled_after_geometry_readjudication","world":read_json(case/"state/world_context.json"), "batch_sha256":batch_hashes,
                    "applied_conflict_ids":sorted(unresolved),"unresolved_conflict_ids":[],"affected_batch_ids":sorted({batch["batch_id"] for batch in batches if any(item["conflict_id"] in unresolved for item in batch["semantic_conflicts"])})})
                return True
            validate_capabilities()
            envelope=completion_envelope("needs_review",diagnostics={"conflict_ids": result["unresolved_conflict_ids"], "world_epochs": [read_json(case/'state/world_context.json')["evidence_epoch"]], "resume_from": "reconciling"})
            progress = self.progress(case); progress.update(envelope); progress.update({"updated_at": now()}); self.save_progress(case, progress)
            return False
        atomic_json(case / "state/world_context.json", result["world"]); return True

    def commit_lane_layout(self, case, batch_hashes):
        """Idempotent recovery of the existing stage's validated local writes."""
        journal_path = case/'state/lane_layout.commit.json'
        if not journal_path.is_file(): return False
        from _lib.behavior.compile import preview_digest
        from _lib.world import admission
        journal = read_json(journal_path)
        body = dict(journal); signature = body.pop('sha256', None)
        if signature != preview_digest(body) or journal['receipt']['batch_sha256'] != batch_hashes:
            raise ContractError('Lane layout commit binding changed', rule_id='world.layout.commit_binding')
        from _lib.runtime.world_adjudication import validate_journal
        if journal.get('post_behavior_revision'):
            from _lib.world.post_behavior import PREPARED
            if Path(journal['post_behavior_revision']['path']) != (case/PREPARED).resolve():
                raise ContractError('Post-Behavior journal belongs to another Case')
        validate_journal(journal)
        if journal.get('status') == 'committed':
            from _lib.runtime.fact_versions import ROOT as FACT_ROOT, commit_world_closure
            if (case/FACT_ROOT/'active.json').is_file() and (
                    journal.get('census_certificate') or journal.get('post_behavior_revision')):
                # A baseline may have been established after the old raw commit.
                # Its historical journal is already effective, not a new revision.
                legacy_committed = (raw_read_json(case/'state/world_context.json') == journal['world']
                    and raw_read_json(case/'state/actor_registry.json') == journal['registry']
                    and raw_read_json(case/'state/reconciliation.json') == journal['receipt']
                    and (not journal.get('census_certificate') or
                         raw_read_json(case/'state/census_adjudication.accepted.json') ==
                         journal['census_certificate']))
                if not legacy_committed:
                    commit_world_closure(case, journal)
                if journal.get('post_behavior_revision'):
                    from _lib.world.post_behavior import proof
                else:
                    from _lib.contracts.census_adjudication import proof
                proof(case)
                return True
            if journal.get('post_behavior_revision'):
                from _lib.world.post_behavior import proof
                proof(case)
            if journal.get('census_certificate'):
                from _lib.contracts.census_adjudication import proof
                proof(case)
            return (raw_read_json(case/'state/world_context.json') == journal['world']
                    and raw_read_json(case/'state/reconciliation.json') == journal['receipt'])
        for path, digest in journal['input_hashes'].items():
            if not Path(path).is_file() or sha256(Path(path)) != digest:
                raise ContractError('Lane layout frozen input changed', rule_id='world.layout.input_binding')
        from _lib.runtime.fact_versions import ROOT as FACT_ROOT, commit_world_closure
        if (case/FACT_ROOT/'active.json').is_file() and (
                journal.get('census_certificate') or journal.get('post_behavior_revision')):
            commit_world_closure(case, journal)
            if journal.get('post_behavior_revision'):
                from _lib.world.post_behavior import retire_dependencies, finish_progress
                retire_dependencies(case, journal)
                finish_progress(self, case, journal)
            journal['status'] = 'committed'
            journal.pop('sha256', None); journal['sha256'] = preview_digest(journal)
            atomic_json(journal_path, journal)
            if journal.get('post_behavior_revision'):
                from _lib.world.post_behavior import proof
            else:
                from _lib.contracts.census_adjudication import proof
            proof(case)
            return True
        if journal.get('census_certificate'):
            from _lib.contracts.census_adjudication import ACCEPTED
            certificate = case/ACCEPTED
            parent = journal['census_certificate']['request'].get('parent_certificate')
            if parent and not certificate.is_file():
                raise ContractError('Census continuation lost its accepted parent')
            if certificate.exists() and read_json(certificate) != journal['census_certificate']:
                if not parent or sha256(certificate) != parent['sha256']:
                    raise ContractError('Census commit would overwrite a different certificate')
        for name, old_hash, value in (
                ('world_context.json', journal['old_world_sha256'], journal['world']),
                ('actor_registry.json', journal['old_registry_sha256'], journal['registry'])):
            target = case/'state'/name
            if sha256(target) != old_hash and read_json(target) != value:
                raise ContractError('Lane layout commit would overwrite a different checkpoint',
                                    rule_id='world.layout.commit_binding')
        if journal.get('post_behavior_revision'):
            receipt = case/'state/reconciliation.json'
            if (not receipt.is_file() or sha256(receipt) != journal['old_receipt_sha256']
                    and read_json(receipt) != journal['receipt']):
                raise ContractError('Post-Behavior receipt destination changed')
        validate_world(journal['world'], admission.duration_for(case), observation_contract=enabled(case))
        if admission.enabled(case): admission.validate(journal['world'], admission.duration_for(case), case=case)
        if journal.get('post_behavior_revision'):
            from _lib.world.post_behavior import retire_dependencies
            retire_dependencies(case, journal)
        atomic_json(case/'state/world_context.json', journal['world'])
        atomic_json(case/'state/actor_registry.json', journal['registry'])
        if journal.get('census_certificate'):
            from _lib.contracts.census_adjudication import ACCEPTED
            atomic_json(case/ACCEPTED, journal['census_certificate'])
        atomic_json(case/'state/reconciliation.json', journal['receipt'])
        if journal.get('post_behavior_revision'):
            from _lib.world.post_behavior import finish_progress
            finish_progress(self, case, journal)
        journal['status'] = 'committed'
        journal.pop('sha256', None); journal['sha256'] = preview_digest(journal)
        atomic_json(journal_path, journal)
        return True

    def close_lane_layout(self, case, result, batch_paths):
        """One targeted World adjudication, then the normal Behavior handoff."""
        from _lib.world import admission, lane_layout
        from _lib.world.closure import ClosureStopped, write_request, materialize, prompt
        from _lib.behavior.compile import preview_digest
        from _lib.contracts.frame_evidence import validate_frame_evidence
        review_id = next(row['question_id'] for row in result['layout_reviews'] if row['rule_ids'])
        base = case/'checkpoints/world'/f"{preview_digest(result['world'])}.layout.json"
        if base.is_file() and read_json(base) != result['world']:
            raise ContractError('Lane layout immutable base changed')
        if not base.is_file(): atomic_json(base, result['world'])
        candidate = case/'state/world_closure.candidate.json'
        request = write_request(case, base, candidate, [lane_layout.ISSUE + review_id],
            result['world']['evidence_epoch'] + 1, batch_paths, HERE/'schemas/world_context.schema.json',
            issue_details=[])
        packet = read_json(request)
        from _lib.contracts import census_adjudication
        if census_adjudication.enabled(case) and census_adjudication.records(case):
            from _lib.runtime.world_adjudication import run
            return run(self, case, request, result)
        progress = self.progress(case)
        # Other closure attempts keep their budget. Never reset attempts on
        # restart; the framework reconciles any active session before submit.
        attempts = progress.get('attempts', {}).get('world_closure', 0)
        prior_review = progress.setdefault('diagnostics', {}).get('lane_layout_review', {})
        start_attempt = (prior_review.get('start_attempt', attempts)
                         if prior_review.get('request_sha256') == packet['request_sha256'] else attempts)
        outcome_path = case/'diagnostics/lane_layout_closure_outcome.json'
        resumed_outcome = None
        recovery_source = None
        validator_revision = lane_layout.validator_version()
        active_layout = any(row.get('stage') == 'world_closure' and row.get('status') in {'submitted','pending','running'}
                            for row in progress.get('sessions', []))
        if outcome_path.is_file() and not active_layout:
            prior = read_json(outcome_path)
            if prior.get('request_sha256') == packet['request_sha256']:
                if prior.get('status') == 'superseded_for_bounded_repair':
                    resumed_outcome = prior
                elif lane_layout.repairable_outcome(prior) and attempts < start_attempt + 2:
                    archive = case/'diagnostics'/(
                        'lane_layout_closure_outcome.'
                        + str(prior.get('candidate_sha256', 'legacy'))[:16] + '.json')
                    if not archive.is_file(): atomic_json(archive, prior)
                    resumed_outcome = dict(prior, status='superseded_for_bounded_repair',
                        superseded_at=now(), recovery_rule='lane-layout-bounded-repair.v1')
                    atomic_json(outcome_path, resumed_outcome)
                    recoveries = progress.setdefault('diagnostics', {}).setdefault(
                        'lane_layout_outcome_recoveries', [])
                    marker = (packet['request_sha256'], prior.get('candidate_sha256'))
                    if not any((row.get('request_sha256'), row.get('candidate_sha256')) == marker
                               for row in recoveries):
                        recoveries.append(dict(request_sha256=marker[0], candidate_sha256=marker[1],
                            prior_rule_id=prior.get('rule_id'), recovery_rule='lane-layout-bounded-repair.v1',
                            at=now()))
                else:
                    digest = prior.get('candidate_sha256')
                    source = case/'diagnostics/candidate_snapshots'/f'world_closure.{digest}.json'
                    if (not any(row.get('stage') == 'world_closure' and row.get('status') in {'submitted','pending','running'}
                                for row in progress.get('sessions', []))
                            and prior.get('validator_revision') != validator_revision
                            and isinstance(digest, str) and len(digest) == 64
                            and all(char in '0123456789abcdef' for char in digest)
                            and source.is_file() and sha256(source) == digest):
                        recovery_source = source
                    else:
                        raise ClosureStopped(prior['code'], prior.get('message') or prior.get('reason'), prior)
        progress['diagnostics']['lane_layout_review'] = {
            'request_sha256':packet['request_sha256'], 'start_attempt':start_attempt,
            'rule_ids':sorted({rule for row in result['layout_reviews'] for rule in row['rule_ids']})}
        self.save_progress(case, progress)
        def adopt(_session=None, *, source=None):
            source = source or candidate
            if not source.is_file(): raise ContractError('missing_output')
            patch, digest = candidate_snapshot(case, 'world_closure', source)
            try:
                revised = materialize(case, patch)
                lane_map = (lane_layout.decision_lane_map(packet, result['world'], patch)
                            if packet['lane_layout_contract']['revision'] in lane_layout.DECISION_REVISIONS
                            else patch['lane_id_map'])
                revised['world']['metadata']['lane_layout_review']['adjudication'] = dict(
                    source_epoch=result['world']['evidence_epoch'], lane_id_map=lane_map,
                    request_sha256=packet['request_sha256'])
                if packet['lane_layout_contract']['revision'] == lane_layout.EVIDENCE_DECISION_REVISION:
                    revised['world']['metadata']['lane_layout_review']['adjudication'][
                        'claim_assessments'] = copy.deepcopy(patch['decision']['claim_assessments'])
                if packet['lane_layout_contract']['revision'] in lane_layout.DERIVED_DECISION_REVISIONS:
                    revised['world']['metadata']['lane_layout_review']['adjudication'].update(
                        claim_assessments=lane_layout.derived_claim_assessments(packet,patch['decision']),
                        claim_disputes=copy.deepcopy(lane_layout.compile_corridor_positions(packet, patch['decision'])['claim_disputes']))
                validate_world(revised, admission.duration_for(case), observation_contract=enabled(case))
                if packet['lane_layout_contract']['revision'] not in lane_layout.DECISION_REVISIONS:
                    validate_frame_evidence({'operations':patch['operations']}, packet['focused_context']['evidence_frames'], stage='world_closure')
                if admission.enabled(case): admission.validate(revised, admission.duration_for(case), case=case)
                validate_registry(result['world']['actor_hypotheses'], revised['actor_hypotheses'])
                validate_evidence(revised)
            except ClosureStopped as error:
                atomic_json(outcome_path, dict(code=error.code, message=str(error),
                    request_sha256=packet['request_sha256'], candidate_sha256=digest,
                    validator_revision=validator_revision, validation_details=error.diagnostics))
                raise
            except ValueError as error:
                details = dict(getattr(error, 'diagnostics', {}) or {})
                details.update(stage='world_closure', request_sha256=packet['request_sha256'],
                               candidate_sha256=digest, validator_revision=validator_revision)
                error.diagnostics = details
                rule_id = getattr(error, 'rule_id', None)
                if rule_id in lane_layout.REPAIRABLE_RULES:
                    used = self.progress(case).get('attempts', {}).get('world_closure', 0)
                    if used < start_attempt + 2 and recovery_source is None:
                        # A changed candidate can repair a bounded, specific
                        # format error or re-examine the independent challenge.
                        # No second identical candidate is ever committed.
                        raise
                    code = ('world_evidence_insufficient' if rule_id == 'world.layout.batch_disagreement'
                            else 'world_patch_contract_blocked')
                    diagnostic = dict(code=code, message=str(error), rule_id=rule_id,
                        request_sha256=packet['request_sha256'], candidate_sha256=digest,
                        automatic_retry=False, validator_revision=validator_revision,
                        validation_details=getattr(error, 'diagnostics', {}))
                    atomic_json(outcome_path, diagnostic)
                    raise ClosureStopped(code, str(error), diagnostic) from error
                diagnostic = dict(code='world_patch_contract_blocked', message=str(error),
                    request_sha256=packet['request_sha256'], candidate_sha256=digest,
                    validator_revision=validator_revision,
                    validation_details=getattr(error, 'diagnostics', {}),
                    rule_id=rule_id or 'world.layout.patch')
                atomic_json(outcome_path, diagnostic)
                raise ClosureStopped(diagnostic['code'], str(error), diagnostic) from error
            registry_path = case/'state/actor_registry.json'
            registry = lane_layout.remap_registry(read_json(registry_path), lane_map, revised)
            validate_registry(read_json(registry_path), registry)
            receipt = dict(result, status='reconciled_after_geometry_readjudication', world=revised,
                layout_adjudication=dict(request_sha256=packet['request_sha256'], candidate_sha256=digest,
                    source_epoch=result['world']['evidence_epoch'], lane_id_map=lane_map))
            receipt['adjudicated_conflict_ids'] = receipt['unresolved_conflict_ids']
            receipt['unresolved_conflict_ids'] = []
            resolved = {q['question_id'] for q in revised['questions_to_verify'] if q['resolution'] == 'resolved'}
            receipt['resolved_verification_ids'] = sorted(set(receipt.get('resolved_verification_ids', [])) | resolved)
            receipt['pending_verification_ids'] = [qid for qid in receipt.get('pending_verification_ids', []) if qid not in resolved]
            atomic_json(case/'diagnostics'/f'lane_layout_patch.{digest[:16]}.json', patch)
            journal = dict(world=revised, registry=registry, receipt=receipt,
                old_world_sha256=sha256(case/'state/world_context.json'), old_registry_sha256=sha256(registry_path),
                input_hashes=packet['input_hashes'] | {str(request.resolve()):sha256(request)})
            journal['sha256'] = preview_digest(journal)
            atomic_json(case/'state/lane_layout.commit.json', journal)
            self.commit_lane_layout(case, result['batch_sha256'])
            if source == candidate:
                candidate.unlink(missing_ok=True)
        if recovery_source is not None:
            adopt(source=recovery_source)
            recovered = self.progress(case)
            recovered.setdefault('diagnostics', {}).setdefault('deterministic_candidate_recoveries', []).append(
                dict(stage='world_closure', source_path=str(recovery_source),
                     source_sha256=sha256(recovery_source), validator_revision=validator_revision,
                     recovery='revalidated_layout_archive', at=now()))
            self.save_progress(case, recovered)
            return True
        task_prompt = prompt(case, request)
        if resumed_outcome is not None:
            focus = ('重新检查物理边界、必要的争议区间及直接帧证据；不要提交程序可推导的支持/反驳标签，'
                     if packet['lane_layout_contract']['revision'] in lane_layout.DERIVED_DECISION_REVISIONS else
                     '重新检查双方主张的逐项评估、争议物理区间及逐帧证据，'
                     if packet['lane_layout_contract']['revision'] == lane_layout.EVIDENCE_DECISION_REVISION else
                     '重新检查裁决的 N+1 行驶边界、右侧连续区域的独立外缘、每个旧车道的一一对应和逐项帧证据，'
                     if packet['lane_layout_contract']['revision'] == lane_layout.DECISION_REVISION else
                     '重新检查裁决的 N+1 物理边界、每个旧车道的一一对应和逐项帧证据，'
                     if packet['lane_layout_contract']['revision'] == lane_layout.LEGACY_DECISION_REVISION else
                     '重新检查边界链的每个 ID 都在 lane_markings 中注册，')
            task_prompt += ('\n上一次候选已通过冻结绑定但未通过确定性布局门禁：'
                + str(resumed_outcome.get('message') or resumed_outcome.get('reason'))
                + '。这是唯一一次有界修正；' + focus + '提交新的完整 candidate，不要解释。')
            if resumed_outcome.get('validation_details'):
                task_prompt += '\n结构化失败定位：' + json.dumps(resumed_outcome['validation_details'], ensure_ascii=False)
        self.model_stage(case, 'world_closure', 'global-video-understanding', task_prompt,
            adopt, lambda:adopt(None), candidate_path=candidate, max_attempts=start_attempt + 2)
        return True

    def ensure_world_ready(self, case, *, execution=True):
        """Accept observation facts, or close questions for strict legacy callers."""
        case = Path(case)
        from _lib.world import admission, field_repair
        from _lib.runtime.fact_versions import ROOT as FACT_ROOT, current_facts
        if (case/FACT_ROOT/'active.json').is_file():
            # A resumed versioned Case must validate its pinned World without
            # re-publishing the historical source path through admitted_world.
            view = current_facts(case)
            from _lib.world.readiness import observation_handoff_issues
            from _lib.world.closure import ClosureStopped
            admission.validate(view.world, admission.duration_for(case), case=case)
            issues = observation_handoff_issues(view.world)
            if issues:
                raise ClosureStopped('world_evidence_insufficient', issues[0]['message'],
                    dict(issues[0], stage='world_closure', automatic_retry=False,
                         input_sha256=sha256(view.world_path)))
            return
        field_prompt = field_repair.prompt(case,'world_closure') if admission.enabled(case) else None
        active_legacy_patch = (not field_prompt and (case/'state/world_closure.request.json').is_file() and
                              any(s.get('stage')=='world_closure' and s.get('status') in field_repair.LIVE
                                  for s in self.progress(case).get('sessions',[])))
        if admission.enabled(case) and not active_legacy_patch and (not execution or field_prompt):
            self.admitted_world(case,source=case/'state/world_context.json',stage='world_closure')
            if not execution:
                from _lib.world.readiness import observation_handoff_issues
                from _lib.world.closure import ClosureStopped
                issues = observation_handoff_issues(read_json(case/'state/world_context.json'))
                if issues:
                    raise ClosureStopped('world_evidence_insufficient', issues[0]['message'],
                        dict(issues[0], stage='world_closure', automatic_retry=False,
                             input_sha256=sha256(case/'state/world_context.json'),
                             recovery='Retain the observations. Resolve missing lane definitions '
                                      'with evidence before starting Behavior.'))
            return
        target = case/"state/world_context.json"
        original = read_json(target)
        active_closure = any(item.get('stage') == 'world_closure' and item.get('status') in
                             {'submitted', 'pending', 'running'}
                             for item in self.progress(case).get('sessions', []))
        from _lib.contracts.v3 import _sync_junction_membership
        _sync_junction_membership(original)
        world = normalize_world(original)
        if active_closure:
            packet_path = case/'state/world_closure.request.json'
            frozen = read_json(packet_path) if packet_path.is_file() else {}
            if (world != original or frozen.get('contract_version') != 'world-closure-patch-v1'
                    or frozen.get('base_sha256') != sha256(target)):
                raise ContractError('active World closure input/contract changed; resolve the existing task before recovery')
        # An existing task keeps its frozen contract until it is reconciled;
        # switching to observation mode cannot orphan it or rewrite its input.
        strict = execution or active_closure
        issues = frozen['issues'] if active_closure else readiness_issues(world, execution=strict, observation_contract=enabled(case))
        if not active_closure:
            validate_world(world, admission.duration_for(case), observation_contract=enabled(case))
        # Topology issues are recoverable by converter fallbacks; do not
        # trigger world_closure for them.
        issues = [i for i in issues
                  if not i.startswith('world road topology:')
                  and 'consistency' not in i.lower()]
        if not issues:
            if world != original:
                atomic_json(case/"diagnostics"/f"world_before_local_normalization.{sha256(target)[:16]}.json", original)
                atomic_json(target, world)
            return
        # Persist lossless local normalizations before binding a model patch.
        # The model never has to reproduce the complete World document.
        if world != original:
            atomic_json(case/"diagnostics"/f"world_before_local_normalization.{sha256(target)[:16]}.json", original)
            atomic_json(target, world)
            original = world
        progress = self.progress(case); progress["substage"] = "reconciling"; self.save_progress(case, progress)
        candidate = case/"state/world_closure.candidate.json"
        expected_epoch = original["evidence_epoch"] + 1
        from _lib.world.closure import ClosureStopped, materialize, normalize_patch_evidence, prompt as closure_prompt, write_request
        packet = write_request(
            case, target, candidate, issues, expected_epoch,
            sorted((case/"batches").glob("batch_*.json")), HERE/"schemas/world_context.schema.json")
        active=next((item for item in reversed(progress.get("sessions",[]))
                     if item.get("stage")=="world_closure" and item.get("status") in {"submitted","pending","running"}),None)
        request_hash=sha256(packet)
        progress=self.progress(case)
        previous_hash=progress.setdefault("diagnostics",{}).get("world_closure_request_sha256")
        if previous_hash and previous_hash != request_hash and not active:
            progress.setdefault("retry_epochs",{})["world_closure"]=progress.setdefault("retry_epochs",{}).get("world_closure",0)+1
            progress.setdefault("attempts",{}).pop("world_closure",None)
        progress["diagnostics"]["world_closure_request_sha256"]=request_hash
        self.save_progress(case,progress)
        def adopt(_session=None, source=None):
            source = candidate if source is None else Path(source)
            if not source.resolve().is_relative_to(case.resolve()):
                raise ContractError("World closure candidate belongs to another Case")
            patch, candidate_digest = candidate_snapshot(case, "world_closure", source)
            try:
                revised, repairs = normalize_patch_evidence(materialize(case, patch))
            except ClosureStopped as error:
                atomic_json(case/'diagnostics/world_closure_outcome.json', {
                    'code':error.code, 'message':str(error), 'response':error.diagnostics,
                    'request_sha256':read_json(packet)['request_sha256'], 'candidate_sha256':candidate_digest})
                if admission.enabled(case): field_repair.record_closure_stop(case,packet,error)
                raise
            if repairs:
                atomic_json(case/'diagnostics/world_closure_local_repairs.json', {
                    'candidate_sha256':candidate_digest, 'repairs':repairs})
            revised = normalize_world(revised); validate_world(revised, admission.duration_for(case), observation_contract=enabled(case))
            if admission.enabled(case): admission.validate(revised, admission.duration_for(case), case=case)
            if revised["evidence_epoch"] != expected_epoch: raise ContractError("closure epoch mismatch")
            validate_registry(original["actor_hypotheses"], revised["actor_hypotheses"])
            for collection, key in (("questions_to_verify","question_id"),("ambiguities","ambiguity_id")):
                if {x[key] for x in original[collection]} != {x[key] for x in revised[collection]}:
                    raise ContractError("closure cannot remove questions or ambiguities")
            validate_evidence(revised)
            remaining = readiness_issues(revised, execution=strict, observation_contract=enabled(case))
            remaining = [i for i in remaining
                         if not i.startswith('world road topology:')
                         and 'consistency' not in i.lower()]
            if remaining and set(issues).issubset(remaining):
                diagnostic = {'code':'world_closure_no_progress', 'issues_before':issues,
                    'issues_after':remaining, 'request_sha256':read_json(packet)['request_sha256'],
                    'candidate_sha256':candidate_digest, 'paths':[op['path'] for op in patch['operations']]}
                atomic_json(case/'diagnostics/world_closure_outcome.json', diagnostic)
                error = ClosureStopped('world_closure_no_progress',
                    'World patch did not resolve any readiness issue; unchanged task will not be retried', diagnostic)
                if admission.enabled(case): field_repair.record_closure_stop(case,packet,error)
                raise error
            if remaining:
                raise ContractError("World handoff unresolved: " + "; ".join(remaining))
            # Audit before commit; an epoch change invalidates Behavior, never the source Batches.
            atomic_json(case/"state"/f"world_before_closure.{sha256(target)[:16]}.json", original)
            atomic_json(case/"diagnostics"/f"world_closure_patch.{candidate_digest[:16]}.json", patch)
            atomic_json(target, revised)
            atomic_json(case/"state/reconciliation.json", {"status":"reconciled_after_world_closure",
                        "world":revised,"source_epoch":original["evidence_epoch"],"unresolved_conflict_ids":[],
                        "request_sha256":sha256(packet)})
            if source == candidate:
                candidate.unlink(missing_ok=True)
        if not active and not candidate.is_file():
            for record in reversed(progress.get("sessions", [])):
                if record.get("stage") != "world_closure" or not record.get("invalid_candidate_path"):
                    continue
                source = Path(record["invalid_candidate_path"])
                if not source.resolve().is_relative_to(case.resolve()) or not source.is_file():
                    continue
                try:
                    adopt(source=source)
                except ClosureStopped:
                    raise
                except ValueError:
                    # Stale hashes and unresolved semantic issues remain invalid.
                    continue
                recovered = self.progress(case)
                recovered.setdefault("diagnostics", {}).setdefault("deterministic_candidate_recoveries", []).append({
                    "stage": "world_closure", "source_path": str(source), "source_sha256": sha256(source),
                    "recovery": "revalidated_closure_archive", "at": now(),
                })
                self.save_progress(case, recovered)
                return
        negative = []
        def adopt_or_record_stop(_session=None):
            try:
                adopt(_session)
            except ClosureStopped as error:
                if error.code != 'world_evidence_insufficient':
                    raise
                negative.append(error)
        if admission.enabled(case) and not active and candidate.is_file():
            adopt_or_record_stop()
            if negative: raise negative[-1]
            return
        prompt = closure_prompt(case, packet)
        self.model_stage(case, "world_closure", "global-video-understanding", prompt, adopt_or_record_stop,
                         lambda: adopt_or_record_stop(None), candidate_path=candidate)
        if negative:
            raise negative[-1]
