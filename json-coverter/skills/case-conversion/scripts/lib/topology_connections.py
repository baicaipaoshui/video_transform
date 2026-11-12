"""Encode explicit World road arms as bidirectional MELA connection records."""
import math
from builders import uid


def connect_junctions(source, roads, junctions, fallbacks=None):
    by_source = {r['road_id']: t for r, t in zip(source['road']['roads'], roads)}
    source_roads = {r['road_id']: r for r in source['road']['roads']}
    labels = ('N', 'W', 'S', 'E')  # frozen ego x forward, y left
    for observed, target in zip(source['junctions'], junctions):
        slots = []
        seen_roads = set()
        for field, role in (('incoming_roads', 'incoming'), ('outgoing_roads', 'outgoing')):
            for ref in observed[field]:
                if ref['road_id'] in seen_roads: continue
                seen_roads.add(ref['road_id'])
                road = by_source[ref['road_id']]
                role = source_roads[ref['road_id']]['road_role']
                heading = source_roads[ref['road_id']]['heading_rad']
                heading = heading.get('value') if isinstance(heading, dict) else heading
                angle = heading + (math.pi if role == 'incoming' else 0)
                index = round(angle/(math.pi/2))
                if abs(angle-index*math.pi/2) > 0.1:
                    # Snap to nearest cardinal
                    best_index = round(angle / (math.pi / 2))
                    index = best_index
                    if fallbacks is not None:
                        fallbacks.append({'field': f'topology.{ref["road_id"]}.heading',
                                          'value': index * math.pi / 2,
                                          'reason': 'topology_arm_snap'})
                slot = labels[index % 4]
                if slot in slots:
                    # Duplicate arm — skip this road
                    if fallbacks is not None:
                        fallbacks.append({'field': f'topology.{ref["road_id"]}.slot',
                                          'value': slot,
                                          'reason': 'topology_duplicate_arm_skipped'})
                    continue
                slots.append(slot)
                endpoint = 'B' if role == 'incoming' else 'A'
                if endpoint in road['connection_points']:
                    # Endpoint already connected — skip
                    if fallbacks is not None:
                        fallbacks.append({'field': f'topology.{ref["road_id"]}.endpoint',
                                          'value': endpoint,
                                          'reason': 'topology_endpoint_already_connected'})
                    continue
                connection = {'id': uid(), 'element_ids': [road['id'], target['id']],
                              'positions': {road['id']: endpoint, target['id']: slot}}
                road['connection_points'][endpoint] = connection
                # The bundled validator registers every deserialized object ID;
                # two serialized endpoint records must have distinct IDs. Peer
                # element_ids/positions carry the reciprocal connectivity.
                target['connection_points'][slot] = {**connection, 'id': uid()}
        # Pad or trim arm count to 3-4
        if len(slots) < 3:
            pad_labels = [l for l in labels if l not in slots]
            while len(slots) < 3 and pad_labels:
                slots.append(pad_labels.pop(0))
            if fallbacks is not None:
                fallbacks.append({'field': 'topology.junction.arm_count',
                                  'value': len(slots),
                                  'reason': 'topology_arm_pad_to_3'})
        elif len(slots) > 4:
            removed = slots[4:]
            slots = slots[:4]
            if fallbacks is not None:
                fallbacks.append({'field': 'topology.junction.arm_count',
                                  'value': len(slots),
                                  'reason': f'topology_arm_trim_to_4; removed={removed}'})
        target['num_connections'] = len(slots)
        target['cardinal_positions'] = slots
