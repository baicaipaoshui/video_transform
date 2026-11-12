"""Interpret Actor categories without rewriting frozen identity fields."""
import json
import re
from functools import lru_cache
from _lib.paths import SKILLS


@lru_cache(maxsize=1)
def _legacy_vehicle_rules():
    schema = json.loads((SKILLS / 'schemas/actor_fields.schema.json').read_text())
    variants = schema['$defs']['legacy_vehicle_type']['anyOf']
    return set(variants[0]['enum']), re.compile(variants[1]['pattern'])


def is_vehicle(actor):
    if not isinstance(actor, dict):
        return False
    kind = str(actor.get('actor_type') or '').strip().lower()
    if kind == 'vehicle_group':
        return False
    if actor.get('actor_id') == 'ego' and kind in {'', 'unknown'}:
        return True
    aliases, pattern = _legacy_vehicle_rules()
    return kind == 'vehicle' or kind in aliases or bool(pattern.fullmatch(kind))


def vehicle_class_hint(actor):
    """Specific observed class wins; legacy broad type is only a last hint."""
    for key in ('vehicle_class', 'subtype'):
        value = actor.get(key)
        if isinstance(value, dict):
            value = None if value.get('status') == 'unknown' else value.get('value')
        if isinstance(value, str) and value.strip().lower() not in {'', 'unknown'}:
            return value.strip().lower()
    kind = str(actor.get('actor_type') or '').strip().lower()
    if kind in {'vehicle', 'unknown', 'ego_vehicle'}:
        return None
    return kind.removeprefix('vehicle_') if is_vehicle(actor) else None
