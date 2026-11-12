"""Frozen observation/execution boundary. Never changes observed facts."""
from pathlib import Path
from _lib.runtime.case_core import read_json, atomic_json, sha256

REVISION = 'observation-execution.v1'


class IdentityEvidenceGap(ValueError):
    code='identity_evidence_insufficient'
    def __init__(self,issues):
        super().__init__('Identity uncertainty still affects execution; no safe scope proof')
        self.diagnostics={**issues[0],'issues':issues,'retry_kind':'new_evidence'}


def enabled(case):
    path=Path(case)/'state/execution_policy.json'
    return path.is_file() and read_json(path).get('fact_contract_revision') == REVISION


def intersection_eligible(world):
    from _lib.runtime.routing import capability_issues
    from _lib.world.topology import validate_topology
    scene=world['world']
    if (scene.get('scene_type')!='intersection' or len(scene.get('road',{}).get('roads',[]))!=4
            or len(scene.get('junctions',[]))!=1 or capability_issues(world)):
        return False
    try: validate_topology(scene, observation=True)
    except (ValueError, KeyError, TypeError): return False
    return True


def classify_gaps(world):
    rows=[]
    from _lib.behavior.review_contract import lane_count_paths
    eligible=intersection_eligible(world)
    count_paths=lane_count_paths(world)
    for collection,key in (('questions_to_verify','question_id'),('ambiguities','ambiguity_id')):
        for item in world.get(collection,[]):
            if item.get('resolution')!='unresolved': continue
            identity='actor_identity' in item.get('impact_domains',[])
            paths=item.get('world_paths',[])
            execution=eligible and bool(paths) and all(
                p=='world.junctions[0].lane_connections' or
                p in count_paths
                for p in paths)
            rows.append(dict(rule_id='identity_uncertain' if identity else 'world.unresolved_fact',
                kind='execution_gap' if execution and not identity else 'evidence_gap', issue_id=item[key],
                field_path=paths[0] if paths else None, world_paths=paths,
                actor_ids=sorted(set(item.get('actor_ids', [])) |
                                 set((item.get('identity_binding') or {}).get('actor_ids', []))),
                road_id=(item.get('localization') or {}).get('road_id'),
                evidence=item.get('evidence',[]),owner=item.get('owner','world'),
                expected='evidenced fact or authorized execution projection',actual='unknown',
                retry_kind='new_evidence'))
    for i,j in enumerate(world['world'].get('junctions',[])):
        if not j.get('lane_connections'):
            rows.append(dict(rule_id='intersection.connections_unknown',kind='execution_gap',
                field_path=f'world.junctions[{i}].lane_connections',
                world_paths=[f'world.junctions[{i}].lane_connections'],owner='world',
                issue_ids=[r['issue_id'] for r in rows if f'world.junctions[{i}].lane_connections' in r.get('world_paths',[])],
                expected='reviewed execution connections',actual=j.get('lane_connections'),retry_kind='new_evidence'))
    return rows


def record_preplan(case, world):
    result=dict(revision=REVISION,route='defer_intersection_execution' if intersection_eligible(world) else 'observe',
                gaps=classify_gaps(world), input_hashes={p:sha256(Path(case)/p) for p in
                ('state/world_context.json','state/execution_policy.json')}, planner_sha256=sha256(__file__))
    atomic_json(Path(case)/'state/observation_plan.json',result)
    return result


def identity_exclusion(world, behavior, item):
    # A background role or absence from events does not remove a vehicle from
    # conversion and cannot discharge an unresolved identity dependency.
    from _lib.world.readiness import _opposing_scope, _observed_separator
    binding=item.get('identity_binding') or {}
    ids=binding.get('actor_ids',[]); frames=binding.get('evidence_frame_ids',[])
    actors={a['actor_id']:a for a in (behavior or {}).get('actors',[])}
    scope=_opposing_scope(world,behavior) if behavior else None
    observed={e.get('frame_id') for e in item.get('evidence',[]) if e.get('status')=='observed' and e.get('reason')}
    if (not scope or not _observed_separator(world)
            or binding.get('kind')!='physically_separated_opposing' or not ids or not frames
            or not set(ids)<=set(actors) or not set(frames)<=observed):
        return False
    for aid in ids:
        a=actors[aid]; points=a.get('trajectory',[])
        if (a.get('simulation_role')!='background' or not points
                or any(p.get('lane_id') is not None for p in points)
                or not {p.get('frame_id') for p in points}<=set(frames)):
            return False
    return True
