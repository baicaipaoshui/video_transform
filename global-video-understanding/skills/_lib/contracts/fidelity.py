"""Versioned visual evidence contract. Schemas, never this module, own wire types.

The interface inventory is generated from the schemas. It is a release lock,
not a second definition of fields. No video-specific IDs or times live here.
"""
import copy
import hashlib
import json
import math
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from _lib.paths import SKILLS, SCENARIO_ROOT
from _lib.contracts.v3 import ContractError

REVISION = 'video_fidelity.v1'
SCHEMAS = SKILLS / 'schemas'
INVENTORY = SKILLS / 'references/video-fidelity-interface.generated.json'
CONTRACTS = ('video_fidelity.schema.json', 'world_context.schema.json',
             'batch_v3.schema.json', 'behavior.schema.json',
             'intersection_review.schema.json', 'analysis.schema.json', 'world_field_repair.schema.json',
             'longitudinal_motion.schema.json', 'limitation_binding.schema.json', 'execution_evidence.schema.json',
             'intersection_review_repair.schema.json')
CONTRACTS += ('scene_corrections.schema.json',)
CONTRACTS += ('actor_fields.schema.json', 'actor_registry.schema.json', 'vehicle_census.schema.json')
CONTRACTS += ('census_observation_refs.schema.json',)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                   separators=(',', ':')).encode()).hexdigest()


def enabled(parameters):
    if isinstance(parameters, (str, Path)):
        file = Path(parameters) / 'case.json'
        parameters = json.loads(file.read_text()).get('parameters', {}) if file.is_file() else {}
    return (parameters or {}).get('video_fidelity_policy') == REVISION


class ContractCompatibilityError(ValueError):
    code = 'contract_incompatible'

    def __init__(self, path, expected, actual):
        self.diagnostics = dict(path=path, expected=expected, actual=actual,
                                retry_kind='code_change', input_sha256=digest(actual))
        super().__init__(f'{path}: incompatible contract; expected {expected}, actual {actual}')


class FidelityEvidenceError(ContractError):
    """A candidate error with an explicit, bounded repair scope."""
    code = 'schema_validation_failed'

    def __init__(self, message, *paths):
        super().__init__(message, rule_id='video_fidelity.evidence')
        self.diagnostics = dict(repair_paths=list(paths), retry_kind='content_repair')


class ReleaseSnapshotMismatch(ContractCompatibilityError):
    code = 'release_snapshot_mismatch'


def frame_index(records):
    """Adapt the existing evidence list or an indexed execution view explicitly.

    These are local producer outputs, not model candidates. Invalid shapes must
    stop as a system contract error before a model repair can be reserved.
    """
    if not isinstance(records, (list, dict)) or not records:
        raise ContractCompatibilityError('evidence_frames', 'nonempty list or ID-indexed dictionary', type(records).__name__)
    index = {}
    for key, row in (records.items() if isinstance(records, dict) else ((None, r) for r in records)):
        if (not isinstance(row, dict) or not isinstance(row.get('frame_id'), str)
                or not row['frame_id'] or type(row.get('time_s')) not in (int, float)
                or not math.isfinite(row['time_s']) or row['time_s'] < 0):
            raise ContractCompatibilityError('evidence_frames/record', 'frame_id and finite nonnegative time_s', row)
        fid = row['frame_id']
        if fid in index or (key is not None and key != fid):
            raise ContractCompatibilityError('evidence_frames/'+fid, 'unique matching frame key', key)
        index[fid] = row
    return index


def validator(name):
    def document(path):
        doc = json.loads(path.read_text())
        if path.name == 'world_context.schema.json':
            # Old layout requests hash this base file. Compose the typed v6
            # addition in memory; never edit the frozen schema or skip hashes.
            # This grants syntax only, not scope authority in older contracts.
            reference = {'$ref': 'census_observation_refs.schema.json'}
            doc['$defs']['question']['properties']['census_issue_ids'] = reference
            doc['properties']['ambiguities']['items']['properties']['census_issue_ids'] = reference
        return doc
    resources = []
    for path in SCHEMAS.glob('*.schema.json'):
        doc = document(path)
        doc.setdefault('$schema', 'https://json-schema.org/draft/2020-12/schema')
        resources.append((path.name, Resource.from_contents(doc)))
    registry = Registry().with_resources(resources)
    return Draft202012Validator(document(SCHEMAS/name), registry=registry)


def inventory():
    result = {}
    for name in CONTRACTS:
        path = SCHEMAS/name
        if not path.exists():
            raise ContractCompatibilityError(str(path), 'schema file', 'missing')
        doc = json.loads(path.read_text())
        Draft202012Validator.check_schema(doc)
        # Keep schema nodes verbatim: no independent type/required/enum maintenance.
        result[name] = dict(sha256=digest(doc), schema=doc)
    consumers = set((SKILLS/'_lib').rglob('*.py'))
    consumers.update((SCENARIO_ROOT/'json-coverter/skills/case-conversion/scripts').rglob('*.py'))
    consumers.update(SKILLS.glob('*/references/*prompt.md'))
    consumers.update((SCENARIO_ROOT/'json-coverter/skills/case-conversion/references').glob('*prompt.md'))
    consumers.add(SKILLS/'behavior-understanding/references/intersection-review.md')
    consumers.add(SKILLS/'references/road-coordinate-convention.json')
    return dict(generated_from='schemas and consumer source fingerprints; do not edit', contracts=result,
                consumers={str(p.relative_to(SCENARIO_ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(consumers)})


def preflight():
    current = inventory()
    saved = json.loads(INVENTORY.read_text()) if INVENTORY.is_file() else None
    if not isinstance(saved,dict) or current.get('contracts') != saved.get('contracts'):
        raise ContractCompatibilityError('interface_inventory', digest(saved), digest(current))
    if current != saved:
        changed = sorted(p for p in set(current['consumers']) | set(saved.get('consumers',{}))
                         if current['consumers'].get(p) != saved.get('consumers',{}).get(p))
        error = ReleaseSnapshotMismatch('release_sources', digest(saved), digest(current))
        error.diagnostics['changed_paths'] = changed
        raise error
    from _lib.contracts.world_metadata import definition
    definition()
    for name in ('behavior.schema.json', 'intersection_review.schema.json'):
        actual = current['contracts'][name]['schema']['properties'].get('video_fidelity')
        expected = {'$ref': 'video_fidelity.schema.json'}
        if actual != expected:
            raise ContractCompatibilityError(name+'/video_fidelity', expected, actual)
    return digest(current)


def prompt(case):
    if not enabled(case):
        return ''
    doc = json.loads((SCHEMAS/'video_fidelity.schema.json').read_text())
    duration = json.loads((Path(case)/'state/probe.json').read_text())['probe']['duration_s']
    return ('\n本次启用视频保真合同，video_fidelity 必须输出。以下为权威 Schema 原文，'
            '类型、枚举和必填性仅以此为准：\n'+json.dumps(doc, ensure_ascii=False)+
            f'\nsource_duration_s 必须为媒体探测值 {duration}。'
            '先看帧，再写事实；不要沿用上游文字的车道或驶出结论。'
            '对 ego 和每辆可见同向汽车至少记录首末可见帧的 observation；'
            'ego 必须覆盖提供的首帧和末帧，关键进入/驶出边界前后也要记录。'
            'frame_id 只用提供的真实帧，时间由程序查表。'
            'lane_id 用已存在的物理车道；路口内无标线可为 null，'
            'junction_state 独立判断，不能从出口车道推导已驶出。'
            '远端横道仍在车头前方时不能声明 after。'
            'near-field 车道边界和车辆轮迹决定 corridor，图像中心不代表自车车道。'
            'ego_lane_ahead 只描述可见区域，遮挡用 unknown；clear 时 ahead_actor_ids 为空。'
            '同向左右邻车即使无交互也应列为 participant；行人保留观察不要求生成。'
            '身份问题和限制项要结构化关联已有 actor_ids；不要只写描述文字，也不要为缺少ID的路人虚构身份。'
            '不把停止线、近横道、路口入口、远横道和出口合并成同一个边界。'
            '未知距离/速度保留 unknown；不得为了仿真补造已驶出或匀速事实。')


def validate_evidence(facts, world, behavior, frames, duration):
    """Cross-field evidence checks, in addition to the authoritative schema."""
    frames = frame_index(frames)
    errors = list(validator('video_fidelity.schema.json').iter_errors(facts))
    if errors:
        error = errors[0]
        parts = list(error.absolute_path)
        path = '/video_fidelity' + ''.join('/'+str(p).replace('~','~0').replace('/','~1') for p in parts)
        raise FidelityEvidenceError(error.message, path)
    if abs(facts['source_duration_s']-duration) > 1e-6:
        raise FidelityEvidenceError('video_fidelity.source_duration_s differs from media probe', '/video_fidelity/source_duration_s')
    actors = {a['actor_id']: a for a in behavior['actors']}
    lanes = {l['lane_id'] for l in world['world']['road']['lanes']}
    seen = set()
    for i, obs in enumerate(facts['observations']):
        path = f'/video_fidelity/observations/{i}'
        aid, fid = obs['actor_id'], obs['frame_id']
        if aid not in actors or fid not in frames or (aid, fid) in seen:
            raise FidelityEvidenceError(f'video_fidelity duplicate or unknown actor/frame: {aid}/{fid}', path)
        seen.add((aid, fid))
        if obs['lane_id'] is not None and obs['lane_id'] not in lanes:
            raise FidelityEvidenceError('video_fidelity references unknown lane', path+'/lane_id')
        # This field describes vehicles ahead of ego, regardless of whose
        # observation row carries it. A lead vehicle may therefore name itself.
        if set(obs['ahead_actor_ids'])-set(actors) or 'ego' in obs['ahead_actor_ids']:
            raise FidelityEvidenceError('video_fidelity invalid ahead_actor_ids', path+'/ahead_actor_ids')
        if obs['ego_lane_ahead'] == 'clear' and obs['ahead_actor_ids']:
            raise FidelityEvidenceError('video_fidelity clear corridor cannot contain lead vehicles', path)
        point = next((p for p in actors[aid]['trajectory'] if p.get('frame_id') == fid), None)
        if point and point.get('lane_id') and obs['lane_id'] and point['lane_id'] != obs['lane_id']:
            raise FidelityEvidenceError(f'video_fidelity lane contradicts trajectory: {aid}/{fid}', path+'/lane_id')
        if aid == 'ego' and obs['corridor'] != 'ego':
            raise FidelityEvidenceError('video_fidelity ego must use ego corridor', path+'/corridor')
    ego = [o for o in facts['observations'] if o['actor_id'] == 'ego']
    if not ego:
        raise FidelityEvidenceError('video_fidelity ego evidence missing', '/video_fidelity/observations')
    ordered = sorted(frames, key=lambda f: frames[f]['time_s'])
    if ('ego', ordered[0]) not in seen or ('ego', ordered[-1]) not in seen:
        raise FidelityEvidenceError('video_fidelity ego must cover first and terminal evidence frames', '/video_fidelity/observations')
    for a in actors.values():
        if a.get('actor_type') == 'vehicle' and a.get('simulation_role') == 'participant':
            if not any(aid == a['actor_id'] for aid, _ in seen):
                raise FidelityEvidenceError('video_fidelity included vehicle lacks visual observations', '/video_fidelity/observations')
    owners={lid:r for r in world['world']['road']['roads'] for lid in r.get('lane_ids',[])}
    ego_by_frame={o['frame_id']:o for o in ego}
    for i, o in enumerate(facts['observations']):
        reference=ego_by_frame.get(o['frame_id'])
        if not reference or not reference['lane_id'] or not o['lane_id']:continue
        road=owners.get(reference['lane_id']);other=owners.get(o['lane_id'])
        if road is None or road is not other:continue
        delta=road['lane_ids'].index(o['lane_id'])-road['lane_ids'].index(reference['lane_id'])
        expected={0:'ego',1:'left_adjacent',-1:'right_adjacent'}.get(delta,'other')
        if o['corridor'] not in ('unknown',expected):
            raise FidelityEvidenceError(f"video_fidelity corridor contradicts lane boundaries: {o['actor_id']}/{o['frame_id']}", f'/video_fidelity/observations/{i}')
    return True


def compare_reviews(original, review):
    left = {(o['actor_id'], o['frame_id']): o for o in original['observations']}
    right = {(o['actor_id'], o['frame_id']): o for o in review['observations']}
    if set(left)-set(right):
        raise ValueError('video_fidelity independent review omitted required observations')
    for key in left:
        for field in ('lane_id', 'corridor', 'junction_state', 'motion_state', 'ego_lane_ahead'):
            a, b = left[key][field], right[key][field]
            if a not in (None, 'unknown') and b != a:
                raise ValueError(f'video_fidelity visual disagreement: {key}/{field}: {a} vs {b}')
    return True


def execution_copy(world, behavior, duration):
    """Only annotate a private copy. Never rewrite accepted evidence."""
    w, b = copy.deepcopy(world), copy.deepcopy(behavior)
    w['world'].setdefault('metadata', {})['duration_s'] = duration
    facts = b.get('video_fidelity')
    if facts:
        by_id = {a['actor_id']: a for a in b['actors']}
        for o in facts['observations']:
            a = by_id.get(o['actor_id'])
            if a and a.get('actor_type') == 'vehicle' and o['corridor'] in {'ego','left_adjacent','right_adjacent'}:
                a['simulation_role'] = 'participant'
    return w, b


def decorate(analysis, count):
    """Optional user scenery, derived from road extent and explicit parking areas."""
    if not count: return
    from _lib.behavior.scene_revision import add_parking
    road=analysis['road']['roads'][0]; length=road['road_geometry']['visible_length_m']
    forbidden=[item['position_ego_m']['x'] for j in analysis.get('junctions',[]) for item in j.get('crosswalks',[])
               if item.get('related_road_id')==road['road_id'] and item.get('position_ego_m')]
    positions=[]
    for side in ('left','right'):
        areas=[a for a in analysis['road'].get('roadside_areas',[]) if a.get('road_id')==road['road_id']
               and a.get('area_type')=='curbside_parking' and a.get('side')==side]
        if not areas:continue
        values=[]
        # Walk backwards from the junction clearance, with fixed physical spacing,
        # rather than copying this video's old x=108/120/132 overrides.
        x=length-15
        while x>=5 and len(values)<count:
            if all(abs(x-stop)>=8 for stop in forbidden):values.append(x)
            x-=9
        positions.extend(dict(side=side,x_m=x,color='silver',vehicle_class='sedan') for x in values)
    add_parking(analysis,positions)
    analysis['quality_report']['video_fidelity']['scenery']={'requested_per_side':count,'placed':positions,
        'source':'user_requested_scenery','observed':False}
