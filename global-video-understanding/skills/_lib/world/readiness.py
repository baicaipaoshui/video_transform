"""Consumer contract at the World -> Behavior handoff (no model inference)."""
import copy
import math
import re
from _lib.contracts.v3 import ContractError, CORE_IMPACT_DOMAINS, validate_world


def normalize_world(source):
    """Lossless aliases only. Never interpret prose or resolve questions here."""
    from _lib.contracts.world_metadata import normalize as normalize_metadata
    result, _ = normalize_metadata(source)
    from _lib.world.topology import normalize_topology
    normalize_topology(result['world'])
    road = result["world"]["road"]
    evidence = result["field_evidence"]
    # travel_lane_count is a legacy total across directions, not an exact
    # synonym for the ego-direction count.  Treating it as an alias caused
    # valid values such as total=4 / ego-direction=2 to conflict.
    for alias in ("lane_count_ego_direction",):
        if alias not in road:
            continue
        value = road[alias]
        if "ego_direction_lanes" in road and road["ego_direction_lanes"] != value:
            raise ContractError(f"conflicting lane counts: {alias}/ego_direction_lanes")
        road["ego_direction_lanes"] = value
        key = f"world.road.{alias}"
        if key in evidence:
            evidence.setdefault("world.road.ego_direction_lanes", copy.deepcopy(evidence[key]))
    # Older reconciliation code copied a whole Batch observation into a
    # question's evidence array.  Its nested evidence items are the actual
    # provenance objects.  Flattening them is lossless and deterministic, so
    # it must not trigger a World model closure/rewrite.
    for collection in ("questions_to_verify", "ambiguities"):
        for item in result.get(collection, []):
            flattened = []
            changed = False
            for record in item.get("evidence", []):
                nested = record.get("evidence") if isinstance(record, dict) else None
                if isinstance(nested, list):
                    changed = True
                    for evidence_item in nested:
                        if evidence_item not in flattened:
                            flattened.append(copy.deepcopy(evidence_item))
                elif record not in flattened:
                    flattened.append(copy.deepcopy(record))
            if changed:
                item["evidence"] = flattened
    # The separator is context for this exact legacy count question. This
    # does not decide whether the unknown count is needed by an execution.
    if _observed_separator(result):
        for collection in ('questions_to_verify', 'ambiguities'):
            for item in result.get(collection, []):
                if _legacy_opposing_paths(item):
                    item['world_paths'] = [_OPPOSING_COUNT]
                    item['related_world_paths'] = list(dict.fromkeys(
                        item.get('related_world_paths', []) + [_OPPOSING_SEPARATOR]))
    return result


def check_lane_numbering(road):
    """Check ordered declarations independently of unrelated World metadata."""
    if not road.get("roads") or not road.get("lanes"):
        return  # Missing definitions are an observation/handoff gap, not an ID order.
    ordered = [lane_id for item in road["roads"] for lane_id in item.get("lane_ids", [])]
    expected = [f"lane_{index:03d}" for index in range(1, len(road["lanes"]) + 1)]
    if ordered != expected:
        error = ContractError(
            "expanded World road lanes require sequential right-to-left lane_NNN numbering; "
            f"expected {expected}, got {ordered}", rule_id="world.lane.global_numbering")
        error.path = "/world/road/roads"
        raise error


def check_world_shape(world, *, observation_contract=False, declarations_only=False):
    validate_world(world, observation_contract=observation_contract)
    check_world_declarations(world, declarations_only=declarations_only)


def check_world_declarations(world, *, declarations_only=False):
    """Structural checks on an already schema/semantically validated World."""
    road = world["world"]["road"]
    if road.get("roads") and road.get("lanes"):
        problems = []
        for key, id_key in (("roads", "road_id"), ("lanes", "lane_id")):
            if not isinstance(road[key], list) or any(not isinstance(x, dict) or not isinstance(x.get(id_key), str) for x in road[key]):
                problems.append(f"world.road.{key} must contain typed {id_key} objects")
        if problems:
            raise ContractError("; ".join(problems))
        if any(not re.fullmatch(r"road_[0-9]{3}", item["road_id"]) for item in road["roads"]):
            problems.append("expanded World road_id must use executable road_NNN identifiers")
        if any(not re.fullmatch(r"lane_[0-9]{3}", item["lane_id"]) for item in road["lanes"]):
            problems.append("expanded World lane_id must use executable lane_NNN identifiers")
        lane_ids = {item["lane_id"] for item in road["lanes"]}
        if len(lane_ids) != len(road["lanes"]):
            problems.append("expanded World contains duplicate lane_id")
        road_ids = [item["road_id"] for item in road["roads"]]
        # road_001 is the canonical ego approach. Even counting every lane
        # (including opposite/parking lanes), it must fit the claimed count.
        ego_road=next((item for item in road["roads"] if item["road_id"]=="road_001"),None)
        count=road.get("ego_direction_lanes")
        if not declarations_only and ego_road and type(count) is int and count > len(set(ego_road.get("lane_ids",[]))):
            problems.append("ego_direction_lanes exceeds road_001 defined lanes; reconcile lane topology and actor lane references together using batch evidence")
        if len(set(road_ids)) != len(road_ids):
            problems.append("expanded World contains duplicate road_id")
        if any(set(item.get("lane_ids", [])) - lane_ids for item in road["roads"]):
            problems.append("expanded World road references unknown lane_id")
        if world["world"].get("scene_type") == "intersection":
            if not declarations_only and len(road["roads"]) < 3:
                problems.append("expanded intersection World requires at least three road arms")
            if road_ids != [f"road_{index:03d}" for index in range(1,len(road_ids)+1)]:
                problems.append("expanded intersection roads require clockwise sequential road_NNN numbering")
        markings=road.get("lane_markings")
        if not isinstance(markings, list):
            problems.append("expanded World road requires lane_markings array")
        else:
            for marking in markings:
                if not isinstance(marking,dict) or not ("type" in marking or "marking_type" in marking):
                    problems.append("expanded World lane markings require type or marking_type")
                    break
                if not any(key in marking for key in ("marking_id","between","side","location")):
                    problems.append("expanded World lane markings require an anchor")
                    break
                if set(marking.get("between",[])) - lane_ids or (marking.get("lane_id") and marking["lane_id"] not in lane_ids):
                    problems.append("expanded World lane marking references unknown lane_id")
                    break
                if marking.get("road_id") and marking["road_id"] not in set(road_ids):
                    problems.append("expanded World lane marking references unknown road_id")
                    break
        if problems:
            error = ContractError("; ".join(problems), rule_id="world.structure")
            error.path = "/world/road"
            raise error
        check_lane_numbering(road)
        from _lib.contracts.lane_locations import lane_locations
        lane_locations(road)
        return
    if declarations_only:
        return
    count = road.get("ego_direction_lanes")
    if type(count) is not int or count < 1:
        raise ContractError("world.road.ego_direction_lanes must be a positive integer, or provide roads/lanes")
    if not isinstance(road.get("one_way"), bool):
        raise ContractError("world.road.one_way must be boolean; prose directionality is not executable")
    parking = road.get("parking_lanes")
    if not isinstance(parking, list) or any(x not in ("left", "right") for x in parking):
        raise ContractError("world.road.parking_lanes must be an array of observed left/right sides")
    if not world["field_evidence"].get("world.road.ego_direction_lanes"):
        raise ContractError("canonical lane count requires field_evidence")


def world_uncertainties(world):
    """Retain unresolved World facts and their provenance, without resolving them."""
    result = {collection: [copy.deepcopy(item) for item in world.get(collection, [])
                          if item.get('resolution') in ('unresolved', 'non_core') and item.get('owner') != 'behavior']
              for collection in ('questions_to_verify', 'ambiguities')}
    records = result['questions_to_verify'] + result['ambiguities']
    paths = {path for item in records for field in ('world_paths', 'related_world_paths')
             for path in item.get(field, [])}
    # Legacy questions may have no paths. Keep their evidence context instead
    # of guessing which field they refer to or losing the estimate's source.
    keep_all = any(not item.get('world_paths') for item in records)
    result['field_evidence'] = {path: copy.deepcopy(evidence)
        for path, evidence in world.get('field_evidence', {}).items()
        if keep_all or any(path == ref or path.startswith(ref + '.') or path.startswith(ref + '[')
                           or ref.startswith(path + '.') or ref.startswith(path + '[') for ref in paths)}
    return result


ROAD_MINIMUM_POLICY = 'noncore_road_minimum.v1'


def _road_minimum_references(value, road_id, lane_ids, boundary_ids):
    """Find typed execution references; prose never proves their absence."""
    if isinstance(value, list):
        return any(_road_minimum_references(item, road_id, lane_ids, boundary_ids)
                   for item in value)
    if not isinstance(value, dict):
        return False
    for key, item in value.items():
        if key.endswith('road_id') and item == road_id:
            return True
        if key.endswith('road_ids') and isinstance(item, list) and road_id in item:
            return True
        if key.endswith('lane_id') and isinstance(item, str) and item in lane_ids:
            return True
        if key.endswith('lane_ids') and isinstance(item, list) and set(item) & lane_ids:
            return True
        if key.endswith(('boundary_id', 'crosswalk_id', 'signal_id')) and isinstance(item, str) and item in boundary_ids:
            return True
        if _road_minimum_references(item, road_id, lane_ids, boundary_ids):
            return True
    return False


def _road_minimum_candidate(world, behavior, road_index):
    scene = world.get('world', {})
    roads = scene.get('road', {}).get('roads', [])
    if scene.get('scene_type') != 'intersection' or not 0 <= road_index < len(roads):
        return None, 'Only evidenced intersection road arms are eligible.'
    road = roads[road_index]
    road_id, lane_ids = road.get('road_id'), road.get('lane_ids')
    knowledge = road.get('lane_knowledge') or {}
    count, anchors = knowledge.get('count'), knowledge.get('anchors') or []
    if (not isinstance(road_id, str) or road.get('road_role') not in {'incoming', 'outgoing'}
            or type(road.get('heading_rad')) not in (int, float)
            or not math.isfinite(road['heading_rad'])
            or knowledge.get('kind') != 'minimum' or type(count) is not int or count < 1
            or not isinstance(lane_ids, list) or len(lane_ids) != count
            or len(set(lane_ids)) != count or len(anchors) != count):
        return None, 'Road existence, role, bearing, or evidenced minimum lanes are incomplete.'
    if ({a.get('lane_id') for a in anchors} != set(lane_ids)
            or {a.get('ordinal_from_right') for a in anchors} != set(range(count))
            or not knowledge.get('evidence_frame_ids')
            or any(not a.get('evidence_frame_ids') for a in anchors)):
        return None, 'Every minimum lane needs a distinct positional and frame anchor.'
    lanes = {lane.get('lane_id'): lane for lane in scene['road'].get('lanes', [])}
    if any(lid not in lanes or lanes[lid].get('road_id') != road_id for lid in lane_ids):
        return None, 'Minimum lane membership is inconsistent.'
    junctions = scene.get('junctions', [])
    members = [j for j in junctions if road_id in j.get('road_ids', [])]
    if len(members) != 1:
        return None, 'Road arm must belong to exactly one evidenced junction.'
    junction = members[0]
    membership = ('incoming_roads' if road['road_role'] == 'incoming' else 'outgoing_roads')
    if not any(ref.get('road_id') == road_id for ref in junction.get(membership, [])):
        return None, 'Junction membership conflicts with the observed road role.'
    boundary_ids = {c.get('crosswalk_id') for c in junction.get('crosswalks', [])
                    if c.get('related_road_id') == road_id}
    boundary_ids.update(s.get('signal_id') for s in junction.get('traffic_signals', [])
                        if s.get('road_id') == road_id)
    boundary_ids.discard(None)
    if _road_minimum_references(behavior, road_id, set(lane_ids), boundary_ids):
        return None, 'A retained Actor, event, primitive, or boundary consumes this road arm.'
    if any(_road_minimum_references(connection, road_id, set(lane_ids), set())
           for connection in junction.get('lane_connections', [])):
        return None, 'A declared junction lane connection consumes this road arm.'
    for collection in ('questions_to_verify', 'ambiguities'):
        for issue in world.get(collection, []):
            if issue.get('resolution') not in {'unresolved', 'non_core'}:
                continue
            paths = issue.get('world_paths') or []
            if paths and all(re.fullmatch(r'world\.road\.roads\[\d+\]\.lane_knowledge\.count',
                                       path) for path in paths):
                continue
            if any(path == 'world.junctions' or path.startswith('world.junctions[') and
                   ('.lane_connections' in path or path == 'world.junctions')
                   for path in paths):
                return None, 'A junction connection needed for the retained scene remains unresolved.'
            if any(path.startswith(f'world.road.roads[{road_index}].') and
                   path.rsplit('.', 1)[-1] in {'road_role', 'heading_rad', 'lane_ids'}
                   for path in paths):
                return None, 'Road role, bearing, or identified minimum lanes remain unresolved.'
    for limitation in behavior.get('semantic_limitations', []):
        if (limitation.get('execution_effect') == 'blocks_execution'
                and set(limitation.get('impact_domains', [])) & {'spatial_conflict', 'road_topology', 'actor_lane'}
                and (_road_minimum_references(limitation, road_id, set(lane_ids), boundary_ids)
                     or not limitation.get('world_paths'))):
            return None, 'A declared execution conflict or topology limitation remains unresolved.'
    return {'road_id': road_id, 'road_index': road_index,
            'proved': {'road_role': road['road_role'], 'heading_rad': road['heading_rad'],
                       'minimum_lane_count': count, 'lane_ids': copy.deepcopy(lane_ids),
                       'evidence_frame_ids': copy.deepcopy(knowledge['evidence_frame_ids']),
                       'anchors': copy.deepcopy(anchors)},
            'execution_approximation': {'lane_count': count,
                                        'scope': 'this road arm in the Target projection'},
            'still_unknown': ['accurate_lane_count', 'unobserved_additional_lanes',
                              'true_arm_width_and_conflict_zone_geometry'],
            'impact': 'Retained paths and declared lane connections do not consume this arm; '
                      'junction geometry remains an authorized abstraction, not a measured map.'}, None


def decide_noncore_road_minimum(world, behavior, scope_audit, input_hashes, policy_sha256):
    """Create one source-bound candidate after the retained execution scope is fixed."""
    from _lib.contracts.conversion_policy import digest
    from _lib.behavior.compile import preview_digest
    if not isinstance(scope_audit, dict) or not scope_audit.get('sha256'):
        raise ContractError('Noncore road decision requires a frozen execution scope')
    scope_body = dict(scope_audit)
    scope_signature = scope_body.pop('sha256')
    if scope_signature != preview_digest(scope_body):
        raise ContractError('Noncore road decision requires an authentic execution scope')
    if not isinstance(policy_sha256, str) or not policy_sha256:
        raise ContractError('Noncore road decision requires a frozen policy hash')
    decisions = []
    uncertainties = world_uncertainties(world)
    roads = world.get('world', {}).get('road', {}).get('roads', [])
    for collection, key in (('questions_to_verify', 'question_id'),
                            ('ambiguities', 'ambiguity_id')):
        for item in uncertainties[collection]:
            paths = item.get('world_paths') or []
            indices = []
            for path in paths:
                match = re.fullmatch(r'world\.road\.roads\[(\d+)\]\.lane_knowledge\.count', path)
                if not match:
                    indices = []
                    break
                indices.append(int(match.group(1)))
            if not indices or len(set(indices)) != len(indices):
                continue
            records, failure = [], None
            for index in indices:
                record, failure = _road_minimum_candidate(world, behavior, index)
                if failure:
                    break
                records.append(record)
            if failure:
                continue
            decisions.append({'issue_id': item[key], 'world_paths': copy.deepcopy(paths),
                              'decision': 'controlled_approximation',
                              'roads': records,
                              'evidence': copy.deepcopy(item.get('evidence', [])),
                              'loss': 'Accurate lane counts and true side-arm geometry remain unknown.'})
    body = {'revision': ROAD_MINIMUM_POLICY, 'execution_scope_sha256': scope_audit['sha256'],
            'fact_version_id': (scope_audit.get('effective_facts') or {}).get('version_id'),
            'world_evidence_epoch': world.get('evidence_epoch'),
            'world_sha256': digest(world), 'behavior_sha256': digest(behavior),
            'input_hashes': copy.deepcopy(input_hashes), 'policy_sha256': policy_sha256,
            'decisions': decisions}
    return {**body, 'sha256': digest(body)}


def validate_noncore_road_minimum(decision, world, behavior, *, scope_sha256=None,
                                  input_hashes=None, policy_sha256=None):
    """Validate the same ruling's binding; do not re-adjudicate its content."""
    from _lib.contracts.conversion_policy import digest
    if not isinstance(decision, dict):
        raise ContractError('Noncore road decision missing')
    body = dict(decision)
    signature = body.pop('sha256', None)
    if (body.get('revision') != ROAD_MINIMUM_POLICY or signature != digest(body)
            or body.get('world_sha256') != digest(world)
            or body.get('behavior_sha256') != digest(behavior)
            or body.get('world_evidence_epoch') != world.get('evidence_epoch')
            or scope_sha256 is not None and body.get('execution_scope_sha256') != scope_sha256
            or input_hashes is not None and body.get('input_hashes') != input_hashes
            or policy_sha256 is not None and body.get('policy_sha256') != policy_sha256):
        raise ContractError('Noncore road decision is stale or changed', rule_id='stale_version')
    return decision


_OPPOSING_COUNT = 'world.road.opposite_direction_lanes'
_OPPOSING_SEPARATOR = 'world.road.roads[0].road_geometry.opposing_separation'


def _legacy_opposing_paths(item):
    """Recognize only a structurally localized count question with its separator."""
    location = item.get('localization') or {}
    return (set(item.get('world_paths', [])) == {_OPPOSING_COUNT, _OPPOSING_SEPARATOR}
            and location.get('attribute') == 'opposite_direction_lanes'
            and location.get('road_id') == 'road_001'
            and location.get('area_id') == 'opposing_carriageway')


def _overlapping_path(left, right):
    return (left == right or left.startswith(right + '.') or left.startswith(right + '[')
            or right.startswith(left + '.') or right.startswith(left + '['))


def _separator_contested(world):
    # A positive observation cannot erase a second unknown or conflicting fact.
    for path, evidence in world.get('field_evidence', {}).items():
        if _overlapping_path(path, _OPPOSING_SEPARATOR):
            if any(item.get('status') == 'unknown' for item in evidence):
                return True
    for collection in ('questions_to_verify', 'ambiguities'):
        for item in world.get(collection, []):
            if item.get('resolution') not in ('unresolved', 'non_core'):
                continue
            if any(_overlapping_path(path, _OPPOSING_SEPARATOR)
                   for path in item.get('world_paths', [])) and not _legacy_opposing_paths(item):
                return True
    return False


def _observed_separator(world):
    roads = world.get('world', {}).get('road', {}).get('roads', [])
    separator = (roads[0].get('road_geometry') or {}).get('opposing_separation') if roads else None
    evidence = world.get('field_evidence', {}).get(_OPPOSING_SEPARATOR, [])
    return (isinstance(separator, dict) and separator.get('physically_separated') is True
            and separator.get('side') in ('left', 'right') and not _separator_contested(world)
            and any(e.get('status') == 'observed' and e.get('frame_id')
                    and str(e.get('reason', '')).strip() for e in evidence))


def world_consistency_issues(world):
    """Check the known-context claim, without resolving any execution dependency."""
    issues = []
    for collection, key in (('questions_to_verify', 'question_id'), ('ambiguities', 'ambiguity_id')):
        for item in world.get(collection, []):
            if (_OPPOSING_SEPARATOR in item.get('related_world_paths', [])
                    and not _observed_separator(world)):
                issues.append('world related evidence inconsistent ' + item[key]
                              + ': opposing_separation lacks uncontested observed separation; '
                              'retain it as unresolved rather than known context')
    return issues


def opposing_planning_exclusions(world):
    """Skip count-only reviews behind an observed physical divider.

    This is a scheduling decision, not a resolved observation or an execution
    waiver. execution_dependencies rechecks the eventual Behavior separately.
    """
    scene = world.get('world', {})
    road = scene.get('road', {})
    roads = road.get('roads', [])
    if (scene.get('scene_type') != 'highway' or scene.get('junctions')
            or road.get('one_way') is not False or len(roads) != 1
            or not _observed_separator(world)):
        return []
    entry = roads[0]
    lanes = {lane.get('lane_id'): lane for lane in road.get('lanes', [])}
    if (not entry.get('lane_ids') or any(lid not in lanes
            or lanes[lid].get('road_id') != entry.get('road_id')
            or lanes[lid].get('travel_direction', 'forward') not in {'forward', 'same_as_ego'}
            for lid in entry['lane_ids'])):
        return []
    result = []
    frames = sorted({e['frame_id'] for e in world['field_evidence'][_OPPOSING_SEPARATOR]
                     if e.get('status') == 'observed' and e.get('frame_id')})
    for collection, key in (('questions_to_verify', 'question_id'), ('ambiguities', 'ambiguity_id')):
        for item in world.get(collection, []):
            paths = set(item.get('world_paths', []))
            if (item.get('resolution') != 'unresolved'
                    or paths != {_OPPOSING_COUNT}
                    or not set(item.get('impact_domains', [])) <= {'lane_count', 'road_topology', 'report'}
                    or any(item.get(k) for k in ('actor_id', 'actor_ids', 'affected_actor_ids',
                        'event_id', 'event_ids', 'affected_event_ids', 'identity_binding', 'scope_binding'))
                    or set(item.get('related_world_paths', [])) - {_OPPOSING_SEPARATOR}):
                continue
            result.append(dict(issue_id=item[key], collection=collection,
                rule_id='batch.isolated_opposing_count.v1', world_path=_OPPOSING_COUNT,
                evidence_frame_ids=frames,
                reason='Observed physical separation places the opposing lane count outside this ego-road review; retain unknown and recheck execution dependencies after Behavior.'))
    return result


def _opposing_scope(world, behavior):
    """Return an auditable scope when the opposing side is unused.

    This never changes the observed World. It only proves that an unknown
    opposing lane count is not a dependency of this ego-carriageway preview.
    """
    scene = world.get('world', {})
    road = scene.get('road', {})
    roads = road.get('roads', [])
    if (scene.get('scene_type') != 'highway' or scene.get('junctions')
            or road.get('one_way') is not False or len(roads) != 1):
        return None
    entry = roads[0]
    lane_ids = entry.get('lane_ids', [])
    lanes = {item.get('lane_id'): item for item in road.get('lanes', [])}
    if (not lane_ids or any(lid not in lanes or lanes[lid].get('road_id') != entry.get('road_id')
                            or lanes[lid].get('travel_direction', 'forward') not in {'forward', 'same_as_ego'}
                            for lid in lane_ids)):
        return None
    geometry = entry.get('road_geometry') or {}
    separator = geometry.get('opposing_separation') or {}
    direction_evidence = world.get('field_evidence', {}).get('world.road.one_way', [])
    evidence = world.get('field_evidence', {}).get(
        'world.road.roads[0].road_geometry.opposing_separation', [])
    if (_separator_contested(world)
            or separator.get('physically_separated') not in {True, False}
            or separator.get('side') not in ('left', 'right')
            or not any(item.get('status') == 'observed' for item in direction_evidence)
            or not any(item.get('status') == 'observed' and item.get('frame_id')
                       and str(item.get('reason', '')).strip() for item in evidence)):
        return None
    from _lib.behavior.lane_change import derive as derive_lane_change, LaneChangeBlocked
    try:
        lane_change = derive_lane_change(world, behavior, _behavior_duration(behavior))
    except LaneChangeBlocked:
        return None
    motion_events = [item for item in behavior.get('events', [])
                     if item.get('event_type') not in {'context_observation'}]
    actions = [item for item in behavior.get('executor_primitives', [])
               if item.get('type') != 'initial_state']
    if (motion_events or actions) and lane_change is None:
        return None
    participants = [item for item in behavior.get('actors', [])
                    if item.get('simulation_role') == 'participant']
    if not participants or not any(item.get('actor_id') == 'ego' for item in participants):
        return None
    for actor in participants:
        points = actor.get('trajectory') or []
        allowed_unknown = lane_change is not None and actor.get('actor_id') == lane_change.actor_id
        if (not points or any(point.get('lane_id') not in lane_ids
                              and not (allowed_unknown and point.get('lane_id') is None)
                              for point in points)):
            return None
    return {
        'scope': 'ego_carriageway_only',
        'road_id': entry.get('road_id'),
        'included_lane_ids': list(lane_ids),
        'included_actor_ids': [item['actor_id'] for item in participants],
        'excluded_actor_ids': [item['actor_id'] for item in behavior.get('actors', [])
                               if item.get('simulation_role') == 'background'],
        'opposing_physically_separated': separator.get('physically_separated'),
        'excluded_world_paths': ['world.road.opposite_direction_lanes'],
        'reason': ('The opposing side is observed outside the retained ego-road actor paths, and '
                   'no executable actor, event, or action depends on its unresolved lane count.'),
        'preserves_world_uncertainty': True,
    }


def ego_carriageway_execution_scope(world, behavior):
    """Compatibility view; all consumers use the same dependency assessment."""
    return execution_dependencies(world, behavior)['opposing_scope']


def _number(value):
    import math
    if isinstance(value, dict): value = value.get('value')
    return value if type(value) in (int, float) and math.isfinite(value) else None


def _behavior_duration(behavior):
    values = [point.get('time_s', point.get('timestamp_s'))
              for actor in behavior.get('actors', [])
              for point in actor.get('trajectory', [])]
    values = [value for value in values if _number(value) is not None]
    return max(values) if values else None


def _roadside_exclusion(world, behavior, item, parameters, scope):
    """Prove that a roadside uncertainty is absent from the executable target.

    The current projector consumes explicit curbside parking only. Other
    roadside observations remain in World/report, but are not execution
    dependencies unless an actor, event or action refers to them.
    """
    road = world['world']['road']
    areas = road.get('roadside_areas', [])
    parking_types = {'parallel_parking', 'curbside_parking'}
    projected = [area for area in areas
                 if area.get('area_type') == 'curbside_parking'
                 or area.get('type') in parking_types]
    location = item.get('localization') or {}
    target_area_id = location.get('area_id')
    def uses_area(node):
        if isinstance(node, dict):
            if any(node.get(key) and (not target_area_id or node[key] == target_area_id)
                   for key in ('roadside_area_id', 'area_id')):
                return True
            return any(uses_area(value) for value in node.values())
        return isinstance(node, list) and any(uses_area(value) for value in node)

    if uses_area([behavior.get('events', []), behavior.get('executor_primitives', [])]):
        return False, 'An executable event or action directly references this roadside area.'
    actor_references = []
    for actor in behavior.get('actors', []):
        if actor.get('roadside_area_id') and (
                not target_area_id or actor.get('roadside_area_id') == target_area_id):
            actor_references.append(actor.get('actor_id'))
        if any(point.get('roadside_area_id') and (
                not target_area_id or point.get('roadside_area_id') == target_area_id)
                for point in actor.get('trajectory', [])):
            actor_references.append(actor.get('actor_id'))
    if projected:
        return False, 'Curbside parking is consumed by the current target projection.'
    if actor_references:
        return False, ('Roadside area is referenced by executable actor trajectories: '
                       + ', '.join(sorted(set(actor_references))))
    dependent = set(actor_references)
    if any(dependent.intersection(event.get('involved_actor_ids', []))
           for event in behavior.get('events', [])) or any(
            primitive.get('actor_id') in dependent
            for primitive in behavior.get('executor_primitives', [])
            if primitive.get('type') != 'initial_state'):
        return False, 'An event or action depends on an Actor bound to this roadside area.'
    if areas:
        return True, ('No roadside area is projected by the current target contract and no '
                      'actor, event, or action references one; the World uncertainty is retained.')

    # Without a structured entity the collection path cannot prove what the
    # missing roadside fact would represent, so retain the evidence gate.
    if location.get('attribute') != 'future_use':
        return False, 'Provide structured road/area localization and affected attribute.'
    roads = road.get('roads', [])
    if (world['world'].get('scene_type') != 'highway' or len(roads) != 1
            or world['world'].get('junctions') or behavior.get('events')
            or any(p.get('type') != 'initial_state' for p in behavior.get('executor_primitives', []))):
        return False, 'Complex topology, event or action requires explicit dependency review.'
    entry = roads[0]; geometry = entry.get('road_geometry', {})
    if (geometry.get('shape_type') != 'straight' or entry.get('heading_rad') != 0
            or location.get('road_id') != entry.get('road_id')):
        return False, 'Straight road and fixed video-start coordinates must be established.'
    areas = [a for a in road.get('roadside_areas', []) if a.get('area_id') == location.get('area_id')]
    if len(areas) != 1 or areas[0].get('side') != 'right':
        return False, 'A unique right roadside area is required.'
    lanes = set(entry.get('lane_ids', [])); points = []
    excluded = set(scope['excluded_actor_ids']) if scope else set()
    for actor in behavior.get('actors', []):
        if actor.get('actor_id') in excluded: continue
        trajectory = actor.get('trajectory') or []
        if (actor.get('simulation_role') != 'participant' or actor.get('is_static') is not False
                or not trajectory or len({p.get('lane_id') for p in trajectory}) != 1
                or any(p.get('lane_id') not in lanes or p.get('roadside_area_id') for p in trajectory)
                or actor.get('roadside_area_id')):
            return False, 'Actor lane change, roadside use or unclassified interaction remains.'
        points.extend(trajectory)
    if not points or not any(a.get('actor_id') == 'ego' for a in behavior.get('actors', [])):
        return False, 'Complete actor trajectories are required.'
    coverage = location.get('observed_corridor') or {}
    fields = ('lane_continuity', 'boundary_continuity', 'no_merge_or_diverge', 'no_intrusion')
    if any(coverage.get(k) is not True for k in fields):
        return False, 'Corridor continuity, boundary, merge/diverge and intrusion evidence required.'
    known = {p.get('frame_id') for p in points if p.get('frame_id')}
    observed = {e.get('frame_id') for e in item.get('evidence', [])
                if e.get('status') == 'observed' and e.get('reason')}
    refs = set(location.get('evidence_frame_ids', []))
    if not known or not known <= refs or not refs <= observed:
        return False, 'Localization evidence must cover every trajectory frame.'
    times = [_number(p.get('time_s')) for p in points]
    window = coverage.get('time_range_s', [])
    spatial = coverage.get('x_range_m', [])
    affected = location.get('affected_x_range_m', [])
    if (None in times or any(len(v) != 2 or any(_number(x) is None for x in v)
                             or v[0] > v[1] for v in (window, spatial, affected))):
        return False, 'Ordered observed time/spatial coverage and affected spatial bounds required.'
    from _lib.contracts.v3 import normalize_parameters
    params = normalize_parameters(parameters or {})
    if params['road_source'] != 'preview_json':
        return False, 'External road generation extent is not established.'
    length = _number(geometry.get('visible_length_m'))
    if length is None: length = params['fallback_policy']['highway_length_m']
    if not (window[0] <= min(times) <= max(times) <= window[1]
            and spatial[0] <= 0 < length <= spatial[1] and affected[0] > length):
        return False, 'Generated road or execution window overlaps the unknown or exceeds evidence coverage.'
    return True, 'Observed corridor covers the generated road and all actor times; future use lies beyond it.'


def _topology_handoff(world, item, parameters):
    """Known current corridor permits Behavior, not a final topology waiver."""
    scene = world.get('world', {})
    roads = scene.get('road', {}).get('roads', [])
    layout = scene.get('metadata', {}).get('lane_layout_review', {}).get('observation', {})
    count = layout.get('same_direction_count', {})
    edge = layout.get('right_outer_boundary', {})
    ego = layout.get('ego_initial', {})
    from _lib.contracts.v3 import normalize_parameters
    params = normalize_parameters(parameters or {})
    if (set(item.get('impact_domains', [])) != {'road_topology'}
            or scene.get('scene_type') != 'highway' or len(roads) != 1 or scene.get('junctions')
            or params['road_source'] != 'preview_json'
            or not params['fallback_policy']['straight_preview']['enabled']):
        return False
    road = roads[0]
    lanes = road.get('lane_ids', [])
    anchors = road.get('lane_knowledge', {}).get('anchors', [])
    return bool(lanes and count.get('kind') == 'exact' and count.get('count') == len(lanes)
        and len(layout.get('boundaries_right_to_left', [])) == len(lanes) + 1
        and edge.get('confirmed') is True
        and set(count.get('evidence_frame_ids', [])) & set(edge.get('evidence_frame_ids', []))
           & set(ego.get('evidence_frame_ids', []))
        and {a.get('lane_id') for a in anchors} == set(lanes)
        and any(a.get('ordinal_from_right') == ego.get('ordinal_from_right') for a in anchors))


def _future_topology_exclusion(world, behavior, item, parameters, scope):
    """Exclude topology explicitly scoped after the observed preview window.

    The straight preview ends at the final reviewed source time. This proof is
    temporal and does not claim that the policy-default road length was seen.
    """
    paths = set(item.get('world_paths', []))
    allowed = {'world.road.ego_direction_lanes', 'world.road.roads[0].lane_ids',
               'world.junctions'}
    location = item.get('localization') or {}
    window = location.get('observed_window') or {}
    from _lib.contracts.v3 import normalize_parameters
    params = normalize_parameters(parameters or {})
    if (not paths or not paths <= allowed or location.get('attribute') not in {
            'future_use', 'junction_beyond_observed_window'}
            or location.get('temporal_scope') != 'after_observed_window'
            or params['road_source'] != 'preview_json'
            or params['fallback_policy']['straight_preview']['enabled'] is not True):
        return False, 'Future topology must be structurally scoped after an enabled preview window.'
    scene = world.get('world', {}); road = scene.get('road', {}); roads = road.get('roads', [])
    if (scene.get('scene_type') != 'highway' or len(roads) != 1 or scene.get('junctions')
            or location.get('road_id') != roads[0].get('road_id')
            or roads[0].get('road_geometry', {}).get('shape_type') != 'straight'
            or roads[0].get('heading_rad') != 0):
        return False, 'Current preview must be one explicit straight highway road with no observed junction.'
    checks = ('lane_count_stable', 'boundary_continuity', 'no_merge_or_diverge',
              'no_junction_entry')
    if any(window.get(key) is not True for key in checks):
        return False, 'Observed-window lane, boundary, merge/diverge and junction evidence is required.'
    if behavior is None:
        # Preliminary handoff only: never turn a future unknown into a fact or
        # certify execution before the actual Behavior exists.
        layout = scene.get('metadata', {}).get('lane_layout_review', {}).get('observation', {})
        count = layout.get('same_direction_count', {})
        boundary = layout.get('right_outer_boundary', {})
        ego = layout.get('ego_initial', {})
        anchors = roads[0].get('lane_knowledge', {}).get('anchors', [])
        time_range = window.get('time_range_s', [])
        observed = {e.get('frame_id'): e.get('time_s') for e in item.get('evidence', [])
                    if e.get('status') == 'observed' and str(e.get('reason', '')).strip()}
        refs = set(location.get('evidence_frame_ids', []))
        if (count.get('kind') != 'exact' or count.get('count') != len(roads[0].get('lane_ids', []))
                or boundary.get('confirmed') is not True
                or not set(ego.get('evidence_frame_ids', [])) & refs
                or not any(a.get('ordinal_from_right') == ego.get('ordinal_from_right')
                           and a.get('lane_id') in roads[0].get('lane_ids', []) for a in anchors)
                or len(time_range) != 2 or any(_number(t) is None for t in time_range)
                or time_range[0] != 0 or time_range[1] <= time_range[0]
                or not set(time_range) <= {observed[f] for f in refs if f in observed}
                or not set(count.get('evidence_frame_ids', [])) & refs
                or not set(boundary.get('evidence_frame_ids', [])) & refs):
            return False, 'Pre-Behavior handoff needs accepted layout, ego corridor and observed window endpoints.'
        return True, 'Future topology is retained for final dependency review against actual Behavior.'
    excluded = set(scope['excluded_actor_ids']) if scope else set()
    participants = [actor for actor in behavior.get('actors', [])
                    if actor.get('actor_id') not in excluded]
    lanes = set(roads[0].get('lane_ids', [])); points = []
    from _lib.behavior.lane_change import derive as derive_lane_change, LaneChangeBlocked
    try:
        lane_change = derive_lane_change(world, behavior, _behavior_duration(behavior))
    except LaneChangeBlocked:
        return False, 'Observed event or action is outside the bounded lane-change contract.'
    if ((behavior.get('events') or any(p.get('type') != 'initial_state'
                                      for p in behavior.get('executor_primitives', [])))
            and lane_change is None):
        return False, 'Observed event or action depends on current topology.'
    for actor in participants:
        trajectory = actor.get('trajectory') or []
        changing = lane_change is not None and actor.get('actor_id') == lane_change.actor_id
        known = {point.get('lane_id') for point in trajectory if point.get('lane_id') is not None}
        if (actor.get('simulation_role') != 'participant' or actor.get('is_static') is not False
                or actor.get('roadside_area_id')
                or any(p.get('roadside_area_id') or p.get('junction_id') or p.get('connection_id')
                       or (p.get('road_id') and p['road_id'] != roads[0]['road_id']) for p in trajectory)
                or not trajectory or (not changing and len(known) != 1)
                or any(point.get('lane_id') not in lanes
                       and not (changing and point.get('lane_id') is None)
                       for point in trajectory)):
            return False, 'Every executed actor must remain in one current-road lane.'
        points.extend(trajectory)
    if not points or not any(actor.get('actor_id') == 'ego' for actor in participants):
        return False, 'Complete ego and participant trajectories are required.'
    times = [_number(point.get('time_s')) for point in points]
    time_range = window.get('time_range_s', [])
    if (None in times or len(time_range) != 2 or any(_number(x) is None for x in time_range)
            or time_range[0] > time_range[1]
            or not (time_range[0] <= min(times) <= max(times) <= time_range[1])):
        return False, 'Observed-window time bounds must cover all executed trajectory samples.'
    ego = next((actor for actor in participants if actor.get('actor_id') == 'ego'), None)
    ego_points = (ego or {}).get('trajectory') or []
    boundary_frames = ({ego_points[0].get('frame_id'), ego_points[-1].get('frame_id')}
                       if ego_points else set())
    boundary_frames.discard(None)
    refs = set(location.get('evidence_frame_ids', []))
    observed = {e.get('frame_id') for e in item.get('evidence', [])
                if e.get('status') == 'observed' and str(e.get('reason', '')).strip()}
    if not boundary_frames or not boundary_frames <= refs or not boundary_frames <= observed:
        return False, 'Future-topology evidence must cover the ego window boundaries.'
    return True, ('The unresolved topology is explicitly after the reviewed time window; '
                  'the preview ends at that window and preserves the future uncertainty.')


def execution_dependencies(world, behavior, parameters=None, uncertainties=None,
                           road_minimum_decision=None):
    if road_minimum_decision is not None:
        validate_noncore_road_minimum(road_minimum_decision, world, behavior)
    road_decisions = {item['issue_id']: item for item in
                      (road_minimum_decision or {}).get('decisions', [])}
    scope = _opposing_scope(world, behavior) if behavior is not None else None
    uncertainties = uncertainties if uncertainties is not None else world_uncertainties(world)
    decisions = []
    for collection, key in (('questions_to_verify', 'question_id'), ('ambiguities', 'ambiguity_id')):
        for item in uncertainties.get(collection, []):
            original_paths = copy.deepcopy(item.get('world_paths', []))
            paths = list(original_paths)
            related_paths = copy.deepcopy(item.get('related_world_paths', []))
            compatibility_reason = None
            if scope and _legacy_opposing_paths(item):
                paths = [_OPPOSING_COUNT]
                if _OPPOSING_SEPARATOR not in related_paths:
                    related_paths.append(_OPPOSING_SEPARATOR)
                compatibility_reason = ('Legacy count question includes the observed separator as context; '
                    'the existing ego-carriageway proof validates that separator independently.')
            decision = 'undetermined'; reason = 'Missing field localization or unsupported execution dependency.'
            if 'actor_identity' in item.get('impact_domains',[]):
                from _lib.world.gaps import identity_exclusion
                safe=identity_exclusion(world,behavior,item)
                decision='excluded' if safe else 'required'
                reason='Proven isolated opposing background identity is not an execution dependency.' if safe else 'Identity uncertainty requires structured scope and evidence; participant identity cannot be approximated.'
            elif paths and scope and set(paths) <= set(scope['excluded_world_paths']):
                decision, reason = 'excluded', scope['reason']
            elif paths and all(p.startswith('world.road.roadside_areas') for p in paths):
                provisional = dict(actors=world.get('actor_hypotheses', []))
                safe, reason = _roadside_exclusion(world, behavior if behavior is not None else provisional,
                                                   item, parameters, scope)
                if behavior is None:
                    location = item.get('localization') or {}
                    areas = world['world']['road'].get('roadside_areas', [])
                    safe = safe and bool(location.get('area_id') and location.get('road_id') and any(
                        area.get('area_id') == location['area_id']
                        and area.get('road_id') == location['road_id'] for area in areas))
                decision = ('deferred' if behavior is None else 'excluded') if safe else 'undetermined'
            elif paths and set(paths) <= {
                    'world.road.ego_direction_lanes', 'world.road.roads[0].lane_ids',
                    'world.junctions'}:
                safe, reason = _future_topology_exclusion(world, behavior, item, parameters, scope)
                if behavior is None and not safe and _topology_handoff(world, item, parameters):
                    safe = True
                    reason = ('Accepted current layout and ego corridor permit Behavior input; '
                              'topology remains unknown and final execution dependency is not established.')
                decision = (('deferred' if behavior is None else 'excluded') if safe else 'undetermined'
                            if (item.get('localization') or {}).get('attribute') in {
                                'future_use', 'junction_beyond_observed_window'}
                            else 'required')
            elif paths and any(p.startswith(('world.road', 'world.junctions')) for p in paths):
                decision, reason = 'required', 'Road geometry/topology consumed by this execution remains unresolved.'
            elif not CORE_IMPACT_DOMAINS.intersection(item.get('impact_domains', [])):
                if paths and all(p.startswith('world.environment.') for p in paths):
                    decision, reason = 'excluded', 'Report-only environment uncertainty under the existing delivery contract.'
            road_decision = road_decisions.get(item[key])
            if road_decision is not None and paths == road_decision['world_paths']:
                decision = 'controlled_approximation'
                reason = ('Only the proved minimum lane count is eligible for an authorized '
                          'execution projection; accurate count and real junction geometry remain unknown.')
            binding = item.get('scope_binding') or {}
            identity = item.get('identity_binding') or {}
            explicit_actor_ids = list(dict.fromkeys([
                *([item['actor_id']] if item.get('actor_id') else []),
                *(item.get('actor_ids') or []), *(item.get('affected_actor_ids') or []),
                *(binding.get('actor_ids') or []), *(binding.get('affected_actor_ids') or []),
                *(identity.get('actor_ids') or []), *(identity.get('affected_actor_ids') or []),
            ]))
            explicit_event_ids = list(dict.fromkeys([
                *(item.get('affected_event_ids') or []),
                *(binding.get('affected_event_ids') or []),
            ]))
            decisions.append({'issue_id': item[key], 'decision': decision, 'reason': reason,
                'world_paths': copy.deepcopy(paths),
                'original_world_paths': original_paths,
                'effective_unresolved_world_paths': copy.deepcopy(paths),
                'related_world_paths': related_paths,
                'compatibility_reason': compatibility_reason,
                'actor_ids': explicit_actor_ids,
                'event_ids': explicit_event_ids,
                'evidence': copy.deepcopy(item.get('evidence', [])),
                'localization': copy.deepcopy(item.get('localization'))})
    return {'decisions': decisions, 'opposing_scope': scope, 'preserves_world_uncertainty': True}


def world_execution_issues(uncertainties, world=None, behavior=None, parameters=None,
                           road_minimum_decision=None):
    world = world or {'world': {}}
    result = execution_dependencies(world, behavior, parameters, uncertainties,
                                    road_minimum_decision=road_minimum_decision)
    return [{'rule_id': 'world.unresolved_fact', 'message': f"unresolved {d['issue_id']}",
             'world_paths': d['world_paths'], 'dependency': d['decision'],
             'required_change': d['reason']} for d in result['decisions'] if d['decision'] != 'excluded']


def readiness_issues(world, *, execution=True, observation_contract=False):
    from _lib.contracts.world_metadata import normalize
    world, _ = normalize(world)
    issues = []
    try:
        check_world_shape(world, observation_contract=observation_contract)
    except ContractError as error:
        return [str(error)]
    if world['world']['road'].get('roads'):
        from _lib.world.topology import validate_topology
        try:
            from _lib.world.gaps import intersection_eligible
            validate_topology(normalize_world(world)['world'], observation=(observation_contract and not execution and intersection_eligible(world)))
        except ValueError as error:
            issues.append('world road topology: ' + str(error))
    elif world['world'].get('scene_type') == 'intersection':
        issues.append('intersection topology requires evidenced road arms/lanes/junctions; '
                      'aggregate lane count cannot determine a three- or four-arm junction')
    else:
        # Counts are valid observations, but Behavior cannot reference them.
        # Route missing definitions through World closure before freezing its
        # handoff, including when execution-only uncertainties are deferred.
        issues.append('World lane definitions missing: world.road.roads and world.road.lanes '
                      'must define evidenced roads/lanes with stable road_id/lane_id before Behavior; '
                      'aggregate lane counts are insufficient')
    issues.extend(world_consistency_issues(world))
    if execution:
        issues.extend(item['message'] for item in world_execution_issues(world_uncertainties(world)))
    return issues


def observation_handoff_issues(world):
    """Minimum referenceable topology after Batch, not execution completeness.

    The caller has already admitted the declarations. Partial road arms and
    unknown lane totals/connections remain legal; counts alone cannot supply
    the lane identities consumed by Behavior.
    """
    road = world['world']['road']
    approach = next((item for item in road.get('roads', [])
                     if item['road_id'] == 'road_001'), None)
    if not road.get('lanes') or not approach or not approach.get('lane_ids'):
        return [{'rule_id': 'world.handoff.lane_definitions', 'path': '/world/road',
                 'message': 'World lane definitions missing: evidenced road_001 and its '
                            'lane references are required before Behavior; aggregate lane counts '
                            'are insufficient'}]
    return []
