"""Validate executable topology; derive indexes only from explicit road facts.

No road count, angle, lane connection or video fact is guessed here.
"""
import copy
import math


def normalize_memberships(scene):
    """Adapt legacy scalar road references in-place without deriving lane facts."""
    from _lib.paths import SKILLS
    import json
    definition = json.loads((SKILLS/'schemas/world_context.schema.json').read_text())['$defs']['junction_membership']
    field = definition['x-legacy-string-field']
    repairs = []
    junctions = scene.get('junctions') if isinstance(scene, dict) else None
    if not isinstance(junctions, list):
        return repairs
    for i, junction in enumerate(junctions):
        if not isinstance(junction, dict):
            continue
        for direction in ('incoming_roads', 'outgoing_roads'):
            refs = junction.get(direction)
            if not isinstance(refs, list):
                continue
            for n, ref in enumerate(refs):
                if not isinstance(ref, str):
                    continue
                refs[n] = {field: ref}
                repairs.append(dict(path=f'/world/junctions/{i}/{direction}/{n}',
                    before=ref, value=copy.deepcopy(refs[n]), was_missing=False,
                    rule='schema.junction_membership_alias',
                    adapter_version=definition['x-adapter-version']))
    return repairs


def finite(value):
    value = value.get('value') if isinstance(value, dict) else value
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError('expected finite topology measurement')
    return float(value)


def normalize_topology(scene):
    """Fill redundant membership/role indexes in the caller's working copy."""
    membership_repairs = normalize_memberships(scene)
    roads = scene.get('road', {}).get('roads') or []
    if not isinstance(roads, list) or any(not isinstance(r,dict) or not isinstance(r.get('road_id'),str) or
                                         not isinstance(r.get('lane_ids'),list) for r in roads):
        return []  # The schema validator will report the malformed shape.
    by_id = {road['road_id']: road for road in roads}
    changes = [row['path'] for row in membership_repairs]
    for road in roads:
        role = road.get('road_role')
        canonical = declared_road_role(road)
        if isinstance(role, list) and isinstance(canonical, str):
            road['road_role'] = canonical
            changes.append(f"{road['road_id']}.road_role")
    junctions = scene.get('junctions') or []
    if not isinstance(junctions,list) or any(not isinstance(j,dict) for j in junctions):
        return []
    for junction in junctions:
        # Some producers repeat road metadata inside junction.roads. Reuse
        # explicit declarations, never derive a bearing from an ID or role.
        repeated = junction.get('roads') or []
        for ref in repeated if isinstance(repeated, list) else []:
            road = by_id.get(ref.get('road_id')) if isinstance(ref, dict) else None
            if road is None:
                continue
            if 'lane_ids' in ref and ref['lane_ids'] != road['lane_ids']:
                raise ValueError('conflicting repeated road lane_ids: ' + road['road_id'])
            for field in ('road_role', 'heading_rad'):
                if field not in ref:
                    continue
                value = ref[field]
                if field == 'heading_rad': finite(value)
                if field in road:
                    same = (finite(road[field]) == finite(value) if field == 'heading_rad'
                            else road[field] == value)
                    if not same:
                        raise ValueError('conflicting repeated road ' + field + ': ' + road['road_id'])
                else:
                    road[field] = copy.deepcopy(value)
                    changes.append(road['road_id'] + '.' + field)
        for field, role in (('incoming_roads', 'incoming'), ('outgoing_roads', 'outgoing')):
            if field not in junction:
                members = junction.get('road_ids')
                if members is None:
                    raw = junction.get('roads') or []
                    members = [r.get('road_id') for r in raw if isinstance(r,dict)] if isinstance(raw,list) else []
                if not isinstance(members,list) or any(not isinstance(key,str) for key in members):
                    continue
                # A single junction owns the explicitly listed roads. Multiple
                # junctions need their own membership; never assign by ID order.
                if not members and len(scene.get('junctions', [])) == 1:
                    members = list(by_id)
                refs = [{'road_id': key, 'lane_ids': list(by_id[key]['lane_ids'])}
                        for key in members if key in by_id and by_id[key].get('road_role') == role]
                if refs:
                    junction[field] = refs
                    changes.append(f"{junction.get('junction_id')}.{field}")
            raw_refs = junction.get(field)
            for ref in raw_refs if isinstance(raw_refs,list) else []:
                road = by_id.get(ref.get('road_id')) if isinstance(ref, dict) else None
                if road is not None:
                    if 'road_role' not in road:
                        other_field = 'outgoing_roads' if role == 'incoming' else 'incoming_roads'
                        other = junction.get(other_field) or []
                        shared = isinstance(other,list) and any(isinstance(r,dict) and
                            r.get('road_id') == road['road_id'] for r in other)
                        road['road_role'] = 'bidirectional' if shared else role
                        changes.append(f"{road['road_id']}.road_role")
                    # A road reference does not declare which of its lanes
                    # participate. Preserve omitted/null directional members.
        # A bidirectional arm has one outward road reference line. Its incoming
        # lanes run backward and outgoing lanes forward on that same geometry.
        def memberships(field):
            refs = junction.get(field)
            return {r['road_id']:r.get('lane_ids',[]) for r in refs
                    if isinstance(r,dict) and isinstance(r.get('road_id'),str)} if isinstance(refs,list) else {}
        incoming, outgoing = memberships('incoming_roads'), memberships('outgoing_roads')
        raw_lanes = scene.get('road',{}).get('lanes')
        lane_by_id = {l['lane_id']:l for l in raw_lanes if isinstance(l,dict) and
                      isinstance(l.get('lane_id'),str)} if isinstance(raw_lanes,list) else {}
        for key in incoming.keys() & outgoing.keys():
            if key not in by_id or by_id[key].get('road_role') != 'bidirectional': continue
            left,right = incoming[key],outgoing[key]
            if (not isinstance(left,list) or not isinstance(right,list) or
                    any(not isinstance(l,str) for l in left+right) or set(left)&set(right)): continue
            for ids,direction in ((left,'backward'),(right,'forward')):
                for lane_id in ids:
                    if lane_id in lane_by_id and 'travel_direction' not in lane_by_id[lane_id]:
                        lane_by_id[lane_id]['travel_direction'] = direction
                        changes.append(f'{lane_id}.travel_direction')
    return changes


def topology_index(scene):
    roads = scene.get('road', {}).get('roads', [])
    lanes = scene.get('road', {}).get('lanes', [])
    road_by_id = {r.get('road_id'): r for r in roads}
    lane_by_id = {l.get('lane_id'): l for l in lanes}
    if None in road_by_id or len(road_by_id) != len(roads):
        raise ValueError('topology duplicate or missing road ID')
    if None in lane_by_id or len(lane_by_id) != len(lanes):
        raise ValueError('topology duplicate or missing lane ID')
    owners = {}
    for road in roads:
        for key in road.get('lane_ids', []):
            if key not in lane_by_id or key in owners:
                raise ValueError(f'topology lane missing or multiply owned: {key}')
            if lane_by_id[key].get('road_id', road['road_id']) != road['road_id']:
                raise ValueError(f'topology lane owner conflict: {key}')
            owners[key] = road['road_id']
    if set(owners) != set(lane_by_id):
        raise ValueError('topology contains unowned lanes')
    return road_by_id, lane_by_id, owners


class ObservationTopologyError(ValueError):
    def __init__(self, message, path, *, rule='world.topology', related_paths=(), kind='fact_conflict'):
        super().__init__(message)
        self.path, self.rule = path, rule
        self.rule_id = rule
        self.related_paths = list(related_paths)
        self.kind = kind


def declared_road_role(road):
    """Interpret schema-legal role arrays without rewriting observed facts."""
    role = road.get('road_role')
    if isinstance(role, list) and role and all(
            isinstance(value, str) and value in ('incoming', 'outgoing') for value in role):
        if len(role) == len(set(role)):
            return 'bidirectional' if len(role) == 2 else role[0]
    return role


def coordinate_convention():
    """One reference document for initial and directed World requests."""
    import json
    from _lib.paths import SKILLS
    return json.loads((SKILLS / 'references/road-coordinate-convention.json').read_text())


def arm_bearing(road):
    """Physical outward bearing from the declared reference line; unknown stays unknown."""
    role = declared_road_role(road)
    if role not in ('incoming', 'outgoing', 'bidirectional') or road.get('heading_rad') is None:
        return None
    return (finite(road['heading_rad']) + (math.pi if role == 'incoming' else 0)) % (2 * math.pi)


def validate_arm_bearings(roads, *, road_ids=None, path='/world/road/roads'):
    """Reject conflicting declared arms without merging IDs or changing evidence."""
    bearings = []
    for index, road in enumerate(roads):
        if road_ids is not None and road['road_id'] not in road_ids:
            continue
        angle = arm_bearing(road)
        if angle is None:
            continue
        for other_index, other, other_angle in bearings:
            if abs(math.atan2(math.sin(angle - other_angle), math.cos(angle - other_angle))) < .1:
                raise ObservationTopologyError(
                    f"roads occupy the same physical arm: {other}/{road['road_id']}",
                    f'{path}/{index}/heading_rad', rule='world.topology.arm_overlap',
                    related_paths=[f'{path}/{other_index}/heading_rad',
                                   f'{path}/{other_index}/road_role', f'{path}/{index}/road_role'])
        bearings.append((index, road['road_id'], angle))


def validate_observed_topology(scene):
    """Check declarations, never demand a complete executable intersection.

    Missing memberships, headings and connections are not negative facts. The
    existing execution validator below still requires them when consumed.
    """
    warnings = []

    def fail(message, path, related=()):
        raise ObservationTopologyError(message, path, related_paths=related)

    try:
        roads, lanes, owners = topology_index(scene)
    except ValueError as error:
        fail(str(error), '/world/road', ['/world/road/roads', '/world/road/lanes'])
    if scene.get('scene_type') != 'intersection':
        return {}, warnings
    edges = {}
    for index, junction in enumerate(scene.get('junctions') or []):
        root = f'/world/junctions/{index}'
        if not isinstance(junction, dict):
            fail('observed junction must be an object', root)
        memberships = {}
        for field, role in (('incoming_roads', 'incoming'), ('outgoing_roads', 'outgoing')):
            refs = junction.get(field)
            if refs is None:
                memberships[field] = None
                continue
            if not isinstance(refs, list):
                fail('observed membership must be an array', root + '/' + field)
            rows = {}
            for n, ref in enumerate(refs):
                path = f'{root}/{field}/{n}'
                if not isinstance(ref, dict) or not isinstance(ref.get('road_id'), str):
                    raise ObservationTopologyError('membership must be an object with a string road_id', path,
                        rule='world.junction.membership_shape', kind='representation_error')
                if ref['road_id'] not in roads:
                    fail('observed membership references an undefined road', path)
                key = ref['road_id']
                if key in rows:
                    fail('duplicate observed road membership', path)
                declared = declared_road_role(roads[key])
                if declared is not None and declared not in (role, 'bidirectional'):
                    fail('observed road role conflicts with membership: ' + key, path,
                         ['/world/road/roads'])
                ids = ref.get('lane_ids')
                if ids is not None and (not isinstance(ids, list) or
                        any(not isinstance(l, str) for l in ids) or len(ids) != len(set(ids)) or
                        set(ids) - set(roads[key]['lane_ids'])):
                    fail('observed lane membership references inconsistent lanes', path)
                rows[key] = ids
            memberships[field] = rows
        incoming, outgoing = memberships['incoming_roads'], memberships['outgoing_roads']
        for key in set(incoming or {}) & set(outgoing or {}):
            left, right = incoming[key], outgoing[key]
            if declared_road_role(roads[key]) not in (None, 'bidirectional'):
                fail('shared observed arm is not bidirectional: ' + key, root,
                     ['/world/road/roads'])
            if left is not None and right is not None and set(left) & set(right):
                fail('shared observed arm has overlapping incoming/outgoing lanes: ' + key,
                     root, ['/world/road/roads', '/world/road/lanes'])
            for ids, direction in ((left, 'backward'), (right, 'forward')):
                for lid in ids or []:
                    if lanes[lid].get('travel_direction') not in (None, direction):
                        fail('observed lane direction conflicts with membership: ' + lid,
                             root, ['/world/road/lanes'])
        # Only compare physical positions when both role and heading were declared.
        validate_arm_bearings(scene['road']['roads'],
                              road_ids=(None if len(scene.get('junctions') or []) == 1 else
                                        set(incoming or {}) | set(outgoing or {})))
        connections = junction.get('lane_connections')
        if connections is None:
            continue
        if not isinstance(connections, list):
            fail('observed connections must be an array', root + '/lane_connections')
        for n, connection in enumerate(connections):
            path = f'{root}/lane_connections/{n}'
            if not isinstance(connection, dict):
                fail('observed connection must be an object', path)
            a, b = connection.get('incoming_lane_id'), connection.get('outgoing_lane_id')
            for lid, field, rows in ((a, 'incoming_road_id', incoming), (b, 'outgoing_road_id', outgoing)):
                rid = connection.get(field)
                if lid not in owners or rid != owners[lid]:
                    fail('observed connection references inconsistent roads/lanes', path)
                role = declared_road_role(roads[rid])
                required_role = 'incoming' if field == 'incoming_road_id' else 'outgoing'
                if role is not None and role not in (required_role, 'bidirectional'):
                    fail('observed connection contradicts road role', path)
                if rows is not None and (rid not in rows or (rows[rid] is not None and lid not in rows[rid])):
                    fail('observed connection contradicts declared membership', path)
            if (a, b) in edges:
                fail('duplicate observed connection', path)
            edges[a, b] = connection
    return edges, warnings


def validate_topology(scene, *, observation=False):
    roads, lanes, owners = topology_index(scene)
    edges = {}
    if scene.get('scene_type') != 'intersection':
        return edges
    junctions = scene.get('junctions') or []
    if not junctions:
        raise ValueError('intersection topology missing junctions')
    for junction in junctions:
        groups = []
        for field, role in (('incoming_roads', 'incoming'), ('outgoing_roads', 'outgoing')):
            refs = junction.get(field)
            if not isinstance(refs, list) or not refs:
                raise ValueError(f'intersection topology missing {field}')
            ids = []
            for ref in refs:
                key = ref.get('road_id') if isinstance(ref, dict) else None
                if key not in roads or key in ids:
                    raise ValueError(f'intersection topology invalid {field} reference')
                if roads[key].get('road_role') not in (role,'bidirectional'):
                    raise ValueError(f'intersection road_role conflicts with {field}: {key}')
                try:
                    finite(roads[key].get('heading_rad'))
                except ValueError as error:
                    raise ValueError(f'intersection road {key} requires finite heading_rad') from error
                if not ref.get('lane_ids') or set(ref['lane_ids']) - set(roads[key]['lane_ids']):
                    raise ValueError(f'intersection lane membership conflict: {key}')
                ids.append(key)
            groups.append(set(ids))
        incoming, outgoing = groups
        if len(incoming | outgoing) < 3:
            raise ValueError('intersection topology requires at least three physical road arms')
        validate_arm_bearings(scene['road']['roads'], road_ids=incoming | outgoing,
                              path='/road/roads')
        for key in incoming | outgoing:
            if roads[key].get('road_role') == 'bidirectional' and key not in incoming & outgoing:
                raise ValueError(f'intersection bidirectional arm missing directional membership: {key}')
        for key in incoming & outgoing:
            in_lanes = set(next(r['lane_ids'] for r in junction['incoming_roads'] if r['road_id']==key))
            out_lanes = set(next(r['lane_ids'] for r in junction['outgoing_roads'] if r['road_id']==key))
            if roads[key].get('road_role') != 'bidirectional' or in_lanes & out_lanes:
                raise ValueError(f'intersection shared road {key} requires disjoint evidenced incoming/outgoing lane membership')
            if in_lanes | out_lanes != set(roads[key]['lane_ids']):
                raise ValueError(f'intersection shared road {key} has unassigned directional lanes')
            for ids, direction in ((in_lanes,'backward'),(out_lanes,'forward')):
                if any(lanes[lane].get('travel_direction') != direction for lane in ids):
                    raise ValueError(f'intersection shared road {key} lane direction conflicts with membership')
        connections = junction.get('lane_connections')
        if observation and connections is None:
            connections = []
        if not isinstance(connections, list) or (not connections and not observation):
            raise ValueError('intersection topology missing lane_connections; cannot infer connectivity from road count')
        for c in connections:
            a, b = c.get('incoming_lane_id'), c.get('outgoing_lane_id')
            if (a not in owners or b not in owners or owners[a] != c.get('incoming_road_id')
                    or owners[b] != c.get('outgoing_road_id') or owners[a] not in incoming or owners[b] not in outgoing):
                raise ValueError('intersection lane_connections reference inconsistent roads/lanes')
            if (a not in next(r['lane_ids'] for r in junction['incoming_roads'] if r['road_id']==owners[a]) or
                    b not in next(r['lane_ids'] for r in junction['outgoing_roads'] if r['road_id']==owners[b])):
                raise ValueError('intersection lane connection uses a lane against its declared direction')
            if (a, b) in edges:
                raise ValueError('duplicate intersection lane connection')
            edges[a, b] = c
    return edges


def compare_connections(scene, target):
    """Check actual peer IDs, membership and cardinal geometry before delivery."""
    if scene.get('scene_type') != 'intersection':
        return {'status': 'not_applicable'}
    validate_topology(scene)
    source_roads = scene['road']['roads']
    target_roads = target['scene']['road_network']['roads']
    source_junctions = scene['junctions']
    target_junctions = target['scene']['road_network']['junctions']
    if len(source_roads) != len(target_roads) or len(source_junctions) != len(target_junctions):
        raise ValueError('target topology object count changed')
    mapped = {r['road_id']: t for r, t in zip(source_roads, target_roads)}
    observed = {r['road_id']: r for r in source_roads}
    for j, t in zip(source_junctions, target_junctions):
        refs = list({r['road_id']:(r, observed[r['road_id']]['road_role'])
                     for field in ('incoming_roads','outgoing_roads') for r in j[field]}.values())
        if t['num_connections'] != len(refs) or len(t['cardinal_positions']) != len(refs):
            raise ValueError('target junction arm count changed')
        slots = []
        for ref, role in refs:
            road = mapped[ref['road_id']]
            endpoint = 'B' if role == 'incoming' else 'A'
            cp = road.get('connection_points', {}).get(endpoint, {})
            if set(cp.get('element_ids', [])) != {road['id'], t['id']}:
                raise ValueError('target road/junction peer connection missing')
            slot = cp.get('positions', {}).get(t['id'])
            if cp.get('positions', {}).get(road['id']) != endpoint or slot in slots:
                raise ValueError('target road endpoint or junction slot changed')
            outward = arm_bearing(observed[ref['road_id']])
            angle = {'N': 0, 'W': math.pi/2, 'S': math.pi, 'E': -math.pi/2}.get(slot)
            if angle is None or abs(math.atan2(math.sin(outward-angle), math.cos(outward-angle))) > 0.1:
                raise ValueError('target junction direction changed')
            peer = t.get('connection_points', {}).get(slot, {})
            if any(peer.get(field) != cp.get(field) for field in ('element_ids', 'positions')):
                raise ValueError('target connection is not reciprocal')
            slots.append(slot)
        if set(slots) != set(t['cardinal_positions']):
            raise ValueError('target cardinal membership changed')
    return {'status': 'passed', 'verification': 'source_topology_projected'}


def validate_actor_routes(scene, actors):
    edges = validate_topology(scene)
    roads, lanes, owners = topology_index(scene)
    for actor in actors:
        previous = None
        for point in actor.get('trajectory', []):
            current = point.get('lane_id')
            if current is None:
                continue  # A point inside a junction need not be on a road lane.
            if current not in owners:
                raise ValueError(f"actor {actor['actor_id']} references unknown lane {current}")
            if previous is not None and current != previous:
                if owners[previous] != owners[current]:
                    if (previous, current) not in edges:
                        raise ValueError(f"actor {actor['actor_id']} has no lane connection: {previous}->{current}")
                else:
                    order = roads[owners[current]]['lane_ids']
                    if abs(order.index(previous) - order.index(current)) != 1:
                        raise ValueError(f"actor {actor['actor_id']} jumps nonadjacent lanes: {previous}->{current}")
            previous = current
    return edges
