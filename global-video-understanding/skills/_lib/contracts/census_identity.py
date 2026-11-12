"""Evidence-bound duplicate registration repair inside combined World closure.

Only a reviewed group can collapse IDs. Raw requests and Batch observations
stay immutable; the compiler produces canonical inputs for the existing gates.
"""
import copy
import re

from _lib.contracts.v3 import ContractError


def fail(message):
    raise ContractError(message, rule_id='identity.census.merge')


def remap(value, aliases):
    """Map explicit IDs (including prose tokens), without guessing object identity."""
    if isinstance(value, str):
        return re.sub(r'\bactor_[0-9]+\b', lambda m: aliases.get(m[0], m[0]), value)
    if isinstance(value, list):
        result = [remap(v, aliases) for v in value]
        # Actor-reference arrays must remain unique. Evidence arrays are retained.
        if all(isinstance(v, str) and re.fullmatch(r'actor_[0-9]+', v) for v in result):
            return list(dict.fromkeys(result))
        return result
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            mapped = remap(key, aliases)
            updated = remap(item, aliases)
            if mapped in result and result[mapped] != updated:
                fail('Canonical identity would collide in a keyed observation')
            result[mapped] = updated
        return result
    return copy.deepcopy(value)


def canonicalize(packet, response, world, registry):
    groups = response['identity_groups']
    aliases, claimed, merged = {}, set(), {}
    old = {a['actor_id']: a for a in registry}
    world_actors = {a['actor_id']: a for a in world['actor_hypotheses']}
    frames = {f['frame_id']: f for f in packet['frames']}
    vehicles = {v['key']: v for v in response['vehicles']}
    obligations = {o['issue_id']: o for o in packet['evidence_plan']['obligations']}
    original_records = {r['issue_id']: r for r in packet['records']}

    def member_facts(members):
        # Batch may register identities absent from the initial World snapshot.
        # Registry is required; World-only facts are additional constraints.
        return [a for aid in sorted(members)
                for a in [old[aid], *([world_actors[aid]] if aid in world_actors else [])]]

    for group in groups:
        members = set(group['actor_ids'])
        if (len(members) < 2 or not members <= set(old)-{'ego'} or members & claimed):
            fail('Duplicate group must contain disjoint existing non-Ego identities')
        canonical = min(members, key=lambda aid: (frames[old[aid]['first_seen_frame_id']]['time_s'], aid))
        vehicle = vehicles.get(group['vehicle_key'])
        if (vehicle is None or vehicle['actor_id'] != canonical or vehicle['role'] != 'binding'
                or vehicle['split_from_actor_id'] is not None):
            fail('Duplicate group needs one binding vehicle with earliest canonical identity')
        if any(v['split_from_actor_id'] in members or
               v['key'] != vehicle['key'] and v['actor_id'] in members for v in response['vehicles']):
            fail('Duplicate group cannot also split or describe a separate member')
        sightings = {s['frame_id']: s for s in vehicle['sightings']}
        if len(sightings) != len(vehicle['sightings']) or len(sightings) < 2 or set(sightings)-set(frames):
            fail('Duplicate group needs unique bound cross-frame sightings')
        anchors = {a['actor_id']: a for a in group['anchor_matches']}
        if len(anchors) != len(group['anchor_matches']) or set(anchors) != members:
            fail('Duplicate proof must match every original identity anchor exactly once')
        required = set()
        for aid in members:
            first = old[aid]['first_seen_frame_id']
            anchor = anchors[aid]
            if (anchor['frame_id'] != first or first not in sightings
                    or anchor['time_s'] != frames[first]['time_s']
                    or old[aid].get('hypothesized_lane') is not None
                    and sightings[first]['lane_id'] != old[aid]['hypothesized_lane']):
                fail('Duplicate proof changed an original frame/time/lane anchor')
            required.add(first)
        bound = [b for b in response['bindings'] if b['vehicle_key'] == vehicle['key']]
        if not bound or any(b['outcome'] != 'existing' for b in bound):
            fail('Duplicate group must bind an assigned existing-identity issue')
        for decision in bound:
            row = original_records.get(decision['issue_id'])
            if row is None or not members & set(row['candidate_actor_ids']):
                fail('Duplicate group is outside its assigned identity hypotheses')
            required.add(row['frame_id'])
            for aid in members:
                link = obligations[row['issue_id']]['links'].get(aid)
                if not link:
                    fail('Duplicate group omitted a frozen member link')
                required.update(link)
        if not required <= set(sightings):
            fail('Duplicate proof omits frozen transition sightings')
        lo = min(frames[f]['time_s'] for f in sightings)
        hi = max(frames[f]['time_s'] for f in sightings)
        if not {fid for fid, f in frames.items() if lo <= f['time_s'] <= hi} <= set(sightings):
            fail('Duplicate proof skips a supplied transition frame')
        if any(members & set(s['distinct_from_actor_ids']) for s in sightings.values()):
            fail('Duplicate proof contradicts co-visible distinct identities')
        # Same-Batch object IDs are the only deterministic co-visibility identity.
        slots = {}
        for row in packet['confirmed_objects']:
            if row['actor_id'] in members:
                slots.setdefault((row['batch_id'], row['frame_id']), set()).add(row['object_id'])
        for decision in bound:
            row = original_records[decision['issue_id']]
            slots.setdefault((row['batch_id'], row['frame_id']), set()).add(row['object_id'])
        if any(len(objects) > 1 for objects in slots.values()):
            fail('Duplicate group contains distinct co-visible census objects')
        for collection in ('questions_to_verify', 'ambiguities'):
            for item in world.get(collection, []):
                ids = set(item.get('actor_ids', [])) | set((item.get('identity_binding') or {}).get('actor_ids', []))
                if len(ids & members) > 1 and item.get('resolution') == 'resolved':
                    fail('Duplicate group challenges an already resolved identity fact')
        # Preserve structured known facts; prose appearance may be corrected by
        # this explicit visual proof, unlike an ordinary same-ID association.
        originals = member_facts(members)
        for field in ('actor_type', 'road_reference', 'is_static'):
            values = {str(a[field]) for a in originals if a.get(field) is not None}
            if len(values) > 1:
                fail('Duplicate group conflicts with known ' + field)
        from _lib.contracts.actors import vehicle_class_hint
        classes = {vehicle_class_hint(a) for a in originals} - {None, 'unknown'}
        if len(classes) > 1 or classes and vehicle['vehicle_class'] not in classes:
            fail('Duplicate group changes a known vehicle class')
        claimed.update(members)
        aliases.update({aid: canonical for aid in members if aid != canonical})
        merged[canonical] = members
    if not aliases:
        return packet, response, world, registry, {}

    p, r = copy.deepcopy(packet), copy.deepcopy(response)
    for row in p['records']:
        row['candidate_actor_ids'] = remap(row['candidate_actor_ids'], aliases)
    p['confirmed_sightings'] = {fid: remap(ids, aliases) for fid, ids in p['confirmed_sightings'].items()}
    for row in p['confirmed_objects']:
        row['actor_id'] = aliases.get(row['actor_id'], row['actor_id'])
    plan = p['evidence_plan']
    plan['review_actor_ids'] = remap(plan['review_actor_ids'], aliases)
    for obligation in plan['obligations']:
        links = {}
        for aid, chain in obligation['links'].items():
            links.setdefault(aliases.get(aid, aid), set()).update(chain)
        obligation['links'] = {aid: sorted(ids, key=lambda f: frames[f]['time_s']) for aid, ids in links.items()}
        obligation['comparison_actor_ids'] = remap(obligation['comparison_actor_ids'], aliases)
    for vehicle in r['vehicles']:
        for sight in vehicle['sightings']:
            sight['distinct_from_actor_ids'] = remap(sight['distinct_from_actor_ids'], aliases)
    for decision in r['bindings']:
        for ref in decision['candidate_refutations']:
            ref['actor_id'] = aliases.get(ref['actor_id'], ref['actor_id'])
            for sight in ref['subject_sightings']:
                sight['distinct_from_actor_ids'] = remap(sight['distinct_from_actor_ids'], aliases)

    w = copy.deepcopy(world)
    w['actor_hypotheses'] = [a for a in w['actor_hypotheses'] if a['actor_id'] not in aliases]
    field_evidence = w.pop('field_evidence')
    w = remap(w, aliases)
    # The reviewed aliases can converge field paths with different historical
    # evidence. Carry every witness into the canonical field; ordinary keyed
    # observations still use remap's collision rejection.
    w['field_evidence'] = {}
    for path, evidence in field_evidence.items():
        target = w['field_evidence'].setdefault(remap(path, aliases), [])
        for witness in remap(evidence, aliases):
            if witness not in target:
                target.append(witness)
    reg = remap([a for a in registry if a['actor_id'] not in aliases], aliases)
    for canonical, members in merged.items():
        descriptor = next(v for v in r['vehicles'] if v['actor_id'] == canonical)
        originals = member_facts(members)
        for collection in (reg, w['actor_hypotheses']):
            target = next((a for a in collection if a['actor_id'] == canonical), None)
            if target is None:
                # The proven canonical identity can be Batch-only. Materialize
                # it from Registry before applying the same fact-preserving merge.
                target = copy.deepcopy(old[canonical])
                collection.append(target)
            target['identity_description'] = descriptor['identity_description']
            target['color'] = copy.deepcopy(descriptor['color'])
            if descriptor['vehicle_class'] != 'unknown':
                first=min(descriptor['sightings'],key=lambda s:s['time_s'])
                target['vehicle_class']=dict(status='observed',value=descriptor['vehicle_class'],
                    frame_id=first['frame_id'],time_s=first['time_s'],reason=first['features'])
            for field in ('road_reference', 'is_static'):
                known = next((a[field] for a in originals if a.get(field) is not None), None)
                if known is not None:
                    target[field] = copy.deepcopy(known)
            for source in originals:
                for evidence in source.get('evidence', []):
                    mapped = remap(evidence, aliases)
                    if mapped not in target.setdefault('evidence', []):
                        target['evidence'].append(mapped)
    # Do not silently mark prose questions resolved. They remain explicit World
    # questions until a structured answer covers them; references are canonical.
    return p, r, w, reg, aliases


def resolve_questions(original, world, packet, response, aliases):
    """Discharge only structured pure identity questions covered by accepted proof."""
    from itertools import combinations
    separated = {}
    for vehicle in response['vehicles']:
        aid=vehicle['actor_id']
        if aid is None or vehicle['role']!='binding':
            continue
        for sight in vehicle['sightings']:
            confirmed=set(packet['confirmed_sightings'].get(sight['frame_id'],[]))
            for other in set(sight['distinct_from_actor_ids']) & confirmed:
                if other!=aid:
                    separated[frozenset((aid,other))]=dict(status='observed',frame_id=sight['frame_id'],
                        time_s=sight['time_s'],reason=sight['features'])
    resolved=[]
    for collection,key in (('questions_to_verify','question_id'),('ambiguities','ambiguity_id')):
        originals={item[key]:item for item in original.get(collection,[])}
        for item in world.get(collection,[]):
            prior=originals[item[key]];binding=prior.get('identity_binding') or {}
            if (prior.get('resolution')!='unresolved' or prior.get('owner') not in {None,'world'}
                    or set(prior.get('impact_domains',[]))!={'actor_identity'} or prior.get('world_paths')
                    or binding.get('kind')!='unknown'):
                continue
            ids=set(prior.get('actor_ids',[])) | set(binding.get('actor_ids',[]))
            if len(ids)<2:
                continue
            mapped={aliases.get(a,a) for a in ids}
            evidence=[]
            if len(mapped)==1:
                # At least two original IDs collapsed only after the merge proof.
                aid=next(iter(mapped))
                vehicle=next(v for v in response['vehicles'] if v['actor_id']==aid)
                evidence=[dict(status='observed',frame_id=s['frame_id'],time_s=s['time_s'],reason=s['features'])
                          for s in vehicle['sightings']]
            elif len(mapped)==len(ids) and all(frozenset(pair) in separated for pair in combinations(mapped,2)):
                evidence=[separated[frozenset(pair)] for pair in combinations(sorted(mapped),2)]
            if evidence:
                item['resolution']='resolved'
                item.pop('identity_binding',None)
                item['evidence']=evidence
                resolved.append(item[key])
    return sorted(resolved)
