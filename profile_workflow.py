"""Draft-profile PREMIS/METS, offline schemas, and immutable package identity.

No production side effects on import. Legacy metadata stays supported explicitly.
"""
from __future__ import annotations
import copy
import hashlib
import json
import shutil
import sys
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path
from lxml import etree as E
from workflow_common import digest, load, write_record, safe_relative, verify, archive

P='http://www.loc.gov/premis/v3'
M='http://www.loc.gov/METS/'
XSI='http://www.w3.org/2001/XMLSchema-instance'
XLINK='http://www.w3.org/1999/xlink'
D='https://docs.wto.org/DocumentsOnline-Metadata-Elements-Namespace-v00-03'
HERE=Path(__file__).resolve().parent
NS={'p':P,'m':M}
EVENTS={'DPM102':('ingestion start','ins'),'DPM103':('message digest calculation','mes'),'DPM104':('fixity check','fix'),'DPM105':('filename change','fil'),'DPM106':('normalization','nor'),'DPM107':('validation','val'),'DPM108':('qualityReview',None),'DPM109':('format identification','for'),'DPM110':('metadata modification','mem'),'DPM111':('information package creation','ipc'),'DPM112':('fixity check','fix'),'DPM113':('information package creation','ipc'),'DPM114':('metadata modification','mem'),'DPM116':('creation','cre')}
SCHEMES={'messageDigestAlgorithm':('cryptographicHashFunctions',{'SHA-512':'sha512'}),'relationshipType':('relationshipType',{'structural':'str','derivation':'der','dependency':'dep'}),'relationshipSubType':('relationshipSubType',{'includes':'inc','is Included In':'isi','has Source':'hss'}),'formatRegistryRole':('formatRegistryRole',{'specification':'spe'}),'eventOutcome':('eventOutcome',{'success':'suc'}),'linkingAgentRole':('eventRelatedAgentRole',{'executing program':'exe','authorizer':'aut','implementer':'imp'}),'linkingObjectRole':('eventRelatedObjectRole',{'source':'sou','outcome':'out'}),'agentType':('agentType',{'software':'sof','person':'per','organization':'org','hardware':'har'})}

def now(): return datetime.now(timezone.utc).replace(microsecond=0).strftime('%Y-%m-%dT%H:%M:%SZ')
def add(parent,name,text=None,ns=P,**attrs):
    e=E.SubElement(parent,'{'+ns+'}'+name,attrs)
    if text is not None: e.text=unicodedata.normalize('NFC',str(text))
    return e

def cv(parent,name,value):
    scheme,values=SCHEMES.get(name,(None,{}))
    attrs={'authority':'local'}
    if value in values:
        uri='http://id.loc.gov/vocabulary/preservation/'+scheme
        attrs={'authority':scheme,'authorityURI':uri,'valueURI':uri+'/'+values[value]}
    return add(parent,name,value,**attrs)

def identifier(parent,kind,value,base='objectIdentifier'):
    e=add(parent,base);add(e,base+'Type',kind);add(e,base+'Value',value);return e

def serialize(path,root):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    data=E.tostring(root,encoding='unicode',pretty_print=True)
    text='<?xml version="1.0" encoding="UTF-8"?>\n'+data
    path.write_bytes(unicodedata.normalize('NFC',text).replace('\r\n','\n').encode('utf-8'))

class LocalResolver(E.Resolver):
    def __init__(self,folder): self.folder=Path(folder)
    def resolve(self,url,pubid,context):
        target=self.folder/Path(url).name
        if not target.is_file(): raise RuntimeError('Schema import is not packaged: '+url)
        return self.resolve_filename(str(target.resolve()),context)

def validate_xml(path,xsd):
    parser=E.XMLParser(no_network=True,resolve_entities=False)
    parser.resolvers.add(LocalResolver(Path(xsd).parent))
    schema=E.XMLSchema(E.parse(str(xsd),parser));schema.assertValid(E.parse(str(path),E.XMLParser(no_network=True,resolve_entities=False)))

def copy_schemas(payload):
    target=Path(payload)/'schemas';target.mkdir(parents=True,exist_ok=True)
    for p in (HERE/'schemas').glob('*.xsd'): shutil.copy2(p,target/p.name)
    lock={p.name:digest(p) for p in (HERE/'schemas').glob('*.xsd')}
    return lock

def check_schema_lock(payload):
    for name,checksum in load(HERE/'schemas/schema-lock.json').items(): verify(Path(payload)/'schemas'/name,checksum)

def object_path(aip_root,job):
    year,seq=job.split('_');return Path(aip_root)/year/f'{int(seq)//1000*1000:06d}'/job

def repository(aip_root,job,object_uuid=None):
    path=object_path(aip_root,job)
    if not path.exists(): return path,None
    inv=load(path/'inventory.json')
    if object_uuid and inv.get('id') != 'urn:uuid:'+object_uuid:
        raise RuntimeError('Repository object identity differs (legacy bare UUID objects require an explicit migration decision)')
    from ocfl_versions import validate
    errors=validate(path,inv['id'])
    if errors: raise RuntimeError('Repository validation failed: '+'; '.join(errors))
    return path,inv

def resolve_logical(obj,inv,logical):
    state=inv['versions'][inv['head']]['state']
    checksum=next((d for d,paths in state.items() if logical in paths),None)
    if checksum is None: raise RuntimeError('Predecessor does not contain '+logical)
    p=safe_relative(obj,inv['manifest'][checksum][0]);verify(p,checksum);return p

class Registry:
    def __init__(self,root,job):
        self.path=Path(root)/'.registries/uuid-registry.json';self.job=job
        self.data=load(self.path,{'entries':{},'keys':{}})
    def assign(self,key,kind,existing=None):
        key=self.job+':'+key
        if key in self.data['keys']:
            value=self.data['keys'][key]
            if existing and existing!=value: raise RuntimeError('Registry key changed identity: '+key)
            return value
        value=existing or str(uuid.uuid4())
        parsed=uuid.UUID(value)
        if parsed.version!=4 or str(parsed)!=value: raise RuntimeError('Profile requires lowercase UUID4: '+value)
        if value in self.data['entries']:
            prior=self.data['entries'][value]
            if existing and prior['entity_type']==kind and prior['job_id']==self.job:
                self.data['keys'][key]=value;write_record(self.path,self.data);return value
            raise RuntimeError('UUID collision; hold for review: '+value)
        self.data['entries'][value]={'entity_type':kind,'job_id':self.job,'description':key,'minted_at':now(),'minted_by':'profile_workflow.py','symbol':''}
        self.data['keys'][key]=value;write_record(self.path,self.data);return value

def configuration(path=None):
    path=Path(path) if path else HERE/'preservation_config.json'
    data=load(path)
    if not data: raise RuntimeError('Missing preservation_config.json; configure operator and tool agents')
    return data

def agent(root,record):
    value=record['uuid']
    existing=root.xpath('p:agent[p:agentIdentifier/p:agentIdentifierValue=$id]',namespaces=NS,id=value)
    if existing: return value
    a=add(root,'agent');identifier(a,'UUID',value,'agentIdentifier');add(a,'agentName',record['name']);cv(a,'agentType',record['type'])
    if record.get('version'): add(a,'agentVersion',record['version'])
    if record.get('note'): add(a,'agentNote',record['note'])
    return value

def event(root,registry,config,rule,key,objects,details=(),note=None,program='metadata',timestamp=None):
    id=registry.assign('event:'+key,'event')
    found=root.xpath('p:event[p:eventIdentifier/p:eventIdentifierValue=$id]',namespaces=NS,id=id)
    if found: return id
    kind,code=EVENTS[rule]
    # Place events before agents, preserving PREMIS schema sequence.
    e=E.Element('{'+P+'}event');agents=root.findall('{'+P+'}agent')
    root.insert(root.index(agents[0]) if agents else len(root),e)
    identifier(e,'UUID',id,'eventIdentifier')
    attrs={'authority':'local'} if code is None else {'authority':'eventType','authorityURI':'http://id.loc.gov/vocabulary/preservation/eventType','valueURI':'http://id.loc.gov/vocabulary/preservation/eventType/'+code}
    if timestamp:
        timestamp=datetime.fromisoformat(timestamp.replace('Z','+00:00')).astimezone(timezone.utc).replace(microsecond=0).strftime('%Y-%m-%dT%H:%M:%SZ')
    add(e,'eventType',kind,**attrs);add(e,'eventDateTime',timestamp or now())
    label='normalisation' if rule=='DPM106' else kind
    for detail in [label+' ('+rule+')']+list(details): add(add(e,'eventDetailInformation'),'eventDetail',detail)
    out=add(e,'eventOutcomeInformation');cv(out,'eventOutcome','success')
    if note: add(add(out,'eventOutcomeDetail'),'eventOutcomeDetailNote',note)
    for record,role in [(config['agents'][program],'executing program'),(config['operator'],'authorizer')]:
        aid=agent(root,record);link=identifier(e,'UUID',aid,'linkingAgentIdentifier');cv(link,'linkingAgentRole',role)
    for oid,role in objects:
        link=identifier(e,'UUID',oid,'linkingObjectIdentifier')
        if role: cv(link,'linkingObjectRole',role)
    return id

def relate(o,kind,sub,target,event_id=None):
    for r in o.findall('{'+P+'}relationship'):
        if r.findtext('{'+P+'}relationshipSubType')==sub and r.findtext('{'+P+'}relatedObjectIdentifier/{'+P+'}relatedObjectIdentifierValue')==target: return
    r=add(o,'relationship');cv(r,'relationshipType',kind);cv(r,'relationshipSubType',sub);identifier(r,'UUID',target,'relatedObjectIdentifier')
    if event_id: identifier(r,'UUID',event_id,'relatedEventIdentifier')

def get_object(root,id):
    found=root.xpath('p:object[p:objectIdentifier/p:objectIdentifierValue=$id]',namespaces=NS,id=id)
    return found[0] if found else None

def new_object(root,id,kind,path=None):
    found=get_object(root,id)
    if found is not None: return found,False
    o=E.Element('{'+P+'}object',{'{'+XSI+'}type':kind})
    first=root.find('{'+P+'}event')
    if first is None: first=root.find('{'+P+'}agent')
    root.insert(root.index(first) if first is not None else len(root),o)
    identifier(o,'UUID',id)
    if path: identifier(o,'filepath',path)
    return o,True

def file_object(root,registry,config,f):
    oid=registry.assign('file:'+f['id'],'file',f['id']);o,created=new_object(root,oid,'file',f.get('path'))
    if not created: return o,False
    oc=add(o,'objectCharacteristics');add(oc,'compositionLevel',0);fix=add(oc,'fixity');cv(fix,'messageDigestAlgorithm','SHA-512');add(fix,'messageDigest',f['sha512']);add(fix,'messageDigestOriginator',config['agents'][f.get('program','metadata')]['uuid']);add(oc,'size',f['size'])
    agent(root,config['agents'][f.get('program','metadata')])
    fmt=add(oc,'format');des=add(fmt,'formatDesignation');add(des,'formatName',f.get('format_name') or 'Unidentified (draft profile gap)')
    if f.get('format_version'): add(des,'formatVersion',f['format_version'])
    if f.get('puid'):
        reg=add(fmt,'formatRegistry');cv(reg,'formatRegistryName','PRONOM');add(reg,'formatRegistryKey',f['puid']);cv(reg,'formatRegistryRole','specification')
    if f.get('master') and not f.get('skipped'):
        app=add(oc,'creatingApplication');rec=config['agents']['converter'];add(app,'creatingApplicationName',rec['name']);add(app,'creatingApplicationVersion',rec['version']);add(app,'dateCreatedByApplication',f['created'])
    if f.get('original_name'): add(o,'originalName',f['original_name'])
    return o,True

def new_root(): return E.Element('{'+P+'}premis',nsmap={None:P,'xsi':XSI},attrib={'version':'3.0','{'+XSI+'}schemaLocation':P+' ../../schemas/premis.xsd'})
def premis_path(job): return f'metadata/preservation/{job}_premis.xml'
def dome_path(job): return f'metadata/descriptive/{job}_dome.xml'
def mets_path(job): return f'{job}_mets.xml'

def assert_append_only(before,after):
    for old in before:
        kind=E.QName(old).localname
        base={'object':'objectIdentifier','event':'eventIdentifier','agent':'agentIdentifier'}.get(kind)
        if not base: raise RuntimeError('Unsupported predecessor element: '+kind)
        oid=old.findtext('{'+P+'}'+base+'/{'+P+'}'+base+'Value')
        matches=after.xpath('p:'+kind+'[p:'+base+'/p:'+base+'Value=$id]',namespaces=NS,id=oid)
        if len(matches)!=1: raise RuntimeError('Predecessor element lost/duplicated: '+str(oid))
        candidate=copy.deepcopy(matches[0]);original=copy.deepcopy(old)
        if kind=='object':
            oldrels=original.findall('{'+P+'}relationship');newrels=candidate.findall('{'+P+'}relationship')
            if len(newrels)<len(oldrels): raise RuntimeError('Predecessor relationships removed')
            for extra in newrels[len(oldrels):]: candidate.remove(extra)
        for tree in [candidate,original]:
            for e in tree.iter():
                e.tail=None
                if e.text is not None and not e.text.strip(): e.text=None
        if E.tostring(candidate,method='c14n') != E.tostring(original,method='c14n'): raise RuntimeError('Predecessor element changed: '+oid)

def validate_premis(payload,job,previous=None):
    path=Path(payload)/premis_path(job);check_schema_lock(payload);validate_xml(path,Path(payload)/'schemas/premis.xsd')
    root=E.parse(str(path)).getroot()
    allowed=set('premis object objectIdentifier objectIdentifierType objectIdentifierValue objectCharacteristics compositionLevel fixity messageDigestAlgorithm messageDigest messageDigestOriginator size format formatDesignation formatName formatVersion formatRegistry formatRegistryName formatRegistryKey formatRegistryRole creatingApplication creatingApplicationName creatingApplicationVersion dateCreatedByApplication originalName relationship relationshipType relationshipSubType relatedObjectIdentifier relatedObjectIdentifierType relatedObjectIdentifierValue relatedEventIdentifier relatedEventIdentifierType relatedEventIdentifierValue event eventIdentifier eventIdentifierType eventIdentifierValue eventType eventDateTime eventDetailInformation eventDetail eventOutcomeInformation eventOutcome eventOutcomeDetail eventOutcomeDetailNote linkingAgentIdentifier linkingAgentIdentifierType linkingAgentIdentifierValue linkingAgentRole linkingObjectIdentifier linkingObjectIdentifierType linkingObjectIdentifierValue linkingObjectRole agent agentIdentifier agentIdentifierType agentIdentifierValue agentName agentType agentVersion agentNote'.split())
    for element in root.iter():
        if not isinstance(element.tag,str): continue
        if E.QName(element).namespace!=P or E.QName(element).localname not in allowed: raise RuntimeError('Profile NOT USED semantic unit: '+str(element.tag))
        if E.QName(element).localname.endswith('IdentifierType') and element.text=='UUID':
            value=element.getnext().text;parsed=uuid.UUID(value)
            if parsed.version!=4 or str(parsed)!=value: raise RuntimeError('Profile identifier is not lowercase UUID4: '+value)
    ids=[e.findtext('{'+P+'}'+{'object':'objectIdentifier','event':'eventIdentifier','agent':'agentIdentifier'}[E.QName(e).localname]+'/{'+P+'}'+{'object':'objectIdentifierValue','event':'eventIdentifierValue','agent':'agentIdentifierValue'}[E.QName(e).localname]) for e in root]
    if len(ids)!=len(set(ids)): raise RuntimeError('Duplicate entity UUID in PREMIS')
    for forbidden in ['storage','rights','preservationLevel','significantProperties','inhibitors','signatureInformation','linkingRightsStatementIdentifier']:
        if root.findall('.//{'+P+'}'+forbidden): raise RuntimeError('Profile NOT USED unit: '+forbidden)
    ies=root.xpath('p:object[@xsi:type="intellectualEntity"]',namespaces={**NS,'xsi':XSI})
    if len(ies)!=1: raise RuntimeError('Expected exactly one intellectual entity')
    if previous is not None: assert_append_only(previous,root)
    return root

def build_dome(row,created,modified):
    root=E.Element('{'+D+'}domeMetadata',nsmap={None:D,'xsi':XSI},attrib={'{'+XSI+'}schemaLocation':D+' ../../schemas/dol-metadata-v-03.xsd'})
    values={'mdSourceSystem':['Documents Online'],'mdDateCreated':[created],'mdDateModified':[modified],
        'documentSymbol':[row.get('Symbol')],'documentType':str(row.get('Document type') or '').split('#'),'documentNumber':[row.get('Document number')],
        'documentDate':[row.get('Document Date')],'title':[(row.get('Title - E'),'en'),(row.get('Title - F'),'fr'),(row.get('Title - S'),'es')],
        'originalLanguage':[{'E':'en','F':'fr','S':'es','English':'en','French':'fr','Spanish':'es'}.get(row.get('Original language'),row.get('Original language'))],
        'subject':str(row.get('Subject') or '').split('#'),'bodiesIssuing':str(row.get('Body issuing') or '').split('#'),'bodiesMentioned':str(row.get('Body mentioned') or '').split('#'),
        'countryTerritoryConcerned':str(row.get('Member concerned') or '').split('#'),'countryTerritoryMentioned':str(row.get('Other member mentioned') or '').split('#'),
        'organizations':str(row.get('Organization') or '').split('#'),'articles':str(row.get('Article basis') or '').split('#')+str(row.get('Article mentioned') or '').split('#'),
        'products':str(row.get('Products') or '').split('#'),'status':str(row.get('Document type status') or '').split('#'),
        'accessRights':['; '.join(f'{k}: {row[k]}' for k in ('Access type','Restriction type') if row.get(k))],
        'source':str(row.get('Document type source') or '').split('#'),'version':str(row.get('Document type version') or '').split('#'),
        'periodicity':str(row.get('Document type periodicity') or '').split('#'),'jobID':[row['Job ID']]}
    schema=E.parse(str(HERE/'schemas/dol-metadata-v-03.xsd'))
    order=schema.xpath('//*[local-name()="complexType"][@name="DomeMetadataType"]/*[local-name()="sequence"]/*/@ref')
    for name in order:
        for raw in values.get(name,[]):
            text,language=raw if isinstance(raw,tuple) else (raw,None)
            if not text: continue
            e=add(root,name,text,ns=D)
            if language: e.set('{http://www.w3.org/XML/1998/namespace}lang',language)
    return root

def write_mets(payload,job,package_id,package_type,object_uuid,sip_id=None):
    payload=Path(payload)
    root=E.Element('{'+M+'}mets',nsmap={None:M,'xlink':XLINK,'xsi':XSI},attrib={'OBJID':package_id,'TYPE':package_type,'LABEL':'Preservation package '+job,'{'+XSI+'}schemaLocation':M+' schemas/mets.xsd'})
    hdr=add(root,'metsHdr',ns=M,CREATEDATE=now())
    if sip_id: add(hdr,'altRecordID',sip_id,ns=M,TYPE='SIP')
    add(hdr,'altRecordID',job,ns=M,TYPE='JOB_ID')
    for section,wrapper,path,kind,id in [('dmdSec',None,dome_path(job),'OTHER','DOME'),('amdSec','digiprovMD',premis_path(job),'PREMIS','PREMIS')]:
        outer=add(root,section,ns=M,ID=id if wrapper is None else 'AMD')
        if wrapper: outer=add(outer,wrapper,ns=M,ID=id)
        f=payload/path
        attrs={'LOCTYPE':'URL','MDTYPE':kind,'MIMETYPE':'application/xml','CHECKSUMTYPE':'SHA-512','CHECKSUM':digest(f),'SIZE':str(f.stat().st_size),'{'+XLINK+'}href':path}
        if kind=='OTHER': attrs['OTHERMDTYPE']='DOME'
        add(outer,'mdRef',ns=M,**attrs)
    sec=add(root,'fileSec',ns=M);groups={};file_ids={}
    for p in sorted(payload.rglob('*')):
        if not p.is_file() or 'record-history' in p.relative_to(payload).parts: continue
        logical=p.relative_to(payload).as_posix()
        if logical==mets_path(job): continue
        rep=next((r for r in ('rep_1','rep_2') if logical.startswith('representations/'+r+'/')),'documentation')
        if rep not in groups: groups[rep]=add(sec,'fileGrp',ns=M,USE=rep)
        id='F'+str(len(file_ids)+1);file_ids[logical]=(id,rep)
        f=add(groups[rep],'file',ns=M,ID=id,CHECKSUMTYPE='SHA-512',CHECKSUM=digest(p),SIZE=str(p.stat().st_size))
        add(f,'FLocat',ns=M,LOCTYPE='URL',**{'{'+XLINK+'}href':logical})
    sm=add(root,'structMap',ns=M,TYPE='physical');doc=add(sm,'div',ns=M,TYPE='intellectualEntity',LABEL=job,DMDID='DOME',ADMID='PREMIS')
    for rep in ('rep_1','rep_2','documentation'):
        div=add(doc,'div',ns=M,TYPE='representation' if rep!='documentation' else 'documentation',LABEL=rep)
        for logical,(fid,r) in file_ids.items():
            if r==rep: add(div,'fptr',ns=M,FILEID=fid)
    serialize(payload/mets_path(job),root)
    validate_xml(payload/mets_path(job),payload/'schemas/mets.xsd')

def metadata(args,obj,job,recs,droid,row):
    if digest(args.xsd)!=digest(HERE/'schemas/dol-metadata-v-03.xsd'): raise RuntimeError('Profile mode requires the supplied DOME v0.3 schema')
    config=configuration(args.agent_registry)
    registry=Registry(obj.parent.parent,job)
    agent_registry=Registry(obj.parent.parent,'__agents__')
    for key,record in {**config['agents'],'operator':config['operator']}.items(): agent_registry.assign('agent:'+record['uuid'],'agent',record['uuid'])
    registry.data=load(registry.path)
    object_uuid=registry.assign('intellectualEntity','intellectualEntity',recs[1]['folder_uuid'])
    repo,inv=repository(args.aip_root or obj.parent.parent/'AIP',job,object_uuid)
    predecessor=inv['head'] if inv else 'none'
    old_path=resolve_logical(repo,inv,premis_path(job)) if inv else None
    root=E.parse(str(old_path)).getroot() if old_path else new_root()
    baseline=copy.deepcopy(root)
    copy_schemas(obj)
    state_path=obj/'documentation'/f'{job}_profile_state.json';state=load(state_path)
    previous_sql=load(obj/'documentation'/f'{job}_dome_sql_extraction.json')
    dome=obj/dome_path(job)
    if previous_sql.get('metadata')!=row or not dome.exists():
        creation=state.get('dome_created') or now();state['dome_created']=creation
        archive(dome)
        serialize(dome,build_dome(row,creation,now()))
        write_record(obj/'documentation'/f'{job}_dome_sql_extraction.json',{'source_dms_job_id':args.dms_job_id,'job_id':job,'extracted_utc':now(),'metadata':row})
    validate_xml(dome,obj/'schemas/dol-metadata-v-03.xsd')
    from poc6_metadata import retained_files
    files=retained_files(obj,droid)
    files=[f for f in files if not f['path'].startswith('metadata/')]+[{'path':dome_path(job),'sha512':digest(dome),'size':dome.stat().st_size,'format_name':'Extensible Markup Language','format_version':'1.0','puid':'fmt/101'}]
    # Optionally characterize new derived files and extraction after they exist.
    formats={f['relative_path']:f for f in droid.get('files',[])}
    if args.java and args.droid_jar:
        from poc5_droid import droid_characterize,parse_droid
        identified={f.findtext('{'+P+'}objectIdentifier[2]/{'+P+'}objectIdentifierValue'):f.findtext('{'+P+'}objectCharacteristics/{'+P+'}fixity/{'+P+'}messageDigest') for f in root.findall('{'+P+'}object') if f.get('{'+XSI+'}type')=='file'}
        pending=[obj/f['path'] for f in files if f['path']!=dome_path(job) and identified.get(f['path'])!=f['sha512']]
        if pending:
            profile,csv,driver=droid_characterize(Path(args.java),Path(args.droid_jar),pending,obj/'documentation',job)
            report=parse_droid(csv,pending,obj)
            formats.update({f['relative_path']:f for f in report})
            files.append({'path':csv.relative_to(obj).as_posix(),'sha512':digest(csv),'size':csv.stat().st_size,'format_name':'Comma Separated Values','puid':'x-fmt/18'})
    for src in recs[1]['files']:
        if src['role']=='published_pdf_working_source': files.append({'path':None,'sha512':src['sha512_staged'],'size':src['size_bytes'],'id':src['file_id'],'original_name':Path(src['original_path']).name,'lookup':Path(src['staged_path']).relative_to(obj).as_posix(),'program':'preparation'})
    identities={Path(f['staged_path']).relative_to(obj).as_posix():f for f in recs[1]['files']}
    relations={};conversions={c['master_file_id']:c for c in recs[2]['conversions']}
    for c in conversions.values():
        identities[Path(c['master_path']).relative_to(obj).as_posix()]={'file_id':c['master_file_id'],'master':True,'created':c.get('completed_at') or recs[2].get('created_at') or now(),'skipped':c.get('converter_status','').startswith('SKIPPED'),'program':'converter'}
        for key,dep in [('conversion_log','isConversionLogOf')]:
            if c.get(key): relations[Path(c[key]).relative_to(obj).as_posix()]=(dep,c['master_file_id'],'DPM106')
    for record,key,pathkey,idkey,dep,rule in [(recs[3],'validations','validation_report_path','validation_report_file_id','isValidationReportOf','DPM107'),(recs[4],'qa_entries','qa_report_path','qa_report_file_id','isQaReportOf','DPM108')]:
        for e in record[key]:
            relative=Path(e[pathkey]).relative_to(obj).as_posix();identities[relative]={'file_id':e[idkey],'program':'validation' if rule=='DPM107' else 'qa'};relations[relative]=(dep,e['master_file_id'],rule)
    for e in recs[5].get('evidence_files',[]): identities.setdefault(e['relative_path'],{'file_id':e['file_id'],'program':'droid'})
    ie,created=new_object(root,object_uuid,'intellectualEntity')
    if created: identifier(ie,'JOB_ID',job)
    reps={}
    for rep in ('rep_1','rep_2'):
        id=registry.assign('representation:'+rep,'representation');o,_=new_object(root,id,'representation','representations/'+rep);reps[rep]=(id,o);relate(ie,'structural','includes',id);relate(o,'structural','is Included In',object_uuid)
    if not inv: event(root,registry,config,'DPM102','ingestion-start',[(object_uuid,None)],program='preparation',timestamp=recs[1]['created_at'][:19]+'Z')
    added=[];gaps=[]
    if any(f.get('language')=='und' for f in recs[1]['files']): gaps.append('Profile language policy for undetermined-language attachments needs approval')
    if not row.get('CatHeaderId'): gaps.append('Missing DOL CatHeaderId for descriptive metadata provenance')
    for f in files:
        identity=identities.get(f.get('path'),{})
        if not f.get('id'): f['id']=identity.get('file_id') or registry.assign('file-content:'+str(f['path'])+':'+f['sha512'],'file')
        f.update({k:v for k,v in identity.items() if k in {'master','created','skipped','program'}})
        if identity.get('original_name') and identity.get('role') in {'authoring_source','notification_attachment'} and f.get('path') and Path(f['path']).name!=Path(identity['original_path']).name: f['original_name']=Path(identity['original_path']).name
        fmt=formats.get(f.get('lookup') or f.get('path'),{})
        ids=fmt.get('identifications',[])
        if len({x.get('puid') for x in ids if x.get('puid')})>1: gaps.append('Ambiguous PRONOM identification: '+str(f.get('path')))
        if ids:
            primary=ids[0];f['puid']=primary.get('puid');f['format_name']=primary.get('format_name');f['format_version']=primary.get('format_version')
        if not f.get('puid'): gaps.append('No PRONOM identification: '+str(f.get('lookup') or f['path']))
        o,is_new=file_object(root,registry,config,f)
        if not is_new: continue
        added.append(f)
        logical=f.get('path') or ''
        rep=next((r for r in reps if logical.startswith('representations/'+r+'/')),None)
        if rep:
            rid,ro=reps[rep];relate(ro,'structural','includes',f['id']);relate(o,'structural','is Included In',rid)
        if logical.startswith('representations/') or not f.get('path'):
            event(root,registry,config,'DPM103','digest:'+f['id'],[(f['id'],None)],program=f.get('program','preparation'),timestamp=identity.get('prepared_at') or f.get('created'))
        if identity.get('role') in {'authoring_source','notification_attachment'} or not f.get('path'):
            event(root,registry,config,'DPM104','copy:'+f['id'],[(f['id'],None)],program='preparation',timestamp=identity.get('prepared_at'))
        if f.get('original_name') and logical and Path(logical).name!=f['original_name']: event(root,registry,config,'DPM105','rename:'+f['id'],[(f['id'],None)],program='preparation')
    if args.strict_profile and (gaps or config.get('draft') or not row.get('CatHeaderId')): raise RuntimeError('Strict profile blocked: '+('; '.join(gaps) or 'provisional registry must be approved/configured'))
    for c in conversions.values():
        master=get_object(root,c['master_file_id']);skipped=c.get('converter_status','').startswith('SKIPPED')
        log_id=next((f['id'] for f in files if f.get('path') and c.get('conversion_log') and obj/f['path']==Path(c['conversion_log'])),None)
        if skipped: continue
        eid=event(root,registry,config,'DPM106','normalization:'+c['master_file_id'],[(c['source_file_id'],'source'),(c['master_file_id'],'outcome')]+([(log_id,'outcome')] if log_id else []),['target_level: PDF/A-2b'],note=c.get('converter_status'),program='converter',timestamp=c.get('completed_at') or recs[2].get('created_at'))
        if not skipped: relate(master,'derivation','has Source',c['source_file_id'],eid)
    for rule,entries,pathkey,idkey in [('DPM107',recs[3]['validations'],'validation_report_path','validation_report_file_id'),('DPM108',recs[4]['qa_entries'],'qa_report_path','qa_report_file_id')]:
        for e in entries:
            event(root,registry,config,rule,rule+':'+e[idkey],[(e['master_file_id'],'source'),(e[idkey],'outcome')],['validation_profile: PDF/A-2B'] if rule=='DPM107' else ['sampled: false'],program='validation' if rule=='DPM107' else 'qa',timestamp=e.get('completed_at'))
    for f in files:
        if f.get('path') not in relations: continue
        dep,master_id,rule=relations[f['path']];key=('normalization:'+master_id) if rule=='DPM106' else rule+':'+f['id'];eid=registry.assign('event:'+key,'event');relate(get_object(root,f['id']),'dependency',dep,master_id,eid)
    dome_file=next(f for f in files if f['path']==dome_path(job))
    if any(f['id']==dome_file['id'] for f in added):
        old_dome=next((o for o in baseline.findall('{'+P+'}object') if o.findtext('{'+P+'}objectIdentifier[2]/{'+P+'}objectIdentifierValue')==dome_path(job)),None)
        links=[(object_uuid,None),(dome_file['id'],'outcome')]
        if old_dome is not None: links.append((old_dome.findtext('{'+P+'}objectIdentifier/{'+P+'}objectIdentifierValue'),'source'))
        event(root,registry,config,'DPM110' if inv else 'DPM116','dome:'+dome_file['id'],links,['cat_header_id: '+str(row.get('CatHeaderId') or 'unavailable (draft gap)')])
    # New DROID reports are outputs of identification, not themselves identified.
    droid_reports=[f for f in added if f.get('path','') and f['path'].endswith('_droid_export.csv')]
    if added and droid_reports:
        report=droid_reports[-1];event(root,registry,config,'DPM109','droid:'+report['id'],[(f['id'],None) for f in added if f['id']!=report['id'] and f['path']!=dome_path(job)]+[(report['id'],'outcome')],['batch_id: '+registry.assign('batch:'+predecessor,'batch',args.batch_id)],program='droid')
    if args.corrections:
        corrections=load(args.corrections,[])
        if not isinstance(corrections,list): raise RuntimeError('Corrections must be a JSON list')
        for correction in corrections:
            required={'entity_type','uuid','element','recorded','correct','reason'}
            if not required <= set(correction): raise RuntimeError('Correction fields missing')
            target=root.xpath('p:'+correction['entity_type']+'[*/*[text()=$id]]',namespaces=NS,id=correction['uuid']) if correction['entity_type'] in {'object','event','agent'} else []
            if not target: raise RuntimeError('Correction target is not described in PREMIS')
            key=hashlib.sha256(json.dumps(correction,sort_keys=True).encode()).hexdigest()
            link=correction['uuid'] if correction['entity_type']=='object' else object_uuid
            event(root,registry,config,'DPM114','correction:'+key,[(link,None)],['corrects: '+correction['entity_type']+' '+correction['uuid']]+[name+': '+str(correction[name]) for name in ('element','recorded','correct','reason')])
    # Preserve existing order; sort only new events, keeping prior elements unchanged.
    evs=root.findall('{'+P+'}event')
    for e in evs: root.remove(e)
    evs.sort(key=lambda e:e.findtext('{'+P+'}eventDateTime'))
    index=next((root.index(e) for e in root if E.QName(e).localname=='agent'),len(root))
    for e in evs: root.insert(index,e);index+=1
    if inv: assert_append_only(baseline,root)
    archive(obj/premis_path(job))
    archive(obj/mets_path(job))
    serialize(obj/premis_path(job),root);validate_premis(obj,job,baseline if inv else None)
    write_mets(obj,job,object_uuid,'PREPARATION',object_uuid)
    state.update({'predecessor':predecessor,'profile_gaps':gaps,'draft_registry':bool(config.get('draft')),'registry_path':str(registry.path),'config_path':str(Path(args.agent_registry).resolve()) if args.agent_registry else str(HERE/'preservation_config.json')});write_record(state_path,state)
    previous_ids={o.findtext('{'+P+'}objectIdentifier/{'+P+'}objectIdentifierValue') for o in baseline.findall('{'+P+'}object')}
    old_languages={f['language'] for f in recs[1]['files'] if f['file_id'] in previous_ids}
    new_sources=[f for f in recs[1]['files'] if f['file_id'] not in previous_ids]
    if not inv: basis='new_document'
    elif args.corrections: basis='premis_correction'
    elif any(f['language'] not in old_languages for f in new_sources): basis='language_added'
    elif any(f['role']=='published_pdf_working_source' for f in new_sources): basis='modified_files'
    elif new_sources: basis='rendition_added'
    elif any(f['id']==dome_file['id'] for f in added): basis='metadata_change'
    elif added: basis='rendition_added'
    else: basis='no_change'
    record={'poc':6,'job_id':job,'folder_uuid':object_uuid,'profile':'DPM-v0.4-draft','created_utc':now(),'status':'STEP_6_METADATA_PROJECTED','source_snapshot':{f['file_id']:f['sha512_staged'] for f in recs[1]['files']},'profile_gaps':gaps,'draft_registry':bool(config.get('draft')),'predecessor':predecessor,'selection_basis_proposed':basis}
    for key,relative in [('dome',dome_path(job)),('premis',premis_path(job)),('mets',mets_path(job))]: record[key]={'path':relative,'sha512':digest(obj/relative),'schema_validation':'PASS'}
    write_record(obj/'documentation'/f'{job}_poc6_metadata_record.json',record)
    print('[PASS] Draft-profile DOME/PREMIS/METS schemas validated; unresolved gaps:',len(gaps));return 0

def tags(bag):
    out={}
    for line in (Path(bag)/'bag-info.txt').read_text(encoding='utf-8-sig').splitlines():
        if ':' not in line: continue
        key,value=line.split(':',1)
        if key in out: raise RuntimeError('Duplicate bag-info field: '+key)
        out[key]=value.strip()
    return out

def event_details(root,rule):
    result=[]
    for e in root.findall('{'+P+'}event'):
        details=e.findall('{'+P+'}eventDetailInformation/{'+P+'}eventDetail')
        if not details or '('+rule+')' not in (details[0].text or ''): continue
        data={}
        for d in details[1:]:
            if ': ' in (d.text or ''):
                k,v=d.text.split(': ',1);data[k]=v
        result.append((e,data))
    return result

def check_contract(bag,job,aip_root):
    bag=Path(bag);info=tags(bag)
    for field in ['Source-Organization','External-Identifier','Internal-Sender-Identifier','External-Description','Bag-Group-Identifier','Bagging-Date','Payload-Oxum','DPR-Object-UUID','DPR-Predecessor-OCFL-Version']:
        if not info.get(field): raise RuntimeError('Required bag-info field absent: '+field)
    if info['Source-Organization']!='World Trade Organization': raise RuntimeError('Source-Organization differs')
    if info['Internal-Sender-Identifier']!=job: raise RuntimeError('Internal-Sender-Identifier/JOB_ID mismatch')
    for field in ['External-Identifier','Bag-Group-Identifier','DPR-Object-UUID']:
        value=uuid.UUID(info[field])
        if value.version!=4 or str(value)!=info[field]: raise RuntimeError('Expected lowercase UUID4: '+field)
    payload=bag/'data';root=validate_premis(payload,job)
    ie=root.xpath('p:object[@xsi:type="intellectualEntity"]',namespaces={**NS,'xsi':XSI})[0]
    identities={i.findtext('{'+P+'}objectIdentifierType'):i.findtext('{'+P+'}objectIdentifierValue') for i in ie.findall('{'+P+'}objectIdentifier')}
    if identities.get('JOB_ID')!=job or identities.get('UUID')!=info['DPR-Object-UUID']: raise RuntimeError('PREMIS intellectual entity/bag identity mismatch')
    mets=E.parse(str(payload/mets_path(job))).getroot()
    if mets.get('OBJID')!=info['External-Identifier'] or mets.get('TYPE')!='SIP': raise RuntimeError('METS/SIP UUID or package type mismatch')
    events=event_details(root,'DPM111')
    if not events or events[-1][1].get('sip_id')!=info['External-Identifier']: raise RuntimeError('PREMIS SIP UUID mismatch')
    if events[-1][1].get('batch_id')!=info['Bag-Group-Identifier']: raise RuntimeError('PREMIS batch UUID mismatch')
    repo,inv=repository(aip_root,job,info['DPR-Object-UUID'])
    expected=inv['head'] if inv else 'none'
    if info['DPR-Predecessor-OCFL-Version']!=expected: raise RuntimeError('Stale predecessor: bag='+info['DPR-Predecessor-OCFL-Version']+', repository='+expected)
    if inv:
        previous=E.parse(str(resolve_logical(repo,inv,premis_path(job)))).getroot();assert_append_only(previous,root)
    # Verify METS references and payload description independently from BagIt.
    for ref in mets.xpath('//m:mdRef|//m:file',namespaces=NS):
        href=ref.get('{'+XLINK+'}href')
        if href is None:
            child=ref.find('{'+M+'}FLocat');href=child.get('{'+XLINK+'}href') if child is not None else None
        if href:
            fp=safe_relative(payload,href);verify(fp,ref.get('CHECKSUM'))
            if int(ref.get('SIZE',fp.stat().st_size))!=fp.stat().st_size: raise RuntimeError('METS size mismatch: '+href)
    validate_xml(payload/mets_path(job),payload/'schemas/mets.xsd')
    validate_xml(payload/dome_path(job),payload/'schemas/dol-metadata-v-03.xsd')
    return info,root,events[-1][1],repo,inv

def package(args,obj,job):
    import poc7_bagit as bagit
    record=load(obj/'documentation'/f'{job}_poc6_metadata_record.json')
    from poc6_metadata import require_prior
    require_prior(obj,job)
    if record.get('source_snapshot')!={f['file_id']:f['sha512_staged'] for f in load(obj/'documentation'/f'{job}_poc1_processing_record.json')['files']}: raise RuntimeError('Metadata source coverage is stale')
    for key in ('dome','premis','mets'): verify(obj/record[key]['path'],record[key]['sha512'])
    config=configuration(args.agent_registry or load(obj/'documentation'/f'{job}_profile_state.json').get('config_path'))
    registry=Registry(obj.parent.parent,job);aip_root=Path(args.aip_root) if args.aip_root else obj.parent.parent/'AIP'
    repo,inv=repository(aip_root,job,record['folder_uuid']);predecessor=inv['head'] if inv else 'none'
    if record['predecessor']!=predecessor: raise RuntimeError('Metadata predecessor is stale; rerun PoC6')
    if record.get('selection_basis_proposed')=='no_change' and not args.selection_basis:
        print('[NO CHANGE] No new content or descriptive metadata; no SIP/version created')
        return 0
    bag=Path(args.staging_dir).resolve()/job
    if bag.exists():
        info,_,_,_,_=check_contract(bag,job,aip_root)
        old=load(bag.parent/f'{job}_poc7_bagit_record.json')
        if not bagit.validate_bag(bag) and old.get('metadata_sha512')==record['premis']['sha512']:
            print('[REUSE] Existing verified SIP');return 0
        raise RuntimeError('Existing bag preserved; choose a distinct staging directory')
    sip=registry.assign('sip:'+str(bag),'SIP');batch=registry.assign('batch:'+predecessor,'batch',args.bag_group_id) if args.bag_group_id else registry.assign('batch:'+predecessor,'batch')
    payload=bag/'data';payload.mkdir(parents=True)
    included,excluded=bagit.collect_payload_source_files(obj)
    for src,rp in included:
        relative=rp.as_posix()
        if relative.startswith('metadata/') and relative not in {dome_path(job),premis_path(job)}: continue
        dst=payload/rp;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dst);verify(dst,digest(src))
    copy_schemas(payload)
    root=E.parse(str(payload/premis_path(job))).getroot()
    selection=args.selection_basis or record.get('selection_basis_proposed') or ('new_document' if not inv else 'rendition_added')
    allowed={'new_document','language_added','rendition_added','modified_files','metadata_change','derestriction','premis_correction','master_reconversion'}
    if selection not in allowed: raise RuntimeError('Unknown selection basis')
    if (selection=='new_document') != (inv is None): raise RuntimeError('Selection basis disagrees with repository existence')
    languages=sorted({f['language'] for f in load(obj/'documentation'/f'{job}_poc1_processing_record.json')['files']})
    event(root,registry,config,'DPM111','sip:'+sip,[(record['folder_uuid'],None)],[f'sip_id: {sip}',f'selection_basis: {selection}',f'batch_id: {batch}','languages: '+', '.join(languages)],'package type: SIP')
    serialize(payload/premis_path(job),root);validate_premis(payload,job)
    write_mets(payload,job,sip,'SIP',record['folder_uuid'])
    description=args.description or f'SIP for {job} ({selection}) [built on {predecessor}]'
    if any('\n' in v or '\r' in v for v in [description,args.contact_name]): raise RuntimeError('Bag-info values must be single lines')
    bagit.write_bagit_txt(bag);bagit.write_payload_manifest(bag)
    octets,count=bagit.payload_oxum(bag)
    info={'Source-Organization':'World Trade Organization','External-Identifier':sip,'Internal-Sender-Identifier':job,'External-Description':description,'Bag-Group-Identifier':batch,'Bagging-Date':now()[:10],'Payload-Oxum':f'{octets}.{count}','DPR-Object-UUID':record['folder_uuid'],'DPR-Predecessor-OCFL-Version':predecessor}
    (bag/'bag-info.txt').write_text(''.join(k+': '+v+'\n' for k,v in info.items()),encoding='utf-8',newline='\n');bagit.write_tagmanifest(bag)
    problems=bagit.validate_bag(bag)
    if problems: raise RuntimeError('; '.join(problems))
    check_contract(bag,job,aip_root)
    write_record(bag.parent/f'{job}_poc7_bagit_record.json',{'job_id':job,'folder_uuid':record['folder_uuid'],'sip_uuid':sip,'batch_uuid':batch,'predecessor':predecessor,'selection_basis':selection,'created_utc':now(),'status':'STEP_7_BAGIT_SIP_STAGED_VALID','metadata_sha512':record['premis']['sha512'],'staging_bag':str(bag),'bag_snapshot':bagit.directory_snapshot(bag),'profile_gaps':record.get('profile_gaps',[]),'draft_registry':config.get('draft',False)})
    print('[PASS] SIP identity, schemas and BagIt verified:',sip);return 0

def check_payload_agreement(root,payload,job,previous_inventory=None):
    described=set()
    for o in root.xpath('p:object[@xsi:type="file"]',namespaces={**NS,'xsi':XSI}):
        paths=o.xpath('p:objectIdentifier[p:objectIdentifierType="filepath"]/p:objectIdentifierValue/text()',namespaces=NS)
        checksum=o.findtext('{'+P+'}objectCharacteristics/{'+P+'}fixity/{'+P+'}messageDigest')
        for path in paths: described.add((path,checksum))
    excluded={premis_path(job),mets_path(job)}
    held={(p.relative_to(payload).as_posix(),digest(p)) for p in Path(payload).rglob('*') if p.is_file() and p.relative_to(payload).as_posix() not in excluded and p.relative_to(payload).parts[0]!='schemas'}
    if previous_inventory:
        for version in previous_inventory['versions'].values():
            for checksum,paths in version['state'].items():
                held.update((path,checksum) for path in paths if path not in excluded and not path.startswith('schemas/'))
    if held!=described: raise RuntimeError('PREMIS/payload history disagreement: missing='+str(held-described)+'; extra='+str(described-held))

def ingest(args,bag):
    import poc7_bagit as bagit
    import poc9_ocfl_ingest as ocfl
    from ocfl_versions import build_delta,commit_delta,validate
    info,root,details,repo,old=check_contract(bag,args.job_id,args.aip_root)
    errors=bagit.validate_bag(bag)
    if errors: raise RuntimeError('BagIt failed: '+'; '.join(errors))
    records_dir=Path(args.ingest_records_dir) if args.ingest_records_dir else Path(args.staging_dir).parent/'ingest-records'
    verification=records_dir/f'{args.job_id}_{info["External-Identifier"]}_poc8_ingest_verification_record.json'
    if not verification.exists(): verification=records_dir/f'{args.job_id}_poc8_ingest_verification_record.json'
    verified=ocfl.verify_poc8(verification,args.job_id)
    current={p.relative_to(bag).as_posix():digest(p) for p in (bag/'data').rglob('*') if p.is_file()}
    recorded={e['path']:e['actual_sha512'] for e in verified['payload_manifest']['entries'] if e['verified']}
    if current!=recorded or verified.get('sip_uuid')!=info['External-Identifier']: raise RuntimeError('Bag differs from exact PoC8-verified SIP')
    for tag in verified.get('tagmanifest',{}).get('entries',[]):
        if not tag.get('verified'): raise RuntimeError('PoC8 tag was not verified')
        verify(safe_relative(bag,tag['path']),tag['actual_sha512'])
    config=configuration(args.agent_registry);operator=config['operator']
    if args.strict_profile:
        if config.get('draft'): raise RuntimeError('Strict ingest rejects the provisional agent registry')
        unidentified=root.xpath('p:object[@xsi:type="file"][not(p:objectCharacteristics/p:format/p:formatRegistry/p:formatRegistryKey)]',namespaces={**NS,'xsi':XSI})
        if unidentified: raise RuntimeError('Strict ingest rejects files without PRONOM identification')
        if 'und' in details.get('languages','').split(', '): raise RuntimeError('Strict profile language policy for undetermined attachments needs approval')
        if any('UNCONFIGURED' in a.get('version','') for a in config['agents'].values()): raise RuntimeError('Strict ingest rejects unconfigured tool versions')
    if args.user_name and args.user_name!=operator['name']: raise RuntimeError('Commit authorizer name differs from registry')
    address='mailto:'+operator['email']
    if args.user_address and args.user_address!=address: raise RuntimeError('Commit authorizer address differs from registry')
    if not operator.get('email') or '@' not in operator['email']: raise RuntimeError('Operator registry email missing')
    registry=Registry(Path(args.aip_root).parent,args.job_id)
    workspace_root=Path(args.workspace_dir) if args.workspace_dir else Path(args.aip_root).parent/'ocfl-workspace'
    run=workspace_root/(args.job_id+'_'+uuid.uuid4().hex);receipt=run/'receipt';payload=receipt/'data';shutil.copytree(bag/'data',payload)
    received=copy.deepcopy(root)
    head='v'+str(int(old['head'][1:])+1) if old else 'v1'
    event(root,registry,config,'DPM112','ingest:'+info['External-Identifier'],[(info['DPR-Object-UUID'],None)],[f'sip_id: {info["External-Identifier"]}','package_manifest: manifest-sha512.txt'],'Payload-Oxum: '+info['Payload-Oxum'],program='ingest')
    event(root,registry,config,'DPM113','aip:'+info['External-Identifier'],[(info['DPR-Object-UUID'],None)],[f'ocfl_version: {head}'],'package type: AIP',program='ingest')
    assert_append_only(received,root);serialize(payload/premis_path(args.job_id),root);validate_premis(payload,args.job_id,received)
    check_payload_agreement(root,payload,args.job_id,old)
    objid='urn:uuid:'+info['DPR-Object-UUID'];write_mets(payload,args.job_id,objid,'AIP',info['DPR-Object-UUID'],info['External-Identifier'])
    entries={p.relative_to(receipt).as_posix():digest(p) for p in payload.rglob('*') if p.is_file()}
    workspace=run/'object';ocfl.ensure_storage_root(Path(args.aip_root))
    if old: inv,copies=build_delta(receipt,entries,objid,repo,workspace,operator['name'],address,now())
    else: inv,copies=ocfl.build_v1_workspace(receipt,entries,objid,workspace,operator['name'],address)
    commit=inv['versions'][inv['head']];commit['message']=details['selection_basis']+' — '+(args.description or info['External-Description'])
    # The authorizer must be the same registered person as on DPM113.
    aip_event=event_details(root,'DPM113')[-1][0]
    authorizers=aip_event.xpath('p:linkingAgentIdentifier[p:linkingAgentRole="authorizer"]/p:linkingAgentIdentifierValue/text()',namespaces=NS)
    recorded_names=root.xpath('p:agent[p:agentIdentifier/p:agentIdentifierValue=$id]/p:agentName/text()',namespaces=NS,id=operator['uuid'])
    if recorded_names!=[operator['name']]: raise RuntimeError('PREMIS authorizer name differs from agent registry/commit record')
    if authorizers!=[operator['uuid']] or aip_event.findtext('{'+P+'}eventDateTime')>commit['created']: raise RuntimeError('Commit record/PREMIS authorizer or time mismatch')
    data=json.dumps(inv,ensure_ascii=False,indent=2)+'\n'
    for parent in [workspace,workspace/inv['head']]:
        (parent/'inventory.json').write_text(data,encoding='utf-8',newline='\n');(parent/'inventory.json.sha512').write_text(digest(parent/'inventory.json')+' inventory.json\n',encoding='utf-8',newline='\n')
    errors=validate(workspace,objid)
    if errors: raise RuntimeError('; '.join(errors))
    if args.ocfl_validator:
        import subprocess
        result=subprocess.run([str(args.validator_python or sys.executable),str(args.ocfl_validator),str(workspace)],capture_output=True,text=True)
        (run/'independent-validator.txt').write_text(result.stdout+'\n'+result.stderr,encoding='utf-8')
        if result.returncode: raise RuntimeError('Independent OCFL validator rejected candidate: '+result.stdout+result.stderr)
    elif args.require_independent_validator: raise RuntimeError('Independent OCFL validator required but not configured')
    # Repeat all identity/predecessor checks immediately before the lock/commit.
    check_contract(bag,args.job_id,args.aip_root)
    if old: commit_delta(workspace,repo,old['head'])
    else:
        repo.parent.mkdir(parents=True,exist_ok=True)
        if repo.exists(): raise RuntimeError('Object appeared during ingest; hold stale SIP')
        workspace.rename(repo)
    records=Path(args.ingest_records_dir) if args.ingest_records_dir else Path(args.staging_dir).parent/'ingest-records'
    write_record(records/f'{args.job_id}_poc9_ocfl_ingest_record.json',{'job_id':args.job_id,'created_utc':now(),'status':'STEP_9_OCFL_AIP_COMMITTED','sip_uuid':info['External-Identifier'],'selection_basis':details['selection_basis'],'ocfl':{'object_path':str(repo),'object_id':objid,'head':inv['head']},'verify_on_write':{'outcome':'PASS','files':copies},'independent_validation':'PASS' if args.ocfl_validator else 'NOT_CONFIGURED','draft_registry':config.get('draft',False)})
    print('[PASS] Profile AIP committed:',inv['head']);return 0
