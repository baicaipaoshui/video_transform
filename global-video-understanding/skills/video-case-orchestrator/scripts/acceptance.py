#!/usr/bin/env python3
"""Bounded, durable four-job real-model acceptance; never edits reference Cases."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from _lib.paths import SCENARIO_ROOT as ROOT, EXTRACTOR
from _lib.runtime.case_core import Cases, atomic_json, now, read_json, sha256
from _lib.runtime.orchestrator import QwenPawBackend, BackendError
from _lib.runtime.v3 import V3Orchestrator
from _lib.contracts.v3 import normalize_parameters
from _lib.fingerprint import pipeline_fingerprint

REFERENCES={'highway':'case_20260911T131114270Z_2c2311a1',
            'intersection':'case_20260906T081107236Z_688d07cf'}
JOBS=('highway_1','intersection_1','highway_2','intersection_2')


def frozen_inputs():
    import importlib.metadata
    from _lib.delivery.converter import converter_fingerprint
    sources={}
    for name, case_id in REFERENCES.items():
        binding=read_json(EXTRACTOR/'cases'/case_id/'state/binding.json')
        if sha256(binding['source_path'])!=binding['source_sha256']:
            raise ValueError('Reference source changed: '+name)
        sources[name]=binding
    config_paths=[ROOT/name/'agent.json' for name in ('video-coordinator','global-video-understanding','video_extractor')]
    config_paths += [Path.home()/'.qwenpaw/config.json']
    configs={str(p):sha256(p) for p in config_paths if p.is_file()}
    instructions={str(p.relative_to(ROOT)):sha256(p) for name in ('video-coordinator','global-video-understanding','video_extractor')
                  for p in [ROOT/name/'AGENTS.md',*(ROOT/name/'skills').glob('*/SKILL.md')]}
    converter=ROOT/'json-coverter/skills/case-conversion/scripts/scenes'
    return dict(pipeline_fingerprint=pipeline_fingerprint(),config_sha256=configs,
                numerical_runtime={name:importlib.metadata.version(name) for name in ('numpy','scipy')},
                converters={name:converter_fingerprint(converter/name/'convert.py') for name in ('highway','intersection')},
                instructions_sha256=instructions,sources=sources)


class BoundedBackend(QwenPawBackend):
    def __init__(self, output, job):
        super().__init__(); self.output=output; self.job=job

    def submit(self, agent, prompt):
        manifest=read_json(self.output/'manifest.json')
        if manifest['frozen']!=frozen_inputs():
            raise BackendError('source_changed','Code, model configuration or instructions changed during acceptance',submission_not_sent=True)
        calls=manifest.setdefault('submissions',[])
        prior=manifest.get('prior_campaign')
        if prior and sha256(prior['path'])!=prior['sha256']:
            raise BackendError('source_changed','Prior campaign ledger changed',submission_not_sent=True)
        if len(calls)+manifest.get('prior_submission_count',0)>=36:
            raise BackendError('model_recovery_budget_exhausted','Acceptance submission limit 36 reached',submission_not_sent=True)
        calls.append({'job':self.job,'agent':agent,'at':now(),'state':'submitting'})
        atomic_json(self.output/'manifest.json',manifest)
        try:
            receipt=super().submit(agent,prompt)
        except Exception as error:
            calls[-1].update(state='submission_failed_or_unresolved',code=getattr(error,'code',None))
            atomic_json(self.output/'manifest.json',manifest)
            raise
        calls[-1].update(receipt,state='submitted');atomic_json(self.output/'manifest.json',manifest)
        return receipt


def run(output, job, prior_manifest=None):
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
    manifest_path=output/'manifest.json';frozen=frozen_inputs()
    if manifest_path.exists():
        manifest=read_json(manifest_path)
        if manifest['frozen']!=frozen: raise ValueError('Acceptance inputs changed; do not mix code/configuration runs')
    else:
        prior = None
        prior_count = 0
        if prior_manifest:
            prior_path=Path(prior_manifest).resolve()
            previous=read_json(prior_path)
            prior_count=len(previous.get('submissions',[]))+previous.get('prior_submission_count',0)
            prior=dict(path=str(prior_path),sha256=sha256(prior_path))
        historical=ROOT/'diagnostics/stable_v3_acceptance_20260912/manifest.json'
        if historical.is_file() and output != historical.parent and not prior:
            raise ValueError('Existing acceptance budget must be carried with --prior-manifest')
        manifest={'created_at':now(),'frozen':frozen,'jobs':list(JOBS),'submissions':[],
                  'submission_limit':36,'prior_campaign':prior,'prior_submission_count':prior_count,
                  'visual_review':'pending','acceptance_passed':False}
        atomic_json(manifest_path,manifest)
    for other in JOBS:
        prior=output/(other+'.json')
        if other!=job and prior.exists() and read_json(prior)['state']!='finished':
            raise ValueError('Another acceptance job needs reconciliation: '+other)
    receipt=output/(job+'.json')
    params=normalize_parameters({'workflow_version':'v3','fallback_policy':{'straight_preview':{'enabled':True}}})
    source=frozen['sources'][job.rsplit('_',1)[0]]['source_path']
    if receipt.exists():
        row=read_json(receipt)
        if row['state']=='finished': return row
        if not row.get('case_id'): raise ValueError('Case allocation receipt unresolved; do not allocate a duplicate')
        case=EXTRACTOR/'cases'/row['case_id']
    else:
        row={'job':job,'state':'allocating','started_at':now(),'parameters':params,'source':source}
        atomic_json(receipt,row)
        case,_=Cases(EXTRACTOR/'cases').bind(source,params,force_new=True)
        row.update(case_id=case.name,case_path=str(case),state='running');atomic_json(receipt,row)
    print(json.dumps({'job':job,'case_id':case.name,'state':'running'}),flush=True)
    runner=V3Orchestrator(BoundedBackend(output,job))
    try:
        envelope=runner.run_v3(source,case_id=case.name,parameters=params)
    except Exception as error:
        envelope=read_json(case/'state/progress.json')
        envelope['acceptance_exception']={'code':getattr(error,'code',None),'message':str(error)}
    unresolved=any(s.get('status') in {'submitted','pending','running'} or
                   (s.get('error_code') in {'task_deadline_exceeded','task_status_unavailable'} and not s.get('deadline_task_resolved'))
                   for s in envelope.get('sessions',[])) or bool(envelope.get('diagnostics',{}).get('unresolved_submissions'))
    row.update(state='reconciliation_required' if unresolved else 'finished',finished_at=now(),envelope=envelope)
    if envelope.get('status') in {'done','done_degraded'}:
        runner.validate_completed(case,envelope)
        row['artifacts_verified']=True
    else: row['artifacts_verified']=False
    row['model_tasks']=len(envelope.get('sessions',[]))
    row['usage_observed']=[s.get('telemetry') for s in envelope.get('sessions',[])]
    atomic_json(receipt,row)
    return row


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-root',required=True);parser.add_argument('--job',choices=JOBS,required=True)
    parser.add_argument('--prior-manifest',help='Carry consumed submissions from the prior acceptance campaign; never reset the cap')
    args=parser.parse_args()
    result=run(args.output_root,args.job,args.prior_manifest)
    print(json.dumps({'job':result['job'],'case_id':result.get('case_id'),'state':result['state'],
                      'status':result.get('envelope',{}).get('status'),'artifacts_verified':result.get('artifacts_verified'),
                      'error':result.get('envelope',{}).get('error')},ensure_ascii=False,indent=2),flush=True)


if __name__=='__main__': main()
