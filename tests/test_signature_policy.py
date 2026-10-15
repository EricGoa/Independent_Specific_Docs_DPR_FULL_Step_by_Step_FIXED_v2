"""Signed-derivative policy is opt-in and retains source fixity/identity."""
import unittest
from unittest.mock import patch
import test_workflow as fixtures
import poc2_aspose as p2
from workflow_common import digest,load

class SignaturePolicyTests(unittest.TestCase):
    def setUp(self):
        self.h=fixtures.WorkflowTests();self.h.setUp()
    def tearDown(self): self.h.tearDown()
    def test_explicit_policy_reaches_converter_and_is_recorded(self):
        record,_=self.h.stage('synthetic.pdf');source=record.files[0];before=digest(self.h.input/'synthetic.pdf')
        def converter(**kw):
            self.assertTrue(kw.get('allow_signed_derivative'))
            self.h.converter(**kw)
            return 'CONVERTED_UNSIGNED_DERIVATIVE','SIGNATURES_REMOVED_IN_DERIVATIVE=1',''
        args=['--preparation-dir',self.h.prep,'--job-id',fixtures.JOB,'--converter-project',self.h.project,'--license',self.h.license,'--allow-signed-derivative']
        with patch.object(p2,'run_converter',side_effect=converter): self.assertEqual(self.h.cli(p2,args),0)
        conversion=load(self.h.obj/'documentation'/f'{fixtures.JOB}_poc2_conversion_record.json')['conversions'][0]
        self.assertEqual(conversion['source_file_id'],source.file_id)
        self.assertIn('signed original retained',conversion['signature_policy'])
        self.assertEqual(before,digest(self.h.input/'synthetic.pdf'))
    def test_signature_removal_is_not_enabled_by_default(self):
        self.h.stage('synthetic.pdf')
        def converter(**kw):
            self.assertNotIn('allow_signed_derivative',kw)
            return self.h.converter(**kw)
        args=['--preparation-dir',self.h.prep,'--job-id',fixtures.JOB,'--converter-project',self.h.project,'--license',self.h.license]
        with patch.object(p2,'run_converter',side_effect=converter): self.assertEqual(self.h.cli(p2,args),0)

    def test_atomic_record_retries_a_transient_windows_share_lock(self):
        import workflow_common as common
        target=self.h.root/'record.json';original_replace=common.os.replace;calls=[]
        def replace(source,destination):
            calls.append(1)
            if len(calls)==1:
                error=PermissionError('transient SMB sharing lock');error.winerror=32;raise error
            return original_replace(source,destination)
        with patch.object(common.os,'replace',side_effect=replace),patch.object(common.time,'sleep') as sleep:
            common.write_record(target,{'verified':True})
        self.assertEqual(common.load(target),{'verified':True});self.assertEqual(len(calls),2);sleep.assert_called_once()
