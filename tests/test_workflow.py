"""Synthetic regressions: real PDF QA, mocked SQL and external preservation tools."""
import contextlib, io, json, shutil, sys, tempfile, unittest, zipfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from pypdf import PdfWriter
import poc1_builder as p1
import poc2_aspose as p2
import poc3_verapdf as p3
import poc4_qa as p4
import poc5_droid as p5
import poc6_metadata as p6
import poc7_bagit as p7
import poc8_ingest_worker as p8
import poc9_ocfl_ingest as p9
import poc_monitor
from workflow_common import digest, load, input_copy, write_record
from ocfl_versions import build_delta, commit_delta, validate
JOB='2026_000001'
PROJECT=Path(__file__).resolve().parents[1]
class WorkflowTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(prefix='dpr-synthetic-');self.root=Path(self.tmp.name)
  self.input=self.root/'input';self.input.mkdir();self.prep=self.root/'preparation';self.obj=self.prep/JOB
  self.calls={'convert':0,'validate':0,'droid':0}
  self.license=self.root/'fake.lic';self.license.write_text('synthetic');self.project=self.root/'fake.csproj';self.project.write_text('synthetic')
 def tearDown(self): self.tmp.cleanup()
 def stage(self,name,language='en',notification=True):
  pdf=self.input/name;w=PdfWriter();w.add_blank_page(width=200,height=200);w.add_metadata({'/Title':name});w.write(pdf)
  row={'job_id':JOB,'language':language,'pdf_file':pdf.name,'source_file':'','document_kind':'notification' if notification else 'dol','pdf_original_path':'F:\\original\\'+name}
  if not notification:
   doc=self.input/(pdf.stem+'.docx')
   with zipfile.ZipFile(doc,'w') as z: z.writestr('word/document.xml','<document/>')
   row['source_file']=doc.name;row['source_original_path']='T:\\original\\'+doc.name
  return p1.process_row(row,self.input,self.prep),row
 def cli(self,module,args):
  capture=io.StringIO()
  with patch.object(sys,'argv',[module.__name__]+[str(a) for a in args]),contextlib.redirect_stdout(capture),contextlib.redirect_stderr(capture): result=module.main()
  if result: self.last_error=capture.getvalue()
  return result
 def converter(self,**kw):
  self.calls['convert']+=1;shutil.copy2(kw['source'],kw['master']);kw['conversion_log'].write_text('<conversion/>');return 'SYNTHETIC_COPY','',''
 def verapdf(self,**kw):
  self.calls['validate']+=1;kw['report_path'].write_text('<report><validationReport isCompliant="true" profileName="PDF/A-2b"/></report>');return p3.parse_verapdf_xml(kw['report_path']),'','',0
 def droid(self,java,jar,files,docs,job):
  import csv
  self.calls['droid']+=1;token=str(self.calls['droid']);profile=docs/f'{job}_{token}_droid_profile.droid';profile.write_text('synthetic');out=docs/f'{job}_{token}_droid_export.csv'
  with out.open('w',newline='',encoding='utf-8') as stream:
   writer=csv.DictWriter(stream,fieldnames=['NAME','FILE_PATH','PUID','MIME_TYPE','FORMAT_NAME']);writer.writeheader()
   for f in files: writer.writerow({'NAME':f.name,'FILE_PATH':str(f.resolve()),'PUID':'fmt/18','MIME_TYPE':'application/pdf','FORMAT_NAME':'Synthetic'})
  driver=docs/f'{job}_{token}_droid_driver.txt';driver.write_text('synthetic');return profile,out,driver
 def stages(self):
  with patch.object(p5,'verify_runtime',return_value='synthetic'),patch.object(p5,'droid_characterize',side_effect=self.droid): self.assertEqual(self.cli(p5,['--preparation-dir',self.prep,'--job-id',JOB,'--java',self.root/'java','--droid-jar',self.root/'droid.jar']),0)
  with patch.object(p2,'run_converter',side_effect=self.converter): self.assertEqual(self.cli(p2,['--preparation-dir',self.prep,'--job-id',JOB,'--converter-project',self.project,'--license',self.license]),0)
  with patch.object(p3,'run_verapdf',side_effect=self.verapdf): self.assertEqual(self.cli(p3,['--preparation-dir',self.prep,'--job-id',JOB,'--verapdf',self.root/'verapdf']),0)
  self.assertEqual(self.cli(p4,['--preparation-dir',self.prep,'--job-id',JOB]),0)
 def metadata(self):
  with patch.object(p6,'extract',return_value={'Job ID':JOB,'Title - E':'Synthetic test'}): self.assertEqual(self.cli(p6,['--preparation-dir',self.prep,'--dms-job-id','1.26','--server','unused','--database','unused','--xsd',PROJECT/'DOME_POC/dol-metadata-v-03-poc.xsd']),0)
 def ingest(self,batch):
  staging=self.root/batch;records=self.root/'records'
  for module,args in [(p7,['--preparation-dir',self.prep,'--staging-dir',staging,'--job-id',JOB]),(p8,['--staging-dir',staging,'--ingest-records-dir',records,'--job-id',JOB]),(p9,['--staging-dir',staging,'--ingest-records-dir',records,'--aip-root',self.root/'AIP','--job-id',JOB])]: self.assertEqual(self.cli(module,args),0,module.__name__)
  return staging/JOB,self.root/'AIP/2026/000000'/JOB
 def test_mixed_incremental_workflow(self):
  for lg in ('en','fr','es'): self.stage('dol_'+lg+'.pdf',lg,False)
  self.stage('second_en.pdf','en',False)
  for name,lg in [('one_src.pdf','en'),('two.pdf','en'),('unknown.pdf','und')]: self.stage(name,lg)
  original=load(self.obj/'documentation'/f'{JOB}_poc1_processing_record.json');self.assertEqual(len(original['files']),11);self.assertEqual(len({f['file_id'] for f in original['files']}),11)
  self.stages();self.metadata();calls=dict(self.calls);self.stages();self.metadata();self.assertEqual(calls,self.calls)
  bag,ocfl=self.ingest('batch1');v1={p.relative_to(ocfl/'v1').as_posix():digest(p) for p in (ocfl/'v1').rglob('*') if p.is_file()};self.assertFalse(validate(ocfl,original['folder_uuid']))
  dome=(self.obj/'metadata'/f'{JOB}_dome.xml').read_text(encoding='utf-8');self.assertIn('attachments/',dome);self.assertIn('und',dome);self.assertNotIn(f'{JOB}_en_src.pdf',dome);self.assertIn('one_src.pdf',dome)
  premis=(self.obj/'metadata'/f'{JOB}_premis.xml').read_text(encoding='utf-8');self.assertIn('derivation',premis)
  for f in original['files']: self.assertIn(f['file_id'],premis)
  self.stage('incremental.pdf','fr')
  with self.assertRaises(RuntimeError): p6.require_prior(self.obj,JOB)
  self.stages();self.metadata();self.ingest('batch2');self.assertEqual(self.calls['convert'],calls['convert']+1);self.assertEqual(self.calls['validate'],calls['validate']+1)
  self.assertEqual(load(ocfl/'inventory.json')['head'],'v2');self.assertEqual(v1,{p.relative_to(ocfl/'v1').as_posix():digest(p) for p in (ocfl/'v1').rglob('*') if p.is_file()});self.assertFalse(validate(ocfl,original['folder_uuid']))
  self.ingest('batch2');self.assertEqual(load(ocfl/'inventory.json')['head'],'v2')
 def test_preparation_rerun_preserves_ids(self):
  record,row=self.stage('first.pdf','en',False);again=p1.process_row(row,self.input,self.prep);self.assertEqual(record.files,again.files);self.assertEqual(record.folder_uuid,again.folder_uuid)
  self.stage('second.pdf','en',False);final=p1.process_row(row,self.input,self.prep);self.assertEqual(len(final.files),4);self.assertTrue(final.files[1].original_path.startswith('F:'))
 def test_tamper_blocks_reuse(self):
  self.stage('notice.pdf');self.stages();conv=load(self.obj/'documentation'/f'{JOB}_poc2_conversion_record.json')['conversions'][0];Path(conv['master_path']).write_bytes(b'changed')
  with patch.object(p2,'run_converter',side_effect=AssertionError('must not convert')): self.assertNotEqual(self.cli(p2,['--preparation-dir',self.prep,'--job-id',JOB,'--converter-project',self.project,'--license',self.license]),0)
 def test_no_inferred_verapdf_pass(self):
  report=self.root/'invalid.xml';report.write_text('<validationReport failedRules="0"/>')
  with self.assertRaises(p3.Poc3Error): p3.parse_verapdf_xml(report)
 def test_non_pdf_notification_rejected(self):
  (self.input/'notice.txt').write_text('not PDF')
  with self.assertRaises(p1.PocError): p1.process_row({'job_id':JOB,'language':'en','document_kind':'notification','pdf_file':'notice.txt'},self.input,self.prep)
 def test_monitor_json(self):
  path=self.root/'live.json';poc_monitor.write_live_state(path,[]);self.assertEqual(load(path)['job_count'],0)
 def test_delta_retains_previous_paths(self):
  self.stage('notice.pdf');self.stages();self.metadata();bag,ocfl=self.ingest('batch1');old=load(ocfl/'inventory.json')
  delta=self.root/'delta';(delta/'data').mkdir(parents=True);(delta/'data/new.txt').write_text('incremental');workspace=self.root/'delta-workspace'
  inv,copies=build_delta(delta,{'data/new.txt':digest(delta/'data/new.txt')},old['id'],ocfl,workspace,'test',None,p9.utc_now_seconds());commit_delta(workspace,ocfl,'v1');self.assertFalse(validate(ocfl,old['id']))
  new_paths={p for paths in inv['versions']['v2']['state'].values() for p in paths};old_paths={p for paths in old['versions']['v1']['state'].values() for p in paths};self.assertTrue(old_paths < new_paths)
 def test_duplicate_basenames_distinct_origins(self):
  left=self.root/'left';right=self.root/'right';left.mkdir();right.mkdir()
  (left/'same.pdf').write_bytes(b'identical');(right/'same.pdf').write_bytes(b'identical')
  a=input_copy(left/'same.pdf',self.input/'same.pdf');b=input_copy(right/'same.pdf',self.input/'same.pdf')
  self.assertNotEqual(a,b);self.assertEqual(b,input_copy(right/'same.pdf',self.input/'same.pdf'))
 def test_incremental_replacement_preserves_original_attachment(self):
  first,row=self.stage('notice.pdf');original=first.files[0]
  writer=PdfWriter();writer.add_blank_page(width=300,height=300);writer.write(self.input/'notice.pdf')
  second=p1.process_row(row,self.input,self.prep)
  self.assertEqual(len(second.files),2);self.assertEqual(digest(Path(original.staged_path)),original.sha512_staged)
  self.assertEqual(len(p1.process_row(row,self.input,self.prep).files),2)
 def test_unsafe_manifest_is_rejected(self):
  bag=self.root/'bag';bag.mkdir();manifest=bag/'manifest-sha512.txt';manifest.write_text('0'*128+'  ../outside.pdf\n')
  self.assertTrue(p8.validate_manifest(manifest,bag)[2]);self.assertTrue(p7.validate_manifest(manifest,bag)[0])
 def test_legacy_attachment_record_converts_without_renaming_original(self):
  record,row=self.stage('legacy_notification_en.pdf')
  path=self.obj/'documentation'/f'{JOB}_poc1_processing_record.json'
  data=load(path);data['files'][0]['original_path']=str((self.input/row['pdf_file']).resolve());write_record(path,data)
  before=digest(Path(data['files'][0]['staged_path']));self.stages()
  self.assertEqual(before,digest(Path(data['files'][0]['staged_path'])))
  conversion=load(self.obj/'documentation'/f'{JOB}_poc2_conversion_record.json')['conversions'][0]
  self.assertEqual(conversion['source_file_id'],data['files'][0]['file_id']);self.assertNotEqual(conversion['master_file_id'],conversion['source_file_id'])
 def test_qa_detects_changed_page_count(self):
  self.stage('notice.pdf');source=self.input/'notice.pdf';master=self.root/'two-pages.pdf'
  writer=PdfWriter();writer.add_blank_page(width=200,height=200);writer.add_blank_page(width=200,height=200);writer.write(master)
  result=p4.perform_qa(source,master);self.assertFalse(result['automated_qa_clear']);self.assertIn('page_count',result['flags'])
 def test_conversion_resumes_after_partial_failure(self):
  self.stage('a.pdf');self.stage('b.pdf')
  def partial(**kw):
   if kw['source'].name=='b.pdf': raise p2.Poc2Error('synthetic tool failure')
   return self.converter(**kw)
  args=['--preparation-dir',self.prep,'--job-id',JOB,'--converter-project',self.project,'--license',self.license]
  with patch.object(p2,'run_converter',side_effect=partial): self.assertNotEqual(self.cli(p2,args),0)
  previous=load(self.obj/'documentation'/f'{JOB}_poc2_conversion_record.json')['conversions'][0]
  with patch.object(p2,'run_converter',side_effect=self.converter): self.assertEqual(self.cli(p2,args),0)
  current=load(self.obj/'documentation'/f'{JOB}_poc2_conversion_record.json')['conversions']
  self.assertEqual(current[0],previous);self.assertEqual(self.calls['convert'],2)
 def test_package_replacement_is_disabled(self):
  self.stage('notice.pdf');self.stages();self.metadata();bag,ocfl=self.ingest('batch1');before=digest(bag/'manifest-sha512.txt')
  self.stage('new.pdf');self.stages();self.metadata()
  result=self.cli(p7,['--preparation-dir',self.prep,'--staging-dir',bag.parent,'--job-id',JOB,'--replace-existing'])
  self.assertNotEqual(result,0);self.assertEqual(before,digest(bag/'manifest-sha512.txt'))
if __name__=='__main__': unittest.main()
