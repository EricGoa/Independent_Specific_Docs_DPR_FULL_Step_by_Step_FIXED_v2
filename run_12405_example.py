"""Run the user-requested live 12405.26 example in a separate workspace."""
import argparse
import os
import hashlib
import json
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parent
JOB='2026_012405'
JAVA=Path(r'C:\Users\Gao\Downloads\droid-binary-6.8.1-bin-win64-with-jre\jre\bin\java.exe')
JAR=JAVA.parents[2]/'droid-command-line-6.8.1.jar'
LICENCE=Path(r'C:\Users\Gao\Downloads\PdfComplianceCheck\PdfComplianceCheck\PdfComplianceCheck\Aspose.PDF.NET 2.lic')
VERA=Path(r'C:\Program Files\Verapdf\verapdf.bat')

def sha(path):
    h=hashlib.sha512()
    with path.open('rb') as stream:
        for b in iter(lambda:stream.read(1024*1024),b''):h.update(b)
    return h.hexdigest()

def snapshot():
    return {str(p.relative_to(ROOT)):sha(p) for top in ['input','preparation','staging','AIP'] for p in (ROOT/top).rglob('*') if p.is_file()}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--run-dir',type=Path);parser.add_argument('--start-stage',type=int,default=1);args=parser.parse_args()
    run=args.run_dir.resolve() if args.run_dir else ROOT/'runs'/('r'+uuid.uuid4().hex[:6])
    run.resolve().relative_to(ROOT.resolve())
    run.mkdir(parents=True,exist_ok=bool(args.run_dir));(run/'logs').mkdir(exist_ok=True)
    baseline=snapshot()
    for name in ['poc1_builder.py','workflow_common.py']:
        if not (run/name).exists():shutil.copy2(ROOT/name,run/name)
    converter=run/'converter'
    if not (converter/'bin/Debug/net8.0/DprPdfaConverter.dll').exists():
        converter.mkdir(exist_ok=True)
        for name in ['Program.cs','DprPdfaConverter.csproj']:
            shutil.copy2(ROOT/'DprPdfaConverter'/name,converter/name)
        sdk=Path(r'C:\Users\Gao\Downloads\dotnet-sdk-8.0.423-win-x64\dotnet.exe')
        build=subprocess.run([str(sdk),'build',str(converter/'DprPdfaConverter.csproj'),'--ignore-failed-sources','--nologo'],capture_output=True,text=True)
        (run/'logs/converter_build.txt').write_text(build.stdout+'\n'+build.stderr,encoding='utf-8')
        if build.returncode: raise RuntimeError('Isolated converter build failed; see logs/converter_build.txt')
    cfg_path=run/'run_registry.json'
    if not cfg_path.exists():
        config=json.loads((ROOT/'preservation_config.json').read_text(encoding='utf-8'))
        config['notice']='User-authorized isolated live example for DmsJobId=12405.26; provisional registry, not production accession.'
        config['operator']={'uuid':str(uuid.uuid4()),'name':'Eric Gao','email':'eric.gao@wto.org','type':'person','note':'Example explicitly requested by the user; name/email supplied in the project team correspondence; registry remains provisional.'}
        config['agents']['droid']={'uuid':str(uuid.uuid4()),'name':'DROID','type':'software','version':'6.8.1; signature version recorded in raw tool evidence','note':'Installed droid-command-line-6.8.1.jar; signature details retained in profile/driver evidence.'}
        source_sets={'conversion_script':['poc2_aspose.py','workflow_common.py','DprPdfaConverter/Program.cs'], 'packaging':['poc7_bagit.py','profile_workflow.py','workflow_common.py'], 'verification':['poc8_ingest_worker.py','profile_workflow.py','workflow_common.py'], 'preparation':['poc1_builder.py','workflow_common.py'],
            'metadata':['poc6_metadata.py','profile_workflow.py','workflow_common.py','crosswalk_metadata.py','mets_profile.py','adjudicate_qa.py'],
            'validation':['poc3_verapdf.py','workflow_common.py'],'qa':['poc4_qa.py','workflow_common.py'],
            'ingest':['poc8_ingest_worker.py','poc9_ocfl_ingest.py','profile_workflow.py','workflow_common.py','ocfl_versions.py','mets_profile.py']}
        for key,names in source_sets.items():
            h=hashlib.sha256()
            for name in names:h.update(name.encode());h.update((ROOT/name).read_bytes())
            config['agents'][key]['uuid']=str(uuid.uuid4())
            config['agents'][key]['version']='source-set SHA-256:'+h.hexdigest()
            config['agents'][key]['note']='User-authorized working registry; measured source identity: '+', '.join(names)
        config['agents']['droid']['version']='6.8.1; signatures V118; container signatures 20240501'
        cfg_path.write_text(json.dumps(config,indent=2),encoding='utf-8')
    os.environ['JAVACMD']=str(JAVA)
    prep=run/'preparation';staging=run/'staging';aip=run/'AIP';records=run/'ingest-records'
    stages=[
      ('01_preparation','specific_step1.py',['--root',run,'--job-id',JOB]),
      ('02_source_droid','poc5_droid.py',['--preparation-dir',prep,'--job-id',JOB,'--java',JAVA,'--droid-jar',JAR]),
      ('03_conversion','poc2_aspose.py',['--preparation-dir',prep,'--job-id',JOB,'--converter-project',run/'converter/DprPdfaConverter.csproj','--license',LICENCE,'--allow-signed-derivative']),
      ('04_verapdf','poc3_verapdf.py',['--preparation-dir',prep,'--job-id',JOB,'--verapdf',VERA]),
      ('05_qa','poc4_qa.py',['--preparation-dir',prep,'--job-id',JOB]),
      ('06_metadata','poc6_metadata.py',['--profile','--preparation-dir',prep,'--dms-job-id','12405.26','--server','Pekan.ext.wto, 14133','--database','DOL2DB','--driver','ODBC Driver 17 for SQL Server','--xsd',ROOT/'schemas/dol-metadata-v-03.xsd','--aip-root',aip,'--agent-registry',cfg_path,'--java',JAVA,'--droid-jar',JAR]),
      ('07_bagit','poc7_bagit.py',['--preparation-dir',prep,'--staging-dir',staging,'--job-id',JOB,'--aip-root',aip,'--agent-registry',cfg_path]),
      ('08_ingest_verification','poc8_ingest_worker.py',['--staging-dir',staging,'--job-id',JOB,'--aip-root',aip,'--ingest-records-dir',records,'--agent-registry',cfg_path,'--no-move-on-fail']),
      ('09_ocfl','poc9_ocfl_ingest.py',['--staging-dir',staging,'--job-id',JOB,'--aip-root',aip,'--workspace-dir',run/'w','--ingest-records-dir',records,'--agent-registry',cfg_path,'--ocfl-validator',ROOT/'validate_ocfl.py','--validator-python',sys.executable,'--require-independent-validator']),
    ]
    report_path=run/'run_result.json'
    report=json.loads(report_path.read_text()) if report_path.exists() else {'job_id':JOB,'dms_job_id':'12405.26','run_directory':str(run),'started_utc':datetime.now(timezone.utc).isoformat(),'external_tools':'REAL; no mocks','production_accession':False,'registry_status':'PROVISIONAL','stages':[]}
    for index,(name,script,options) in enumerate(stages,1):
        if index<args.start_stage:continue
        print('[RUN]',name,flush=True);start=time.monotonic()
        result=subprocess.run([sys.executable,str(ROOT/script)]+[str(a) for a in options],cwd=ROOT,capture_output=True,text=True,encoding='utf-8',errors='replace')
        log=run/'logs'/(name+'_'+uuid.uuid4().hex[:6]+'.txt');log.write_text(result.stdout+'\nSTDERR\n'+result.stderr,encoding='utf-8')
        report['stages'].append({'stage':name,'exit_code':result.returncode,'seconds':round(time.monotonic()-start,2),'log':str(log)})
        report['status']='RUNNING' if not result.returncode else 'HELD_AT_'+name.upper()
        report['existing_production_files_unchanged']=baseline==snapshot()
        report_path.write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(result.stdout[-3500:],flush=True)
        if result.stderr:print(result.stderr[-2000:],flush=True)
        if result.returncode:
            print('[HOLD]',name,'exit',result.returncode,'REPORT',report_path,flush=True);return result.returncode
    report['status']='COMPLETED_ISOLATED_LIVE_EXAMPLE';report['completed_utc']=datetime.now(timezone.utc).isoformat();report['existing_production_files_unchanged']=baseline==snapshot()
    report['sip_path']=str(staging/JOB);report['aip_path']=str(aip/'2026/012000'/JOB)
    report_path.write_text(json.dumps(report,indent=2),encoding='utf-8');print('[COMPLETE]',report_path,flush=True);return 0

if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8');raise SystemExit(main())
