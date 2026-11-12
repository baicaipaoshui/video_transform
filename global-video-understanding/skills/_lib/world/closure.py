"""Hash-bound, field-level World closure patches.

The World model owns evidence-backed adjudication.  This module owns the
mechanical merge, immutable-field protection and request/input binding so a
retry never has to regenerate the complete World document.
"""
import copy
import hashlib
import json
import re
from pathlib import Path

from _lib.runtime.case_core import atomic_json, read_json, sha256
from _lib.world.topology import coordinate_convention


CONTRACT = "world-closure-patch-v1"
IMMUTABLE_ACTOR_FIELDS = {"actor_id", "actor_type", "identity_description", "first_seen_frame_id"}
ACTOR_MUTABLE_FIELDS = {"hypothesized_lane", "lane_id", "roadside_area_id", "is_static", "evidence"}
ROAD_COLLECTIONS = {"roads", "lanes", "lane_markings", "roadside_areas"}
ROAD_DOMAINS = {"road_topology", "lane_count", "actor_lane"}


class ClosureStopped(ValueError):
    """A bound response that cannot benefit from replaying the same model task."""
    def __init__(self, code, message, diagnostics=None):
        super().__init__(message)
        self.code = code
        self.diagnostics = diagnostics or {}


def normalize_patch_evidence(world):
    """Missing confidence is unspecified, never high confidence. Keep an audit."""
    result = copy.deepcopy(world)
    repairs = []
    def visit(value, path):
        if isinstance(value, dict):
            if value.get('status') == 'estimated' and 'confidence' not in value:
                value['confidence'] = 0
                repairs.append({'path': path + '/confidence', 'value': 0,
                                'reason': 'unspecified confidence; conservative lower bound'})
            for key, child in value.items():
                visit(child, path + '/' + _pointer_escape(key))
        elif isinstance(value, list):
            for index, child in enumerate(value): visit(child, path + '/' + str(index))
    # Only evidence records, not measurements or visual facts.
    visit(result.get('field_evidence', {}), '/field_evidence')
    return result, repairs


def _pointer_escape(value):
    return str(value).replace("~", "~0").replace("/", "~1")


def _issue_scope(world, issues):
    joined = "\n".join(map(str, issues))
    identifiers = set(re.findall(r"\b(?:question|ambiguity|amb)_[0-9]+\b", joined))
    # A readiness issue may name an ambiguity while its answer is recorded
    # against the linked question (or vice versa). Include both evidence views.
    changed = True
    while changed:
        before = set(identifiers)
        for item in world.get('ambiguities', []):
            related = set(item.get('related_question_ids', []))
            if item.get('ambiguity_id') in identifiers or related & identifiers:
                identifiers.update(related)
                identifiers.add(item['ambiguity_id'])
        changed = identifiers != before
    road_issue = any(token in joined for token in (
        "road", "lane", "marking", "intersection", "topology", "parking"))
    for collection, id_key in (("questions_to_verify", "question_id"), ("ambiguities", "ambiguity_id")):
        for item in world.get(collection, []):
            if item.get(id_key) in identifiers and ROAD_DOMAINS.intersection(item.get("impact_domains", [])):
                road_issue = True
    return joined, identifiers, road_issue


def allowed_paths(world, issues):
    """Derive a bounded repair surface from the deterministic readiness errors."""
    from . import lane_layout
    if lane_layout.applicable(issues):
        return lane_layout.paths(world)
    if issues == ['world.opposing_separation_evidence_required']:
        return ['/world/road/roads/0/road_geometry/opposing_separation',
                '/field_evidence/world.road.roads[0].road_geometry.opposing_separation']
    if issues and all(str(i).startswith('world.execution_verification_required ') for i in issues):
        ids = {str(i).split()[-1] for i in issues}
        return sorted(f'/{collection}/{index}/{field}'
            for collection, key in (('questions_to_verify','question_id'), ('ambiguities','ambiguity_id'))
            for index, item in enumerate(world.get(collection, [])) if item.get(key) in ids
            for field in ('resolution', 'evidence'))
    if issues and all(str(i).startswith('world.execution_localization_required ') for i in issues):
        ids = {str(i).split()[-1] for i in issues}
        return sorted(f'/{collection}/{index}/{field}'
            for collection, key in (('questions_to_verify','question_id'), ('ambiguities','ambiguity_id'))
            for index, item in enumerate(world.get(collection, [])) if item.get(key) in ids
            for field in ('localization', 'evidence'))
    if issues and all(str(i).startswith('world related evidence inconsistent ') for i in issues):
        ids = {str(i).split()[4].rstrip(':') for i in issues}
        return sorted(f'/{collection}/{index}/{field}'
            for collection, key in (('questions_to_verify', 'question_id'), ('ambiguities', 'ambiguity_id'))
            for index, item in enumerate(world.get(collection, [])) if item.get(key) in ids
            for field in ('world_paths', 'related_world_paths'))
    paths = set()
    joined, _identifiers, road_issue = _issue_scope(world, issues)
    if road_issue:
        paths.update({"/world/road/ego_direction_lanes", "/world/road/one_way",
                      "/world/road/opposite_direction_lanes", "/world/road/parking_lanes"})
        # Existing collections are never replaceable as a whole. Expose
        # individual fields; only a missing collection can be added atomically.
        def fields(value, prefix):
            if isinstance(value, dict):
                for key, child in value.items():
                    fields(child, prefix + '/' + _pointer_escape(key))
            elif isinstance(value, list) and any(isinstance(x, (dict, list)) for x in value):
                for index, child in enumerate(value):
                    fields(child, prefix + '/' + str(index))
            else:
                paths.add(prefix)
        road = world['world']['road']
        for name in ROAD_COLLECTIONS:
            prefix = f'/world/road/{name}'
            if name not in road or (name in {'roads', 'lanes'} and road[name] == []):
                paths.add(prefix)
            else:
                for index, item in enumerate(road[name]) if isinstance(road[name], list) else []:
                    fields(item, prefix + '/' + str(index))
        for index, item in enumerate(road.get('roads', [])):
            for field in ('road_role', 'heading_rad'):
                paths.add(f'/world/road/roads/{index}/{field}')
        junctions = world['world'].get('junctions')
        if not junctions:
            paths.add('/world/junctions')
        else:
            for index, junction in enumerate(junctions):
                for field in ('incoming_roads', 'outgoing_roads', 'lane_connections'):
                    prefix = f'/world/junctions/{index}/{field}'
                    # Relations are an atomic local repair unit: correcting
                    # membership may change cardinality. Roads/lanes stay locked.
                    paths.add(prefix)
                    if junction.get(field):
                        fields(junction[field], prefix)
        paths.add('/field_evidence/world.junctions')
        for name in ROAD_COLLECTIONS | {"ego_direction_lanes", "one_way", "opposite_direction_lanes", "parking_lanes"}:
            paths.add("/field_evidence/" + _pointer_escape(f"world.road.{name}"))
        for index, actor in enumerate(world.get("actor_hypotheses", [])):
            for field in ACTOR_MUTABLE_FIELDS:
                paths.add(f"/actor_hypotheses/{index}/{field}")
    for collection, id_key in (("questions_to_verify", "question_id"), ("ambiguities", "ambiguity_id")):
        for index, item in enumerate(world.get(collection, [])):
            identifier = item.get(id_key)
            if identifier and (f"unresolved {identifier}" in joined or f"{collection}.{index}" in joined):
                paths.add(f"/{collection}/{index}/resolution")
                paths.add(f"/{collection}/{index}/evidence")
    return sorted(paths)


def _read_pointer(document, pointer):
    cursor = document
    try:
        for token in _tokens(pointer):
            cursor = cursor[int(token)] if isinstance(cursor, list) else cursor[token]
    except (KeyError, IndexError, TypeError, ValueError):
        return {"$missing": True}
    return copy.deepcopy(cursor)


def _focused_context(world, issues, paths, batch_paths, frames_path, *, issue_details=()):
    """Extract only the facts relevant to the reported readiness issues."""
    _joined, identifiers, road_issue = _issue_scope(world, issues)
    field_paths={p for row in issue_details for p in row.get('world_paths',[])}
    actor_ids={a for row in issue_details for a in row.get('actor_ids',[])}
    road_ids={row.get('road_id') for row in issue_details}-{None}
    for collection,key in (('questions_to_verify','question_id'),('ambiguities','ambiguity_id')):
        for item in world.get(collection,[]):
            targets=item.get('world_paths',[])
            if (any(a==b or a.startswith(b+'.') or a.startswith(b+'[') or b.startswith(a+'.') or b.startswith(a+'[')
                    for a in targets for b in field_paths)
                    or set((item.get('identity_binding') or {}).get('actor_ids',[])) & actor_ids
                    or (item.get('localization') or {}).get('road_id') in road_ids):
                identifiers.add(item[key])
    verification = []
    for collection, id_key in (("questions_to_verify", "question_id"), ("ambiguities", "ambiguity_id")):
        verification.extend(copy.deepcopy(item) for item in world.get(collection, [])
                            if item.get(id_key) in identifiers)
    batch_evidence = []
    for path in batch_paths:
        batch = read_json(path)
        observations = [copy.deepcopy(item) for item in batch.get("observations", [])
                        if item.get("question_id") in identifiers or item.get("ambiguity_id") in identifiers]
        conflicts = [copy.deepcopy(item) for item in batch.get("semantic_conflicts", [])
                     if road_issue or item.get("conflict_id") in identifiers]
        if observations or conflicts:
            batch_evidence.append({"batch_id": batch.get("batch_id"),
                                   "observations": observations, "semantic_conflicts": conflicts})
    context = {
        "current_values": {path: _read_pointer(world, path) for path in paths},
        "verification_items": verification,
        "relevant_batch_evidence": batch_evidence,
    }
    if issue_details:
        context['issue_details']=list(issue_details)
        context['field_evidence']={p:copy.deepcopy(e) for p,e in world.get('field_evidence',{}).items()
            if any(p==q or p.startswith(q+'.') or p.startswith(q+'[') for q in field_paths)}
    if road_issue:
        context["actor_identity_lock"] = [
            {key: item.get(key) for key in ("actor_id", "actor_type", "identity_description", "first_seen_frame_id")}
            for item in world.get("actor_hypotheses", [])
        ]
    if frames_path.is_file():
        context["frame_index_path"] = str(frames_path.resolve())
    return context


def write_request(case, base_path, candidate_path, issues, expected_epoch, batch_paths, schema_path, *, request_path=None, issue_details=None):
    """Write a small path/hash packet; large World/Batch bodies stay in source files."""
    case, base_path, candidate_path = Path(case), Path(base_path), Path(candidate_path)
    batch_paths = list(map(Path, batch_paths))
    inputs = [base_path, *batch_paths, Path(schema_path)]
    frames = case / "state/global_frames.json"
    if frames.is_file():
        inputs.append(frames)
    manifest = case / 'manifest.json'
    if manifest.is_file():
        inputs.append(manifest)
    request = Path(request_path) if request_path else case / "state/world_closure.request.json"
    # Layout recovery reuses its frozen shape/template; code upgrades must not
    # silently replace an inactive v2-v5 request with the new v6 contract.
    if request.is_file():
        frozen = read_json(request)
        if (frozen.get('lane_layout_contract')
                and frozen.get('base_path') == str(base_path.resolve())
                and frozen.get('base_sha256') == sha256(base_path)
                and frozen.get('issues') == list(issues)
                and frozen.get('expected_epoch') == expected_epoch
                and frozen.get('output_path') == str(candidate_path.resolve())
                and all(str(path.resolve()) in frozen.get('input_hashes', {}) for path in batch_paths)):
            if frozen.get('request_sha256') != request_binding(frozen):
                raise ClosureStopped('source_changed', 'Frozen layout request binding changed')
            if any(not Path(path).is_file() or sha256(path) != digest
                   for path, digest in frozen['input_hashes'].items()):
                raise ClosureStopped('source_changed', 'Frozen layout input changed; do not rebind ordinary recovery')
            return request
    world = read_json(base_path)
    paths = allowed_paths(world, issues)
    from _lib.world.gaps import enabled, classify_gaps
    supplied_details=issue_details
    issue_details=list(supplied_details or [])
    if enabled(case) and supplied_details is None:
        # Structured dependencies determine evidence selection; IDs in prose are only a legacy fallback.
        candidates=classify_gaps(world)
        issue_details=[r for r in candidates if r.get('issue_id') in _issue_scope(world,issues)[1]]
        if any('lane_connections' in str(i) for i in issues):
            issue_details.extend(r for r in candidates if r['rule_id']=='intersection.connections_unknown')
    from . import lane_layout
    layout_scope = lane_layout.applicable(issues)
    policy = case/'state/execution_policy.json'
    decision_revision = (read_json(policy).get('lane_layout_closure')
                         if layout_scope and policy.is_file() else None)
    decision_scope = decision_revision in lane_layout.DECISION_REVISIONS
    if decision_scope:
        inputs.append(policy)
    if enabled(case) and issue_details and not layout_scope:
        scopes={p for row in issue_details for p in row.get('world_paths',[])}
        pointers={'/'+ '/'.join(re.findall(r'[^.\[\]]+',p)) for p in scopes}
        evidence_pointers={'/field_evidence/'+_pointer_escape(p) for p in scopes}
        ids={r.get('issue_id') for r in issue_details}|{i for r in issue_details for i in r.get('issue_ids',[])}
        item_pointers={f'/{collection}/{index}' for collection,key in
            (('questions_to_verify','question_id'),('ambiguities','ambiguity_id'))
            for index,item in enumerate(world.get(collection,[])) if item[key] in ids}
        if pointers:
            paths=[p for p in paths if any(p==root or p.startswith(root+'/') for root in pointers|evidence_pointers)
                   or any(p in {root+'/resolution',root+'/evidence'} for root in item_pointers)]
    payload = {
        "contract_version": CONTRACT,
        "case_id": case.name,
        "base_path": str(base_path.resolve()),
        "base_sha256": sha256(base_path),
        "expected_epoch": expected_epoch,
        "issues": list(issues),
        "allowed_paths": paths,
        "resolution_values": ["unresolved", "resolved", "non_core"],
        "resolution_rule": "Core evidence insufficient: use stop_contract needs_evidence; never keep unverified topology as resolved. For related evidence inconsistency, move unverified context into world_paths, preserve all existing unresolved paths and the union of both path lists; do not change facts or resolve the question.",
        "coordinate_convention": coordinate_convention(),
        "focused_context": _focused_context(world, issues, paths, batch_paths, frames,issue_details=issue_details),
        "input_hashes": {str(path.resolve()): sha256(path) for path in inputs},
        "output_path": str(candidate_path.resolve()),
        "output_contract": {
            "contract_version": CONTRACT,
            "request_sha256": "copy exact request hash",
            "base_sha256": "copy exact base hash",
            "operations": [{"op": "add|replace", "path": "one exact allowed JSON Pointer",
                            "value": "replacement value", "evidence_frame_ids": ["frame_NNNN"]}],
        },
        "stop_contract": {
            "status": "needs_evidence|blocked_by_patch_contract",
            "operations": [], "blocking_issues": "nonempty subset of issues",
            "reason": "specific reason; never add unrelated operations to satisfy minimum size",
            "required_paths": "blocked_by_patch_contract only: nonempty JSON Pointer array",
            "binding": "same contract_version/request_sha256/base_sha256 as a patch",
        },
    }
    # Global sampling is sparse. The assigned Batch may have inspected a
    # decisive frame absent from global_frames; make that evidence directly
    # available to its World owner without reading whole Batch documents.
    indexes = read_json(frames).get('frames', []) if frames.is_file() else []
    if manifest.is_file():
        indexes = indexes + [frame for batch in read_json(manifest).get('batches', [])
                             for frame in batch.get('frames', [])]
    evidence_ids = set()
    def collect(value):
        if isinstance(value, dict):
            if isinstance(value.get('frame_id'), str): evidence_ids.add(value['frame_id'])
            for child in value.values(): collect(child)
        elif isinstance(value, list):
            for child in value: collect(child)
    context = payload['focused_context']
    if layout_scope:
        lane_layout.configure(payload, world, read_json(schema_path))
        if decision_scope:
            lane_layout.configure_decision(payload, revision=decision_revision)
    collect(context['verification_items'])
    collect(context['relevant_batch_evidence'])
    collect(context.get('field_evidence',{}))
    selected = {}
    for frame in indexes:
        if frame.get('frame_id') in evidence_ids and frame.get('path'):
            # Prefer the later Batch image for the same source frame.
            selected[frame['frame_id']] = copy.deepcopy(frame)
    if layout_scope:
        # Review the independent Batch's source witnesses plus real endpoints;
        # never manufacture new frames or lose first-frame ego evidence.
        review_id = payload['lane_layout_contract']['question_id']
        wanted = ({fid for claim in context['layout_claims']
                   for fid in claim['evidence_frame_ids']}
                  if 'layout_claims' in context else
                  {fid for row in context['relevant_batch_evidence'] for obs in row['observations']
                   if obs.get('question_id') == review_id
                   for fact in obs.get('layout_observation', {}).values() if isinstance(fact, dict)
                   for fid in fact.get('evidence_frame_ids', [])})
        if indexes:
            wanted.update(f['frame_id'] for f in (min(indexes, key=lambda f:f['time_s']), max(indexes, key=lambda f:f['time_s'])))
        for frame in indexes:
            if frame['frame_id'] in wanted: selected[frame['frame_id']] = copy.deepcopy(frame)
    if issues == ['world.opposing_separation_evidence_required']:
        from _lib.behavior.compile import evidence_frames
        selected={f['frame_id']:copy.deepcopy(f) for f in evidence_frames(case)}
        context['road_context']=copy.deepcopy(world['world']['road'])
        context['scope_rule']='Determine only whether opposing traffic is physically separated and on which side. Preserve all lane counts, identities, events and unresolved questions. Use needs_evidence if undecidable. Bind observed field_evidence to the exact opposing_separation path and reviewed frame IDs.'
        schema=read_json(schema_path)
        geometry=schema['properties']['world']['properties']['road']['properties']['roads']['items']['properties']['road_geometry']
        payload['value_schemas']={
            paths[0]:geometry['properties']['opposing_separation'],
            paths[1]:{'type':'array','minItems':1,'items':{
                'type':'object','required':['status','frame_id','reason'],
                'properties':{'status':{'const':'observed'},'frame_id':{'type':'string'},
                              'reason':{'type':'string','minLength':1},'time_s':{'type':'number'}}}}}
        context['scope_rule']+=' Use exactly the value_schemas shapes; no root resolution field. Do not invent alternative field names.'
    if issues and all(str(i).startswith('world.execution_localization_required ') for i in issues):
        from _lib.behavior.compile import evidence_frames
        selected = {f['frame_id']:copy.deepcopy(f) for f in evidence_frames(case)}
        context['road_context'] = copy.deepcopy(world['world']['road'])
        for label, source in (('behavior', case/'state/behavior.json'), ('case_parameters', case/'case.json')):
            if source.is_file():
                data = read_json(source)
                context[label] = data.get('parameters', {}) if label == 'case_parameters' else data
                payload['input_hashes'][str(source.resolve())] = sha256(source)
        context['scope_rule'] = ('Only add structured localization and append evidence to assigned questions. '
            'Do not change their resolution, paths, existing evidence or any World fact. '
            'Use fixed video-start ego +x coordinates for spatial bounds. '
            'Never infer metric coverage from clip duration, distant prose or policy defaults. '
            'Use needs_evidence when observed spatial bounds or boundary/interaction evidence cannot be established.')
        schema = read_json(schema_path)
        payload['value_schemas'] = {path: (schema['$defs']['question']['properties']['localization']
            if path.endswith('/localization') else {'type':'array','items':schema['$defs']['evidence']}) for path in paths}
    if issues and all(str(i).startswith('world.execution_verification_required ') for i in issues):
        from _lib.behavior.compile import evidence_frames
        selected = {f['frame_id']:copy.deepcopy(f) for f in evidence_frames(case)}
        context['road_context'] = copy.deepcopy(world['world']['road'])
        context['scope_rule'] = (
            'Re-adjudicate only the assigned lane-count questions against every supplied frame. '
            'An observation time window refers to the visible ego carriageway across those frames, '
            'not to an unseen spatial extension beyond the vanishing point. '
            'Resolve only if both outer boundaries and all internal boundaries establish the existing '
            'lane count throughout the sampled window, without observed merge/diverge or conflicting evidence. '
            'Append per-frame observed evidence, explicitly state the visible observation scope and retain '
            'unknown distant/unsampled coverage. Do not claim metric coverage or real topology over a default road length. '
            'Do not change World facts, lane IDs/counts, question text/paths or existing evidence. '
            'Only unresolved -> resolved is allowed; non_core is forbidden. '
            'If the visible count remains uncertain, return needs_evidence; '
            'if topology must change, return blocked_by_patch_contract.')
        schema = read_json(schema_path)
        payload['value_schemas'] = {path: ({'const':'resolved'} if path.endswith('/resolution') else
            {'type':'array','items':schema['$defs']['evidence']}) for path in paths}
    context['evidence_frames'] = list(selected.values())
    for frame in selected.values():
        source = Path(frame['path'])
        if not source.is_file() or sha256(source) != frame.get('sha256'):
            raise ValueError('World closure evidence image missing or changed: ' + str(source))
        payload['input_hashes'][str(source.resolve())] = frame['sha256']
        # Contact sheets and resized packs may erase small direction signs.
        # Reuse decoded originals; this is neither a new extraction nor a new
        # model task. Bind the actual review bytes as well as the packed image.
        original = Path(frame.get('source_path', frame['path']))
        review = original if original.is_file() else source
        frame['review_path'] = str(review.resolve())
        frame['review_sha256'] = sha256(review)
        payload['input_hashes'][frame['review_path']] = frame['review_sha256']
    if decision_revision in lane_layout.PHYSICAL_DECISION_REVISIONS:
        version = 'v7' if decision_revision == lane_layout.CORRECTION_DECISION_REVISION else 'v6'
        template = Path(__file__).resolve().parents[2] / f'world-understanding/references/lane-layout-decision-{version}-prompt.md'
        payload['prompt_template'] = template.read_text(encoding='utf-8')
        from _lib.world.lane_layout import validator_version
        payload['validator_revision'] = validator_version()
    payload["request_sha256"] = request_binding(payload)
    progress_path = case / 'state/progress.json'
    active = progress_path.is_file() and any(
        item.get('stage') == 'world_closure' and item.get('status') in {'submitted', 'pending', 'running'}
        for item in read_json(progress_path).get('sessions', []))
    if active:
        if not request.is_file() or read_json(request) != payload:
            raise ValueError('active World closure request cannot be rebound; resolve the existing task first')
        return request
    atomic_json(request, payload)
    if enabled(case) and not payload['focused_context'].get('evidence_frames'):
        diagnostic={'code':'world_evidence_insufficient','owner':'world','retry_kind':'new_evidence',
                    'issues':list(issues),'request_path':str(request),'reason':'No relevant evidence frames in targeted packet'}
        atomic_json(case/'diagnostics/world_closure_outcome.json',diagnostic)
        raise ClosureStopped('world_evidence_insufficient',diagnostic['reason'],diagnostic)
    if issues and not paths:
        atomic_json(case/'diagnostics/world_closure_outcome.json', {
            'code':'world_patch_contract_blocked', 'issues':list(issues),
            'request_sha256':payload['request_sha256'], 'reason':'No assigned repair paths'})
        raise ClosureStopped('world_patch_contract_blocked', 'World issues have no assigned repair paths',
                             {'issues': list(issues), 'request_path': str(request)})
    return request


def request_binding(request):
    value = copy.deepcopy(request)
    value.pop("request_sha256", None)
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _known_frame_ids(request):
    result = set()
    def visit(value):
        if isinstance(value, dict):
            if isinstance(value.get("frame_id"), str):
                result.add(value["frame_id"])
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    for path in request["input_hashes"]:
        if Path(path).suffix.lower() != '.json':
            continue
        try:
            visit(read_json(path))
        except (OSError, ValueError):
            continue
    return result


def _tokens(pointer):
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise ValueError("World closure operation requires JSON Pointer")
    return [part.replace("~1", "/").replace("~0", "~") for part in pointer[1:].split("/")]


def _apply(document, operation, *, array_append_paths=frozenset()):
    if not isinstance(operation, dict) or set(operation) != {"op", "path", "value", "evidence_frame_ids"}:
        raise ValueError("World closure operation requires op/path/value/evidence_frame_ids")
    if operation["op"] not in {"add", "replace"}:
        raise ValueError("World closure supports add/replace only")
    frames = operation["evidence_frame_ids"]
    if not isinstance(frames, list) or not frames or any(not isinstance(item, str) or not item for item in frames):
        raise ValueError("World closure operation requires evidence frame IDs")
    tokens = _tokens(operation["path"])
    cursor = document
    for token in tokens[:-1]:
        if isinstance(cursor, list):
            if not token.isdecimal() or int(token) >= len(cursor):
                raise ValueError("World closure pointer has invalid array index")
            cursor = cursor[int(token)]
        elif isinstance(cursor, dict) and token in cursor:
            cursor = cursor[token]
        else:
            raise ValueError("World closure pointer parent does not exist")
    leaf = tokens[-1]
    if isinstance(cursor, list):
        if leaf == "-" and operation["op"] == "add" and operation["path"] in array_append_paths:
            cursor.append(copy.deepcopy(operation["value"]))
            return
        if not leaf.isdecimal() or int(leaf) >= len(cursor):
            raise ValueError("World closure cannot insert or delete array items")
        index = int(leaf)
        if operation["op"] == "add":
            raise ValueError("World closure cannot add array items")
        cursor[index] = copy.deepcopy(operation["value"])
    elif isinstance(cursor, dict):
        exists = leaf in cursor
        if operation["op"] == "replace" and not exists:
            raise ValueError("World closure replace path does not exist")
        if operation["op"] == "add" and exists:
            raise ValueError("World closure add path already exists")
        cursor[leaf] = copy.deepcopy(operation["value"])
    else:
        raise ValueError("World closure pointer parent is scalar")


def normalize_operations(base, operations, allowed):
    """Expand only equivalent object replacements into exact authorized fields.

    Array edits, deletions, missing objects and unauthorized changes are rejected;
    the caller still checks operation shape, evidence and overlapping paths.
    """
    if not isinstance(operations, list):
        raise ValueError("World closure operations must be an array")
    result = []
    for operation in operations:
        path = operation.get("path") if isinstance(operation, dict) else None
        if (isinstance(path, str) and path in allowed and operation.get('op') in {'add','replace'}
                and set(operation) == {'op','path','value','evidence_frame_ids'}):
            # Dictionary assignment has one meaning in this contract. Resolve
            # add/replace from the immutable base; never insert array items.
            tokens = _tokens(path)
            parent = _read_pointer(base, '/' + '/'.join(_pointer_escape(x) for x in tokens[:-1])) if len(tokens)>1 else base
            if isinstance(parent, dict):
                operation = dict(operation, op='replace' if tokens[-1] in parent else 'add')
            result.append(operation)
            continue
        if (not isinstance(path, str) or path in allowed or operation.get("op") != "replace"
                or set(operation) != {"op", "path", "value", "evidence_frame_ids"}):
            result.append(operation)
            continue
        before = _read_pointer(base, path)
        after = operation["value"]
        if not isinstance(before, dict) or not isinstance(after, dict):
            raise ValueError("World closure path is unassigned: " + path)
        changes = []
        def diff(old, new, pointer):
            if type(old) is type(new) and old == new:
                return
            if isinstance(old, dict) and isinstance(new, dict):
                if set(old) - set(new):
                    raise ValueError("World closure normalization cannot delete fields: " + pointer)
                for key, value in new.items():
                    child = pointer + '/' + _pointer_escape(key)
                    if key in old:
                        diff(old[key], value, child)
                    else:
                        if child not in allowed:
                            raise ValueError("World closure path is unassigned: " + child)
                        changes.append(dict(operation, op='add', path=child, value=value))
                return
            if pointer not in allowed or isinstance(old, (dict, list)) or isinstance(new, (dict, list)):
                raise ValueError("World closure path is unassigned or changes a collection: " + pointer)
            changes.append(dict(operation, path=pointer, value=new))
        diff(before, after, path)
        if not changes:
            raise ValueError("World closure object replacement has no changes: " + path)
        result.extend(changes)
    if not 1 <= len(result) <= 32:
        raise ValueError("World closure requires 1..32 field operations")
    return result


def materialize(case, candidate, *, request_path=None):
    """Apply a patch to its immutable base after checking every dependency hash."""
    case = Path(case)
    request_path = Path(request_path) if request_path else case / "state/world_closure.request.json"
    request = read_json(request_path)
    if request.get("contract_version") != CONTRACT:
        raise ValueError("World closure request contract is not patch-v1")
    if request.get("request_sha256") != request_binding(request):
        raise ValueError("World closure request binding is corrupt")
    if request.get('lane_layout_contract', {}).get('revision') in {
            'lane-layout-decision.v2', 'lane-layout-decision.v3', 'lane-layout-decision.v4',
            'lane-layout-decision.v5', 'lane-layout-decision.v6', 'lane-layout-decision.v7'}:
        from .lane_layout import materialize_decision
        return materialize_decision(request, candidate)
    required = {"contract_version", "request_sha256", "base_sha256", "operations"}
    if not isinstance(candidate, dict) or candidate.get("contract_version") != CONTRACT:
        raise ValueError("World closure candidate must be a patch-v1 object")
    missing = required - set(candidate)
    extra = set(candidate) - required - {"case_id", "expected_epoch", "status", "blocking_issues", "reason", "required_paths"}
    if request.get('lane_layout_contract'):
        extra.discard('lane_id_map')
    if missing or extra:
        raise ValueError(f"World closure fields: missing={sorted(missing)}, unexpected={sorted(extra)}")
    for key in ("case_id", "expected_epoch"):
        if key in candidate and (type(candidate[key]) is not type(request[key]) or candidate[key] != request[key]):
            raise ValueError(f"World closure metadata mismatch: {key}")
    if candidate["request_sha256"] != request["request_sha256"]:
        raise ValueError("World closure candidate request hash mismatch")
    base = Path(request["base_path"])
    if candidate["base_sha256"] != request["base_sha256"] or sha256(base) != request["base_sha256"]:
        raise ValueError("World closure base hash mismatch")
    for path, digest in request["input_hashes"].items():
        source = Path(path)
        if not source.is_file() or sha256(source) != digest:
            raise ValueError("World closure input changed; rebuild the patch request")
    operations = candidate["operations"]
    if len(json.dumps(candidate, ensure_ascii=False, allow_nan=False).encode()) > 16 * 1024:
        raise ValueError('World closure patch exceeds 16 KiB; targeted repair required')
    status = candidate.get('status', 'patch')
    if status in ('needs_evidence', 'blocked_by_patch_contract'):
        blocking = candidate.get('blocking_issues')
        reason = candidate.get('reason')
        if (operations != [] or not isinstance(blocking,list) or not blocking or
                any(not isinstance(issue,str) or issue not in request['issues'] for issue in blocking) or
                not isinstance(reason,str) or not reason.strip() or len(reason)>2000):
            raise ValueError('World closure stop requires zero operations, assigned blocking_issues and reason')
        if status == 'blocked_by_patch_contract':
            paths = candidate.get('required_paths')
            if not isinstance(paths,list) or not paths or len(paths)>32:
                raise ValueError('World closure blocked response requires bounded required_paths')
            for path in paths: _tokens(path)
        code = 'world_evidence_insufficient' if status == 'needs_evidence' else 'world_patch_contract_blocked'
        raise ClosureStopped(code, reason, copy.deepcopy(candidate))
    if status != 'patch' or any(key in candidate for key in ('blocking_issues','reason','required_paths')):
        raise ValueError('World closure patch has invalid status or stop-only fields')
    if not isinstance(operations, list) or not 1 <= len(operations) <= 32:
        raise ValueError("World closure requires 1..32 field operations")
    allowed = set(request["allowed_paths"])
    append_contracts = request.get("array_append_contracts", {})
    if (not isinstance(append_contracts, dict)
            or any(path not in allowed or not isinstance(spec, dict)
                   or set(spec) != {'contract', 'actor_ids'}
                   or spec.get('contract') != "roadside_parking_area.v1"
                   or not isinstance(spec.get('actor_ids'), list) or not spec['actor_ids']
                   or any(not isinstance(actor, str) or not actor for actor in spec['actor_ids'])
                   for path, spec in append_contracts.items())):
        raise ValueError("World closure array append contract is invalid")
    known_frames = _known_frame_ids(request)
    seen = set()
    result = copy.deepcopy(read_json(base))
    operations = normalize_operations(result, operations, allowed)
    for operation in operations:
        path = operation.get("path") if isinstance(operation, dict) else None
        if not isinstance(path,str) or path not in allowed or path in seen:
            raise ValueError("World closure path is unassigned or duplicated")
        if any(path.startswith(other+'/') or other.startswith(path+'/') for other in seen):
            raise ValueError('World closure operations overlap parent and child paths')
        frame_ids = operation.get('evidence_frame_ids')
        if not isinstance(frame_ids, list) or not frame_ids or any(not isinstance(frame, str) for frame in frame_ids):
            raise ValueError('World closure operation requires evidence frame IDs')
        if any(frame not in known_frames for frame in frame_ids):
            raise ValueError("World closure operation references foreign evidence frame")
        if path in append_contracts:
            value = operation.get('value')
            base_world = read_json(base)
            road_ids = {road.get('road_id') for road in base_world.get('world', {}).get('road', {}).get('roads', [])}
            actor_ids = set(append_contracts[path]['actor_ids'])
            existing_ids = {area.get('area_id') for area in
                            base_world.get('world', {}).get('road', {}).get('roadside_areas', [])}
            required = {'area_id', 'road_id', 'side', 'area_type', 'occupied_by', 'evidence_frame_ids'}
            parking_types = {'curbside_parking', 'roadside_parking', 'parallel_parking',
                             'parallel_parking_strip', 'parking', 'parking_band', 'parking_strip'}
            if (operation.get('op') != 'add' or not isinstance(value, dict)
                    or not required <= set(value) or value.get('area_id') in existing_ids
                    or value.get('road_id') not in road_ids or value.get('side') not in {'left', 'right'}
                    or value.get('area_type') not in parking_types
                    or not isinstance(value.get('occupied_by'), list) or not value['occupied_by']
                    or not set(value['occupied_by']) <= actor_ids
                    or not isinstance(value.get('evidence_frame_ids'), list)
                    or not value['evidence_frame_ids']
                    or not set(value['evidence_frame_ids']) <= set(frame_ids)):
                raise ValueError('World closure roadside parking append is invalid')
        seen.add(path)
        if (path.startswith(('/questions_to_verify/', '/ambiguities/')) and path.endswith('/resolution')
                and operation.get('value') in ('unresolvable_from_clip', 'needs_evidence', 'unknown')):
            raise ClosureStopped('world_evidence_insufficient',
                'World explicitly reports insufficient clip evidence at ' + path,
                {'request_sha256':request['request_sha256'], 'path':path, 'response':candidate})
        if (path.startswith(('/questions_to_verify/', '/ambiguities/')) and path.endswith('/resolution')
                and operation.get('value') == 'resolved_by_evidence'):
            operation = dict(operation, value='resolved')
        _apply(result, operation, array_append_paths=frozenset(append_contracts))
    original = read_json(base)
    for collection in ('questions_to_verify', 'ambiguities'):
        for old, new in zip(original.get(collection, []), result.get(collection, [])):
            if any(old.get(f, []) != new.get(f, []) for f in ('world_paths', 'related_world_paths')):
                old_paths = set(old.get('world_paths', []) + old.get('related_world_paths', []))
                new_paths = set(new.get('world_paths', []) + new.get('related_world_paths', []))
                if old_paths != new_paths or not set(old.get('world_paths', [])) <= set(new.get('world_paths', [])):
                    raise ValueError('World closure must retain all fact paths and existing unresolved paths')
    result["evidence_epoch"] = request["expected_epoch"]
    identities = lambda value: [(item.get("actor_id"), item.get("actor_type"),
                                 item.get("identity_description"), item.get("first_seen_frame_id"))
                                for item in value.get("actor_hypotheses", [])]
    if identities(result) != identities(read_json(base)):
        raise ValueError("World closure changed immutable Actor identity")
    if request.get('lane_layout_contract'):
        from .lane_layout import validate
        validate(request, original, result, candidate)
    return result


def prompt(case, request):
    from _lib.world.admission import enabled as admission_enabled
    packet = read_json(request)
    if packet.get('lane_layout_contract'):
        if 'prompt_template' in packet:
            from string import Template
            return Template(packet['prompt_template']).substitute(
                case_id=Path(case).name, request_path=request, candidate_path=packet['output_path'])
        from _lib.prompts import render
        revision = packet['lane_layout_contract'].get('revision')
        multi_roadside = 'roadside_intervals' in (packet.get('value_schemas', {})
            .get('decision', {}).get('properties', {}))
        template = ('lane-layout-decision-v7-prompt.md' if revision == 'lane-layout-decision.v7'
                    else 'lane-layout-decision-v6-prompt.md' if revision == 'lane-layout-decision.v6'
                    else 'lane-layout-decision-v5-bands-prompt.md'
                    if revision == 'lane-layout-decision.v5' and multi_roadside
                    else 'lane-layout-decision-v5-prompt.md' if revision == 'lane-layout-decision.v5'
                    else 'lane-layout-decision-v4-prompt.md' if revision == 'lane-layout-decision.v4'
                    else 'lane-layout-decision-v3-prompt.md' if revision == 'lane-layout-decision.v3'
                    else 'lane-layout-decision-prompt.md' if revision == 'lane-layout-decision.v2'
                    else 'lane-layout-closure-prompt.md')
        return render('world-understanding', template, case_id=Path(case).name,
                      request_path=request, candidate_path=packet['output_path'])
    if admission_enabled(case):
        from _lib.world.field_repair import closure_prompt
        return closure_prompt(case,request)
    candidate = Path(read_json(request)["output_path"])
    from _lib.prompts import render
    return render('world-understanding', 'closure-prompt.md', case_id=Path(case).name, request_path=request, candidate_path=candidate)
