"""Synthetic DOME crosswalk cases; no production database access."""
import tempfile,unittest
from pathlib import Path
from lxml import etree as E
import profile_workflow as p

class CrosswalkTests(unittest.TestCase):
 def test_schema_language_dates_and_notification_structures(self):
  row={'Job ID':'2026_012405','Original language':'EN',
   'DOME crosswalk':{'jobID':['12405.26'],'documentDate':['2026-10-05'],
    'language':['en','fr','es'],
    'pages':[{'value':'0','language':'en'},{'value':'2','language':'fr'}],
    'dateIssued':[{'value':'2026-10-12','language':'fr'}],
    'accessRights':['Public'],'ACLInfo':['U'],
    'requirementName':['Synthetic requirement'],'requirementShortName':['SYN'],
    'notificationAttachment':[{'value':'a.pdf','url':'2026/TBT/a.pdf','language':'en'},
                              {'value':'b.pdf','url':'2026/TBT/b.pdf','language':'en'}],
    'tradeCoverage':[{'scheme':'ICS','code':'65.100'},{'scheme':'HS','code':'3808'},
                     {'scheme':'HS','code':'3808'}]},
   'Crosswalk provenance':{'schema_unsupported':{'CRNid':['123'],'TraslationRule':True}}}
  tree=p.build_dome(row,'2026-10-16T08:00:00Z','2026-10-16T08:00:00Z')
  with tempfile.TemporaryDirectory() as d:
   f=Path(d)/'dome.xml';p.serialize(f,tree)
   p.validate_xml(f,p.HERE/'schemas/dol-metadata-v-03.xsd')
  ns={'d':p.D}
  self.assertEqual(tree.findtext('{'+p.D+'}jobID'),'12405.26')
  self.assertEqual(tree.findtext('{'+p.D+'}documentDate'),'2026-10-05')
  self.assertEqual(tree.findtext('{'+p.D+'}originalLanguage'),'en')
  self.assertEqual(tree.xpath('d:pages[@xml:lang="en"]/text()',namespaces=ns),['0'])
  self.assertEqual(tree.xpath('d:notificationAttachment/@url',namespaces=ns),['2026/TBT/a.pdf','2026/TBT/b.pdf'])
  self.assertEqual([E.QName(c).localname for c in tree.find('{'+p.D+'}tradeCoverage')],['HS','ICS'])
  self.assertEqual(tree.xpath('d:tradeCoverage/d:HS/text()',namespaces=ns),['3808'])
  self.assertFalse(tree.xpath('d:CRNid|d:TraslationRule|d:derestrictionType',namespaces=ns))
 def test_legacy_row_still_supported(self):
  tree=p.build_dome({'Job ID':'2026_000001','Title - E':'Synthetic'},p.now(),p.now())
  self.assertEqual(tree.findtext('{'+p.D+'}jobID'),'2026_000001')
  self.assertEqual(tree.find('{'+p.D+'}title').get('{http://www.w3.org/XML/1998/namespace}lang'),'en')
