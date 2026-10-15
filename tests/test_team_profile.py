"""Team-profile integration and negative ingest gates; all documents are synthetic."""
import copy,json,sys,unittest
from pathlib import Path
from unittest.mock import patch
from lxml import etree as E
import test_workflow as fixtures
JOB,PROJECT=fixtures.JOB,fixtures.PROJECT
import poc6_metadata as p6
import poc7_bagit as p7
import poc8_ingest_worker as p8
import poc9_ocfl_ingest as p9
from workflow_common import load,digest
import profile_workflow as profile

class TeamProfileTests(unittest.TestCase):
 def setUp(self):
  self.h=fixtures.WorkflowTests();self.h.setUp();self.root=self.h.root;self.aip=self.root/'AIP';self.records=self.root/'records'
 def tearDown(self): self.h.tearDown()
 def run_ok(self,module,args):
  result=self.h.cli(module,args)
  self.assertEqual(result,0,getattr(self.h,'last_error',''))
 def metadata(self):
  with patch.object(p6,'extract',return_value={'Job ID':JOB,'CatHeaderId':'123','Title - E':'Synthetic','Original language':'E','Symbol':'WT/TEST/1'}):
   self.run_ok(p6,['--profile','--preparation-dir',self.h.prep,'--dms-job-id','1.26','--server','unused','--database','unused','--xsd',PROJECT/'schemas/dol-metadata-v-03.xsd','--aip-root',self.aip])
 def ready(self):
  self.h.stage('notice.pdf');self.h.stages();self.metadata()
 def bag(self,batch='batch1'):
  self.run_ok(p7,['--preparation-dir',self.h.prep,'--staging-dir',self.root/batch,'--job-id',JOB,'--aip-root',self.aip]);return self.root/batch/JOB
 def verify(self,bag):
  return self.h.cli(p8,['--staging-dir',bag.parent,'--ingest-records-dir',self.records,'--job-id',JOB,'--aip-root',self.aip,'--no-move-on-fail'])
 def ingest(self,bag):
  return self.h.cli(p9,['--ocfl-validator',PROJECT/'validate_ocfl.py','--require-independent-validator','--staging-dir',bag.parent,'--ingest-records-dir',self.records,'--job-id',JOB,'--aip-root',self.aip])
 def retag(self,bag,key,value):
  text=(bag/'bag-info.txt').read_text(encoding='utf-8');text='\n'.join(key+': '+value if line.startswith(key+':') else line for line in text.splitlines())+'\n';(bag/'bag-info.txt').write_text(text,encoding='utf-8');p7.write_tagmanifest(bag)
 def test_profile_v1_v2_preserves_history_and_original_bag(self):
  self.ready();bag=self.bag();self.assertEqual(self.verify(bag),0,getattr(self.h,'last_error',''))
  before={p.relative_to(bag).as_posix():digest(p) for p in bag.rglob('*') if p.is_file()}
  self.assertEqual(self.ingest(bag),0,getattr(self.h,'last_error',''))
  obj=profile.object_path(self.aip,JOB);inv=load(obj/'inventory.json');self.assertTrue(inv['id'].startswith('urn:uuid:'))
  commit=inv['versions']['v1'];self.assertTrue(commit['message'].startswith('new_document — '));self.assertEqual(commit['user']['address'],'mailto:preservation-test@example.invalid')
  oldpremis=E.parse(str(profile.resolve_logical(obj,inv,profile.premis_path(JOB)))).getroot()
  self.assertEqual(len(profile.event_details(oldpremis,'DPM112')),1);self.assertEqual(len(profile.event_details(oldpremis,'DPM113')),1)
  self.assertEqual(before,{p.relative_to(bag).as_posix():digest(p) for p in bag.rglob('*') if p.is_file()})
  self.h.stage('french.pdf','fr');self.h.stages();self.metadata();second=self.bag('batch2');self.assertEqual(profile.tags(second)['DPR-Predecessor-OCFL-Version'],'v1');self.assertEqual(self.verify(second),0,getattr(self.h,'last_error',''));self.assertEqual(self.ingest(second),0,getattr(self.h,'last_error',''))
  inv=load(obj/'inventory.json');self.assertEqual(inv['head'],'v2');newpremis=E.parse(str(profile.resolve_logical(obj,inv,profile.premis_path(JOB)))).getroot();profile.assert_append_only(oldpremis,newpremis)
 def test_sip_uuid_mismatch_holds_without_commit(self):
  self.ready();bag=self.bag();self.retag(bag,'External-Identifier','11111111-1111-4111-8111-111111111111');self.assertNotEqual(self.verify(bag),0);self.assertFalse(self.aip.exists())
 def test_object_uuid_mismatch_holds_without_commit(self):
  self.ready();bag=self.bag();self.retag(bag,'DPR-Object-UUID','11111111-1111-4111-8111-111111111111');self.assertNotEqual(self.verify(bag),0);self.assertFalse(self.aip.exists())
 def test_stale_predecessor_holds_at_both_ingest_stages(self):
  self.ready();first=self.bag();second=self.bag('competing');self.assertEqual(self.verify(first),0);self.assertEqual(self.verify(second),0);self.assertEqual(self.ingest(first),0,getattr(self.h,'last_error',''));self.assertNotEqual(self.ingest(second),0);self.assertEqual(load(profile.object_path(self.aip,JOB)/'inventory.json')['head'],'v1')
 def test_batch_uuid_mismatch_holds(self):
  self.ready();bag=self.bag();self.retag(bag,'Bag-Group-Identifier','11111111-1111-4111-8111-111111111111');self.assertNotEqual(self.verify(bag),0)
 def test_schema_tampering_holds(self):
  self.ready();bag=self.bag();(bag/'data/schemas/premis.xsd').write_bytes(b'<changed/>');p7.write_payload_manifest(bag);p7.write_tagmanifest(bag);self.assertNotEqual(self.verify(bag),0)
 def test_strict_profile_rejects_provisional_configuration(self):
  self.h.stage('notice.pdf');self.h.stages()
  with patch.object(p6,'extract',return_value={'Job ID':JOB}):
   result=self.h.cli(p6,['--profile','--strict-profile','--preparation-dir',self.h.prep,'--dms-job-id','1.26','--server','unused','--database','unused','--xsd',PROJECT/'schemas/dol-metadata-v-03.xsd','--aip-root',self.aip]);self.assertNotEqual(result,0)

 def test_dome_stays_identical_when_only_files_arrive(self):
  self.ready();before=digest(self.h.obj/profile.dome_path(JOB));bag=self.bag();self.assertEqual(self.verify(bag),0);self.assertEqual(self.ingest(bag),0,getattr(self.h,'last_error',''))
  self.h.stage('another.pdf');self.h.stages();self.metadata();self.assertEqual(before,digest(self.h.obj/profile.dome_path(JOB)))
 def test_independent_validator_rejects_changed_content(self):
  import subprocess
  self.ready();bag=self.bag();self.assertEqual(self.verify(bag),0);self.assertEqual(self.ingest(bag),0,getattr(self.h,'last_error',''))
  obj=profile.object_path(self.aip,JOB);inv=load(obj/'inventory.json');path=next(iter(inv['manifest'].values()))[0];(obj/path).write_bytes(b'tampered synthetic object')
  result=subprocess.run([sys.executable,str(PROJECT/'validate_ocfl.py'),str(obj)],capture_output=True,text=True);self.assertNotEqual(result.returncode,0)
 def test_job_identifier_mismatch_holds(self):
  self.ready();bag=self.bag();self.retag(bag,'Internal-Sender-Identifier','2026_999999');self.assertNotEqual(self.verify(bag),0);self.assertFalse(self.aip.exists())
 def test_commit_authorizer_mismatch_holds(self):
  self.ready();bag=self.bag();self.assertEqual(self.verify(bag),0)
  result=self.h.cli(p9,['--staging-dir',bag.parent,'--ingest-records-dir',self.records,'--job-id',JOB,'--aip-root',self.aip,'--user-name','Different Operator']);self.assertNotEqual(result,0);self.assertFalse(self.aip.exists())

 def test_profile_rejects_schema_valid_unused_semantic_unit(self):
  self.ready();bag=self.bag();path=bag/'data'/profile.premis_path(JOB);root=E.parse(str(path)).getroot();detail=root.find('.//{'+profile.P+'}eventDetailInformation');E.SubElement(detail,'{'+profile.P+'}eventDetailExtension');profile.serialize(path,root)
  with self.assertRaises(Exception): profile.validate_premis(bag/'data',JOB)

 def test_tag_change_after_verification_holds(self):
  self.ready();bag=self.bag();self.assertEqual(self.verify(bag),0);self.retag(bag,'External-Description','Changed after verification');self.assertNotEqual(self.ingest(bag),0);self.assertFalse(self.aip.exists())
