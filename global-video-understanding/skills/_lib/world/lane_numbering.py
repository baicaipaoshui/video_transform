"""Canonical names for a uniquely evidenced initial single-road lane chain.

No visual inference, topology repair or committed-World migration. Unsupported
or contradictory candidates are returned untouched for the existing gates.
"""
import copy
import re

from _lib.contracts.v3 import ContractError

REVISION = 'initial-lane-numbering.v1'
TOKEN = re.compile(r'(?<![A-Za-z0-9_])lane_[0-9]+(?![A-Za-z0-9_])')
SCALARS = {'lane_id', 'hypothesized_lane', 'incoming_lane_id', 'outgoing_lane_id',
           'from_lane_id', 'to_lane_id', 'source_lane_id', 'target_lane_id'}
ARRAYS = {'lane_ids', 'between'}
PROSE = {'reason', 'description', 'identity_description', 'question', 'location',
         'hypothesized_lane_reason', 'detail'}


def _mapping(raw, request):
    from .single_world import LAYOUT_REVISION, validate_layout
    scene = raw.get('world', {})
    if (not isinstance(scene, dict) or 'road_observation' in raw
            or request.get('layout_review_revision') != LAYOUT_REVISION
            or scene.get('scene_type') != 'highway' or scene.get('junctions') != []):
        return None
    road = scene.get('road')
    if not isinstance(road, dict) or not isinstance(road.get('roads'), list) or len(road['roads']) != 1:
        return None
    arm = road['roads'][0]
    if not isinstance(arm, dict) or arm.get('road_id') != 'road_001':
        return None
    lanes = road.get('lanes', [])
    if not isinstance(lanes, list) or not lanes or any(not isinstance(l, dict) for l in lanes):
        return None
    ids = arm.get('lane_ids')
    expected = [f'lane_{i+1:03}' for i in range(len(lanes))]
    # Initial version only permutes an existing complete set of names; it does
    # not create missing lane entities or reinterpret another road's namespace.
    if (not isinstance(ids, list) or any(not isinstance(i, str) for i in ids)
            or len(ids) != len(expected) or set(ids) != set(expected)
            or any(not isinstance(l.get('lane_id'), str) for l in lanes)
            or {l['lane_id'] for l in lanes} != set(ids)
            or any(l.get('road_id') != arm['road_id'] for l in lanes)):
        return None
    knowledge = arm.get('lane_knowledge')
    if not isinstance(knowledge, dict):
        return None
    anchors = knowledge.get('anchors')
    if (not isinstance(anchors, list) or len(anchors) != len(ids)
            or any(not isinstance(a, dict) or not isinstance(a.get('lane_id'), str)
                   or type(a.get('ordinal_from_right')) is not int for a in anchors)
            or {a['lane_id'] for a in anchors} != set(ids)
            or {a['ordinal_from_right'] for a in anchors} != set(range(len(ids)))):
        return None
    positions = {a['lane_id']: a['ordinal_from_right'] for a in anchors}
    ordered = sorted(ids, key=positions.__getitem__)
    mapping = dict(zip(ordered, expected))
    if ordered == expected and ids == expected:
        return None
    frame_ids = {f['frame_id'] for f in request.get('frames', [])}
    if any(not isinstance(a.get('evidence_frame_ids'), list) or not a['evidence_frame_ids']
           or any(not isinstance(f, str) or f not in frame_ids for f in a['evidence_frame_ids'])
           for a in anchors):
        return None
    layout = raw.get('layout_observation')
    if not isinstance(layout, dict) or not frame_ids:
        return None
    validate_layout(layout, request['frames'])
    chain = layout['boundaries_right_to_left']
    if len(chain) != len(ids)+1 or len(set(chain)) != len(chain):
        return None
    markings = road.get('lane_markings', [])
    if (not isinstance(markings, list) or any(not isinstance(m, dict)
            or not isinstance(m.get('marking_id'), str) for m in markings)
            or len({m['marking_id'] for m in markings}) != len(markings)
            or not set(chain) <= {m['marking_id'] for m in markings}):
        return None
    for lane in lanes:
        ordinal = positions[lane['lane_id']]
        if (lane.get('right_boundary') != chain[ordinal]
                or lane.get('left_boundary') != chain[ordinal+1]
                or ('ordinal_from_right' in lane and lane['ordinal_from_right'] != ordinal)
                or any(key in lane and lane[key] != lane[key.removesuffix('_id')]
                       for key in ('left_boundary_id', 'right_boundary_id'))):
            return None
    for marking in markings:
        if marking['marking_id'] not in chain:
            continue
        index = chain.index(marking['marking_id'])
        adjacent = ordered[max(0, index-1):min(len(ordered), index+1)]
        if ('between' in marking and (not isinstance(marking['between'], list)
                or any(not isinstance(l, str) for l in marking['between'])
                or len(marking['between']) != 2 or set(marking['between']) != set(adjacent))):
            return None
        if marking.get('lane_id') is not None and marking['lane_id'] not in adjacent:
            return None
        if marking.get('side') in ('left', 'right') and index != (len(ids) if marking['side'] == 'left' else 0):
            return None
    ego = layout['ego_initial']; ordinal = ego['ordinal_from_right']
    if ordinal is not None and (ordinal >= len(ids) or ego['right_boundary_id'] != chain[ordinal]
                               or ego['left_boundary_id'] != chain[ordinal+1]):
        return None
    # Reuse the normal positional gate on a copy with only the list order
    # repaired. Counts, entity ordinals, owners and anchors must already agree.
    from _lib.contracts.lane_locations import lane_locations
    probe = copy.deepcopy(road); probe['roads'][0]['lane_ids'] = ordered
    lane_locations(probe)
    return mapping, expected, chain, sorted({f for a in anchors for f in a['evidence_frame_ids']})


def normalize_lane_ids(raw, request):
    """Return an isolated candidate plus audit, or the unchanged input and None."""
    if not isinstance(raw, dict):
        return raw, None
    try:
        plan = _mapping(raw, request)
    except ContractError:
        return raw, None  # Original validators retain ownership of conflicts.
    if plan is None:
        return raw, None
    mapping, expected, chain, witnesses = plan

    def rewrite(node, field=None):
        if isinstance(node, dict):
            # ID-keyed extensions need their own reference contract. Never
            # silently rename an unknown dictionary namespace.
            if any(TOKEN.search(k) for k in node):
                raise ValueError('Unrecognized lane reference key')
            return {k: rewrite(v, k) for k, v in node.items()}
        if isinstance(node, list):
            return [rewrite(v, field) for v in node]
        if not isinstance(node, str):
            return node
        tokens = TOKEN.findall(node)
        if not tokens:
            return node
        if any(token not in mapping for token in tokens):
            raise ValueError('Unresolved lane reference')
        if field in SCALARS | ARRAYS:
            if node not in mapping:
                raise ValueError('Malformed structured lane reference')
            return mapping[node]
        if field in PROSE:
            # One simultaneous token substitution, so swaps cannot cascade.
            # Only ID tokens change; positional words and all other prose stay.
            return TOKEN.sub(lambda match: mapping[match.group()], node)
        raise ValueError('Unrecognized lane reference field')

    try:
        result = rewrite(raw)
    except ValueError:
        return raw, None
    result['world']['road']['roads'][0]['lane_ids'] = expected
    from _lib.contracts.lane_locations import lane_locations
    from .readiness import check_lane_numbering
    lane_locations(result['world']['road'])
    check_lane_numbering(result['world']['road'])
    changes = []

    def differences(before, after, path=''):
        if before == after:
            return
        if isinstance(before, dict):
            for key in before:
                differences(before[key], after[key], path+'/'+key.replace('~', '~0').replace('/', '~1'))
        elif isinstance(before, list) and any(isinstance(v, dict) for v in before):
            for i, (a, b) in enumerate(zip(before, after)):
                differences(a, b, path+'/'+str(i))
        else:
            changes.append(dict(path=path, before=before, after=after))
    differences(raw, result)
    return result, dict(revision=REVISION, lane_id_map=mapping, changes=changes,
                        boundaries_right_to_left=chain, evidence_frame_ids=witnesses,
                        basis='existing positional anchors agree with adjacent boundary pairs')
