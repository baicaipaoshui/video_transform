"""Evidence-bound removal of spurious internal dividers, for layout v7 only."""
import copy


def absent_dividers(request, decision, claim, dispute):
    from .lane_layout import CORRECTION_DECISION_REVISION, layout_error
    interval = dispute['disputed_interval']
    path = '/decision/claim_disputes/' + str(decision['claim_disputes'].index(dispute))
    def reject(message):
        layout_error('world.layout.claim_dispute', path, message, claim_id=claim['claim_id'])
    if request['lane_layout_contract']['revision'] != CORRECTION_DECISION_REVISION:
        reject('Absent-divider correction requires layout v7')
    old = claim['boundaries_right_to_left']
    chosen = [row['boundary_id'] for row in decision['boundaries_right_to_left']]
    right, left = interval['right_boundary_id'], interval['left_boundary_id']
    removed = interval['absent_boundary_ids']
    if right not in old or left not in old:
        reject('Absent-divider interval must retain two original boundary IDs')
    interior = old[old.index(right)+1:old.index(left)]
    if (interval['observed_role'] != 'driving' or not interior or interior != removed
            or len(set(old)) != len(old) or any(b in chosen for b in removed)
            or (right, left) not in list(zip(chosen, chosen[1:]))
            or chosen != [b for b in old if b not in removed]
            or claim['same_direction_count']['kind'] != 'exact'
            or claim['same_direction_count']['count'] != len(old)-1):
        reject('Absent dividers must explain the entire count difference inside one retained driving corridor')
    bounds = {row['boundary_id']:row for row in decision['boundaries_right_to_left']}
    frames = (set(dispute['evidence_frame_ids']) & set(bounds[right]['evidence_frame_ids'])
              & set(bounds[left]['evidence_frame_ids']))
    if len(frames) < 2:
        reject('Absent-divider correction needs two common frames showing both surviving edges')
    return set(removed)


def removed_boundaries(request, decision):
    from .lane_layout import CORRECTION_DECISION_REVISION
    if request['lane_layout_contract']['revision'] != CORRECTION_DECISION_REVISION:
        return set()
    claims = {row['claim_id']:row for row in request['focused_context']['layout_claims']}
    removed = set()
    for dispute in decision['claim_disputes']:
        if dispute.get('disputed_interval', {}).get('absent_boundary_ids'):
            removed.update(absent_dividers(request, decision, claims[dispute['claim_id']], dispute))
    return removed


def lane_sources(request, decision, original):
    """Preserve known fields; merging is permitted only across proved absent edges."""
    from .lane_layout import layout_error, CANONICAL_LANE_FIELDS
    path = '/decision/old_lane_corridors'
    def reject(message, **details):
        layout_error('world.layout.lane_mapping', path, message, **details)
    road = original['world']['road']
    lanes = {row['lane_id']:row for row in road['lanes']}
    mapping = decision['old_lane_corridors']
    if set(mapping) != set(lanes):
        reject('Every old physical lane requires a corridor mapping',
               actual=sorted(mapping), expected=sorted(lanes))
    removed = removed_boundaries(request, decision)
    ordered = road['roads'][0]['lane_ids']
    result = {}
    mutable = set(CANONICAL_LANE_FIELDS) | {'evidence_frame_ids'}
    for ordinal in sorted({row['ordinal_from_right'] for row in mapping.values()}):
        group = [lid for lid in ordered if mapping[lid]['ordinal_from_right'] == ordinal]
        if not group:
            reject('Road lane order omits a mapped lane')
        if len(group) > 1:
            indexes = [ordered.index(lid) for lid in group]
            right, left = (decision['boundaries_right_to_left'][ordinal+i]['boundary_id'] for i in (0,1))
            internal = [lanes[lid].get('left_boundary') for lid in group[:-1]]
            if (indexes != list(range(indexes[0], indexes[-1]+1))
                    or lanes[group[0]].get('right_boundary') != right
                    or lanes[group[-1]].get('left_boundary') != left
                    or not set(internal) <= removed
                    or any(lanes[a].get('left_boundary') != lanes[b].get('right_boundary')
                           for a,b in zip(group,group[1:]))):
                reject('Every old physical lane requires a distinct evidenced corridor mapping unless its internal dividers were refuted',
                       actor_lane_ids=group)
        merged = copy.deepcopy(lanes[group[0]])
        for lid in group[1:]:
            for key,value in lanes[lid].items():
                if key in mutable or value in (None, 'unknown'):
                    continue
                if merged.get(key) not in (None, 'unknown') and merged[key] != value:
                    reject('Merged lanes disagree on a known property: '+key, lane_ids=group)
                merged[key] = copy.deepcopy(value)
        result[ordinal] = merged
    return result
