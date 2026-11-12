"""Bounded World field repair on the existing task/budget machinery.

Public World documents never contain repair metadata. The worker sees a small
packet; only Python reads the immutable full baseline and merges operations.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from jsonschema import Draft202012Validator, ValidationError

from _lib.runtime.case_core import atomic_json, read_json, sha256
from _lib.world import admission
from _lib.world.closure import ClosureStopped, _tokens, _read_pointer, _apply
from _lib.paths import SKILLS

CONTRACT = read_json(SKILLS/'schemas/world_field_repair.schema.json')['properties']['contract_version']['const']
MAX_BYTES = 32 * 1024
MAX_FRAMES = 4
LIVE = {'submitted', 'pending', 'running'}
IDENTITY_FIELDS = {'actor_id', 'actor_type', 'identity_description', 'first_seen_frame_id',
                   'road_id', 'lane_id', 'question_id', 'ambiguity_id'}
METADATA_FIELDS = {'impact_domains', 'owner', 'subject_field', 'resolution',
                   'world_paths', 'related_world_paths'}


def response_schema():
    return read_json(SKILLS/'schemas/world_field_repair.schema.json')


def validator_version():
    from _lib.contracts import world_metadata, lane_locations, v3, frame_evidence
    from _lib.world import readiness, topology, single_world, semantic, actor_facts, lane_numbering, compilation_repair, point_review
    return digest([sha256(Path(admission.__file__)), sha256(Path(__file__)),
                   sha256(Path(world_metadata.__file__)), sha256(Path(readiness.__file__)),
                   sha256(Path(topology.__file__)), sha256(Path(lane_locations.__file__)),
                   sha256(Path(v3.__file__)), sha256(Path(actor_facts.__file__)),
                   sha256(Path(single_world.__file__)), sha256(Path(semantic.__file__)),
                   sha256(Path(lane_numbering.__file__)),
                   sha256(Path(compilation_repair.__file__)), sha256(Path(point_review.__file__)),
                   sha256(Path(frame_evidence.__file__)),
                   admission.schema(), read_json(SKILLS/'schemas/actor_fields.schema.json'), response_schema()])


def canonical_patch(patch, *, schema=None):
    """One explicit legacy wrapper adapter, not arbitrary field stripping."""
    value = copy.deepcopy(patch)
    notes = None
    if isinstance(value, dict) and 'notes' in value:
        notes = value.pop('notes')
        Draft202012Validator({'type':'object', 'properties':{
            'notes':response_schema()['$defs']['legacy_notes']}}).validate({'notes':notes})
    Draft202012Validator(response_schema() if schema is None else schema).validate(value)
    return value, notes


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def active_path(case):
    return Path(case) / 'state/world_field_repair.active.json'


def snapshot(case, data):
    path = Path(case) / 'diagnostics/world_field_repair' / (digest(data) + '.json')
    if not path.is_file():
        atomic_json(path, data)
    return path


def _ledger(case):
    path = Path(case) / 'diagnostics/world_repair_outcomes.json'
    return path, read_json(path) if path.is_file() else {}


def stop(case, fingerprint, code, reason):
    path, records = _ledger(case)
    record = {'code': code, 'reason': reason, 'automatic_retry': False}
    records[fingerprint] = record
    atomic_json(path, records)
    raise ClosureStopped(code, reason, record)


def replay_stop(case, fingerprint):
    _, records = _ledger(case)
    if fingerprint in records:
        record = records[fingerprint]
        raise ClosureStopped(record['code'], record['reason'], record)


def closure_worker_packet(case, request):
    """Expose only the bounded worker view of the existing closure contract."""
    frozen = read_json(request)
    context = copy.deepcopy(frozen['focused_context'])
    context.pop('frame_index_path', None)
    packet = {key: copy.deepcopy(frozen[key]) for key in (
        'contract_version', 'case_id', 'base_sha256', 'request_sha256', 'expected_epoch',
        'issues', 'allowed_paths', 'output_contract', 'stop_contract', 'coordinate_convention', 'output_path')}
    packet['focused_context'] = context
    for key in ('value_schemas','resolution_rule','resolution_values'):
        if key in frozen: packet[key] = frozen[key]
    packet['access_enforcement'] = 'prompt_only; current QwenPaw task API exposes no per-task tool ACL'
    frames = context.get('evidence_frames', [])

    def meaningful(value):
        if isinstance(value, dict):
            return {k: meaningful(v) for k,v in value.items()
                    if k not in {'evidence_epoch','reason','description','review_path','path'}}
        if isinstance(value, list):
            rows = [meaningful(v) for v in value]
            return sorted(rows,key=digest) if rows and all(isinstance(v,dict) and 'status' in v for v in rows) else rows
        return value
    fingerprint = digest({'contract': frozen['contract_version'], 'issues': frozen['issues'],
        'values': meaningful(context.get('current_values',{})),
        'frames': sorted((f['frame_id'],f.get('review_sha256',f.get('sha256'))) for f in frames),
        'validator': [validator_version(), sha256(Path(__file__).with_name('closure.py'))]})
    return packet, fingerprint


def closure_prompt(case, request):
    packet, fingerprint = closure_worker_packet(case, request)
    progress_path = Path(case)/'state/progress.json'
    progress = read_json(progress_path) if progress_path.is_file() else {}
    active = any(s.get('stage') == 'world_closure' and s.get('status') in LIVE
                 for s in progress.get('sessions',[]))
    if not active: replay_stop(case, fingerprint)
    if not packet['allowed_paths']:
        return stop(case,fingerprint,'world_patch_contract_blocked','No executable field repair surface')
    baseline = read_json(read_json(request)['base_path'])
    for path in packet['allowed_paths']:
        tokens = _tokens(path)
        parent = _read_pointer(baseline, admission.pointer(tokens[:-1])) if len(tokens)>1 else baseline
        if (not isinstance(parent,(dict,list)) or parent == {'$missing':True} or
                (isinstance(parent,list) and (not tokens[-1].isdecimal() or int(tokens[-1]) >= len(parent)))):
            return stop(case,fingerprint,'world_patch_contract_blocked',
                        'Existing closure executor cannot assign path: '+path)
    if len(packet['focused_context'].get('evidence_frames',[])) > MAX_FRAMES or len(
            json.dumps(packet,ensure_ascii=False).encode()) > MAX_BYTES:
        return stop(case,fingerprint,'world_patch_contract_blocked',
                    'Dependent closure exceeds 32 KiB / four-frame bound; no unbounded fallback')
    path = Path(case)/'diagnostics/world_field_repair'/(packet['request_sha256']+'.closure-worker.json')
    atomic_json(path,packet)
    return (f'Skill: world-understanding。World 定向补丁。只读 {path} 和包内 evidence_frames 图片；'
            f'只写 {packet["output_path"]}。只输出 output_contract 小补丁，禁止输出整份 World。'
            '只修改 assigned paths，不读取完整 World、Batch、Schema、历史 session 或额外帧。'
            '既有未知不反复裁决；缺证据返回 needs_evidence，权限不足返回 blocked_by_patch_contract。')


def record_closure_stop(case, request, error):
    _, fingerprint = closure_worker_packet(case, request)
    return stop(case,fingerprint,getattr(error,'code','world_patch_contract_blocked'),str(error))


def prompt(case, stage='world'):
    path = active_path(case)
    if not path.is_file():
        return None
    record = read_json(path)
    if record.get('stage', 'world') != stage:
        return None
    if record.get('baseline_kind') == 'initial-world-answer.v1':
        replay_stop(case, record['fingerprint'])
    return (f'Skill: world-understanding。World 局部修复。只读任务包 {record["packet_path"]} '
            f'及 evidence_frames 中指定的图片，只写 {record["output_path"]}。'
            '禁止读取完整 World、完整 Schema、历史错误、历史 session 或其他视频帧。'
            '以任务包 output_schema 为字段合同（旧包使用 output_contract 示例）；不得输出整份 World。'
            '成功或停止均须复制 contract_version、base_sha256、request_sha256；停止时 operations=[] 并提供 reason。'
            '只处理 assigned_issues；无新证据不重新评价其他 confirmed/partial/unknown。'
            '证据不足使用 needs_evidence；权限不够使用 blocked_by_patch_contract。写完后立即停止。')


def _schema_at(document, tokens):
    """Resolve only the reachable field schema; do not ship the full schema."""
    def resolve(node, seen=()):
        if '$ref' not in node:
            return node
        ref = node['$ref']
        if ref in seen:
            raise ValueError('unsupported recursive repair schema')
        if ref.startswith('actor_fields.schema.json#/'):
            target = read_json(SKILLS/'schemas/actor_fields.schema.json')
            fragment = ref.split('#', 1)[1]
        elif ref.startswith('#/'):
            target, fragment = document, ref[1:]
        else:
            raise ValueError('unsupported repair schema dependency')
        for token in _tokens(fragment):
            target = target[token]
        return resolve(target, (*seen, ref))
    node = document
    for token in tokens:
        node = resolve(node)
        node = (node.get('items', {}) if token.isdecimal() else
                node.get('properties', {}).get(token, node.get('additionalProperties', {})))
        if not isinstance(node, dict): node = {}
    def expand(node):
        if isinstance(node, list):
            return [expand(item) for item in node]
        if not isinstance(node, dict):
            return node
        return {key: expand(value) for key, value in resolve(node).items()}
    return expand(node)


def _world_path(tokens):
    return ''.join('[' + token + ']' if token.isdecimal() else ('.' if i else '') + token
                   for i, token in enumerate(tokens))


def _frame_ids(value):
    found = set()
    if isinstance(value, dict):
        if isinstance(value.get('frame_id'), str):
            found.add(value['frame_id'])
        for child in value.values():
            found.update(_frame_ids(child))
    elif isinstance(value, list):
        for child in value:
            found.update(_frame_ids(child))
    return found


def _frames(case, evidence):
    ids = _frame_ids(evidence)
    rows = []
    global_path = Path(case) / 'state/global_frames.json'
    if global_path.is_file():
        rows.extend(read_json(global_path).get('frames', []))
    manifest = Path(case) / 'manifest.json'
    if manifest.is_file():
        rows.extend(f for b in read_json(manifest).get('batches', []) for f in b.get('frames', []))
    selected = {}
    for row in rows:
        if row.get('frame_id') in ids:
            # Model-readable evidence: use the packed image bound to the
            # observation request; source_path is provenance only and may
            # exceed the model-side image viewer size cap.
            source = Path(row['path'])
            if not source.is_file():
                continue
            selected[row['frame_id']] = {'frame_id': row['frame_id'], 'path': str(source.resolve()),
                                         'sha256': sha256(source), 'time_s': row.get('time_s')}
    return [selected[key] for key in sorted(selected)]


def _directionality_context(data):
    """Read-only dependencies for the one-field, unknown-opposing-count repair."""
    road = data['world']['road']
    context = {'one_way': road.get('one_way'),
               'opposite_direction_lanes': road.get('opposite_direction_lanes'),
               'opposing_separation': [
                   {'road_id': r.get('road_id'),
                    'value': r.get('road_geometry', {}).get('opposing_separation')}
                   for r in road.get('roads', [])], 'verification_items': []}
    paths = {'world.road.one_way', 'world.road.opposite_direction_lanes'}
    paths.update(f'world.road.roads[{i}].road_geometry.opposing_separation'
                 for i, _ in enumerate(road.get('roads', [])))
    for collection in ('questions_to_verify', 'ambiguities'):
        context['verification_items'].extend(
            {'collection': collection, 'item': copy.deepcopy(item)}
            for item in data.get(collection, [])
            if paths.intersection(item.get('world_paths', []) + item.get('related_world_paths', [])))
    context['field_evidence'] = {p: copy.deepcopy(data.get('field_evidence', {}).get(p, []))
                                 for p in sorted(paths)}
    return context


def _directionality_binding(value):
    """Wording/order alone must not reopen an unchanged stopped repair."""
    if isinstance(value, dict):
        return {k: _directionality_binding(v) for k, v in value.items()
                if k not in {'reason', 'question', 'description', 'evidence_epoch'}}
    if isinstance(value, list):
        return sorted((_directionality_binding(v) for v in value), key=digest)
    return value


def prepare(case, candidate, archived, error, stage='world'):
    """Called only after the previous durable task has been reconciled."""
    case = Path(case)
    progress = read_json(case / 'state/progress.json')
    if any(s.get('stage') == stage and s.get('status') in LIVE for s in progress.get('sessions', [])):
        raise RuntimeError('Reconcile active World task before preparing a repair')
    from . import compilation_repair
    if stage == 'world' and isinstance(error, compilation_repair.CompilationError):
        if error.code != compilation_repair.CODE:
            raise error
        atomic_json(case/'diagnostics/world_compilation_issues.json', error.diagnostics)
        return compilation_repair.prepare(case, candidate, archived, error)
    if compilation_repair.active(case) and isinstance(error, FileNotFoundError):
        return prompt(case, stage)
    details = getattr(error, 'diagnostics', {})
    base_path = details.get('candidate_data_path')
    if base_path is None:
        if archived is None:
            if prompt(case, stage):
                # Recompute from the immutable baseline plus current evidence.
                # Changed evidence may justify a new request; unchanged retries
                # encounter the same persisted fingerprint below.
                archived = read_json(active_path(case))['base_path']
            else:
                diagnostic = case / 'diagnostics/world_admission.json'
                prior = read_json(diagnostic) if diagnostic.is_file() else {}
                if prior.get('stage') == stage and prior.get('issues'):
                    details = prior
                    archived = prior['candidate_data_path']
                else:
                    last = next((s for s in reversed(progress.get('sessions', [])) if s.get('stage') == stage), {})
                    archived = last.get('invalid_candidate_path')
                    if archived is None:
                        return None
        try:
            raw = read_json(archived)
        except json.JSONDecodeError:
            return stop(case, sha256(archived), 'world_patch_contract_blocked',
                        'Candidate is not parseable JSON; retain raw output, no whole-World regeneration')
        data, _ = admission.normalize(raw)
        base_path = snapshot(case, data)
    else:
        data = read_json(base_path)
    found = details.get('issues') or admission.issues(data, admission.duration_for(case), case=case)
    atomic_json(case / 'diagnostics/world_admission.json', {'base_sha256': sha256(base_path), 'issues': found,
                'stage':stage, 'candidate_data_path':str(base_path)})
    # Changes to prose, evidence ordering or epoch must not bypass terminal deduplication.
    version = validator_version()
    fields = {i['path']: _read_pointer(data, i['path']) for i in found}
    # Do not reinterpret committed World/closure facts, or broaden generic
    # fact-conflict permissions. This branch owns only initial directionality.
    directionality = (stage == 'world' and not (case/'state/world_context.json').exists()
                      and len(found) == 1
                      and found[0]['rule_id'] == 'world.directionality_conflict'
                      and found[0]['kind'] == 'fact_conflict'
                      and found[0]['path'] == '/world/road/one_way')
    direction_context = _directionality_context(data) if directionality else None
    direction_evidence = []
    if directionality:
        direction_evidence = [e for rows in direction_context['field_evidence'].values() for e in rows]
        direction_evidence.extend(e for row in direction_context['verification_items']
                                  for e in row['item'].get('evidence', []))
    related_evidence = []
    for issue in found:
        tokens = _tokens(issue['path']) if issue['path'] else []
        parent = _read_pointer(data, admission.pointer(tokens[:-1])) if tokens else {}
        if isinstance(parent, dict):
            related_evidence.extend(parent.get('evidence', []))
        related_evidence.extend(data.get('field_evidence', {}).get(_world_path(tokens), []))
    if directionality:
        related_evidence.extend(direction_evidence)
        fields['directionality_context'] = _directionality_binding(direction_context)
    # Evidence set order and reason wording do not establish new observations.
    fingerprint = digest({'issues': [(i['rule_id'], i['path']) for i in found],
                          'values': fields, 'evidence': _frames(case, related_evidence), 'version': version})
    replay_stop(case, fingerprint)
    if directionality:
        bound = [row['item'] for row in direction_context['verification_items']
                 if row['item'].get('resolution') == 'unresolved'
                 and row['item'].get('world_paths') == ['world.road.opposite_direction_lanes']]
        evidence = direction_context['field_evidence']
        if (direction_context['one_way'] is not True
                or direction_context['opposite_direction_lanes'] is not None or not bound
                or not any(e.get('status') == 'unknown' for e in evidence['world.road.opposite_direction_lanes'])):
            return stop(case, fingerprint, 'world_patch_contract_blocked',
                        'Directionality repair requires a preserved, bound unknown opposing count')
        required_frames = _frame_ids(direction_evidence)
        if (not any(e.get('status') == 'observed' and e.get('frame_id')
                    for e in evidence['world.road.one_way'])
                or not required_frames
                or required_frames != {f['frame_id'] for f in _frames(case, related_evidence)}):
            return stop(case, fingerprint, 'world_evidence_insufficient',
                        'Directionality repair requires all bound evidence images and observed direction evidence')
    document = admission.schema()
    groups = {}
    for issue in found:
        path = issue['path']
        tokens = _tokens(path) if path else []
        if tokens and tokens[-1].isdecimal():
            parent = _read_pointer(data, admission.pointer(tokens[:-1]))
            if isinstance(parent,list) and all(not isinstance(v,(dict,list)) for v in parent):
                # A metadata scalar array is one field, not an entity collection.
                tokens = tokens[:-1]
                path = admission.pointer(tokens)
        if not directionality and (issue['kind'] != 'representation_error' or not tokens or
                issue['rule_id'] not in {'schema.required', 'schema.type', 'schema.enum', 'schema.minItems'} or
                tokens[-1] in IDENTITY_FIELDS or len(tokens) < 2):
            return stop(case, fingerprint, 'world_patch_contract_blocked',
                        'Repair requires a fact/structure change outside field permissions: ' + issue['message'])
        field_schema = _schema_at(document, tokens)
        if not field_schema:
            return stop(case, fingerprint, 'world_patch_contract_blocked', 'No bounded schema for ' + path)
        parent_path = admission.pointer(tokens[:-1])
        parent = _read_pointer(data, parent_path)
        if not isinstance(parent, dict) or parent == {'$missing': True}:
            return stop(case, fingerprint, 'world_patch_contract_blocked', 'Missing field parent: ' + parent_path)
        # Object/collection replacement could delete identities or unrelated facts.
        existing = _read_pointer(data, path)
        if field_schema.get('type') == 'object' or ('items' in field_schema and
                field_schema['items'].get('type') == 'object'):
            return stop(case, fingerprint, 'world_patch_contract_blocked', 'Structural replacement is not a field repair: ' + path)
        group = groups.setdefault(parent_path, {'issues': [], 'values': {}, 'schemas': {}, 'context': {}, 'evidence': []})
        group['issues'].append(issue)
        group['values'][path] = existing
        group['schemas'][path] = field_schema
        group['context'][parent_path] = {k: v for k, v in parent.items()
                                         if not isinstance(v, (dict, list)) and k not in {'evidence_epoch'}}
        if directionality:
            group['context']['directionality'] = direction_context
            group['context']['directionality_rule'] = (
                'one_way describes the observed road including its opposing carriageway, not only the ego-side carriageway. '
                'Inspect the supplied images to determine whether opposing traffic exists beyond the median. '
                'Only /world/road/one_way may change. Preserve the unknown opposing lane count, its unresolved '
                'items, separation and all other facts. Do not infer opposing traffic solely from a null count. '
                'If the images do not support a decision, return needs_evidence; if other fields must change, '
                'return blocked_by_patch_contract. Cite the supplied evidence in the existing patch format.')
            group['evidence'].extend(direction_evidence)
        elif tokens[-1] not in METADATA_FIELDS:
            dotted = _world_path(tokens)
            group['evidence'].extend(data.get('field_evidence', {}).get(dotted, []))
            group['evidence'].extend(parent.get('evidence', []))
            if not group['evidence']:
                return stop(case, fingerprint, 'world_evidence_insufficient', 'No evidence for factual field ' + path)
    # Same-parent errors are dependent. Pack independent groups greedily, without truncation.
    packet = {'contract_version': CONTRACT, 'base_sha256': sha256(base_path), 'assigned_issues': [],
              'allowed_paths': [], 'current_values': {}, 'value_schemas': {}, 'context': {},
              'evidence': [], 'evidence_frames': [], 'output_path': str(candidate),
              'output_contract': {'contract_version': CONTRACT, 'base_sha256': 'copy from packet',
                                  'request_sha256': 'copy from packet', 'operations': [
                                      {'op': '|'.join(response_schema()['$defs']['operation']['properties']['op']['enum']), 'path': 'one allowed path', 'value': 'field value',
                                       'evidence_frame_ids': '[] for metadata; supplied frame IDs for facts'}]},
              'stop_contract': {'status': '|'.join(s for s in response_schema()['properties']['status']['enum'] if s != 'patch'), 'operations': [], 'reason': 'specific reason'},
              'access_enforcement': 'prompt_only; current QwenPaw task API exposes no per-task tool ACL'}
    packet['output_schema'] = response_schema()
    for _, group in sorted(groups.items()):
        trial = copy.deepcopy(packet)
        trial['assigned_issues'].extend(group['issues'])
        trial['allowed_paths'].extend(group['values'])
        for key, source in (('current_values', 'values'), ('value_schemas', 'schemas'), ('context', 'context')):
            trial[key].update(group[source])
        trial['evidence'].extend(group['evidence'])
        trial['evidence_frames'] = _frames(case, trial['evidence'])
        if trial['evidence'] and not trial['evidence_frames']:
            return stop(case, fingerprint, 'world_evidence_insufficient', 'Referenced evidence images unavailable')
        if len(trial['evidence_frames']) > MAX_FRAMES or len(json.dumps(trial, ensure_ascii=False).encode()) > MAX_BYTES - 256:
            if not packet['assigned_issues']:
                return stop(case, fingerprint, 'world_patch_contract_blocked', 'Dependent repair exceeds 32 KiB / four-frame bound')
            break
        packet = trial
    packet['request_sha256'] = digest(packet)
    path = case / 'diagnostics/world_field_repair' / (packet['request_sha256'] + '.request.json')
    atomic_json(path, packet)
    atomic_json(active_path(case), {'packet_path': str(path), 'base_path': str(base_path),
                'base_sha256': sha256(base_path), 'request_sha256': packet['request_sha256'],
                'fingerprint': fingerprint, 'output_path': str(candidate), 'issues_before': found, 'stage': stage})
    return prompt(case, stage)


def materialize(case, patch):
    from . import compilation_repair
    if compilation_repair.active(case):
        return compilation_repair.materialize(case, patch)
    record = read_json(active_path(case))
    packet = read_json(record['packet_path'])
    fingerprint = record['fingerprint']
    if sha256(record['base_path']) != record['base_sha256']:
        return stop(case, fingerprint, 'world_patch_contract_blocked', 'Immutable repair baseline changed')
    bound = dict(packet)
    bound.pop('request_sha256')
    if digest(bound) != record['request_sha256']:
        return stop(case, fingerprint, 'world_patch_contract_blocked', 'Repair packet binding changed')
    original_digest = digest(patch)
    try:
        patch, notes = canonical_patch(patch)
    except ValueError as error:
        return stop(case, fingerprint, 'world_patch_contract_blocked', str(error))
    except ValidationError as error:
        path = admission.pointer(error.absolute_path) or '/'
        return stop(case, fingerprint, 'world_patch_contract_blocked', path+': '+error.message)
    if notes is not None:
        atomic_json(Path(case)/'diagnostics/world_patch_adapters'/f'{original_digest}.json',
                    dict(adapter_version='world-patch-notes.v1', input_sha256=original_digest,
                         canonical_sha256=digest(patch), notes=notes))
    for key in ('contract_version', 'base_sha256', 'request_sha256'):
        if patch[key] != packet[key]:
            return stop(case, fingerprint, 'world_patch_contract_blocked', 'Patch binding mismatch: ' + key)
    status = patch.get('status', 'patch')
    if status in ('needs_evidence', 'blocked_by_patch_contract') and patch['operations'] == [] and patch.get('reason'):
        return stop(case, fingerprint, 'world_evidence_insufficient' if status == 'needs_evidence' else
                    'world_patch_contract_blocked', str(patch['reason']))
    operations = patch['operations']
    if (status != 'patch' or not isinstance(operations, list) or not 1 <= len(operations) <= 32 or
            len(json.dumps(patch,ensure_ascii=False).encode()) > 16 * 1024):
        return stop(case, fingerprint, 'world_closure_no_progress', 'Repair contains no bounded field operations')
    data = read_json(record['base_path'])
    seen = set()
    frames = {f['frame_id']: f for f in packet['evidence_frames']}
    for frame in frames.values():
        if not Path(frame['path']).is_file() or sha256(frame['path']) != frame['sha256']:
            return stop(case, fingerprint, 'world_evidence_insufficient', 'Repair evidence changed')
    for op in operations:
        path = op.get('path') if isinstance(op, dict) else None
        if (not isinstance(path, str) or path not in packet['allowed_paths'] or path in seen or
                set(op) != {'op', 'path', 'value', 'evidence_frame_ids'} or
                op['op'] not in {'add', 'replace'} or not isinstance(op['evidence_frame_ids'], list) or
                any(f not in frames for f in op['evidence_frame_ids'])):
            return stop(case, fingerprint, 'world_patch_contract_blocked', 'Unassigned or invalid field operation')
        if _tokens(path)[-1] not in METADATA_FIELDS and not op['evidence_frame_ids']:
            return stop(case, fingerprint, 'world_evidence_insufficient', 'Factual field repair requires evidence')
        if _tokens(path)[-1] == 'impact_domains':
            from _lib.contracts.world_metadata import definition
            aliases = definition()['x-legacy-value-aliases']
            previous = _read_pointer(data, path)
            required_domains = {aliases[v] for v in previous if isinstance(v,str) and v in aliases} if isinstance(previous,list) else set()
            if not isinstance(op['value'], list) or not required_domains <= set(v for v in op['value'] if isinstance(v,str)):
                return stop(case, fingerprint, 'world_patch_contract_blocked', path+': patch removes a schema-defined execution impact')
        # Existing executor validates pointer semantics. A private marker is used
        # only to allow evidence-free metadata operations; never stored in World.
        try:
            kind = 'add' if _read_pointer(data,path) == {'$missing':True} else 'replace'
            _apply(data, dict(op, op=kind, evidence_frame_ids=op['evidence_frame_ids'] or ['metadata-only']))
        except ValueError as error:
            return stop(case, fingerprint, 'world_patch_contract_blocked', str(error))
        seen.add(path)
    remaining = admission.blocking_issues(admission.issues(data, admission.duration_for(case), case=case))
    key = lambda issue: (issue['rule_id'], issue['path'])
    before, after = {key(i) for i in record['issues_before']}, {key(i) for i in remaining}
    targets = {key(i) for i in packet['assigned_issues']}
    if after - before or not targets - after:
        return stop(case, fingerprint, 'world_closure_no_progress', 'Patch did not reduce target errors without new errors')
    return data
