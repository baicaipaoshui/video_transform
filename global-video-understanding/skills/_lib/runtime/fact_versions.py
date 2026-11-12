"""Immutable current-fact snapshots; historical Case files are never rewritten."""
from __future__ import annotations

import copy
import fcntl
import os
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from _lib.contracts.conversion_policy import digest
from _lib.contracts.v3 import ContractError
from _lib.runtime.case_core import atomic_bytes, atomic_json, now, raw_read_json, read_json, sha256

REVISION = 'case-facts.v1'
ROOT = Path('state/fact_versions')
POINTER = ROOT/'active.json'
FILES = ('world_context.json', 'actor_registry.json', 'reconciliation.json',
         'census_adjudication.accepted.json', 'claims.json', 'revisions.json')


class FactVersionError(ContractError):
    def __init__(self, message):
        super().__init__(message, rule_id='facts.version')


@dataclass(frozen=True)
class FactView:
    version_id: str
    directory: Path
    world: dict
    registry: list
    reconciliation: dict
    census: dict | None
    claims: dict
    revisions: list

    @property
    def world_path(self):
        return self.directory/'world_context.json'

    @property
    def registry_path(self):
        return self.directory/'actor_registry.json'


def enabled(case):
    path = Path(case)/'state/execution_policy.json'
    return path.is_file() and read_json(path).get('fact_versions') == REVISION


def _root(case):
    return Path(case).resolve()/ROOT


def _sync_directory(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextmanager
def _lock(case):
    root = _root(case)
    root.mkdir(parents=True, exist_ok=True)
    with (root/'commit.lock').open('a+b') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def _source_claims(census):
    if census is None:
        return {}
    from _lib.contracts.census_history import rounds
    from _lib.contracts.census_adjudication import normalize_confirmed_layout_sightings
    claims = {}
    for item in rounds(census):
        response = item['response']
        if item['request'].get('mode') == 'layout_and_census':
            lane_map = item['result']['world']['world']['metadata'][
                'lane_layout_review']['adjudication']['lane_id_map']
            response = normalize_confirmed_layout_sightings(
                response, read_json(item['request']['registry_path']), lane_map)
        records = {row['issue_id']: row for row in item['request']['records']}
        bindings = {}
        for row in response['bindings']:
            if row.get('outcome') in {'existing', 'new'}:
                record = records[row['issue_id']]
                bindings.setdefault((row['vehicle_key'], record['frame_id']), []).append(row)
        for vehicle in response['vehicles']:
            if vehicle.get('role') == 'comparison':
                continue
            for sighting in vehicle['sightings']:
                matches = bindings.get((vehicle['key'], sighting['frame_id']), [])
                # Certificate-local anchors remain stable through actor remaps.
                # All issue IDs are retained when several Batch objects resolve
                # to the same reviewed sighting in one frame.
                anchor_prefix = f"certificate:{item['sha256']}:{vehicle['key']}"
                anchor = f"{anchor_prefix}:{sighting['frame_id']}"
                common = dict(observation_anchor_id=anchor,
                            actor_id=item['result']['vehicle_ids'][vehicle['key']],
                            frame_id=sighting['frame_id'], time_s=sighting['time_s'],
                            features=sighting['features'],
                            distinct_from_actor_ids=sighting['distinct_from_actor_ids'],
                            source_certificate_sha256=item['sha256'],
                            source_vehicle_key=vehicle['key'],
                            source_issue_ids=sorted(row['issue_id'] for row in matches))
                for field, value in (('actor_lane', sighting['lane_id']),
                                     ('actor_identity', common['actor_id'])):
                    key = f'{anchor}:{field}'
                    if key in claims:
                        raise FactVersionError('Duplicate observation anchor in Census history')
                    body = dict(common, fact_key=key, field=field, value=value)
                    claims[key] = dict(body, claim_id=digest(body))
    return claims


def _publish(case, version_id, files):
    root = _root(case)
    directory = root/version_id
    if directory.exists():
        raise FactVersionError('Fact version already exists')
    temporary = root/f'.{version_id}.{uuid.uuid4().hex}'
    temporary.mkdir()
    try:
        for name, payload in files.items():
            target = temporary/name
            if isinstance(payload, bytes):
                atomic_bytes(target, payload)
            else:
                atomic_json(target, payload)
        hashes = {name: sha256(temporary/name) for name in files}
        manifest = dict(revision=REVISION, version_id=version_id,
                        files=hashes, created_at=now())
        atomic_json(temporary/'manifest.json', manifest)
        _sync_directory(temporary)
        os.replace(temporary, directory)
        _sync_directory(root)
        return sha256(directory/'manifest.json')
    except BaseException:
        # An unreferenced temporary directory is harmless and can be inspected
        # after an interrupted commit. Never remove a published version.
        raise


def _read(case, pointer):
    version_id = pointer.get('version_id')
    if not isinstance(version_id, str) or not version_id or '/' in version_id or version_id.startswith('.'):
        raise FactVersionError('Active fact pointer has an invalid version ID')
    directory = _root(case)/version_id
    manifest_path = directory/'manifest.json'
    if (not manifest_path.is_file()
            or sha256(manifest_path) != pointer.get('manifest_sha256')):
        raise FactVersionError('Active fact version manifest missing or changed')
    manifest = read_json(manifest_path)
    if (manifest.get('revision') != REVISION or manifest.get('version_id') != version_id
            or set(manifest.get('files', {})) != set(FILES)):
        raise FactVersionError('Active fact version manifest is invalid')
    for name, expected in manifest['files'].items():
        path = directory/name
        if not path.is_file() or sha256(path) != expected:
            raise FactVersionError(f'Active fact version file missing or changed: {name}')
    census = read_json(directory/'census_adjudication.accepted.json')
    return FactView(version_id, directory,
                    read_json(directory/'world_context.json'),
                    read_json(directory/'actor_registry.json'),
                    read_json(directory/'reconciliation.json'),
                    census or None, read_json(directory/'claims.json'),
                    read_json(directory/'revisions.json'))


def current_facts(case, expected_version=None):
    """Read one pointer once and pin all returned facts to its verified version."""
    pointer_path = _root(case)/'active.json'
    if not pointer_path.is_file():
        if enabled(case):
            raise FactVersionError('Fact-version policy enabled but baseline is missing')
        raise FactVersionError('Case has not adopted fact versions')
    pointer = read_json(pointer_path)
    view = _read(case, pointer)
    if expected_version is not None and view.version_id != expected_version:
        raise FactVersionError('Fact version changed during stage execution')
    return view


def facts_at(case, version_id, manifest_sha256):
    """Read and verify one immutable fact version without following the active pointer."""
    return _read(case, dict(version_id=version_id, manifest_sha256=manifest_sha256))


def source_path(case, name):
    """Return one effective fact file without changing the original source path."""
    if name not in FILES[:4]:
        raise FactVersionError('Unknown versioned source file')
    case = Path(case)
    if (case/POINTER).is_file():
        return current_facts(case).directory/name
    return case/'state'/name


def census_path(case):
    """Return the certificate in the same effective snapshot as World facts."""
    return source_path(case, 'census_adjudication.accepted.json')


def reviewed_world(case, view):
    """Verify a later World question review without changing its Census parent."""
    reviews = [row for row in view.revisions if row.get('kind') == 'world-question-review.v1']
    latest = reviews[-1] if reviews else None
    if not latest:
        return False
    case = Path(case)
    directory = case/ROOT/latest['version_id']
    manifest = directory/'manifest.json'
    predecessor = case/ROOT/latest['parent_version']/'world_context.json'
    if not manifest.is_file() or not predecessor.is_file():
        return False
    reviewed = _read(case, dict(version_id=latest['version_id'],
                                manifest_sha256=sha256(manifest)))
    return (reviewed.world == view.world and
            latest.get('after_world_digest') == digest(view.world) and
            latest.get('before_world_sha256') == sha256(predecessor))


def _apply_reconciliation_lane_points(case, parent, decisions):
    """Replay the narrow first admission of physical lane observations."""
    if not isinstance(decisions, list) or not decisions:
        raise FactVersionError('Lane-point handoff needs observed decisions')
    world, registry = copy.deepcopy(parent.world), copy.deepcopy(parent.registry)
    receipt, claims = copy.deepcopy(parent.reconciliation), copy.deepcopy(parent.claims)
    from _lib.behavior.compile import evidence_frames
    frames = {row['frame_id']: row for row in evidence_frames(case, include_census=False)}
    observation = world['world']['metadata']['lane_layout_review']['observation']
    boundaries = observation['boundaries_right_to_left']
    anchor_rows = [row for road in world['world']['road']['roads']
                   for row in road['lane_knowledge']['anchors']]
    anchors = {row['ordinal_from_right']: row['lane_id'] for row in anchor_rows}
    if len(anchor_rows) != len(anchors) or len(boundaries) != len(anchors) + 1:
        raise FactVersionError('Lane-point boundaries and lane anchors disagree')
    seen = set()
    for row in decisions:
        if set(row) not in ({'actor_id', 'frame_id', 'lane_id', 'ordinal_from_right',
                        'right_boundary_id', 'left_boundary_id', 'source_path',
                        'source_sha256', 'reason'},
                        {'actor_id', 'frame_id', 'lane_id', 'ordinal_from_right',
                        'right_boundary_id', 'left_boundary_id', 'source_path',
                        'source_sha256', 'reason', 'review_source_ids'}):
            raise FactVersionError('Lane-point decision has invalid fields')
        aid, fid, ordinal = row['actor_id'], row['frame_id'], row['ordinal_from_right']
        key = f'point:{aid}:{fid}:actor_lane'
        if (key in seen or key in claims or fid not in frames or type(ordinal) is not int
                or anchors.get(ordinal) != row['lane_id'] or ordinal < 0
                or ordinal + 1 >= len(boundaries)
                or (row['right_boundary_id'], row['left_boundary_id']) !=
                    (boundaries[ordinal], boundaries[ordinal + 1])
                or ('review_source_ids' in row and
                    (not isinstance(row['review_source_ids'], list)
                     or not row['review_source_ids']
                     or any(not isinstance(s, str) or not s for s in row['review_source_ids'])))
                or not isinstance(row['reason'], str) or not row['reason'].strip()):
            raise FactVersionError('Lane-point decision has no unique physical lane binding')
        seen.add(key)
        frame_path = case/'frames/packed/global'/f'global__{fid}.jpg'
        if not frame_path.is_file() or sha256(frame_path) != frames[fid]['sha256']:
            frame_path = Path(frames[fid].get('path') or frames[fid].get('source_path') or '')
        if (not frame_path.is_file() or not frame_path.resolve().is_relative_to(case.resolve())
                or sha256(frame_path) != frames[fid]['sha256']):
            raise FactVersionError('Lane-point source frame changed')
        if not isinstance(row['source_path'], str):
            raise FactVersionError('Lane-point source path is invalid')
        source = case/row['source_path']
        if (Path(row['source_path']).is_absolute()
                or '..' in Path(row['source_path']).parts
                or not ((row['source_path'].startswith('batches/') and source.suffix == '.json') or
                        (row['source_path'].startswith('diagnostics/') and source.name == 'world.json') or
                        row['source_path'] == 'state/world_context.json')
                or not source.is_file() or not source.resolve().is_relative_to(case.resolve())
                or sha256(source) != row['source_sha256']):
            raise FactVersionError('Lane-point cited source changed or is outside frozen evidence')
        if aid == 'ego':
            initial = observation.get('ego_initial', {})
            if fid in initial.get('evidence_frame_ids', []):
                observation['ego_initial'] = dict(evidence_frame_ids=[fid],
                    ordinal_from_right=ordinal, right_boundary_id=boundaries[ordinal],
                    left_boundary_id=boundaries[ordinal + 1], reason=row['reason'])
                world['field_evidence']['world.metadata.lane_layout_review.observation.ego_initial'] = [
                    dict(frame_id=fid, time_s=frames[fid]['time_s'], status='observed',
                         reason=row['reason'])]
        else:
            actors = [actor for actor in world['actor_hypotheses'] if actor.get('actor_id') == aid]
            registered = [actor for actor in registry if actor.get('actor_id') == aid]
            if len(actors) != 1 or len(registered) != 1 or not any(
                item.get('binding') == 'existing' and item.get('actor_id') == aid
                for batch_path in (case/'batches').glob('batch_*.json')
                for frame in read_json(batch_path).get('vehicle_census', [])
                if frame.get('frame_id') == fid
                for item in frame.get('vehicles', [])):
                raise FactVersionError('Lane-point Actor/frame lacks a frozen Registry and Batch binding')
            if fid == actors[0]['first_seen_frame_id']:
                actors[0]['hypothesized_lane'] = row['lane_id']
                registered[0]['hypothesized_lane'] = row['lane_id']
                world['field_evidence'][f'world.actors.{aid}.hypothesized_lane'] = [
                    dict(frame_id=fid, time_s=frames[fid]['time_s'], status='observed',
                         reason=row['reason'])]
        body = dict(fact_key=key, observation_anchor_id=f'point:{aid}:{fid}',
                    actor_id=aid, frame_id=fid, time_s=frames[fid]['time_s'],
                    field='actor_lane', value=row['lane_id'],
                    ordinal_from_right=ordinal, right_boundary_id=boundaries[ordinal],
                    left_boundary_id=boundaries[ordinal + 1], reason=row['reason'],
                    source_path=row['source_path'], source_sha256=row['source_sha256'],
                    frame_sha256=frames[fid]['sha256'])
        if 'review_source_ids' in row:
            body['review_source_ids'] = row['review_source_ids']
        claims[key] = dict(body, claim_id=digest(body))
    receipt['world'] = copy.deepcopy(world)
    receipt['point_lane_adjudication'] = [
        *receipt.get('point_lane_adjudication', []),
        *(dict(fact_key=key, claim_id=claims[key]['claim_id']) for key in sorted(seen))]
    return world, registry, receipt, claims


def verified_reconciliation_lane_points(case, view):
    """Confirm that a Census mismatch is exactly the published point handoff."""
    markers = [row for row in view.revisions
               if row.get('kind') == 'reconciliation-lane-points.v1']
    if not markers:
        return False
    try:
        for row in markers:
            predecessor = _read(case, dict(version_id=row['parent_version'],
                                           manifest_sha256=row['parent_manifest_sha256']))
            if row['parent_version'] != predecessor.version_id or \
                    row['parent_manifest_sha256'] != sha256(predecessor.directory/'manifest.json'):
                return False
            expected = _apply_reconciliation_lane_points(Path(case), predecessor, row['decisions'])
            directory = Path(case)/ROOT/row['version_id']
            successor = _read(case, dict(version_id=row['version_id'],
                                         manifest_sha256=sha256(directory/'manifest.json')))
            if ((successor.world, successor.registry, successor.reconciliation, successor.claims) != expected
                    or successor.census != predecessor.census
                    or successor.revisions != [*predecessor.revisions, row]):
                return False
        point_claims = {key: value for key, value in successor.claims.items()
                        if key.startswith('point:')}
        if any(view.claims.get(key) != value for key, value in point_claims.items()):
            return False
    except (KeyError, TypeError, ValueError, OSError, FactVersionError):
        return False
    return True


def commit_reconciliation_lane_points(case, parent_version, decisions, *, review_binding=None):
    """Publish reviewed single-frame lanes without changing frozen Case sources."""
    case = Path(case).resolve()
    with _lock(case):
        parent = current_facts(case, parent_version)
        world, registry, receipt, claims = _apply_reconciliation_lane_points(
            case, parent, decisions)
        number = int(parent.version_id[1:].split('-', 1)[0]) + 1
        version_id = f'v{number:06d}-{uuid.uuid4().hex[:12]}'
        marker = dict(kind='reconciliation-lane-points.v1',
            version_id=version_id, parent_version=parent.version_id,
            parent_manifest_sha256=sha256(parent.directory/'manifest.json'),
            decisions=copy.deepcopy(decisions))
        if review_binding is not None:
            if (not isinstance(review_binding, dict)
                    or set(review_binding) != {'request_sha256', 'candidate_sha256'}
                    or any(not isinstance(value, str) or len(value) != 64
                           for value in review_binding.values())):
                raise FactVersionError('Lane-point review binding is invalid')
            marker['review_binding'] = copy.deepcopy(review_binding)
        history = [*parent.revisions, marker]
        files = {name: (parent.directory/name).read_bytes() for name in FILES}
        files.update({'world_context.json': world, 'actor_registry.json': registry,
                      'reconciliation.json': receipt, 'claims.json': claims,
                      'revisions.json': history})
        manifest_hash = _publish(case, version_id, files)
        atomic_json(_root(case)/'active.json', dict(revision=REVISION,
                    version_id=version_id, manifest_sha256=manifest_hash))
        _mark_downstream_pending(case, version_id,
            dict(status='committed', version_id=version_id,
                 parent_version_id=parent.version_id,
                 point_claim_ids=[claims[f'point:{row["actor_id"]}:{row["frame_id"]}:actor_lane'][
                     'claim_id'] for row in decisions]))
        return current_facts(case, version_id)


def _mark_downstream_pending(case, version_id, summary):
    progress_path = case/'state/progress.json'
    if not progress_path.is_file():
        return
    progress = read_json(progress_path)
    diagnostics = progress.setdefault('diagnostics', {})
    diagnostics['fact_revision'] = summary
    diagnostics['behavior_revalidation'] = dict(status='pending',
        validation_version_id=version_id)
    diagnostics['target_delivery'] = dict(status='pending',
        validation_version_id=version_id)
    if progress.get('phase') == 'done':
        progress.update(phase='failed', status='needs_review',
            error={'code': 'fact_revision_requires_revalidation',
                   'message': 'Effective facts changed; Behavior and Target need revalidation',
                   'resume_from': 'aggregating'})
    atomic_json(progress_path, progress)


def commit_world_closure(case, journal, *, fault_at=None):
    """Publish one proved Census/World closure through the existing fact pointer."""
    case = Path(case).resolve()
    from _lib.behavior.compile import preview_digest
    from _lib.runtime.world_adjudication import validate_journal

    body = {key: value for key, value in journal.items() if key != 'sha256'}
    if (journal.get('sha256') != preview_digest(body) or
            bool(journal.get('census_certificate')) == bool(journal.get('post_behavior_revision'))):
        raise FactVersionError('World closure needs one signed journal authority')
    validate_journal(journal)
    for path, expected in journal['input_hashes'].items():
        if not Path(path).is_file() or sha256(path) != expected:
            raise FactVersionError('World closure frozen input changed')
    batches = {path.name: sha256(path) for path in (case/'batches').glob('batch_*.json')}
    if journal['receipt'].get('batch_sha256') != batches:
        raise FactVersionError('World closure Batch binding changed')
    source_digest = digest({key: value for key, value in journal.items()
                            if key not in {'status', 'sha256'}})
    with _lock(case):
        parent = current_facts(case)
        published = [row for row in parent.revisions if
                     row.get('kind') == 'world-closure-commit.v1'
                     and row.get('journal_digest') == source_digest]
        if published:
            expected_certificate = (journal['census_certificate']['sha256']
                                    if journal.get('census_certificate') else
                                    parent.census['sha256'] if parent.census else None)
            if len(published) != 1 or published[0]['certificate_sha256'] != expected_certificate:
                raise FactVersionError('World closure published marker conflicts with journal')
            if (journal.get('census_certificate') and
                    parent.census != journal['census_certificate']):
                raise FactVersionError('World closure published certificate changed')
            return parent
        parent_manifest = sha256(parent.directory/'manifest.json')
        if 'fact_parent_version' in journal:
            if (journal['fact_parent_version'] != parent.version_id or
                    journal.get('fact_parent_manifest_sha256') != parent_manifest):
                raise FactVersionError('World closure parent fact version is stale')
        elif (parent.version_id != 'v000000' or parent.revisions or
              any(not (case/'state'/name).is_file() or
                  sha256(case/'state'/name) != sha256(parent.directory/name)
                  for name in FILES[:4])):
            raise FactVersionError('Legacy World closure requires an unchanged v000000 baseline')
        if (sha256(parent.world_path) != journal['old_world_sha256'] or
                sha256(parent.registry_path) != journal['old_registry_sha256']):
            raise FactVersionError('World closure predecessor facts changed')
        certificate = journal.get('census_certificate')
        if certificate:
            request = certificate['request']
            link = request.get('parent_certificate')
            if link:
                if (not parent.census or request['mode'] != 'census_after_layout' or
                        request.get('layout_request') is not None or
                        sha256(parent.directory/'census_adjudication.accepted.json') != link['sha256']):
                    raise FactVersionError('World closure parent certificate changed')
            else:
                if parent.census or request['mode'] not in {'census_after_layout', 'layout_and_census'}:
                    raise FactVersionError('World closure first Census has an incompatible parent')
                if (request['mode'] == 'census_after_layout' and
                        (request.get('layout_request') is not None or
                         parent.reconciliation.get('status') not in {
                             'reconciled', 'reconciled_after_geometry_readjudication'} or
                         parent.reconciliation.get('world') != parent.world)):
                    raise FactVersionError('World closure requires an accepted layout')
                if (request['mode'] == 'layout_and_census' and
                        request.get('layout_request') is None):
                    raise FactVersionError('World closure joint request lacks its layout')
            if (Path(request['case_dir']).resolve() != case or
                    raw_read_json(request['world_path']) != parent.world or
                    raw_read_json(request['registry_path']) != parent.registry):
                raise FactVersionError('World closure Census source differs from parent facts')
        elif sha256(parent.directory/'reconciliation.json') != journal.get('old_receipt_sha256'):
            raise FactVersionError('Post-Behavior World closure receipt changed')
        if (parent.reconciliation != raw_read_json(case/'state/reconciliation.json')
                and 'fact_parent_version' not in journal):
            raise FactVersionError('Legacy World closure receipt changed')
        if certificate:
            from _lib.contracts.census_history import replay
            source_claims = _source_claims(replay(certificate))
            if any(key.startswith('point:') for key in parent.claims):
                if not verified_reconciliation_lane_points(case, parent):
                    raise FactVersionError('World closure point claims lack a verified handoff')
                source_claims.update({key: value for key, value in parent.claims.items()
                                      if key.startswith('point:')})
        else:
            source_claims = parent.claims
        claims = copy.deepcopy(parent.claims)
        if not set(claims) <= set(source_claims):
            raise FactVersionError('World closure would delete an existing source claim')
        for key, source in source_claims.items():
            if key in claims:
                stable = set(source) - {'claim_id', 'value'}
                if any(claims[key].get(field) != source[field] for field in stable):
                    raise FactVersionError('World closure conflicts with an existing source claim')
            else:
                claims[key] = source
        number = int(parent.version_id[1:].split('-', 1)[0]) + 1
        version_id = f'v{number:06d}-{source_digest[:12]}'
        history = [*parent.revisions, dict(kind='world-closure-commit.v1',
            version_id=version_id, parent_version=parent.version_id,
            parent_manifest_sha256=parent_manifest, journal_digest=source_digest,
            certificate_sha256=certificate['sha256'] if certificate else
                parent.census['sha256'] if parent.census else None)]
        files = {name: (parent.directory/name).read_bytes() for name in FILES}
        files.update({'world_context.json': journal['world'],
                      'actor_registry.json': journal['registry'],
                      'reconciliation.json': journal['receipt'],
                      'claims.json': claims, 'revisions.json': history})
        if certificate:
            files['census_adjudication.accepted.json'] = certificate
        if fault_at == 'before_publish':
            raise FactVersionError('Injected interruption before World closure publish')
        directory = _root(case)/version_id
        if directory.exists():
            manifest_hash = sha256(directory/'manifest.json')
            view = _read(case, dict(version_id=version_id, manifest_sha256=manifest_hash))
            if (view.revisions != history or view.world != journal['world'] or
                    view.registry != journal['registry'] or view.reconciliation != journal['receipt'] or
                    view.census != (certificate or parent.census) or view.claims != claims):
                raise FactVersionError('Published World closure version conflicts with journal')
        else:
            manifest_hash = _publish(case, version_id, files)
        if fault_at == 'before_pointer':
            raise FactVersionError('Injected interruption before World closure pointer')
        atomic_json(_root(case)/'active.json', dict(revision=REVISION,
                    version_id=version_id, manifest_sha256=manifest_hash))
        if fault_at == 'after_pointer':
            raise FactVersionError('Injected interruption after World closure pointer')
        return current_facts(case, version_id)


def establish_baseline(case, *, explicit=False):
    """Capture an accepted World/Census handoff without touching its source files."""
    case = Path(case).resolve()
    if not enabled(case) and not explicit:
        raise FactVersionError('Old Case requires explicit fact-version adoption')
    with _lock(case):
        pointer = _root(case)/'active.json'
        if pointer.exists():
            return current_facts(case)
        from _lib.contracts.census_adjudication import proof
        census = proof(case)
        originals = {name: case/'state'/name for name in FILES[:4]}
        if any(not originals[name].is_file() for name in FILES[:3]):
            raise FactVersionError('Accepted World/Registry/reconciliation handoff is incomplete')
        receipt = read_json(originals['reconciliation.json'])
        if receipt.get('status') not in {'reconciled', 'reconciled_after_geometry_readjudication',
                                         'reconciled_after_identity_review'}:
            raise FactVersionError('Fact baseline requires accepted reconciliation')
        files = {name: path.read_bytes() if path.is_file() else b'{}\n'
                 for name, path in originals.items()}
        files['claims.json'] = _source_claims(census)
        files['revisions.json'] = []
        manifest_hash = _publish(case, 'v000000', files)
        atomic_json(pointer, dict(revision=REVISION, version_id='v000000',
                                  manifest_sha256=manifest_hash))
        return current_facts(case)


def commit_revision(case, parent_version, revision, *, fault_at=None):
    """Commit one proved claim replacement, then switch the sole active pointer."""
    return commit_revisions(case, parent_version, [revision], fault_at=fault_at)


def commit_revisions(case, parent_version, revisions, *, fault_at=None):
    """Commit a scoped set of independent claim replacements as one version."""
    case = Path(case).resolve()
    with _lock(case):
        parent = current_facts(case, parent_version)
        from _lib.contracts.fact_disagreement import validate_revision
        if not isinstance(revisions, list) or not revisions:
            raise FactVersionError('Fact commit requires at least one proved replacement')
        validated = [validate_revision(case, parent, row) for row in revisions]
        keys = [row['fact_key'] for row in validated]
        if len(keys) != len(set(keys)):
            raise FactVersionError('Fact commit repeats an observation field')
        claims = copy.deepcopy(parent.claims)
        for row in validated:
            claims[row['fact_key']] = row['new_claim']
        history = [*parent.revisions, *validated]
        number = int(parent.version_id[1:].split('-', 1)[0]) + 1
        version_id = f'v{number:06d}-{uuid.uuid4().hex[:12]}'
        files = {name: (parent.directory/name).read_bytes() for name in FILES[:4]}
        files.update({'claims.json': claims, 'revisions.json': history})
        if fault_at == 'before_publish':
            raise FactVersionError('Injected interruption before fact version publish')
        manifest_hash = _publish(case, version_id, files)
        if fault_at == 'before_pointer':
            raise FactVersionError('Injected interruption before active pointer switch')
        atomic_json(_root(case)/'active.json', dict(revision=REVISION,
                    version_id=version_id, manifest_sha256=manifest_hash))
        if fault_at == 'after_pointer':
            raise FactVersionError('Injected interruption after active pointer switch')
        _mark_downstream_pending(case, version_id,
            dict(status='committed', version_id=version_id,
                 parent_version_id=parent.version_id,
                 replacements=[dict(fact_key=row['fact_key'],
                                    new_claim_id=row['new_claim_id']) for row in validated]))
        return current_facts(case, version_id)
