"""Content-bound retries and strictly additive World evidence rebinding."""
import copy
from pathlib import Path
from _lib.behavior.compile import preview_digest, preview_observations
from _lib.runtime.case_core import read_json, sha256

SEPARATION_PATH = 'world.road.roads[0].road_geometry.opposing_separation'


def normalize_separation_response(response, request):
    """Narrow compatibility for the old scoped packet lacking value_schemas.

    Reformat explicit assertions only; retain the entire original response in
    the caller's audit. Never infer a separator or a frame from prose.
    """
    result=copy.deepcopy(response); repairs=[]
    if request.get('value_schemas') or request.get('issues') != ['world.opposing_separation_evidence_required']:
        return result,repairs
    if result.get('resolution') == 'resolved':
        del result['resolution']; repairs.append('Removed redundant root resolution=resolved; evidence gates still decide acceptance.')
    for op in result.get('operations',[]):
        value=op.get('value')
        if op.get('path')=='/world/road/roads/0/road_geometry/opposing_separation' and isinstance(value,dict):
            if (set(value) <= {'separated','side','separation_type','note','observed_time_range_s'}
                    and type(value.get('separated')) is bool and value.get('side') in ('left','right')):
                op['value']={'physically_separated':value['separated'],'side':value['side']}
                repairs.append('Mapped explicit separated boolean to physically_separated; descriptive metadata retained in original response audit.')
        if op.get('path')=='/field_evidence/'+SEPARATION_PATH and isinstance(value,dict):
            if (set(value)=={'frame_ids','observation'} and isinstance(value['frame_ids'],list)
                    and value['frame_ids']==op.get('evidence_frame_ids') and value['frame_ids']
                    and isinstance(value['observation'],str) and value['observation'].strip()):
                op['value']=[{'status':'observed','frame_id':fid,'reason':value['observation']} for fid in value['frame_ids']]
                repairs.append('Expanded explicit frame_ids/observation into individual observed evidence records without adding frames or observations.')
    return result,repairs


def separation_fingerprint(record, candidate):
    root=Path(__file__).resolve().parents[1]
    request=read_json(record['request_path'])
    sources=[root/'behavior/preview_recovery.py',root/'world/closure.py',root/'world/readiness.py',
             root/'contracts/v3.py',root/'stages/preview_separation.py',root/'behavior/compile.py']
    sources+=sorted((root.parent/'schemas').glob('*.json'))
    return preview_digest({'candidate':sha256(candidate),'request':sha256(Path(record['request_path'])),
        'inputs':{p:sha256(Path(p)) if Path(p).is_file() else None for p in request['input_hashes']},
        'sources':{str(p):sha256(p) for p in sources}})


def validation_fingerprint(case, candidate):
    root = Path(__file__).resolve().parents[1]
    request = Path(case) / 'state/preview_review.request.json'
    packet = read_json(request)
    inputs = {p: sha256(Path(p)) if Path(p).is_file() else None for p in packet['input_hashes']}
    sources = [root/'behavior'/name for name in ('repair.py', 'compile.py', 'preview_contract.py', 'preview_recovery.py', 'preview_repair.py')]
    sources += [root/'contracts/v3.py', root/'world/readiness.py', root/'stages/behavior.py']
    sources += sorted((root.parent/'schemas').glob('*.json'))
    return preview_digest({'request':sha256(request), 'candidate':sha256(candidate), 'inputs':inputs,
                           'validators':{str(p):sha256(p) for p in sources}})


def rebind_separation(old_world, new_world, behavior, certificate, frames, *, localization=False, verification=False, observation_contract=False):
    """Never mutate a frozen request/certificate; return a new auditable binding."""
    from _lib.world.readiness import ego_carriageway_execution_scope
    from _lib.contracts.v3 import validate_world, validate_behavior
    if certificate.get('status') != 'accepted' or certificate['world_sha256'] != preview_digest(old_world):
        raise ValueError('preview migration old World binding mismatch')
    if certificate['observation_sha256'] != preview_digest(preview_observations(behavior['actors'])):
        raise ValueError('preview migration observations changed')
    left, right = copy.deepcopy(old_world), copy.deepcopy(new_world)
    if right['evidence_epoch'] != left['evidence_epoch'] + 1:
        raise ValueError('preview migration epoch mismatch')
    evidence = []
    if localization and verification:
        raise ValueError('preview migration must use one repair scope')
    if verification:
        changed = False
        required_frames = {p['frame_id'] for a in behavior['actors'] for p in a.get('trajectory', [])}
        for collection in ('questions_to_verify', 'ambiguities'):
            if len(left[collection]) != len(right[collection]):
                raise ValueError('preview verification question set changed')
            for old, new in zip(left[collection], right[collection]):
                if old == new: continue
                paths = set(old.get('world_paths', []))
                if (not paths or not paths <= {'world.road.ego_direction_lanes', 'world.road.roads[0].lane_ids'}
                        or old.get('owner') == 'behavior'):
                    raise ValueError('preview verification may only adjudicate ego lane-count questions')
                if old.get('resolution') != 'unresolved' or new.get('resolution') != 'resolved':
                    raise ValueError('preview verification requires unresolved -> resolved')
                prior = old.get('evidence', [])
                if new.get('evidence', [])[:len(prior)] != prior:
                    raise ValueError('preview verification changed existing evidence')
                added = new.get('evidence', [])[len(prior):]
                observed = {e.get('frame_id') for e in added
                            if e.get('status') == 'observed' and str(e.get('reason', '')).strip()}
                if not required_frames or not required_frames <= observed:
                    raise ValueError('preview verification requires appended evidence for every trajectory frame')
                evidence.extend(added)
                new['resolution'] = old['resolution']
                if 'evidence' in old: new['evidence'] = copy.deepcopy(prior)
                else: new.pop('evidence', None)
                changed = True
        if not changed: raise ValueError('preview verification did not resolve a question')
    elif localization:
        changed = False
        for collection in ('questions_to_verify', 'ambiguities'):
            if len(left[collection]) != len(right[collection]):
                raise ValueError('preview migration question set changed')
            for old, new in zip(left[collection], right[collection]):
                if old != new:
                    old_location = old.get('localization')
                    new_location = new.get('localization')
                    preserved = True
                    if old_location:
                        for key, value in old_location.items():
                            if key == 'evidence_frame_ids':
                                old_refs = value if isinstance(value, list) else []
                                new_refs = new_location.get(key, []) if new_location else []
                                if (not isinstance(new_refs, list)
                                        or not set(old_refs) <= set(new_refs)):
                                    preserved = False
                            elif not new_location or new_location.get(key) != value:
                                preserved = False
                    if (not new_location or (old_location and
                            (new_location == old_location or not preserved))):
                        raise ValueError('preview migration must only add or enrich localization')
                    if new.get('evidence', [])[:len(old.get('evidence', []))] != old.get('evidence', []):
                        raise ValueError('preview migration changed existing evidence')
                    evidence.extend(new.get('evidence', [])[len(old.get('evidence', [])):])
                    if old_location is None: new.pop('localization')
                    else: new['localization'] = copy.deepcopy(old_location)
                    if 'evidence' in old: new['evidence'] = copy.deepcopy(old['evidence'])
                    else: new.pop('evidence', None)
                    changed = True
        if not changed: raise ValueError('preview migration requires new localization evidence')
    else:
        left_geom = left['world']['road']['roads'][0]['road_geometry']
        right_geom = right['world']['road']['roads'][0]['road_geometry']
        if 'opposing_separation' in left_geom or SEPARATION_PATH in left['field_evidence']:
            raise ValueError('preview migration must only add previously absent separation evidence')
        right_geom.pop('opposing_separation', None)
        evidence = right['field_evidence'].pop(SEPARATION_PATH, [])
    right['evidence_epoch'] = left['evidence_epoch']
    if left != right:
        raise ValueError('preview migration contains unrelated World changes')
    known = {f['frame_id']: f for f in frames}
    if not evidence or any(e.get('frame_id') not in known or
                          (e.get('time_s') is not None and e['time_s'] != known[e['frame_id']]['time_s'])
                          for e in evidence):
        raise ValueError('preview migration evidence frame mismatch')
    if localization:
        for collection in ('questions_to_verify', 'ambiguities'):
            for item in new_world[collection]:
                refs = item.get('localization', {}).get('evidence_frame_ids', [])
                if any(fid not in known for fid in refs):
                    raise ValueError('preview migration localization frame mismatch')
    for frame in certificate.get('review_frames', []):
        if frame['frame_id'] not in known or sha256(Path(frame['path'])) != frame['sha256']:
            raise ValueError('preview migration reviewed frame changed')
    validate_world(new_world, observation_contract=observation_contract)
    if not localization and not verification:
        separation = (new_world['world']['road']['roads'][0].get('road_geometry') or {}).get(
            'opposing_separation') or {}
        if (separation.get('physically_separated') is not True
                or not ego_carriageway_execution_scope(new_world, behavior)):
            raise ValueError('preview migration separation does not establish execution scope')
    updated = copy.deepcopy(behavior)
    updated['world_evidence_epoch'] = new_world['evidence_epoch']
    validate_behavior(updated, new_world['evidence_epoch'], execution=False)
    rebound = copy.deepcopy(certificate)
    rebound['world_sha256'] = preview_digest(new_world)
    rebound['migration'] = {'reason':('evidence-backed question verification only; observed World unchanged' if verification else
        'additive question localization evidence only' if localization else 'additive opposing separation evidence only'),
                            'source_certificate_sha256':preview_digest(certificate),
                            'source_world_sha256':preview_digest(old_world),
                            'target_world_sha256':preview_digest(new_world)}
    return updated, rebound
