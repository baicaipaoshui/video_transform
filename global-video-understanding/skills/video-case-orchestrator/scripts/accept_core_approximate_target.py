#!/usr/bin/env python3
"""Accept an isolated core approximation without changing strict Case delivery."""
import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from _lib.behavior.lane_change import from_analysis
from _lib.contracts import (census_adjudication, core_identity_hypothesis,
                            semantic_gap, semantic_motion)
from _lib.contracts.core_lane_continuity import validate_analysis_motion
from _lib.contracts.motion_witnesses import POLICY_KEY
from _lib.contracts.conversion_policy import digest, validate as validate_policy
from _lib.delivery.checks import validate_target
from _lib.delivery.converter import converter_fingerprint, mela_valid
from _lib.paths import SCENARIO_ROOT
from _lib.runtime.case_core import atomic_json, read_json, sha256
from _lib.runtime.conversion_work import validate_committed
from _lib.runtime.fact_versions import current_facts
from _lib.runtime.orchestrator import validate_analysis

REVISION = 'core-approximate-delivery.v1'


class ImplementationVersionIncompatible(ValueError):
    code = 'implementation_version_incompatible'

    def __init__(self, frozen, current):
        self.frozen_sha256, self.current_sha256 = frozen, current
        super().__init__(f'{self.code}: Original Converter implementation changed since generation; '
                         f'frozen={frozen}, current={current}. Historical artifacts remain unchanged.')


def _inside(path, root):
    path = Path(path).resolve(strict=True)
    if not path.is_relative_to(root) or path.is_symlink():
        raise ValueError('Approximate delivery path leaves its isolation root')
    return path


def replay_dispositions(root, case, projection, view):
    """Rebuild execution choices from the bound candidate, never from risk labels."""
    from _lib.runtime import core_approximate_delivery as producer

    modern = projection.get('revision') in {'core-approximate-probe.v2',
                                            producer.PROJECTION_REVISION}
    if not modern and not any(r['handling'] == 'single_representative'
               for r in projection['observation_risks']):
        return None  # Preserve the existing non-representative receipt contract.
    if 'census_dispositions_digest' not in projection:
        raise ValueError('Representative disposition has no frozen source binding')
    candidates = [Path(p) for p, h in projection['source_hashes'].items()
                  if h == projection['raw_candidate_sha256']]
    if len(candidates) != 1:
        raise ValueError('Execution disposition candidate binding is ambiguous')
    candidate = _inside(candidates[0], case)
    adoption = projection.get('behavior_archive_adoption')
    source = SCENARIO_ROOT/'video_extractor/cases'/case.name
    frames = producer.evidence_frames(case)
    if producer._source_hashes(case, view, candidate, frames,
            adoption_source=source if adoption else None) != projection['source_hashes']:
        raise ValueError('Execution disposition frozen source set changed')
    behavior, review = producer.split(read_json(candidate))
    behavior, normalizations = producer.prepare_source_behavior(source, view, behavior, frames)
    if (digest(behavior) != projection['normalized_behavior_digest']
            or normalizations != projection['normalizations']):
        raise ValueError('Execution disposition Behavior normalization changed')
    saved = census_adjudication.proof(case)
    pending = producer.behavior_census_scope.pending(case, saved)
    dispositions = (producer.validated_dispositions(case, behavior, pending, review, frames)
                    if modern else producer.census_execution.decisions(
                        case, behavior, pending, review, frames=frames))
    if not dispositions or dispositions['sha256'] != projection['census_dispositions_digest']:
        raise ValueError('Execution disposition source, version or mapping changed')
    risks = producer.disposition_risks(pending, review, dispositions)
    if risks != projection['observation_risks']:
        raise ValueError('Execution disposition observation or review binding changed')
    return behavior, frames, dispositions, review


def validate_representatives(world, behavior, frames, dispositions, analysis, target, decision, result):
    """Require the original scope projection and an executable, preserved proxy."""
    from _lib.contracts import peripheral_uncertainty
    from _lib.contracts.core_scene_scope import required_actors
    from _lib.runtime import execution_scope
    from _lib.behavior.lane_change import actor_name

    choices = [r for r in dispositions['choices'] if r['handling'] == 'single_representative']
    if not choices:
        return []
    # Replay source/identity/interaction constraints before testing presence.
    peripheral = peripheral_uncertainty.validate_projection(world, behavior, frames, dispositions)
    actors = [analysis['ego'], *analysis['actors']]
    ids = [a['actor_id'] for a in actors]
    required = required_actors(world, behavior, ['ego'])
    if not required <= set(ids):
        raise ValueError('Representative projection is missing a core interaction dependency')
    for row in choices:
        if ids.count(row['actor_id']) != 1:
            raise ValueError('Required execution representative missing or duplicated in core Analysis: '
                             + row['actor_id'] + ' for ' + row['issue_id'])
    projected, representatives = execution_scope.append_representatives(
        copy.deepcopy(world), copy.deepcopy(behavior), dispositions['choices'], peripheral)
    # The core Builder uses observation_contract=True and preserves these
    # evidence-rich Actor fields, including explicit unknown speed values.
    expected = {a['actor_id']: a for a in projected['actors']}
    actual = {a['actor_id']: a for a in actors}
    for aid in {r['actor_id'] for r in choices}:
        if any(actual[aid].get(k) != v for k, v in expected[aid].items()):
            raise ValueError('Execution representative source Actor or trajectory changed: ' + aid)
        if (sum(p['actor_id'] == aid for p in decision.get('plans', [])) != 1
                or sum(v['name'] == actor_name(aid) for v in target['scenario']['vehicles']) != 1
                or aid in result.get('excluded_actor_ids', [])):
            raise ValueError('Execution representative numeric or Target mapping is missing: ' + aid)
    return representatives


def validate_disposition_projection(case, projection, full, analysis, world, behavior,
                                    dispositions, frames):
    """Replay whole-limit consumption, including mixed and Actor-shared issues."""
    from _lib.runtime import core_approximate_delivery as producer
    from _lib.contracts.core_scene_scope import required_actors
    from _lib.contracts.core_projection_sources import (execution_required_actors,
                                                         required_background_actors)
    from _lib.behavior.input_binding import current_evidence_timeline, timeline_enabled
    required = required_actors(world, behavior, ['ego']) | {
        r['actor_id'] for r in dispositions['choices'] if r['handling'] == 'single_representative'}
    timeline = current_evidence_timeline(case) if timeline_enabled(case) else None
    # The producer adds every background Actor that lacks a replayable
    # exclusion proof before it builds the core projection.  Acceptance must
    # replay the same selection, including retained single-frame vehicles.
    required.update(required_background_actors(world, behavior, frames, timeline))
    if 'retained_execution_relevant_actor_ids' in projection:
        retained = (execution_required_actors(world, behavior, full,
            timeline) if timeline is not None else set())
        if projection['retained_execution_relevant_actor_ids'] != sorted(retained):
            raise ValueError('Execution-relevant Actor source retention changed')
        required.update(retained)
    if sorted(required) != projection['core_actor_ids']:
        raise ValueError('Core disposition required Actor selection changed')
    deferrals = producer.disposition_deferrals(full, dispositions)
    if projection.get('disposition_limitations') != deferrals:
        raise ValueError('Core limitation dispositions differ from verified source proof')
    for row in deferrals:
        item = full['limitations'][row['source_limitation_index']]
        if item not in behavior['semantic_limitations']:
            raise ValueError('Disposed limitation differs from its source Behavior')
    expected, deferred, issues = producer._projection(full, required,
        {r['issue_id']: r['observation'] for r in projection['observation_risks']},
        projection['identity_hypotheses'], projection['observation_only_identity_deferrals'],
        dispositions=dispositions)
    context = core_identity_hypothesis.execution_context(projection['identity_hypotheses'],
        projection['observation_only_identity_deferrals'])
    if context is not None:
        expected.setdefault('metadata', {})['core_identity_execution_context'] = context
    actual = copy.deepcopy(analysis)
    from _lib.contracts.core_projection_sources import ANALYSIS_BINDING_KEY
    (actual.get('metadata') or {}).pop(ANALYSIS_BINDING_KEY, None)
    if (expected != actual or projection['deferred_limitations'] != deferred
            or projection['deferred_execution_issues'] != issues):
        raise ValueError('Core execution projection differs from verified issue dispositions')


def validate_v3_projection_sources(case, projection, replay, full, analysis):
    """Independently replay the v3 source and core-event receipt at Target acceptance."""
    if projection['revision'] != 'core-approximate-probe.v3':
        return
    if replay is None or projection.get('source_projection_disposition') is None:
        raise ValueError('Current core projection lacks its source disposition')
    behavior, _, _, review = replay
    validate_analysis_motion(case, behavior, analysis, projection_source=dict(
        full_analysis=full, disposition=projection['source_projection_disposition'],
        review=review, representatives=projection.get('representative_actors', [])))


def prepare(root, case):
    root = Path(root).resolve(strict=True)
    case = _inside(case, root)
    if case.parent != root/'cases':
        raise ValueError('Expected one isolated Case under isolation-root/cases')
    output = root/'approximate_output'
    target = _inside(output/'target.json', root)
    result_path = _inside(output/'conversion_result.json', root)
    projection_path = _inside(root/'inputs/projection_receipt.json', root)
    projection = read_json(projection_path)
    modern = projection.get('revision') in {'core-approximate-probe.v2',
                                            'core-approximate-probe.v3'}
    if (projection.get('revision') not in {'core-approximate-probe.v1',
                                           'core-approximate-probe.v2',
                                           'core-approximate-probe.v3'}
            or projection.get('protected_inputs_unchanged') is not True
            or projection.get('approximate_target_generated') is not False
            or projection.get('approximate_acceptance') != 'not_run'
            or projection.get('strict_completion') != 'not_attempted_or_modified'
            or not projection.get('source_hashes')):
        raise ValueError('Core projection receipt is not the frozen pre-delivery record')
    for name, expected in projection['source_hashes'].items():
        path = Path(name)
        if not path.is_absolute() or not path.is_file() or sha256(path) != expected:
            raise ValueError('Frozen projection source changed: ' + name)
    view = current_facts(case, expected_version=projection['fact_version'])
    if census_adjudication.proof(case)['sha256'] != projection['census_certificate_digest']:
        raise ValueError('Census certificate differs from core projection')
    analysis_path = _inside(case/'final/analysis.json', root)
    if sha256(analysis_path) != projection['core_analysis_sha256']:
        raise ValueError('Core Analysis changed since projection')
    analysis = read_json(analysis_path)
    validate_analysis(copy.deepcopy(analysis))
    actor_ids = sorted([analysis['ego']['actor_id'], *(a['actor_id'] for a in analysis['actors'])])
    if actor_ids != projection['core_actor_ids'] or set(actor_ids) & set(projection['omitted_actor_ids']):
        raise ValueError('Core or omitted Actor selection changed')
    lane_change = from_analysis(analysis)
    if lane_change is None or vars(lane_change) != projection['lane_change']:
        raise ValueError('Evidence-bound core lane change changed')
    risks = projection['observation_risks']
    if (not isinstance(risks, list) or not risks and not modern
            or len({r['issue_id'] for r in risks}) != len(risks)
            or any(r['issue_id'] != r['observation']['issue_id']
                   or (r.get('review') is None and not modern)
                   or (r.get('review') is not None and r['issue_id'] != r['review']['issue_id'])
                   or (not modern and r['handling'] not in {'needs_evidence', 'observation_only_unknown', 'single_representative'})
                   for r in risks)
            or sorted(r['issue_id'] for r in risks if r['handling'] == 'needs_evidence')
            != sorted(projection['unresolved_issue_ids'])
            or not projection.get('fidelity_loss') or not projection.get('use_restrictions')):
        raise ValueError('Unresolved observation and fidelity-loss receipt changed')
    replay = replay_dispositions(root, case, projection, view)
    representatives = []
    if replay:
        behavior, frames, dispositions, review = replay
        representatives = validate_representatives(
            view.world, behavior, frames, dispositions, analysis, read_json(target),
            read_json(case/'state/conversion_semantic_gap.json'), read_json(result_path))
        if projection.get('representative_actors', []) != representatives:
            raise ValueError('Representative projection audit differs from frozen execution mapping')
    full = None
    from _lib.behavior.source_revision import KEY, QUALITATIVE
    source_limitations = [row for row in analysis.get('limitations', [])
                          if KEY in row or QUALITATIVE in row]
    if 'identity_hypotheses' in projection:
        full_path = _inside(root/'inputs/full_observation_analysis.json', root)
        if sha256(full_path) != projection['full_analysis_sha256']:
            raise ValueError('Full observation Analysis changed since projection')
        full = read_json(full_path)
        expected = core_identity_hypothesis.select(
            case, view.world, view.registry, full, risks,
            census_adjudication.proof(case), actor_ids)
        if projection['identity_hypotheses'] != expected:
            raise ValueError('Core identity hypothesis differs from frozen evidence')
        deferrals = core_identity_hypothesis.observation_only_deferrals(full, risks)
        if projection.get('observation_only_identity_deferrals') != deferrals:
            raise ValueError('Observation-only identity deferral differs from frozen scope')
        context = analysis.get('metadata', {}).get('core_identity_execution_context')
        if 'identity_execution_context_sha256' in projection:
            expected_context = core_identity_hypothesis.execution_context(expected, deferrals)
            if (context != expected_context
                    or digest(context) != projection['identity_execution_context_sha256']):
                raise ValueError('Identity execution context differs from frozen evidence')
        elif context is not None:
            raise ValueError('Identity execution context has no frozen receipt binding')
        for row in [*expected, *deferrals]:
            source = full['limitations'][row['source_limitation_index']]
            if source in analysis.get('limitations', []):
                raise ValueError('Core identity limitation remains in execution Analysis')
    if modern:
        if 'identity_hypotheses' not in projection:
            raise ValueError('Core disposition projection lacks its source Analysis binding')
        validate_disposition_projection(case, projection, full, analysis, view.world,
                                        behavior, dispositions, frames)
    validate_v3_projection_sources(case, projection, replay, full, analysis)
    request = read_json(_inside(case/'state/conversion_semantic_gap.request.json', root))
    witness_revision = request.get('policy', {}).get(POLICY_KEY)
    rebuilt = semantic_motion.request(analysis, sha256(analysis_path),
                                      witness_revision=witness_revision)
    if (request != rebuilt
            or request['request_sha256'] != digest({k: v for k, v in request.items()
                                                    if k != 'request_sha256'})
            or len(request['evidence']) != projection['semantic_evidence_count']
            or [row['actor_id'] for row in request['eligible']]
            != projection['semantic_eligible_actor_ids']):
        raise ValueError('Frozen conversion request differs from current core facts')
    decision_path = _inside(case/'state/conversion_semantic_gap.json', root)
    decision = read_json(decision_path)
    if semantic_gap.validate(analysis, sha256(analysis_path), decision) != decision:
        raise ValueError('Numeric decision differs from frozen Analysis')
    validate_committed(case, request, decision)
    result = read_json(result_path)
    if (result.get('status') not in {'done', 'done_degraded'}
            or result.get('workflow_contract') != 'video-case-world-behavior'
            or result.get('input_sha256') != sha256(analysis_path)
            or result.get('target_sha256') != sha256(target)
            or result.get('output_path') != str(target)
            or result.get('semantic_gap') != decision):
        raise ValueError('Original Converter result is not bound to this Target')
    parameters = read_json(case/'case.json')['parameters']
    policy = validate_policy(result.get('conversion_policy'),
                             input_sha256=sha256(analysis_path),
                             scene_type=analysis['scene_type'],
                             road_source=parameters['road_source'])
    if (result.get('policy_sha256') != policy['policy_sha256']
            or policy['case_id'] != case.name
            or policy['parameters_sha256'] != digest(parameters)
            or read_json(_inside(case/'state/conversion_policies'/
                                 f"{policy['policy_sha256']}.json", root)) != policy):
        raise ValueError('Frozen Converter policy or Case parameters changed')
    script = (SCENARIO_ROOT/'json-coverter/skills/case-conversion/scripts/scenes'/
              analysis['scene_type']/'convert.py')
    current_fingerprint = converter_fingerprint(script)
    if result.get('converter_sha256') != current_fingerprint:
        raise ImplementationVersionIncompatible(result.get('converter_sha256'), current_fingerprint)
    if not mela_valid(target):
        raise ValueError('Target schema validation failed')
    checks = validate_target(analysis, read_json(target), parameters['road_source'], result)
    if result.get('delivery_checks') != checks:
        raise ValueError('Original Converter checks differ on replay')
    progress = read_json(case/'state/progress.json')
    if progress.get('status') in {'done', 'done_degraded'} or progress.get('artifacts'):
        raise ValueError('Approximation cannot be accepted as strict Case completion')
    manifest = {
        'revision': REVISION, 'delivery_level': 'core_scene_approximate',
        'strict_validate_completed': 'not_claimed', 'fact_version': view.version_id,
        'case_id': case.name, 'core_actor_ids': actor_ids,
        'omitted_actor_ids': projection['omitted_actor_ids'],
        'observation_risks': risks,
        'unresolved_issue_ids': projection['unresolved_issue_ids'],
        'fidelity_loss': projection['fidelity_loss'],
        'use_restrictions': projection['use_restrictions'],
        'simulation_assumptions': {
            'builder_defaults': projection['simulation_assumptions']['builder_defaults'],
            'converter_fallbacks': result.get('fallbacks', []),
            'numeric_plans': decision.get('plans', []),
        },
        'evidence_sha256': {
            'projection_receipt': sha256(projection_path),
            'probe_request': projection['semantic_request_sha256'],
            'census_certificate': projection['census_certificate_digest'],
            'source_files': projection['source_hashes'],
            'analysis': sha256(analysis_path), 'request': request['request_sha256'],
            'numeric_decision': sha256(decision_path),
            'conversion_result': sha256(result_path), 'target': sha256(target),
            **({'identity_execution_context': projection['identity_execution_context_sha256']}
               if 'identity_execution_context_sha256' in projection else {}),
        },
        'checks': checks,
        'request_binding': ('probe_and_frozen_request_match'
                            if projection['semantic_request_sha256'] == request['request_sha256']
                            else 'probe_request_hash_historical; frozen_request_rebuilt_from_unchanged_analysis'),
    }
    if source_limitations:
        manifest['source_revision_limitations'] = source_limitations
    if 'identity_hypotheses' in projection:
        manifest['identity_hypotheses'] = projection['identity_hypotheses']
        manifest['observation_only_identity_deferrals'] = projection.get(
            'observation_only_identity_deferrals', [])
        manifest['unselected_identity_explanations'] = [
            {'issue_ids': row['issue_ids'], 'candidate_actor_ids': row['unselected_actor_ids'],
             'explanations': row.get('unselected_explanations', []),
             'status': 'unresolved; not adopted for this execution projection'}
            for row in projection['identity_hypotheses']]
    if representatives:
        manifest['representative_actors'] = representatives
        manifest['evidence_sha256']['census_dispositions'] = dispositions['sha256']
    if modern:
        manifest['disposition_limitations'] = projection['disposition_limitations']
        manifest['evidence_sha256']['census_dispositions'] = dispositions['sha256']
    report = ('# Core scene approximate delivery\n\n'
              'Delivery level: core_scene_approximate; strict validate_completed: not claimed.\n\n'
              f"Fact version: {view.version_id}; Target SHA256: {sha256(target)}.\n\n"
              f"Request binding: {manifest['request_binding']}; probe request SHA256: "
              f"{projection['semantic_request_sha256']}; frozen request SHA256: "
              f"{request['request_sha256']}.\n\n"
              f"Core Actors: {', '.join(actor_ids)}.\n\n"
              f"Omitted Actors: {', '.join(projection['omitted_actor_ids'])}.\n\n"
              'Unresolved and observation-only risks (not declared irrelevant):\n\n'
              + ''.join(f"- {r['issue_id']}: {r['handling']}\n" for r in risks)
              + '\nFidelity loss: ' + projection['fidelity_loss'] + '\n\n'
              + 'Use restrictions:\n\n'
              + ''.join(f'- {item}\n' for item in projection['use_restrictions'])
              + (('\nIdentity hypotheses (not formal findings):\n\n'
                  + ''.join(f"- {row['selected_actor_id']} for {', '.join(row['issue_ids'])}; "
                            f"alternatives: {', '.join(row['unselected_actor_ids'])}; "
                            f"window: {row['time_range_s']} s.\n"
                            for row in projection['identity_hypotheses']))
                 if projection.get('identity_hypotheses') else '')
              + (('\nObservation-only identity uncertainty (no Actor association):\n\n'
                  + ''.join(f"- {', '.join(row['issue_ids'])}; candidate Actors retained: "
                            f"{', '.join(row['candidate_actor_ids'])}.\n"
                            for row in projection['observation_only_identity_deferrals']))
                 if projection.get('observation_only_identity_deferrals') else '')
              + '\nNumeric plans and Converter fallbacks are simulation assumptions, not video measurements. '
                'Road, reference, projection and motion checks passed for modeled core Actors; '
                'collision safety against omitted vehicles remains unproved.\n')
    if source_limitations:
        report += ('\nSource revision limitations:\n\n'
                   + ''.join(f"- {row['limitation_id']}: {row['description']}\n"
                             for row in source_limitations)
                   + '\nBody-part observations retain their original source owner in the manifest. '
                     'They are qualitative constraints; numerical body-part reproduction and '
                     'overtake completion are not established by them.\n')
    report_bytes = report.encode('utf-8')
    manifest['report_sha256'] = hashlib.sha256(report_bytes).hexdigest()
    manifest['manifest_sha256'] = digest(manifest)
    return output, manifest, report_bytes


def run(action, root, case):
    output, manifest, report = prepare(root, case)
    manifest_path, report_path = output/'manifest.json', output/'report.md'
    if manifest_path.is_symlink() or report_path.is_symlink():
        raise ValueError('Approximate artifact path is a symlink')
    if action == 'accept':
        from _lib.runtime.case_core import atomic_bytes
        if report_path.exists() and report_path.read_bytes() != report:
            raise ValueError('Existing approximate report differs')
        if manifest_path.exists() and read_json(manifest_path) != manifest:
            raise ValueError('Existing approximate manifest differs')
        if not report_path.exists():
            atomic_bytes(report_path, report)
        if not manifest_path.exists():
            atomic_json(manifest_path, manifest)
    if manifest_path.exists() or report_path.exists():
        if not manifest_path.is_file() or not report_path.is_file():
            raise ValueError('Approximate artifact pair is incomplete')
        if read_json(manifest_path) != manifest or report_path.read_bytes() != report:
            raise ValueError('Approximate artifacts changed after acceptance')
    return {'status': 'accepted' if manifest_path.is_file() else 'ready_to_accept',
            'delivery_level': manifest['delivery_level'],
            'target_sha256': manifest['evidence_sha256']['target'],
            'unresolved': len(manifest['unresolved_issue_ids']),
            'manifest': str(manifest_path), 'report': str(report_path),
            'strict_validate_completed': 'not_claimed'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('check', 'accept'))
    parser.add_argument('--isolation-root', required=True, type=Path)
    parser.add_argument('--case-dir', required=True, type=Path)
    args = parser.parse_args()
    try:
        result = run(args.action, args.isolation_root, args.case_dir)
    except ImplementationVersionIncompatible as exc:
        print(json.dumps({'status': 'failed', 'delivery_level': 'core_scene_approximate',
                          'strict_validate_completed': 'not_claimed',
                          'error': {'code': exc.code, 'message': str(exc),
                                    'frozen_sha256': exc.frozen_sha256,
                                    'current_sha256': exc.current_sha256}}, ensure_ascii=False))
        raise SystemExit(1)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
