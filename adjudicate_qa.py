"""Apply a supplied human QA decision without changing automated evidence."""
import argparse,json,shutil
from pathlib import Path
from datetime import datetime,timezone
from workflow_common import load,digest,verify,write_record,registered_uuid

def adjudicate(obj,decision_path,registry_path):
    obj=Path(obj);job=obj.name;decision=load(decision_path)
    config=load(registry_path)
    people=[config['operator']]+config.get('reviewers',[])
    reviewer=next((r for r in people if r['uuid']==decision.get('reviewer_uuid') and r.get('type')=='person'),None)
    if not reviewer:raise RuntimeError('Reviewer must be a configured person agent')
    record_path=obj/'documentation'/f'{job}_poc4_qa_record.json';record=load(record_path)
    entries=record.get('qa_entries',[])
    conversions=load(obj/'documentation'/f'{job}_poc2_conversion_record.json').get('conversions',[])
    validations=load(obj/'documentation'/f'{job}_poc3_validation_record.json').get('validations',[])
    master=decision.get('master_file_id')
    entry=next((r for r in entries if r['master_file_id']==master),None)
    conv=next((r for r in conversions if r['master_file_id']==master),None)
    validation=next((r for r in validations if r['master_file_id']==master and r.get('verdict')=='PASS' and r.get('conformance_validated')),None)
    if not entry or not conv or not validation:raise RuntimeError('Decision must identify an active, veraPDF-passing master')
    for key in ['source_sha512','master_sha512']:
        if decision.get(key)!=conv[key]:raise RuntimeError('Decision source/master digest differs')
        verify(conv['source_path' if key=='source_sha512' else 'master_path'],conv[key])
    if decision.get('qa_report_sha512')!=entry['qa_report_sha512']:raise RuntimeError('Decision QA report digest differs')
    verify(entry['qa_report_path'],entry['qa_report_sha512'])
    if decision.get('verdict') not in {'accept','reject'} or not str(decision.get('reason','')).strip():
        raise RuntimeError('Decision requires accept/reject and reason')
    timestamp=datetime.fromisoformat(decision['reviewed_at'].replace('Z','+00:00'))
    if timestamp.tzinfo is None or timestamp>datetime.now(timezone.utc):raise RuntimeError('Review time must be timezone-qualified and not in the future')
    checksum=digest(decision_path);target=obj/'documentation'/('qa_adjudication_'+checksum[:16]+'.json')
    if target.exists():verify(target,checksum)
    else:shutil.copy2(decision_path,target);verify(target,checksum)
    if 'automated_status' not in entry:entry['automated_status']=entry['status']
    entry['status']='MANUAL_QA_CLEAR' if decision['verdict']=='accept' else 'FLAGGED_FOR_REVIEW'
    entry['manual_adjudication']={'path':str(target.resolve()),'sha512':checksum,
        'file_id':registered_uuid(obj,'file',str(target.resolve())+checksum),
        'reviewer':reviewer,'reviewed_at':decision['reviewed_at'],'reason':decision['reason']}
    record['clear_count']=sum(e['status'] in {'AUTO_QA_CLEAR','MANUAL_QA_CLEAR'} for e in entries)
    record['flagged_count']=len(entries)-record['clear_count']
    record['status']='STEP_4_MANUAL_QA_CLEAR' if not record['flagged_count'] else 'STEP_4_QA_FLAGGED_HOLD'
    write_record(record_path,record)
    return record

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--object-dir',type=Path,required=True)
    ap.add_argument('--decision',type=Path,required=True)
    ap.add_argument('--agent-registry',type=Path,required=True)
    a=ap.parse_args()
    try:
        result=adjudicate(a.object_dir,a.decision,a.agent_registry)
        print(result['status']);return 10 if result['flagged_count'] else 0
    except Exception as exc:print('[HOLD]',exc);return 10
if __name__=='__main__':raise SystemExit(main())
