"""WorldStage: deterministic pipeline adapter, never a model worker."""
from __future__ import annotations

from _lib.runtime.orchestrator import candidate_snapshot, quarantine_candidate
from _lib.world.readiness import check_world_shape, normalize_world, world_consistency_issues
from _lib.contracts.v3 import validate_world
from _lib.runtime.case_core import atomic_json, now, read_json
from ..prompts import world_request_text
from _lib.world.gaps import enabled


class WorldStage:
    def topology_answers(self, case):
        """One bounded World subtask, using the existing lease/budget lifecycle."""
        from _lib.world import semantic
        from _lib.behavior.compile import preview_digest
        from _lib.prompts import render
        progress = self.progress(case)
        progress.setdefault('diagnostics', {})['initial_world_task'] = 'world_topology'
        self.save_progress(case, progress)
        request = semantic.packet(case)
        candidate = case/'state/topology_answers.candidate.json'
        receipt = case/'state/topology_answers.json'

        def adopt(_session=None):
            # Callback may have committed just before the process died. The
            # model-stage lifecycle still reconciles any live task first.
            if receipt.is_file():
                semantic.load_compiled(case)
                return
            semantic.validate_packet(case, request)
            raw, digest = candidate_snapshot(case, 'world_topology', candidate)
            try:
                compiled = semantic.compile_road(raw, request)
            except Exception as error:
                if isinstance(getattr(error, 'diagnostics', None), dict):
                    error.diagnostics.update(stage='world_topology', candidate_sha256=digest,
                        input_sha256=request['request_sha256'],
                        recovery='Correct only the reported answer using the frozen evidence; retain unknown if unresolved.')
                raise
            atomic_json(receipt, dict(revision=semantic.REVISION,
                request_sha256=request['request_sha256'], outcome=raw['outcome'],
                candidate_snapshot=str(case/'diagnostics/candidate_snapshots'/f'world_topology.{digest}.json'),
                candidate_sha256=digest, compiled_sha256=preview_digest(compiled)))
            candidate.unlink(missing_ok=True)

        prompt = render('world-understanding', 'topology-answers-prompt.md',
                        request_path=case/'state/topology_answers.request.json',
                        candidate_path=candidate)
        try:
            self.model_stage(case, 'world_topology', 'global-video-understanding',
                             prompt, adopt, precheck=adopt, candidate_path=candidate,
                             max_attempts=2)
        except RuntimeError as cause:
            if str(cause) != 'model_attempts_exhausted':
                raise
            # Preserve the actual failure, not just the exhausted retry counter.
            from _lib.contracts.v3 import ContractError
            last = next((s for s in reversed(self.progress(case).get('sessions', []))
                         if s.get('stage') == 'world_topology'), {})
            code = last.get('error_code')
            if not code:
                raise
            error = ContractError(last.get('message') or code,
                                  rule_id=last.get('rule_id'))
            error.code = ('world_topology_contract_blocked'
                          if code == 'schema_validation_failed' else code)
            error.diagnostics = dict(last.get('validation_details') or {},
                                     automatic_retry=False, attempts_exhausted=True)
            raise error from cause

    def world_understanding(self, case):
        from _lib.runtime.execution_policy import require_active_policy
        case_record = case / "case.json"
        if case_record.is_file():
            require_active_policy(read_json(case_record).get("parameters"))
        from _lib.world import single_world
        if single_world.enabled(case):
            if not (case/'state/world_context.json').exists():
                single_world.packet(case)
            return self.admitted_world(case)
        return self.legacy_world_understanding(case)

    def legacy_world_understanding(self, case):
        """Frozen pre-v2 observation paths; never selected by a new v3 Case."""
        from _lib.world.admission import enabled as new_admission
        from _lib.world.semantic import legacy_topology_enabled
        if legacy_topology_enabled(case) and not (case/'state/world_context.json').exists():
            self.topology_answers(case)
            progress = self.progress(case)
            progress.setdefault('diagnostics', {})['initial_world_task'] = 'world'
            self.save_progress(case, progress)
        if new_admission(case):
            return self.admitted_world(case)
        target = case / "state/world_context.json"; candidate = case / "state/world_context.candidate.json"
        duration = read_json(case / "state/probe.json")["probe"]["duration_s"]
        def adopt(_session=None):
            raw, digest = candidate_snapshot(case, "world", candidate)
            data = normalize_world(raw)
            if data != raw:
                progress = self.progress(case)
                progress.setdefault("diagnostics", {}).setdefault("deterministic_candidate_repairs", []).append({
                    "stage":"world", "input_sha256":digest, "action":"normalize_world", "at":now()})
                self.save_progress(case, progress)
            validate_world(data, duration, observation_contract=enabled(case))
            check_world_shape(data, observation_contract=enabled(case))
            atomic_json(target, data)
            candidate.unlink(missing_ok=True)
        if target.is_file():
            data = normalize_world(read_json(target))
            validate_world(data, duration, observation_contract=enabled(case))
            # Existing Cases may contain a legacy World checkpoint.  Keep it
            # resumable: ensure_world_ready owns deterministic closure later.
            if world_consistency_issues(data): self.ensure_world_ready(case, execution=False)
            return
        if candidate.is_file():
            try:
                adopt()
            except Exception: quarantine_candidate(case,"world",candidate,0)
            else:
                if world_consistency_issues(read_json(target)): self.ensure_world_ready(case, execution=False)
                return
        self.model_stage(case, "world", "global-video-understanding", world_request_text(case), adopt, lambda: adopt(None), candidate_path=candidate)
        if world_consistency_issues(read_json(target)): self.ensure_world_ready(case, execution=False)

    def admitted_world(self, case, *, source=None, stage='world', destination=None):
        """Same task lifecycle, new admission/field repair only for frozen opt-in Cases."""
        from _lib.world import admission, field_repair
        from _lib.world.single_world import validate_inputs
        validate_inputs(case)
        from _lib.contracts.world_metadata import definition
        definition()  # Local compatibility errors precede any model reservation.
        from _lib.world.closure import ClosureStopped
        import json
        from _lib.runtime.case_core import sha256
        target = destination or case / 'state/world_context.json'
        candidate = case / ('state/world_context.candidate.json' if stage == 'world'
                            else 'state/world_closure.candidate.json')
        probe = case / 'state/probe.json'
        duration = read_json(probe)['probe']['duration_s'] if probe.is_file() else None

        def check(data):
            raw_hash = field_repair.digest(data)
            # First-sighting adaptation belongs to initial admission. Once a
            # World is committed, registry/Batch bindings freeze its identity;
            # later changes require the dedicated identity review certificate.
            data, repairs = admission.normalize(data, case=case,
                normalize_identity=not (case/'state/world_context.json').is_file(),
                normalize_actor_paths=(stage == 'world' and not target.is_file()))
            path = field_repair.snapshot(case, data)
            if repairs:
                atomic_json(case / 'diagnostics' / ('world_normalization.' + sha256(path) + '.json'),
                            {'repairs': repairs, 'input_sha256':raw_hash, 'normalized_sha256': sha256(path)})
            found = admission.issues(data, duration, case=case)
            atomic_json(case / 'diagnostics/world_admission.json', {'issues': found, 'candidate_data_path': str(path), 'stage':stage})
            blocking = admission.blocking_issues(found)
            if blocking:
                error = admission.AdmissionError(blocking)
                error.diagnostics['candidate_data_path'] = str(path)
                raise error
            return data

        def record_compilation(data, candidate_digest):
            from _lib.world import single_world
            if stage == 'world' and single_world.enabled(case):
                compiled_path = field_repair.snapshot(case, data)
                receipt = {
                    'revision': single_world.REVISION,
                    'request_sha256': read_json(case/single_world.REQUEST)['request_sha256'],
                    'candidate_sha256': candidate_digest,
                    'compiled_path': str(compiled_path), 'compiled_sha256': sha256(compiled_path),
                }
                from _lib.world.lane_numbering import normalize_lane_ids
                # Reproduce the pure adapter's audit from the exact archived
                # candidate, without adding private fields to the World schema.
                source = case/'diagnostics/candidate_snapshots'/f'world.{candidate_digest}.json'
                _, numbering = normalize_lane_ids(read_json(source), read_json(case/single_world.REQUEST))
                if numbering:
                    receipt['lane_numbering'] = numbering
                atomic_json(case/'diagnostics'/f'world_compilation.{candidate_digest}.json', receipt)

        def adopt(_session=None):
            try:
                raw, candidate_digest = candidate_snapshot(case, stage, candidate)
            except json.JSONDecodeError as error:
                raise ClosureStopped('world_patch_contract_blocked',
                                     'Invalid JSON retained; no whole-World regeneration') from error
            if field_repair.prompt(case, stage):
                raw = field_repair.materialize(case, raw)
            elif stage == 'world':
                from _lib.world.semantic import materialize_world
                raw = materialize_world(case, raw, initial=True)
            data = check(raw)
            if not field_repair.prompt(case, stage):
                record_compilation(data, candidate_digest)
            atomic_json(target, data)
            candidate.unlink(missing_ok=True)
            if field_repair.prompt(case, stage):
                field_repair.active_path(case).unlink(missing_ok=True)

        live = any(s.get('stage') == stage and s.get('status') in field_repair.LIVE
                   for s in self.progress(case).get('sessions', []))
        if source is None and target.is_file() and not live:
            check(read_json(target))
            return
        if source is None and self._recover_admitted_world(
                case, stage, target, candidate, live, check, record_compilation):
            return
        prompt = field_repair.prompt(case, stage) or world_request_text(case)
        if source is not None and not live:
            try:
                data = check(read_json(source))
            except admission.AdmissionError as error:
                # Baseline is not the output candidate: keep the committed World intact.
                if not field_repair.prompt(case, stage):
                    prompt = field_repair.prepare(case, candidate, source, error, stage)
            else:
                atomic_json(target, data)
                return
        self.model_stage(case, stage, 'global-video-understanding', prompt, adopt,
                         precheck=adopt, candidate_path=candidate)

    def _recover_admitted_world(self, case, stage, target, candidate, live, check, record_compilation):
        """Archived-candidate recovery only, under the same lifecycle owner.

        Normal acceptance and recovery reuse the same compiler/validator and
        audit callback. This helper does not submit tasks or reset budgets.
        """
        from pathlib import Path
        from _lib.world import admission, field_repair
        from _lib.world.closure import ClosureStopped
        from _lib.runtime.case_core import sha256
        # Revalidate the immutable baseline before retrying a rejected patch.
        # An adapter fix can make the original observation admissible without
        # spending another visual task. Active/uncertain tasks retain ownership.
        from _lib.world import compilation_repair
        state = self.progress(case)
        unresolved = state.get('diagnostics', {}).get('unresolved_submissions') or any(
            s.get('stage') == stage and s.get('error_code') in {'task_deadline_exceeded','task_status_unavailable'}
            and not s.get('deadline_task_resolved') for s in state.get('sessions', []))
        active_path = field_repair.active_path(case)
        if not live and not unresolved and not candidate.exists() and active_path.exists():
            record = read_json(active_path)
            baseline = Path(record['base_path'])
            packet = read_json(record['packet_path'])
            bound = dict(packet); bound.pop('request_sha256', None)
            if (record.get('stage', 'world') != stage
                    or not baseline.resolve().is_relative_to(case.resolve()) or sha256(baseline) != record['base_sha256']
                    or packet.get('base_sha256') != record['base_sha256']
                    or packet.get('request_sha256') != record['request_sha256']
                    or field_repair.digest(bound) != record['request_sha256']):
                raise ClosureStopped('world_patch_contract_blocked', 'Archived World repair input binding changed')
            try:
                raw = read_json(baseline)
                if record.get('baseline_kind') == compilation_repair.KIND:
                    from _lib.world.semantic import materialize_world
                    raw = materialize_world(case, raw, initial=True)
                data = check(raw)
            except (admission.AdmissionError, compilation_repair.CompilationError):
                pass  # Existing bounded patch/retry machinery owns remaining gaps.
            else:
                atomic_json(target, data)
                state.setdefault('diagnostics', {}).setdefault('deterministic_candidate_recoveries', []).append(
                    dict(stage=stage, source_path=str(baseline), source_sha256=sha256(baseline),
                         recovery='world_metadata_revalidated', at=now()))
                self.save_progress(case, state)
                active_path.unlink()
                return True
        # A structural stop may precede repair-packet creation. Revalidate only
        # the last session's rejected candidate against its exact-byte snapshot.
        if (not live and not unresolved and not candidate.exists()
                and not active_path.exists()):
            last = next((s for s in reversed(state.get('sessions', []))
                         if s.get('stage') == stage), {})
            archived = last.get('invalid_candidate_path')
            if archived and last.get('status') == 'invalid_output':
                baseline = Path(archived)
                if (not baseline.is_file() or
                        not baseline.resolve().is_relative_to((case/'diagnostics/invalid_candidates').resolve())):
                    raise ClosureStopped('world_patch_contract_blocked', 'Archived World candidate path binding changed')
                digest = sha256(baseline)
                snapshot = case/'diagnostics/candidate_snapshots'/f'{stage}.{digest}.json'
                if (digest[:16] not in baseline.name.split('.') or not snapshot.is_file()
                        or not snapshot.resolve().is_relative_to(case.resolve())
                        or sha256(snapshot) != digest):
                    raise ClosureStopped('world_patch_contract_blocked', 'Archived World candidate hash binding changed')
                try:
                    raw = read_json(baseline)
                    if stage == 'world':
                        from _lib.world.semantic import materialize_world
                        raw = materialize_world(case, raw, initial=True)
                    data = check(raw)
                except (admission.AdmissionError, compilation_repair.CompilationError) as error:
                    field_repair.prepare(case, candidate, baseline, error, stage)
                else:
                    record_compilation(data, digest)
                    atomic_json(target, data)
                    state.setdefault('diagnostics', {}).setdefault('deterministic_candidate_recoveries', []).append(
                        dict(stage=stage, source_path=str(baseline), source_sha256=digest,
                             validator_revision=field_repair.validator_version(),
                             recovery='world_candidate_revalidated', at=now()))
                    self.save_progress(case, state)
                    return True
        return False
