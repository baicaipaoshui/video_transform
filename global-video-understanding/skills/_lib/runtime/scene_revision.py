"""Formal deterministic revision of a completed Case; never overwrite its artifacts."""
import copy
from pathlib import Path
from _lib.runtime.case_core import Cases, read_json, atomic_json, sha256, now
from _lib.behavior.compile import preview_digest
from _lib.contracts.v3 import ContractError, normalize_parameters
from _lib.paths import EXTRACTOR


def run(video, parent_id, corrections_path, parameters):
    from _lib.runtime.v3 import V3Orchestrator
    from _lib.runtime.orchestrator import validate_analysis
    from _lib.behavior.scene_revision import build, HIGHWAY_SOURCE
    parent=(EXTRACTOR/'cases'/parent_id).resolve()
    if parent.parent!=(EXTRACTOR/'cases').resolve():raise ContractError('Invalid parent case')
    runner=V3Orchestrator(object()); original=read_json(parent/'state/progress.json')
    if original['status'] not in {'done','done_degraded'}:raise ContractError('Scene revision requires a completed parent')
    runner.validate_completed(parent,original)
    record=read_json(parent/'case.json'); binding=read_json(parent/'state/binding.json')
    if normalize_parameters(parameters)!=normalize_parameters(record['parameters']):raise ContractError('case parameters mismatch')
    if Path(video).resolve()!=Path(binding['source_path']).resolve() or sha256(video)!=binding['source_sha256']:raise ContractError('source_changed')
    corrections=read_json(Path(corrections_path));source_path=parent/'final/analysis.json'
    if corrections.get('parent_analysis_sha256')!=sha256(source_path):raise ContractError('Correction source hash mismatch')
    source=read_json(source_path);certificate=source['quality_report'].get('intersection_preview')
    probe=read_json(parent/'state/probe.json')['probe']
    if source['scene_type']=='highway':
        certificate={'kind':HIGHWAY_SOURCE,'source_analysis':copy.deepcopy(source),
            'analysis_digest':preview_digest(source),'parent_analysis_sha256':sha256(source_path)}
        if 'actor_initial_gaps_m' in corrections:
            certificate['parameters']=copy.deepcopy(record['parameters'])
    else:
        if not certificate:raise ContractError('Intersection revision requires a certified projection')
        certificate=copy.deepcopy(certificate);certificate['media_duration_s']=probe['duration_s']
    # Build and validate before allocating a new Case; no model, budget or fact writes.
    analysis,status=build(certificate,corrections)
    for key in ('execution_actor_scope','observation_source'):
        if key in source['quality_report']:analysis['quality_report'][key]=copy.deepcopy(source['quality_report'][key])
    registry=read_json(parent/'state/actor_registry.json');validate_analysis(analysis,registry)
    from _lib.delivery.converter import strict_v3_preflight
    strict_v3_preflight(analysis,record['parameters']['road_source'])
    # A scene revision fixes the analysis facts, while target compilation is
    # still a derived product.  Bind receipt reuse to the active converter so
    # a corrected mapping cannot silently return an older target artifact.
    from _lib.delivery.converter import converter_fingerprint
    converter_script=(EXTRACTOR.parent/'json-coverter/skills/case-conversion/scripts/scenes'/analysis['scene_type']/'convert.py')
    converter_hash=converter_fingerprint(converter_script)
    analysis_hash=preview_digest(analysis)
    revision_hash=preview_digest({'analysis_sha256':analysis_hash,'converter_sha256':converter_hash})
    receipt=parent/'diagnostics/scene_revisions'/revision_hash/'receipt.json'
    if receipt.exists():
        case=Path(read_json(receipt)['case_path']);state=runner.progress(case)
        if state['status'] in {'done','done_degraded'}:
            runner.validate_completed(case,state);return state
    else:
        # Inherit the parent's effective completion permission, including an
        # audited adoption, without copying its Case/input-bound policy itself.
        derived_parameters=copy.deepcopy(record['parameters'])
        parent_policy=read_json(parent/'state/conversion_result.json').get('conversion_policy')
        if parent_policy:
            derived_parameters['allowed_degradations']=copy.deepcopy(parent_policy['allowed_degradations'])
        if (parent/'state/behavior.json').is_file():
            from _lib.contracts.core_lane_continuity import validate as validate_motion_obligations
            validate_motion_obligations(parent, read_json(parent/'state/behavior.json'))
        case,_=Cases(EXTRACTOR/'cases').bind(video,normalize_parameters(derived_parameters),force_new=True)
        # A user-authored revision already fixes its execution choice. This
        # delivery-only Case does not run the new model stage.
        execution_policy = case/'state/execution_policy.json'
        if execution_policy.is_file():
            frozen = read_json(execution_policy)
            frozen.pop('conversion_semantic_gap', None)
            frozen.pop('conversion_continuation', None)
            atomic_json(execution_policy, frozen)
        atomic_json(receipt,{'case_path':str(case),'parent_case_id':parent_id,'revision_sha256':revision_hash,
                            'analysis_revision_sha256':analysis_hash,'converter_sha256':converter_hash})
        for name in ('state/world_context.json','state/actor_registry.json','state/global_frames.json','state/behavior_frames.json','state/probe.json','manifest.json'):
            path=parent/name
            if path.exists():atomic_json(case/name,read_json(path))
        for path in (parent/'batches').glob('*.json'):atomic_json(case/'batches'/path.name,read_json(path))
        if (parent/'state/behavior.json').is_file():
            from _lib.behavior.input_binding import write_behavior
            write_behavior(case, read_json(parent/'state/behavior.json'), source_case=parent)
    if (case/'state/behavior.json').is_file():
        if sha256(case/'state/behavior.json') != sha256(parent/'state/behavior.json'):
            raise ContractError('Derived scene revision Behavior differs from completed parent')
        from _lib.contracts.core_lane_continuity import validate as validate_motion_obligations
        validate_motion_obligations(parent, read_json(case/'state/behavior.json'))
    atomic_json(case/'state/scene_corrections.json',corrections)
    atomic_json(case/'final/analysis.json',analysis)
    state=runner.progress(case);state.update(phase='converting',status='converting',error=None,updated_at=now())
    state.setdefault('diagnostics',{})['scene_revision']={'parent_case_id':parent_id,'parent_analysis_sha256':sha256(source_path),
        'revision_sha256':revision_hash,'analysis_revision_sha256':analysis_hash,
        'converter_sha256':converter_hash,'corrections':corrections,'new_model_sessions':0,
        'parent_model_recovery_budget':copy.deepcopy(original.get('diagnostics',{}).get('model_recovery_budget'))}
    runner.save_progress(case,state)
    try:
        runner.strict_convert(case)
        return runner.finish_v3(case,status)
    except Exception as error:
        state=runner.progress(case);state.update(status='failed',generation_status='not_generated',artifacts={},
            error={'code':getattr(error,'code','revision_conversion_failed'),'message':str(error)})
        runner.save_progress(case,state);return state
