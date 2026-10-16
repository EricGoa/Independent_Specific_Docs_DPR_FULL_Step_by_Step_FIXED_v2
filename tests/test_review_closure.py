"""Evidence-based checks for outstanding review fixes and local design."""
import json,tempfile,unittest
from pathlib import Path
from lxml import etree as E
import profile_workflow as p
from workflow_common import export_uuid_registry
import test_team_profile as team

class ReviewClosureTests(unittest.TestCase):
 def test_all_workbook_controlled_values_and_reject_wrong_attributes(self):
  import openpyxl
  sheet=openpyxl.load_workbook(p.HERE/'team-review/2026_011922_premis_conformance_review.xlsx',data_only=True)['Controlled values']
  for name,value,authority,authorityuri,valueuri,*_ in list(sheet.values)[1:]:
   if not name:continue
   root=E.Element('test')
   if name=='eventType':
    code=next(code for label,code in p.EVENTS.values() if label==value)
    attrs={'authority':'local'} if code is None else {'authority':'eventType','authorityURI':'http://id.loc.gov/vocabulary/preservation/eventType','valueURI':'http://id.loc.gov/vocabulary/preservation/eventType/'+code}
    node=p.add(root,name,value,**attrs)
   else:node=p.cv(root,name,value)
   expected={'authority':authority}
   if authorityuri:expected['authorityURI']=authorityuri
   if valueuri:expected['valueURI']=valueuri
   self.assertEqual(dict(node.attrib),expected,(name,value))
   p.validate_controlled_values(root)
   node.set('authority','incorrect')
   with self.assertRaises(RuntimeError):p.validate_controlled_values(root)
 def test_actual_log_findings_quoted_without_inferred_repairs(self):
  with tempfile.TemporaryDirectory() as d:
   log=Path(d)/'log.xml';log.write_text('<Compliance><Problem Severity="Error" Clause="6.6.4">Missing property</Problem></Compliance>')
   text=p.converter_outcome_note({'conversion_log':str(log),'converter_status':'CONVERTED'})
   self.assertIn('Missing property',text);self.assertIn('Clause=6.6.4',text);self.assertIn('not individual repair assertions',text)
 def test_action_policy_holds_unsupported_and_restricted(self):
  self.assertEqual(p.route_selection('new_document',{'last_dms_action':'NEW','restriction_type_id':91},False),'new_document')
  self.assertEqual(p.route_selection('language_added',{'last_dms_action':'NEW','restriction_type_id':91},True),'language_added')
  self.assertEqual(p.route_selection('metadata_change',{'last_dms_action':'DMS_META_DATA_CHANGE','restriction_type_id':91},True),'metadata_change')
  for action,restriction in [('PATH_FILE_WRONG',91),('NEW',2),('unexpected',91)]:
   with self.assertRaises(RuntimeError):p.route_selection('modified_files',{'last_dms_action':action,'restriction_type_id':restriction},True)
 def test_csv_registry_appends_and_rejects_rewritten_identity(self):
  with tempfile.TemporaryDirectory() as d:
   registry=p.Registry(d,'2026_000001');registry.assign('first','file')
   path=export_uuid_registry(registry.path);old=path.read_bytes()
   registry.assign('second','file');export_uuid_registry(registry.path)
   self.assertTrue(path.read_bytes().startswith(old))
   export_uuid_registry(registry.path);self.assertEqual(len(path.read_text().splitlines()),3)
   data=json.loads(registry.path.read_text());next(iter(data['entries'].values()))['description']='changed'
   registry.path.write_text(json.dumps(data))
   with self.assertRaises(RuntimeError):export_uuid_registry(registry.path)
 def test_dome_event_extraction_outcome_and_mets_membership(self):
  h=team.TeamProfileTests();h.setUp()
  try:
   h.ready()
   root=p.validate_premis(h.h.obj,h.h.obj.name)
   event=p.event_details(root,'DPM116')[0][0]
   outcomes=event.xpath('p:linkingObjectIdentifier[p:linkingObjectRole="outcome"]/p:linkingObjectIdentifierValue/text()',namespaces=p.NS)
   self.assertEqual(len(outcomes),2)
   from mets_profile import validate
   payload=h.bag()/'data'
   mets=validate(payload,h.h.obj.name)
   rep=mets.xpath('//m:structMap//m:div[@LABEL="rep_1"]',namespaces=p.NS)[0]
   rep.set('LABEL','wrong')
   p.serialize(payload/p.mets_path(h.h.obj.name),mets)
   with self.assertRaises(RuntimeError):validate(payload,h.h.obj.name)
  finally:h.tearDown()

 def test_human_qa_decision_is_bound_to_exact_evidence(self):
  from adjudicate_qa import adjudicate
  from workflow_common import load,digest,write_record
  h=team.TeamProfileTests();h.setUp()
  try:
   h.ready()
   record_path=h.h.obj/'documentation'/f'{h.h.obj.name}_poc4_qa_record.json'
   record=load(record_path);entry=record['qa_entries'][0]
   record['status']='STEP_4_QA_FLAGGED_HOLD';entry['status']='FLAGGED_FOR_REVIEW'
   write_record(record_path,record)
   conversion=load(h.h.obj/'documentation'/f'{h.h.obj.name}_poc2_conversion_record.json')['conversions'][0]
   config=p.configuration()
   decision={'reviewer_uuid':config['operator']['uuid'],'master_file_id':entry['master_file_id'],
     'source_sha512':conversion['source_sha512'],'master_sha512':conversion['master_sha512'],
     'qa_report_sha512':entry['qa_report_sha512'],'reviewed_at':p.now(),'verdict':'accept',
     'reason':'Synthetic explicit reviewer decision'}
   path=h.root/'decision.json';path.write_text(json.dumps(decision))
   before=digest(Path(entry['qa_report_path']))
   decision['master_sha512']='0'*128;path.write_text(json.dumps(decision))
   with self.assertRaises(RuntimeError):adjudicate(h.h.obj,path,p.HERE/'preservation_config.json')
   decision['master_sha512']=conversion['master_sha512'];path.write_text(json.dumps(decision))
   result=adjudicate(h.h.obj,path,p.HERE/'preservation_config.json')
   self.assertEqual(result['status'],'STEP_4_MANUAL_QA_CLEAR')
   self.assertEqual(before,digest(Path(entry['qa_report_path'])))
   h.metadata()
   root=p.validate_premis(h.h.obj,h.h.obj.name)
   events=p.event_details(root,'DPM108')
   self.assertTrue(any(e.xpath('p:linkingAgentIdentifier[p:linkingAgentRole="implementer"]',namespaces=p.NS) for e,_ in events))
  finally:h.tearDown()

 def test_explicit_reconversion_retains_v1_and_passes_new_gates(self):
  from unittest.mock import patch
  import poc2_aspose as p2
  from workflow_common import load,digest
  import uuid
  h=team.TeamProfileTests();h.setUp()
  try:
   h.ready();bag=h.bag();self.assertEqual(h.verify(bag),0);self.assertEqual(h.ingest(bag),0)
   record=h.h.obj/'documentation'/f'{h.h.obj.name}_poc2_conversion_record.json'
   original=load(record)['conversions'][0];master=Path(original['master_path']);before=digest(master)
   token=str(uuid.uuid4())
   args=['--preparation-dir',h.h.prep,'--job-id',h.h.obj.name,'--converter-project',h.h.project,
         '--license',h.h.license,'--reconvert-source-id',original['source_file_id'],'--reconversion-token',token]
   with patch.object(p2,'run_converter',side_effect=h.h.converter):
    self.assertEqual(h.h.cli(p2,args),0,getattr(h.h,'last_error',''))
    calls=h.h.calls['convert'];self.assertEqual(h.h.cli(p2,args),0)
    self.assertEqual(calls,h.h.calls['convert'])
   new=load(record)['conversions'][0]
   self.assertNotEqual(new['master_file_id'],original['master_file_id'])
   self.assertNotEqual(new['master_path'],original['master_path'])
   self.assertEqual(before,digest(master))
   h.h.stages();h.metadata()
   second=h.bag('reconversion');self.assertEqual(h.verify(second),0);self.assertEqual(h.ingest(second),0,getattr(h.h,'last_error',''))
   obj=p.object_path(h.aip,h.h.obj.name);inv=load(obj/'inventory.json')
   self.assertEqual(inv['head'],'v2');self.assertTrue(inv['versions']['v2']['message'].startswith('master_reconversion'))
  finally:h.tearDown()
