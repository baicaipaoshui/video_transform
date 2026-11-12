"""Pure entry aliases shared by observation and execution consumers.

Crossing/turning compound events are deliberately not entry aliases.
Actor and boundary-evidence validation remains owned by the v3 contract.
"""
ENTRY_TYPES = frozenset({'intersection_entry', 'ego_intersection_entry', 'vehicle_intersection_entry'})


def execution_primitive(kind):
    return 'intersection_entry' if kind in ENTRY_TYPES else kind


def ego_entry_intervals(analysis):
    """Read optional actor-bound entry intervals without requiring a scenario."""
    import math
    result = []
    for event in analysis.get('events', []):
        if event.get('event_type') not in ENTRY_TYPES or 'ego' not in event.get('involved_actor_ids', []):
            continue
        interval = event.get('time_range_s', event.get('time_interval_s'))
        if interval is None:
            continue
        if isinstance(interval, dict):
            interval = [interval.get('start'), interval.get('end')]
        if (not isinstance(interval, (list, tuple)) or len(interval) != 2
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in interval)
                or not 0 <= interval[0] <= interval[1]):
            raise ValueError('entry.invalid_interval: ' + str(event.get('event_id')))
        result.append((event.get('event_id'), float(interval[0]), float(interval[1])))
    return result
