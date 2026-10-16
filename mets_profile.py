"""Local user-authorized METS profile v1; not an institutional approval."""
from pathlib import Path
from lxml import etree as E

def validate(payload,job):
    import profile_workflow as p
    payload=Path(payload);root=E.parse(str(payload/p.mets_path(job))).getroot()
    ns={'m':p.M,'x':p.XLINK}
    if root.tag!='{'+p.M+'}mets' or root.get('TYPE') not in {'PREPARATION','SIP','AIP'}:
        raise RuntimeError('METS profile package type/root invalid')
    if root.get('PROFILE')!='urn:dpr:poc:mets-profile:1':raise RuntimeError('METS profile identifier missing')
    md=root.xpath('m:dmdSec[@ID="DOME"]/m:mdRef',namespaces=ns)
    pm=root.xpath('m:amdSec/m:digiprovMD[@ID="PREMIS"]/m:mdRef',namespaces=ns)
    if len(md)!=1 or len(pm)!=1:raise RuntimeError('METS DOME/PREMIS references missing')
    for node,logical in [(md[0],p.dome_path(job)),(pm[0],p.premis_path(job))]:
        if node.get('{'+p.XLINK+'}href')!=logical or node.get('CHECKSUM')!=p.digest(payload/logical):
            raise RuntimeError('METS metadata reference/fixity mismatch')
    docs=root.xpath('m:structMap/m:div[@TYPE="intellectualEntity"]',namespaces=ns)
    if len(docs)!=1 or docs[0].get('DMDID')!='DOME' or docs[0].get('ADMID')!='PREMIS':
        raise RuntimeError('METS document metadata links missing')
    if sorted(docs[0].xpath('m:div[@TYPE="representation"]/@LABEL',namespaces=ns))!=['rep_1','rep_2']:
        raise RuntimeError('METS representation divisions missing')
    actual={f.relative_to(payload).as_posix() for f in payload.rglob('*') if f.is_file()
        and 'record-history' not in f.relative_to(payload).parts and f.relative_to(payload).as_posix()!=p.mets_path(job)}
    mapped={}
    for node in root.xpath('m:fileSec/m:fileGrp/m:file',namespaces=ns):
        loc=node.xpath('m:FLocat/@x:href',namespaces=ns)
        if len(loc)!=1 or loc[0] in mapped:raise RuntimeError('METS duplicate/missing location')
        path=p.safe_relative(payload,loc[0])
        if node.get('CHECKSUMTYPE')!='SHA-512' or node.get('CHECKSUM')!=p.digest(path) or node.get('SIZE')!=str(path.stat().st_size):
            raise RuntimeError('METS file fixity/size mismatch')
        mapped[loc[0]]=node.get('ID')
    if set(mapped)!=actual:raise RuntimeError('METS payload coverage differs')
    pointers=root.xpath('m:structMap//m:fptr/@FILEID',namespaces=ns)
    if len(pointers)!=len(set(pointers)) or set(pointers)!=set(mapped.values()):raise RuntimeError('METS file pointers differ')
    for logical,id in mapped.items():
        if logical.startswith('representations/'):
            rep=logical.split('/')[1]
            if id not in docs[0].xpath('m:div[@LABEL=$rep]/m:fptr/@FILEID',namespaces=ns,rep=rep):
                raise RuntimeError('METS representation membership differs')
    p.validate_xml(payload/p.mets_path(job),payload/'schemas/mets.xsd')
    return root
