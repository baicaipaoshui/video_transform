"""Hash-bound actor evidence patches; no changes to identity, lanes or motion."""
import copy
from pathlib import Path
from _lib.runtime.case_core import atomic_json, read_json, sha256


def repair_prompt(case, candidate, archived, error):
    details = getattr(error, 'diagnostics', {})
    if details.get('retry_kind') != 'content_repair' or details.get('rule_id') != 'preview.actor_evidence':
        return None
    case=Path(case); packet=read_json(case/'state/preview_review.request.json')
    if 'required_actor_evidence' not in packet:
        return None
    if not packet['required_actor_evidence'].get(details.get('actor_id')):
        return None
    raw=getattr(error, 'preview_materialized', None) or read_json(archived)
    if set(raw) != {'base_sha256','outcome','replacements','relations'}:
        return None
    from _lib.behavior.compile import preview_digest
    archived=case/'diagnostics/preview_repairs'/f'{preview_digest(raw)}.base.json'
    atomic_json(archived, raw)
    prefix=details['field_path'].rsplit('/',1)[0]
    index=int(prefix.rsplit('/',1)[1]); actor=raw['relations']['actors'][index]
    if actor['actor_id'] != details['actor_id']:
        raise ValueError('Preview repair actor binding changed')
    request={'base_path':str(archived),'base_sha256':sha256(archived),
             'output_path':str(candidate),'validation_error':details,
             'actor':actor, 'required_evidence_frame_ids':packet['required_actor_evidence'][actor['actor_id']],
             'review_frames':packet['review_frames'],
             'allowed_paths':[prefix+'/'+key for key in ('evidence_frame_ids','context_frame_ids','reason')],
             'input_hashes':{**packet['input_hashes'],str(case/'state/preview_review.request.json'):sha256(case/'state/preview_review.request.json')},
             'output_contract':{'base_sha256':'copy base_sha256','replacements':[{'path':'one allowed path','value':'replacement value'}]}}
    path=case/'state/preview_repairs'/f'{sha256(archived)}.request.json'
    if path.exists() and read_json(path)!=request:
        raise ValueError('Frozen preview repair request changed')
    atomic_json(path,request)
    prompt=(f'Skill: behavior-understanding. Read only {path} and its review_frames. '
            'Fix only the assigned actor evidence_frame_ids/context_frame_ids/reason. '
            'Disappearance context must not become trajectory evidence. Output the scoped replacement envelope, '
            f'not a full preview response, to {candidate} once; keep all other facts unchanged.')
    atomic_json(case/'state/preview_repair.active.json',{'request_path':str(path),'request_sha256':sha256(path),'prompt':prompt})
    return prompt


def materialize(case, response):
    active=Path(case)/'state/preview_repair.active.json'
    if not active.exists(): return response
    binding=read_json(active); request=Path(binding['request_path'])
    if sha256(request)!=binding['request_sha256']: raise ValueError('Preview repair request hash changed')
    packet=read_json(request)
    for path,digest in packet['input_hashes'].items():
        if sha256(path)!=digest: raise ValueError('Preview repair input hash changed')
    if sha256(packet['base_path'])!=packet['base_sha256']: raise ValueError('Preview repair base changed')
    if not isinstance(response,dict) or set(response)!={'base_sha256','replacements'} or response['base_sha256']!=packet['base_sha256']:
        raise ValueError('Preview repair requires the bound replacement envelope')
    ops=response['replacements']
    if not isinstance(ops,list) or not 1<=len(ops)<=3: raise ValueError('Preview repair requires 1..3 leaf replacements')
    result=copy.deepcopy(read_json(packet['base_path'])); seen=set()
    for op in ops:
        if not isinstance(op,dict) or set(op)!={'path','value'} or op['path'] not in packet['allowed_paths'] or op['path'] in seen:
            raise ValueError('Preview repair unassigned/duplicate path')
        seen.add(op['path']); parts=op['path'].split('/'); actor=result['relations']['actors'][int(parts[3])]
        if actor.get(parts[4])==op['value']: raise ValueError('Preview repair same-value replacement')
        actor[parts[4]]=copy.deepcopy(op['value'])
    atomic_json(Path(case)/'diagnostics/preview_repairs'/f'{sha256(packet["base_path"])}.materialized.json',result)
    return result
