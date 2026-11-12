"""Schema-owned metadata aliases; no interpretation of observation prose."""
import copy
import json
from _lib.paths import SKILLS


def definition():
    document = json.loads((SKILLS/'schemas/world_context.schema.json').read_text())
    field = document['$defs']['question']['properties']['impact_domains']
    aliases = field['x-legacy-value-aliases']
    values = set(field['items']['enum'])
    if any(k in values or v not in values for k, v in aliases.items()):
        from _lib.contracts.fidelity import ContractCompatibilityError
        raise ContractCompatibilityError('impact_domains/aliases', 'legacy values mapped to authoritative enum', aliases)
    return field


def normalize(source, *, fill_missing=False):
    value = copy.deepcopy(source)
    repairs = []
    if not isinstance(value, dict):
        return value, repairs
    field = definition()
    aliases, valid = field['x-legacy-value-aliases'], set(field['items']['enum'])
    for collection in ('questions_to_verify', 'ambiguities'):
        rows = value.get(collection, [])
        if not isinstance(rows, list):
            continue
        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            previous = row.get('impact_domains')
            missing = 'impact_domains' not in row
            if missing and fill_missing:
                subject = row.get('subject_field')
                if not isinstance(subject, str):
                    continue
                domain = aliases.get(subject, subject)
                if domain not in valid:
                    continue
                result = [domain]
            elif isinstance(previous, list) and all(isinstance(v, str) for v in previous) and any(v in aliases for v in previous):
                result = list(dict.fromkeys(aliases.get(v, v) for v in previous))
            else:
                continue
            row['impact_domains'] = result
            repairs.append(dict(path=f'/{collection}/{i}/impact_domains', before=previous,
                value=result, was_missing=missing, rule='subject_field.impact_domains' if missing else 'schema.impact_domain_alias',
                adapter_version=field['x-adapter-version']))
    return value, repairs
