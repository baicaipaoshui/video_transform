"""Road-local, zero-based lane positions shared by observation and delivery.

Lane entities have no spatial storage order. Road lane_ids and explicit
positional anchors define order; a partial lane census must not imply a full
set of ordinal positions.
"""


def _fail(rule, path, message, actor_id=None):
    from _lib.contracts.v3 import ContractError
    error = ContractError(message, rule_id=rule, actor_id=actor_id)
    error.path = path
    error.diagnostics = {'rule_id': rule, 'path': path, 'actor_id': actor_id}
    raise error


def lane_locations(road):
    """Return lane_id -> (road_id, known ordinal), without changing inputs."""
    entities = {lane['lane_id']: lane for lane in road.get('lanes', [])}
    result = {}
    for ri, arm in enumerate(road.get('roads', [])):
        rid, ids = arm['road_id'], arm.get('lane_ids', [])
        root = f'/road/roads/{ri}'
        knowledge = arm.get('lane_knowledge')
        anchors = (knowledge or {}).get('anchors', [])
        mapped, ordinals = {}, set()
        for ai, anchor in enumerate(anchors):
            lid, ordinal = anchor['lane_id'], anchor['ordinal_from_right']
            path = root + f'/lane_knowledge/anchors/{ai}'
            if (lid not in ids or lid in mapped or type(ordinal) is not int
                    or ordinal < 0 or ordinal in ordinals):
                _fail('lane.position.anchor', path, 'Lane anchors require unique road-local zero-based positions')
            if (knowledge.get('kind') == 'exact' and type(knowledge.get('count')) is int
                    and ordinal >= knowledge['count']):
                _fail('lane.position.anchor', path, 'Lane ordinal exceeds the exact road lane count')
            mapped[lid] = ordinal
            ordinals.add(ordinal)
        known_order = [mapped[lid] for lid in ids if lid in mapped]
        if known_order != sorted(known_order):
            _fail('lane.position.order', root + '/lane_ids', 'Road lane_ids contradict right-to-left positional anchors')
        complete = knowledge is None or (knowledge.get('kind') == 'exact' and knowledge.get('count') == len(ids))
        for index, lid in enumerate(ids):
            if lid in result or lid not in entities:
                _fail('lane.reference', root + '/lane_ids', 'Lane is undefined or belongs to multiple roads: ' + str(lid))
            if entities[lid].get('road_id', rid) != rid:
                _fail('lane.road_mismatch', root + '/lane_ids', 'Lane entity owner disagrees with road membership: ' + lid)
            if complete and lid in mapped and mapped[lid] != index:
                _fail('lane.position.anchor', root + '/lane_knowledge/anchors',
                      'Complete road lane positions must start at 0 and match lane_ids')
            ordinal = index if complete else mapped.get(lid)
            declared = entities[lid].get('ordinal_from_right')
            if declared is not None:
                path = '/road/lanes/' + str(next(i for i, lane in enumerate(road['lanes']) if lane['lane_id'] == lid)) + '/ordinal_from_right'
                if type(declared) is not int or declared < 0:
                    _fail('lane.position.ordinal', path, 'Lane entity ordinal must be a zero-based integer')
                if (knowledge or {}).get('kind') == 'exact' and declared >= knowledge['count']:
                    _fail('lane.position.ordinal', path, 'Lane entity ordinal exceeds the exact road lane count')
                if ordinal is not None and declared != ordinal:
                    _fail('lane.position.ordinal_mismatch', path, 'Lane entity ordinal contradicts road order or positional anchor')
                # Explicit entity positions do not create evidence anchors for a partial census.
            result[lid] = (rid, ordinal)
    return result


def validate_location(value, locations, road_ids, path, actor_id=None):
    """Check declared references; unknown fields remain unknown."""
    rid, lid, ordinal = value.get('road_id'), value.get('lane_id'), value.get('ordinal_from_right')
    if rid is not None and (not isinstance(rid, str) or rid not in road_ids):
        _fail('actor.location.road_reference', path + '/road_id', 'Unknown road reference: ' + str(rid), actor_id)
    if ordinal is not None and (type(ordinal) is not int or ordinal < 0):
        _fail('actor.location.ordinal', path + '/ordinal_from_right', 'Lane ordinal must be a zero-based integer', actor_id)
    if lid is None:
        return
    if not isinstance(lid, str) or lid not in locations:
        _fail('actor.location.lane_reference', path + '/lane_id',
              'Behavior references unknown World lanes: ' + str(lid), actor_id)
    owner, position = locations[lid]
    if rid is not None and rid != owner:
        _fail('actor.location.lane_road_mismatch', path + '/road_id',
              f'Lane {lid} belongs to {owner}, not {rid}', actor_id)
    if ordinal is not None and position is not None and ordinal != position:
        _fail('actor.location.ordinal_mismatch', path + '/ordinal_from_right',
              f'Lane {lid} has ordinal_from_right={position}, not {ordinal}', actor_id)


def validate_actor_locations(actors, road, *, primitives=()):
    locations = lane_locations(road)
    road_ids = {arm['road_id'] for arm in road.get('roads', [])}
    for ai, actor in enumerate(actors):
        for pi, point in enumerate(actor.get('trajectory', [])):
            validate_location(point, locations, road_ids, f'/actors/{ai}/trajectory/{pi}', actor.get('actor_id'))
    for pi, primitive in enumerate(primitives):
        validate_location(primitive, locations, road_ids, f'/executor_primitives/{pi}', primitive.get('actor_id'))
