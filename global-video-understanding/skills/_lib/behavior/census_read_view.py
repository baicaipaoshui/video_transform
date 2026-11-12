"""Deterministic, source-bound Census reading view for new Behavior requests."""
import copy
from pathlib import Path

from _lib.contracts.conversion_policy import digest
from _lib.runtime.case_core import atomic_json, read_json, sha256

REVISION = 'behavior-census-read-view.v1'
COMPACT_REVISION = 'behavior-census-read-view.v2'
DIRECTORY = 'state/behavior_census_views'


def _fail(message):
    from _lib.behavior.input_binding import InputChanged
    raise InputChanged('Behavior Census reading view: ' + message)


def path_for(case, version_id, revision=REVISION):
    if not isinstance(version_id, str) or not version_id or '/' in version_id or version_id.startswith('.'):
        _fail('invalid fact version')
    if revision not in {REVISION, COMPACT_REVISION}:
        _fail('unknown reading view revision')
    suffix = '.v2' if revision == COMPACT_REVISION else ''
    return Path(case)/DIRECTORY/f'{version_id}{suffix}.json'


def revision_for(case):
    from _lib.behavior.input_binding import REQUEST
    origin = Path(case)/REQUEST
    if origin.is_file():
        return read_json(origin).get('census_read_view', {}).get('revision', REVISION)
    policy = Path(case)/'state/execution_policy.json'
    if policy.is_file() and read_json(policy).get('behavior_census_read_view') == COMPACT_REVISION:
        return COMPACT_REVISION
    return REVISION


def _source(case, row, batch_hashes):
    source_id = row.get('source_id')
    if not isinstance(source_id, str) or '#/' not in source_id:
        _fail('unresolved observation has no source pointer')
    batch_id, pointer = source_id.split('#', 1)
    path = (Path(case)/'batches'/f'{batch_id}.json').resolve()
    if path.parent != (Path(case)/'batches').resolve() or str(path) not in batch_hashes:
        _fail('unresolved observation has an unknown Batch source')
    expected_hash = batch_hashes[str(path)]
    if not path.is_file() or sha256(path) != expected_hash:
        _fail('unresolved observation Batch source changed')
    value = read_json(path)
    try:
        for part in pointer.lstrip('/').split('/'):
            part = part.replace('~1', '/').replace('~0', '~')
            value = value[int(part)] if isinstance(value, list) else value[part]
    except (IndexError, KeyError, TypeError, ValueError):
        _fail('unresolved observation source pointer is missing')
    text = value.get('description', value.get('reason')) if isinstance(value, dict) else value
    if text != row.get('text'):
        _fail('unresolved observation text differs from its Batch source')
    return str(path), expected_hash, pointer


def build(case, timeline, revision=REVISION):
    """Project verified facts only; never infer that two sightings share an Actor."""
    from _lib.contracts.census_adjudication import proof, summary
    from _lib.contracts.census_history import certificate_paths, rounds
    from _lib.contracts.behavior_census_scope import pending
    from _lib.runtime.fact_versions import ROOT, census_path, current_facts

    case = Path(case).resolve()
    saved = proof(case)
    if not saved:
        _fail('accepted Census certificate is missing or incomplete')
    view = current_facts(case) if (case/ROOT/'active.json').is_file() else None
    version_id = view.version_id if view else 'unversioned'
    if timeline.get('fact_version_id') != (view.version_id if view else None):
        _fail('evidence timeline and effective fact version differ')
    certificate = census_path(case).resolve()
    source_paths = {saved['sha256']: str(certificate)}
    for ancestor in certificate_paths(saved):
        ancestor = Path(ancestor).resolve()
        source_paths[read_json(ancestor)['sha256']] = str(ancestor)

    projected_rounds = []
    record_refs = {}
    issue_status = {}
    for item in rounds(saved):
        cert_path = source_paths.get(item['sha256'])
        if cert_path is None or not Path(cert_path).is_file() or \
                read_json(cert_path).get('sha256') != item['sha256']:
            _fail('Census history source is missing or changed')
        cert_hash = sha256(Path(cert_path))
        records = item['request']['records']
        bindings = item['response']['bindings']
        if len({r['issue_id'] for r in records}) != len(records) or \
                {r['issue_id'] for r in records} != {b['issue_id'] for b in bindings}:
            _fail('Census records and bindings do not cover the same issues')
        issue_references = []
        for index, record in enumerate(records):
            key = (record['batch_id'], record['frame_id'], record['object_id'])
            ref = dict(issue_id=record['issue_id'], certificate_path=cert_path,
                       certificate_sha256=cert_hash,
                       record_pointer=f'/request/records/{index}',
                       binding_pointer=f'/response/bindings/{next(i for i, b in enumerate(bindings) if b["issue_id"] == record["issue_id"])}')
            prior = record_refs.get(key)
            if prior and prior['issue_id'] != ref['issue_id']:
                _fail('one Batch object has conflicting Census issue IDs')
            record_refs[key] = ref
            issue_references.append({key: ref[key] for key in
                ('issue_id', 'record_pointer', 'binding_pointer')})
        outcome = summary(item['result'])
        for name, status in (('resolved_issue_ids', 'resolved'),
                             ('excluded_issue_ids', 'excluded'),
                             ('remaining_issue_ids', 'remaining')):
            issue_status.update({issue_id: status for issue_id in outcome[name]})
        # Current World carries the accepted layout; result.relevance is the
        # same signed decision already present in response.relevance.
        if outcome.get('relevance') != item['response'].get('relevance'):
            _fail('Census response/result relevance differs')
        outcome.pop('relevance', None)
        response = {key: copy.deepcopy(value) for key, value in item['response'].items()
                    if key != 'layout'}
        projected_rounds.append(dict(certificate_path=cert_path,
            certificate_sha256=cert_hash,
            request=dict(revision=item['request']['revision'], mode=item['request']['mode'],
                         request_sha256=item['request']['request_sha256'],
                         records=copy.deepcopy(records)),
            response=response, result=outcome, issue_references=issue_references))

    batch_hashes = timeline.get('batch_sha256')
    if not isinstance(batch_hashes, dict):
        _fail('frozen evidence timeline has no Batch hashes')
    unresolved = []
    for row in timeline.get('unresolved', []):
        source_path, _, pointer = _source(case, row, batch_hashes)
        link = None
        if row.get('object_id') is not None:
            link = record_refs.get((Path(source_path).stem, row['frame_id'], row['object_id']))
        if row.get('source_binding') == 'unresolved' and link is None:
            _fail('unresolved Census source has no exact issue mapping: ' + row['source_id'])
        if link and link['issue_id'] not in issue_status:
            _fail('mapped Census issue has no accepted status: ' + link['issue_id'])
        unresolved.append(dict(source_id=row['source_id'], frame_id=row['frame_id'],
            source_binding=row['source_binding'], source_pointer=pointer,
            census_issue_id=link['issue_id'] if link else None,
            census_issue_status=issue_status[link['issue_id']] if link else None))

    latest = summary(saved['result'])
    pending_issues = pending(case, saved)
    if not set(latest['remaining_issue_ids']) <= set(pending_issues) and pending_issues:
        _fail('remaining Census issues are missing from Behavior scope')
    sources = dict(certificate_path=str(certificate), certificate_sha256=sha256(certificate),
                   batch_sha256=batch_hashes)
    if view:
        for name, path in (('fact_manifest', view.directory/'manifest.json'),
                           ('world', view.world_path), ('registry', view.registry_path),
                           ('claims', view.directory/'claims.json')):
            sources[name+'_path'] = str(path.resolve())
            sources[name+'_sha256'] = sha256(path)
        actor_ids = [actor['actor_id'] for actor in view.registry]
    else:
        for name, path in (('world', case/'state/world_context.json'),
                           ('registry', case/'state/actor_registry.json')):
            sources[name+'_path'] = str(path.resolve())
            sources[name+'_sha256'] = sha256(path)
        actor_ids = [actor['actor_id'] for actor in read_json(case/'state/actor_registry.json')]
    value = dict(revision=REVISION, fact_version_id=version_id, sources=sources,
        actor_ids=actor_ids, evidence_timeline_sha256=digest(timeline),
        rounds=projected_rounds, pending_issues=pending_issues,
        unresolved_sources=unresolved)
    return _compact(value, timeline) if revision == COMPACT_REVISION else value


def _compact(value, timeline):
    """Transfer final Census dispositions, retaining exact pointers to their proof."""
    if value['revision'] != REVISION:
        _fail('compact view requires verified Census history')
    records, bindings, relevance, certificates = {}, {}, {}, {}
    road_proofs = {}
    for turn in value['rounds']:
        request = turn['request']
        certificates[turn['certificate_sha256']] = turn['certificate_path']
        for ref in turn['issue_references']:
            iid = ref['issue_id']
            record = next(r for r in request['records'] if r['issue_id'] == iid)
            binding = next(b for b in turn['response']['bindings'] if b['issue_id'] == iid)
            records[iid] = dict(record=record, ref=dict(ref,
                certificate_sha256=turn['certificate_sha256']))
            bindings[iid] = binding
        relevance.update({r['issue_id']: r for r in turn['response'].get('relevance', [])})
    source_rows = {(r['source_id'], r['frame_id']): r for r in timeline['unresolved']}
    if len(source_rows) != len(timeline['unresolved']):
        _fail('duplicate source and frame in unresolved evidence timeline')
    dispositions = []
    for link in value['unresolved_sources']:
        source = source_rows[(link['source_id'], link['frame_id'])]
        iid, status = link['census_issue_id'], link['census_issue_status']
        item = dict(**link, time_s=source['time_s'], object_id=source.get('object_id'),
                    candidate_actor_ids=source.get('candidate_actor_ids', []),
                    preliminary_relevance=(relevance.get(iid) or {}).get('assessment'),
                    final_disposition=status or 'unresolved_source')
        if iid:
            record, binding = records[iid]['record'], bindings[iid]
            if (record['frame_id'] != source['frame_id'] or
                    record['object_id'] != source.get('object_id') or
                    (status == 'resolved') != (binding['outcome'] in {'existing', 'new'}) or
                    (status == 'excluded') != (binding['outcome'] == 'excluded_opposing')):
                _fail('final Census disposition conflicts with source or binding')
            item.update(census_proof=records[iid]['ref'], binding_outcome=binding['outcome'])
            if status == 'resolved':
                actor_id = turn_actor_id(value['rounds'], iid, binding)
                if actor_id not in value['actor_ids']:
                    _fail('resolved Census identity is absent from effective Registry')
                item['accepted_actor_id'] = actor_id
            elif status == 'excluded':
                separation = binding.get('separation')
                if not separation or separation.get('separator') != 'barrier' or separation.get('no_interaction') is not True:
                    _fail('excluded source lacks accepted isolation proof')
                key = digest(dict(side=separation['side'], separator=separation['separator'],
                    direction=separation['direction'], evidence_frame_ids=separation['evidence_frame_ids'],
                    world_sha256=value['sources']['world_sha256']))
                turn = next(t for t in reversed(value['rounds'])
                    if iid in {r['issue_id'] for r in t['request']['records']})
                original = read_json(turn['certificate_path'])['request']['frames']
                by_frame = {f['frame_id']: f for f in original}
                evidence_frames = [dict(frame_id=fid, time_s=by_frame[fid]['time_s'],
                    sha256=by_frame[fid]['sha256']) for fid in separation['evidence_frame_ids']]
                window_s = [min(f['time_s'] for f in original), max(f['time_s'] for f in original)]
                if not window_s[0] <= item['time_s'] <= window_s[1]:
                    _fail('excluded observation is outside accepted road evidence window')
                key = digest(dict(proof_id=key, evidence_frames=evidence_frames, window_s=window_s))
                road_proofs.setdefault(key, dict(proof_id=key,
                    side=separation['side'], direction=separation['direction'],
                    separator=separation['separator'], no_interaction=True,
                    evidence_frames=evidence_frames, window_s=window_s,
                    world_sha256=value['sources']['world_sha256'], applicable_issue_ids=[]))
                road_proofs[key]['applicable_issue_ids'].append(iid)
                item['road_proof_id'] = key
                item['applicable_window_s'] = window_s
                item['separation_reason'] = separation['reason']
        dispositions.append(item)
    if len(dispositions) != len(source_rows) or \
            {(d['source_id'], d['frame_id']) for d in dispositions} != set(source_rows):
        _fail('compact view does not cover every original unresolved source')
    if set(records) != {d['census_issue_id'] for d in dispositions if d['census_issue_id']}:
        _fail('compact view does not cover every Census record source')
    return dict(revision=COMPACT_REVISION, fact_version_id=value['fact_version_id'],
        sources=value['sources'], actor_ids=value['actor_ids'],
        certificates=certificates,
        evidence_timeline_sha256=value['evidence_timeline_sha256'],
        pending_issues=value['pending_issues'], unresolved_sources=dispositions,
        shared_road_proofs=sorted(road_proofs.values(), key=lambda r:r['proof_id']))


def turn_actor_id(rounds, iid, binding):
    for index in range(len(rounds)-1, -1, -1):
        turn = rounds[index]
        if iid in {r['issue_id'] for r in turn['request']['records']}:
            actor_id = turn['result']['vehicle_ids'][binding['vehicle_key']]
            for later in rounds[index:]:
                actor_id = later['result'].get('canonical_actor_ids', {}).get(actor_id, actor_id)
            return actor_id
    _fail('resolved Census issue has no vehicle identity')


def source_coverage(case, pending, execution_dispositions):
    """Join inherited Census decisions and new Behavior choices for acceptance."""
    from _lib.behavior.input_binding import current_census_read_view
    path = current_census_read_view(case)
    if path is None:
        return None
    view = read_json(path)
    if view['revision'] != COMPACT_REVISION:
        return None
    if set(view['pending_issues']) != set(pending):
        _fail('compact pending issues differ from Behavior review obligations')
    choices = {r['issue_id']: r for r in (execution_dispositions or {}).get('choices', [])}
    if set(choices) != set(pending):
        _fail('Behavior decisions do not cover every pending Census issue')
    if any(r['final_disposition'] in {'resolved', 'excluded'} and not r.get('census_proof')
           or r['final_disposition'] == 'remaining' and r['census_issue_id'] not in choices
           for r in view['unresolved_sources']):
        _fail('inherited and Behavior dispositions leave a Census source uncovered')
    def refs(status):
        return sorted((dict(source_id=r['source_id'], frame_id=r['frame_id'])
            for r in view['unresolved_sources'] if r['final_disposition'] == status),
            key=lambda r:(r['source_id'], r['frame_id']))
    return dict(view_sha256=sha256(path),
        inherited_resolved_sources=refs('resolved'),
        inherited_excluded_sources=refs('excluded'),
        behavior_issue_ids=sorted(choices),
        unbound_sources=refs('unresolved_source'))


def materialize(case, timeline):
    revision = revision_for(case)
    value = build(case, timeline, revision)
    path = path_for(case, value['fact_version_id'], revision)
    if path.is_file():
        if read_json(path) != value:
            _fail('frozen Census reading view differs from effective sources')
    else:
        atomic_json(path, value)
    return dict(revision=revision, path=str(path.resolve()), sha256=sha256(path))


def verify(case, metadata, timeline):
    if not isinstance(metadata, dict) or metadata.get('revision') not in {REVISION, COMPACT_REVISION}:
        _fail('unknown frozen Census reading view revision')
    revision = metadata['revision']
    case = Path(case).resolve()
    version_id = timeline.get('fact_version_id') or 'unversioned'
    local = path_for(case, version_id, revision).resolve()
    frozen = Path(metadata.get('path', ''))
    if not frozen.is_absolute() or frozen != frozen.resolve() or len(frozen.parents) < 3:
        _fail('frozen Census reading view path is not canonical and absolute')
    if frozen != local:
        # Core copies preserve frozen requests and view bytes. Verify the
        # original owner, then require every local source to match that proof;
        # never rebuild/re-sign the frozen view under the copy's new prefix.
        owner = frozen.parents[2]
        if (owner.name != case.name or frozen != path_for(owner, version_id, revision)
                or not local.is_file() or sha256(local) != metadata.get('sha256')):
            _fail('copied Census reading view binding changed')
        from _lib.runtime.fact_versions import ROOT, current_facts
        if (case/ROOT/'active.json').is_file():
            current_facts(case, expected_version=version_id)
        value = read_json(local)
        sources = value.get('sources', {})
        bindings = dict(sources.get('batch_sha256', {}))
        for key, name in sources.items():
            if key.endswith('_path'):
                bindings[name] = sources.get(key[:-5] + '_sha256')
        if not bindings:
            _fail('copied Census reading view has no source bindings')
        for name, expected_hash in bindings.items():
            source = Path(name)
            if not source.is_absolute() or not source.is_relative_to(owner):
                _fail('copied Census source leaves its frozen owner')
            copied = case/source.relative_to(owner)
            if (not copied.resolve().is_relative_to(case) or not copied.is_file()
                    or sha256(copied) != expected_hash):
                _fail('copied Census reading view source changed')
        verify(owner, metadata, timeline)
        return local
    expected = build(case, timeline, revision)
    path = path_for(case, expected['fact_version_id'], revision).resolve()
    if metadata.get('path') != str(path) or not path.is_file() or \
            sha256(path) != metadata.get('sha256') or read_json(path) != expected:
        _fail('frozen Census reading view or source version changed')
    return path
