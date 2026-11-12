"""World-owned scoped closure, with a durable atomic-replacement journal."""
from pathlib import Path
from _lib.runtime.case_core import read_json, atomic_json, sha256, now
from _lib.behavior.compile import preview_digest, evidence_frames, validate_compiled_behavior
from _lib.behavior.preview_recovery import rebind_separation, normalize_separation_response, separation_fingerprint
from _lib.world.readiness import ego_carriageway_execution_scope
from _lib.world.closure import write_request, materialize, prompt, ClosureStopped
from _lib.paths import SKILLS
from _lib.world.gaps import enabled


def ensure_preview_separation(runner, case, *, localization=False, verification=False):
    purpose = "execution_verification" if verification else "execution_localization" if localization else "preview_separation"
    case=Path(case)
    progress=runner.progress(case)
    record=progress.setdefault('diagnostics',{}).get(purpose)
    if record and record['status']=='completed': return
    certificate_path=case/'diagnostics/preview_relations.json'
    if not certificate_path.is_file(): return
    from _lib.runtime.execution_scope import inputs, enabled as scoped, registry, view_paths, save_review
    world, behavior, _ = inputs(case)
    world_path, behavior_path = view_paths(case)
    if not record:
        road=world['world']['road']
        if not localization and not verification and road.get('one_way') is not False or len(road.get('roads',[]))!=1: return
        if not localization and not verification and ego_carriageway_execution_scope(world,behavior): return
        geometry=road['roads'][0].get('road_geometry',{})
        # Never reinterpret an already explicit negative/unknown assertion here.
        if not localization and not verification and 'opposing_separation' in geometry: return
        issues = ['world.opposing_separation_evidence_required']
        if localization or verification:
            from _lib.world.readiness import execution_dependencies
            decisions = execution_dependencies(world, behavior, read_json(case/'case.json')['parameters'])['decisions']
            issues = ['world.execution_localization_required '+d['issue_id'] for d in decisions
                      if d['decision']=='undetermined' and d['world_paths']
                      and (all(p.startswith('world.road.roadside_areas') for p in d['world_paths'])
                           or set(d['world_paths']) <= {'world.road.ego_direction_lanes',
                                                       'world.road.roads[0].lane_ids',
                                                       'world.junctions'})]
            if verification:
                lane_paths = {'world.road.ego_direction_lanes', 'world.road.roads[0].lane_ids'}
                issues = ['world.execution_verification_required '+d['issue_id'] for d in decisions
                          if d['decision']=='required' and d['world_paths']
                          and set(d['world_paths']) <= lane_paths]
            if not issues: return
        if any(s.get('status') in {'submitted','pending','running'} for s in progress.get('sessions',[])):
            raise ValueError('Reconcile active model tasks before separation closure')
        base=case/f'diagnostics/{purpose}.world.json'
        old_behavior=case/f'diagnostics/{purpose}.behavior.json'
        old_certificate=case/f'diagnostics/{purpose}.certificate.json'
        for path,value in ((base,world),(old_behavior,behavior),(old_certificate,read_json(certificate_path))):
            atomic_json(path,value)
        candidate=case/f'state/{purpose}.candidate.json'
        request=write_request(case,base,candidate,issues,
                              world['evidence_epoch']+1,sorted((case/'batches').glob('batch_*.json')),
                              SKILLS/'schemas/world_context.schema.json',
                              request_path=case/f'state/{purpose}.request.json' if localization or verification else None)
        record={'status':'pending','base_path':str(base),'behavior_path':str(old_behavior),
                'certificate_path':str(old_certificate),'candidate_path':str(candidate),
                'request_path':str(request),'request_sha256':sha256(request),
                'snapshot_hashes':{str(p):sha256(p) for p in (base,old_behavior,old_certificate)},
                'max_attempts':progress.get('attempts',{}).get('world_closure',0)+1}
        progress['diagnostics'][purpose]=record
        runner.save_progress(case,progress)
    progress=runner.progress(case); progress['substage']='world_closure'; runner.save_progress(case,progress)
    for path,digest in record['snapshot_hashes'].items():
        if sha256(Path(path))!=digest: raise ValueError('separation frozen snapshot changed')
    if sha256(Path(record['request_path']))!=record['request_sha256']:
        raise ValueError('separation closure request changed')
    candidate=Path(record['candidate_path']); prepared=case/f'diagnostics/{purpose}.prepared.json'
    old_world=read_json(record['base_path']); old_behavior=read_json(record['behavior_path'])
    old_certificate=read_json(record['certificate_path'])
    source=candidate
    if not source.is_file():
        sessions=runner.progress(case).get('sessions',[])
        archived=next((Path(s['invalid_candidate_path']) for s in reversed(sessions)
                       if s.get('stage')=='world_closure' and s.get('attempt')==record['max_attempts']
                       and s.get('invalid_candidate_path')),None)
        if archived and archived.is_file() and archived.resolve().is_relative_to((case/'diagnostics').resolve()):
            source=archived

    def adopt(_session=None):
        if prepared.is_file():
            bundle=read_json(prepared)
        else:
            response=read_json(source)
            normalized,repairs=normalize_separation_response(response,read_json(record['request_path']))
            if repairs:
                atomic_json(case/f'diagnostics/{purpose}.normalization.json',{
                    'original_response':response,'original_sha256':sha256(source),
                    'normalized_response':normalized,'repairs':repairs})
            revised=materialize(case,normalized,request_path=record['request_path'])
            from _lib.world import admission
            if admission.enabled(case): admission.validate(revised, admission.duration_for(case), case=case)
            data,certificate=rebind_separation(old_world,revised,old_behavior,old_certificate,evidence_frames(case),localization=localization,verification=verification,observation_contract=enabled(case))
            validate_compiled_behavior(data,revised,registry(case,data),evidence_frames(case),execution=False)
            bundle={'world':revised,'behavior':data,'certificate':certificate}
            atomic_json(prepared,bundle)
        # Recompute before every commit/resume, including a partially committed journal.
        data,certificate=rebind_separation(old_world,bundle['world'],old_behavior,old_certificate,evidence_frames(case),localization=localization,verification=verification,observation_contract=enabled(case))
        if data!=bundle['behavior'] or certificate!=bundle['certificate']:
            raise ValueError('separation prepared result changed')
        outputs=((world_path,old_world,bundle['world']),
                 (behavior_path,old_behavior,data),
                 (certificate_path,old_certificate,certificate))
        for path,old,new in outputs:
            if read_json(path) not in (old,new): raise ValueError('separation commit checkpoint changed')
        atomic_json(case/f'diagnostics/{purpose}.migration.json',{
            **certificate['migration'],'request_sha256':record['request_sha256'],
            'original_response_sha256':old_certificate.get('response_sha256'),
            'prepared_sha256':sha256(prepared)})
        for path,old,new in outputs: atomic_json(path,new)
        if scoped(case): save_review(case,bundle['world'],data)
        current=runner.progress(case)
        current['diagnostics'][purpose].update(status='completed',finished_at=now())
        current['diagnostics']['preview_review'].update(world_sha256=sha256(world_path),
                                                       behavior_sha256=sha256(behavior_path))
        runner.save_progress(case,current)
    try:
        reservations=runner.progress(case).get('diagnostics',{}).get('model_task_reservations',[])
        last_reservation=next((r for r in reversed(reservations) if r.get('stage')=='world_closure'),{})
        definitely_unsubmitted=(last_reservation.get('status')=='abandoned_before_task_id'
                                and last_reservation.get('reason')=='backend_unavailable')
        if not prepared.is_file() and source.is_file() and record.get('validation_fingerprint')==separation_fingerprint(record,source):
            raise ClosureStopped(record.get('error_code','world_patch_contract_blocked'),record.get('error','Unchanged separation validation; retry has no effect.'))
        if prepared.is_file() or source.is_file():
            adopt()
        elif record.get('status') in {'needs_review','failed'} and not definitely_unsubmitted:
            raise ClosureStopped('world_evidence_insufficient',record.get('error','Separation closure requires new evidence.'))
        else:
            runner.model_stage(case,'world_closure','global-video-understanding',prompt(case,record['request_path']),adopt,
                               candidate_path=candidate,max_attempts=record['max_attempts'])
    except Exception as error:
        current=runner.progress(case)
        code=('model_recovery_budget_exhausted' if str(error)=='model_recovery_budget_exhausted'
              else runner.candidate_error_code(error))
        pending=code in {'backend_unavailable','submission_unresolved','task_status_unavailable','task_deadline_exceeded','gateway_429','gateway_5xx'}
        cause=error
        while cause.__cause__ is not None: cause=cause.__cause__
        message=str(cause)
        if str(error)=='model_attempts_exhausted':
            session=next((s for s in reversed(current.get('sessions',[])) if s.get('stage')=='world_closure'),{})
            message=session.get('message',message)
            if session.get('error_code')=='schema_validation_failed': code='world_patch_contract_blocked'
        elif code=='schema_validation_failed': code='world_patch_contract_blocked'
        entry=current['diagnostics'][purpose]
        entry.update(status='pending' if pending else 'needs_review',
            error=message,error_code=code,required_change=(
                'Existing global recovery budget is exhausted; provide new evidence without resetting the budget.'
                if code=='model_recovery_budget_exhausted' else
                'Restore service access or reconcile the existing task before retrying.' if pending else
                'Correct the scoped candidate or provide new evidence; do not alter lane counts or reset the recovery budget.'))
        if source.is_file(): entry['validation_fingerprint']=separation_fingerprint(record,source)
        runner.save_progress(case,current)
        if code=='world_patch_contract_blocked':
            raise ClosureStopped(code,message,{'required_change':entry['required_change']}) from error
        raise
