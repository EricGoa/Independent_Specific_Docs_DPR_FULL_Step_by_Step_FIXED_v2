#!/usr/bin/env python3
from __future__ import annotations

import argparse, hashlib, json, mimetypes, sys
from workflow_common import coverage, source_records, load, archive, verify, working_pdf
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

DOME_NS = "https://docs.wto.org/dpr/dome/v1"
METS_NS = "http://www.loc.gov/METS/v2"
PREMIS_NS = "http://www.loc.gov/premis/v3"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"

ET.register_namespace("dome", DOME_NS)
ET.register_namespace("mets", METS_NS)
ET.register_namespace("premis", PREMIS_NS)
ET.register_namespace("xsi", XSI_NS)

SINGLE = [
    ("Job ID","jobID"),("Document number","documentNumber"),
    ("Access type","accessType"),("Document Date","documentDate"),
    ("Restriction type","restrictionType"),("Symbol","symbol"),
    ("Original language","originalLanguage"),("Title - E","titleE"),
    ("Title - F","titleF"),("Title - S","titleS"),
]
MULTI = [
    ("Document type","documentType"),("Document type source","documentTypeSource"),
    ("Document type periodicity","documentTypePeriodicity"),
    ("Document type status","documentTypeStatus"),("Document type version","documentTypeVersion"),
    ("Body issuing","bodyIssuing"),("Body mentioned","bodyMentioned"),
    ("Article basis","articleBasis"),("Article mentioned","articleMentioned"),
    ("Organization","organization"),("Products","products"),("Subject","subject"),
    ("Member concerned","memberConcerned"),("Other member mentioned","otherMemberMentioned"),
]

def q(ns, name): return f"{{{ns}}}{name}"
def now(): return datetime.now(timezone.utc).isoformat().replace("+00:00","Z")
def sha512(p):
    h=hashlib.sha512()
    with open(p,"rb") as f:
        for b in iter(lambda:f.read(1024*1024),b""): h.update(b)
    return h.hexdigest()
def write_json(p,o):
    p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(o,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
def write_xml(p,root):
    try: ET.indent(root,space="  ")
    except AttributeError: pass
    ET.ElementTree(root).write(p,encoding="utf-8",xml_declaration=True)
def add(parent,ns,name,text=None,**attrs):
    e=ET.SubElement(parent,q(ns,name),attrs)
    if text is not None: e.text=str(text)
    return e
def split_multi(v):
    return [x.strip() for x in str(v or "").split("#") if x.strip()]
def preservation_job_id(v):
    s=str(v).strip()
    seq,yy=s.rsplit(".",1)
    if not(seq.isdigit() and yy.isdigit() and len(yy)==2):
        raise ValueError(f"Unexpected DmsJobId: {s}")
    return f"20{yy}_{int(seq):06d}"

QUERY = r"""
SELECT
    h.ID AS [CatHeaderId],
    CONCAT('20',RIGHT(CAST(h.DmsJobId AS varchar(30)),2),'_',
      RIGHT('000000'+LEFT(CAST(h.DmsJobId AS varchar(30)),
      CHARINDEX('.',CAST(h.DmsJobId AS varchar(30)))-1),6)) AS [Job ID],
    ISNULL(h.Serial,'') AS [Document number],
    ISNULL((SELECT TOP (1) l.ListName FROM dbo.[List] l WHERE l.ID=h.AccessType),'') AS [Access type],
    CONVERT(varchar(10),h.IssuingDate,23) AS [Document Date],
    ISNULL((SELECT TOP (1) l.ListName FROM dbo.[List] l WHERE l.ID=h.RestrictionType),'') AS [Restriction type],
    ISNULL(h.Symbols,'') AS [Symbol],
    ISNULL((SELECT TOP (1) l.ListName FROM dbo.[List] l WHERE l.ID=h.OriginalLanguage),'') AS [Original language],
    ISNULL((SELECT TOP (1) d.Title FROM dbo.CatalogueDetail d WHERE d.CatHeaderID=h.ID AND d.LCID=1033),'') AS [Title - E],
    ISNULL((SELECT TOP (1) d.Title FROM dbo.CatalogueDetail d WHERE d.CatHeaderID=h.ID AND d.LCID=1036),'') AS [Title - F],
    ISNULL((SELECT TOP (1) d.Title FROM dbo.CatalogueDetail d WHERE d.CatHeaderID=h.ID AND d.LCID=3082),'') AS [Title - S],

    ISNULL((SELECT STRING_AGG(x.ElementValue,'#') FROM
      (SELECT DISTINCT el.ElementValue FROM dbo.CatDocumentType cdt JOIN dbo.Element el ON cdt.DocumentTypeID=el.ListID
       WHERE cdt.CatHeaderID=h.ID AND el.LCID=1033)x),'') AS [Document type],
    ISNULL((SELECT STRING_AGG(x.ElementValue,'#') FROM
      (SELECT DISTINCT el.ElementValue FROM dbo.CatSource cs JOIN dbo.Element el ON cs.SourceID=el.ListID
       WHERE cs.CatHeaderID=h.ID AND el.LCID=1033)x),'') AS [Document type source],
    ISNULL((SELECT STRING_AGG(x.ElementValue,'#') FROM
      (SELECT DISTINCT el.ElementValue FROM dbo.CatPeriodicity cp JOIN dbo.Element el ON cp.PeriodicityID=el.ListID
       WHERE cp.CatHeaderID=h.ID AND el.LCID=1033)x),'') AS [Document type periodicity],
    ISNULL((SELECT STRING_AGG(x.ElementValue,'#') FROM
      (SELECT DISTINCT el.ElementValue FROM dbo.CatStatus cs JOIN dbo.Element el ON cs.StatusID=el.ListID
       WHERE cs.CatHeaderID=h.ID AND el.LCID=1033)x),'') AS [Document type status],
    ISNULL((SELECT STRING_AGG(x.ElementValue,'#') FROM
      (SELECT DISTINCT el.ElementValue FROM dbo.CatVersion cv JOIN dbo.Element el ON cv.VersionID=el.ListID
       WHERE cv.CatHeaderID=h.ID AND el.LCID=1033)x),'') AS [Document type version],

    ISNULL((SELECT STRING_AGG(x.DName,'#') FROM
      (SELECT DISTINCT be.DName FROM dbo.CatBody cb JOIN dbo.BodyList bl ON bl.ID=cb.BodyListID
       JOIN dbo.BodyElement be ON be.BodyListID=bl.ID WHERE cb.CatHeaderID=h.ID AND be.LCID=1033)x),'') AS [Body issuing],
    ISNULL((SELECT STRING_AGG(x.DName,'#') FROM
      (SELECT DISTINCT be.DName FROM dbo.CatBodyMentioned cbm JOIN dbo.BodyList bl ON bl.ID=cbm.BodyListID
       JOIN dbo.BodyElement be ON be.BodyListID=bl.ID WHERE cbm.CatHeaderID=h.ID AND be.LCID=1033)x),'') AS [Body mentioned],

    ISNULL((SELECT STRING_AGG(x.DCode,'#') FROM
      (SELECT DISTINCT ace.DCode FROM dbo.CatArticle ca JOIN dbo.ArticleCodeList al ON ca.ArticleCodeListID=al.ID
       JOIN dbo.ArticleCodeElement ace ON ace.ArticleCodeListID=al.ID WHERE ca.CatHeaderID=h.ID AND ace.LCID=1033)x),'') AS [Article basis],
    ISNULL((SELECT STRING_AGG(x.DCode,'#') FROM
      (SELECT DISTINCT ace.DCode FROM dbo.CatArticleMentioned cam JOIN dbo.ArticleCodeList al ON cam.ArticleCodeListID=al.ID
       JOIN dbo.ArticleCodeElement ace ON ace.ArticleCodeListID=al.ID WHERE cam.CatHeaderID=h.ID AND ace.LCID=1033)x),'') AS [Article mentioned],

    ISNULL((SELECT STRING_AGG(x.HName,'#') FROM
      (SELECT DISTINCT o.HName FROM dbo.CatOrganization co JOIN dbo.OrganizationList o ON co.OrganizationListID=o.ID
       WHERE co.CatHeaderID=h.ID)x),'') AS [Organization],
    ISNULL((SELECT STRING_AGG(x.HName,'#') FROM
      (SELECT DISTINCT pl.HName FROM dbo.CatProduct cp JOIN dbo.ProductList pl ON pl.ID=cp.ProductListID
       WHERE cp.CatHeaderID=h.ID)x),'') AS [Products],
    ISNULL((SELECT STRING_AGG(x.HName,'#') FROM
      (SELECT DISTINCT sl.HName FROM dbo.CatSubject cs JOIN dbo.DOLSubjectList sl ON cs.DOLSubjectListID=sl.ID
       WHERE cs.CatHeaderID=h.ID)x),'') AS [Subject],

    ISNULL((SELECT STRING_AGG(x.Name,'#') FROM
      (SELECT DISTINCT cn.Name FROM dbo.CatCountry cc JOIN dbo.v_CountryNames cn ON cc.CountryCode=cn.Code
       WHERE cc.CatHeaderID=h.ID AND cc.CountryRoleCat=35 AND cn.Lang=1)x),'') AS [Member concerned],
    ISNULL((SELECT STRING_AGG(x.Name,'#') FROM
      (SELECT DISTINCT cn.Name FROM dbo.CatCountry cc JOIN dbo.v_CountryNames cn ON cc.CountryCode=cn.Code
       WHERE cc.CatHeaderID=h.ID AND cc.CountryRoleCat=36 AND cn.Lang=1)x),'') AS [Other member mentioned]
FROM dbo.CatalogueHeader h
WHERE h.MetaCollection=2 AND h.IsToPublish=1
  AND CAST(h.DmsJobId AS varchar(30))=?;
"""

def extract(server,database,driver,dms,crosswalk=False):
    try: import pyodbc
    except ImportError: raise RuntimeError("Install pyodbc: python -m pip install pyodbc")
    cs=f"DRIVER={{{driver}}};SERVER={server};DATABASE={database};Trusted_Connection=yes;TrustServerCertificate=yes;"
    with pyodbc.connect(cs) as conn:
        cur=conn.cursor(); cur.execute(QUERY,dms)
        cols=[c[0] for c in cur.description]; rows=cur.fetchall()
        if len(rows)!=1: raise RuntimeError(f"Expected 1 row for {dms}; got {len(rows)}")
        r=rows[0]
        row={cols[i]:("" if r[i] is None else str(r[i])) for i in range(len(cols))}
        if crosswalk:
            from crosswalk_metadata import enrich
            row=enrich(conn,row)
        return row

def validate_xsd(xml,xsd):
    try: from lxml import etree
    except ImportError: raise RuntimeError("Install lxml: python -m pip install lxml")
    schema=etree.XMLSchema(etree.parse(str(xsd)))
    doc=etree.parse(str(xml))
    ok=schema.validate(doc)
    return ok,[str(x) for x in schema.error_log]

def build_dome(row,xsd_name):
    root=ET.Element(q(DOME_NS,"domeMetadata"),{q(XSI_NS,"schemaLocation"):f"{DOME_NS} {xsd_name}"})
    for src,tgt in SINGLE:
        v=(row.get(src) or "").strip()
        if v: add(root,DOME_NS,tgt,v)
    for src,tgt in MULTI:
        for v in split_multi(row.get(src)): add(root,DOME_NS,tgt,v)
    return root

def require_prior(obj,job):
    docs=obj/"documentation"
    expected={
      1:"STEP_1_PREPARED_FIXITY_VERIFIED",2:"STEP_2_MASTERS_CREATED_UNVALIDATED",
      3:"STEP_3_VERAPDF_PASSED",4:"STEP_4_AUTO_QA_CLEAR",5:"STEP_5_DROID_CHARACTERIZED"}
    recs={}
    names={1:f"{job}_poc1_processing_record.json",2:f"{job}_poc2_conversion_record.json",
           3:f"{job}_poc3_validation_record.json",4:f"{job}_poc4_qa_record.json",
           5:f"{job}_poc5_characterization_record.json"}
    for n,pn in names.items():
        p=docs/pn
        if not p.exists(): raise RuntimeError(f"Missing {p}")
        d=json.loads(p.read_text(encoding="utf-8")); recs[n]=d
        if d.get("status")!=expected[n] and not (n==4 and d.get("status")=="STEP_4_MANUAL_QA_CLEAR"): raise RuntimeError(f"PoC {n} not passed: {d.get('status')}")
    droid=docs/f"{job}_droid_characterization.json"
    if not droid.exists(): raise RuntimeError(f"Missing {droid}")
    droid_data=json.loads(droid.read_text(encoding="utf-8"))
    coverage(obj,recs,droid_data)
    return recs,droid_data

def retained_files(obj,droid):
    dm={x.get("relative_path",""):x for x in droid.get("files",[])}
    out=[]
    for top in ("representations","documentation"):
        base=obj/top
        if not base.exists(): continue
        for p in sorted(base.rglob("*")):
            if not p.is_file(): continue
            if "record-history" in p.relative_to(obj).parts: continue
            n=p.name.lower()
            if n.endswith("_profile_state.json"): continue
            if working_pdf(p.relative_to(obj)): continue
            if top == "documentation" and ("_poc" in n or n.endswith("_driver.txt") or n.endswith("_droid_profile.droid") or n.endswith("_droid_characterization.json")): continue
            rp=p.relative_to(obj).as_posix()
            ids=dm.get(rp,{}).get("identifications",[])
            primary=ids[0] if ids else {}
            out.append({"path":rp,"sha512":sha512(p),"size":p.stat().st_size,
                        "mime":primary.get("mime_type") or mimetypes.guess_type(p.name)[0] or "",
                        "puid":primary.get("puid",""),"format_name":primary.get("format_name","")})
    return out

def build_premis(folder_uuid,files):
    root=ET.Element(q(PREMIS_NS,"premis"),{"version":"3.0"})
    rep=add(root,PREMIS_NS,"object",**{q(XSI_NS,"type"):"premis:representation"})
    oi=add(rep,PREMIS_NS,"objectIdentifier")
    add(oi,PREMIS_NS,"objectIdentifierType","UUID"); add(oi,PREMIS_NS,"objectIdentifierValue",folder_uuid)
    for f in files:
        o=add(root,PREMIS_NS,"object",**{q(XSI_NS,"type"):"premis:file"})
        oi=add(o,PREMIS_NS,"objectIdentifier"); add(oi,PREMIS_NS,"objectIdentifierType","UUID" if f.get("file_id") else "local-path"); add(oi,PREMIS_NS,"objectIdentifierValue",f.get("file_id") or f["path"])
        oc=add(o,PREMIS_NS,"objectCharacteristics"); fx=add(oc,PREMIS_NS,"fixity")
        add(fx,PREMIS_NS,"messageDigestAlgorithm","SHA-512"); add(fx,PREMIS_NS,"messageDigest",f["sha512"]); add(oc,PREMIS_NS,"size",f["size"])
        add(o,PREMIS_NS,"originalName",f.get("original_path") or f["path"])
        if f.get("source_file_id"):
            rel=add(o,PREMIS_NS,"relationship")
            add(rel,PREMIS_NS,"relationshipType","derivation")
            add(rel,PREMIS_NS,"relationshipSubType","has source")
            related=add(rel,PREMIS_NS,"relatedObjectIdentifier")
            add(related,PREMIS_NS,"relatedObjectIdentifierType","UUID")
            add(related,PREMIS_NS,"relatedObjectIdentifierValue",f["source_file_id"])
    return root

def build_mets(job,folder_uuid,files):
    root=ET.Element(q(METS_NS,"mets"),{"OBJID":folder_uuid,"LABEL":f"DOL preservation object {job}"})
    fs=add(root,METS_NS,"fileSec"); grp=add(fs,METS_NS,"fileGrp",USE="PRESERVATION_OBJECT")
    for i,f in enumerate(files,1):
        attrs={"ID":("F_"+f["file_id"] if f.get("file_id") else f"F{i:04d}"),"CHECKSUM":f["sha512"],"CHECKSUMTYPE":"SHA-512","SIZE":str(f["size"])}
        if f["mime"]: attrs["MIMETYPE"]=f["mime"]
        fe=add(grp,METS_NS,"file",**attrs); add(fe,METS_NS,"FLocat",LOCTYPE="SYSTEM",LOCREF=f["path"])
    ss=add(root,METS_NS,"structSec"); sm=add(ss,METS_NS,"structMap",TYPE="physical"); div=add(sm,METS_NS,"div",TYPE="document",LABEL=job)
    for i,f in enumerate(files,1): add(div,METS_NS,"fptr",FILEID=("F_"+f["file_id"] if f.get("file_id") else f"F{i:04d}"))
    return root


def add_preservation_filepaths_to_dome(dome_path: Path, object_dir: Path) -> None:
    """Add portable object-relative representation paths to DOME XML."""
    tree=ET.parse(dome_path); root=tree.getroot()
    for child in list(root):
        if child.tag.split("}")[-1]=="preservationFiles": root.remove(child)
    # Preserve the DOME namespace used by the existing root document.
    # ElementTree represents a namespaced tag as "{namespace-uri}localName".
    if root.tag.startswith("{"):
        dome_ns = root.tag[1:].split("}", 1)[0]
    else:
        dome_ns = "https://docs.wto.org/dpr/dome/v1"

    def qname(local_name: str) -> str:
        return f"{{{dome_ns}}}{local_name}"

    # Keep the existing default namespace when serializing rather than
    # introducing ns0 prefixes.
    ET.register_namespace("", dome_ns)

    block=ET.SubElement(root,qname("preservationFiles")); count=0
    reps=object_dir/"representations"
    for rep in ("rep_1","rep_2"):
        data=reps/rep/"data"
        if not data.is_dir(): continue
        for lang_dir in sorted(p for p in data.iterdir() if p.is_dir()):
            lang=lang_dir.name.lower()
            if lang not in {"en","fr","es","attachments"}: continue
            for fp in sorted(p for p in lang_dir.rglob("*") if p.is_file()):
                if working_pdf(fp.relative_to(object_dir)): continue
                file_lang=lang
                if lang == "attachments":
                    sources=source_records(object_dir)
                    c=next((x for x in load(object_dir/"documentation"/f"{object_dir.name}_poc2_conversion_record.json").get("conversions",[]) if Path(x["master_path"]) == fp), {})
                    source=next((x for x in sources if Path(x["staged_path"]) == fp or x["file_id"] == c.get("source_file_id")), {})
                    file_lang=source.get("language", "und")
                f=ET.SubElement(block,qname("file"))
                ET.SubElement(f,qname("language")).text=file_lang
                ET.SubElement(f,qname("representation")).text=rep
                ET.SubElement(f,qname("filePath")).text=fp.relative_to(object_dir).as_posix()
                count+=1
    if not count: root.remove(block)
    try: ET.indent(tree,space="  ")
    except AttributeError: pass
    tree.write(dome_path,encoding="utf-8",xml_declaration=True)


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--preparation-dir",required=True)
    ap.add_argument("--dms-job-id",required=True)
    ap.add_argument("--server",required=True)
    ap.add_argument("--database",required=True)
    ap.add_argument("--driver",default="ODBC Driver 18 for SQL Server")
    ap.add_argument("--xsd",required=True)
    ap.add_argument("--profile",action="store_true",help="Use the team draft PREMIS/METS and DOME v0.3 profile")
    ap.add_argument("--aip-root",type=Path)
    ap.add_argument("--agent-registry",type=Path)
    ap.add_argument("--java",type=Path)
    ap.add_argument("--droid-jar",type=Path)
    ap.add_argument("--strict-profile",action="store_true")
    ap.add_argument("--corrections",type=Path)
    ap.add_argument("--batch-id")
    args=ap.parse_args()
    try:
        job=preservation_job_id(args.dms_job_id)
        obj=Path(args.preparation_dir).resolve()/job
        if not obj.is_dir(): raise RuntimeError(f"Preparation object not found: {obj}")
        recs,droid=require_prior(obj,job)
        row=extract(args.server,args.database,args.driver,args.dms_job_id,crosswalk=args.profile)
        if row.get("Job ID","").strip()!=job: raise RuntimeError("SQL/Python JOB_ID conversion mismatch")
        if args.profile:
            from profile_workflow import metadata as profile_metadata
            return profile_metadata(args,obj,job,recs,droid,row)
        metadata=obj/"metadata"; docs=obj/"documentation"; metadata.mkdir(exist_ok=True); docs.mkdir(exist_ok=True)
        previous=load(docs/f"{job}_poc6_metadata_record.json")
        prior_sql=load(docs/f"{job}_dome_sql_extraction.json")
        snapshot={f["file_id"]:f["sha512_staged"] for f in recs[1]["files"]}
        if previous.get("source_snapshot") == snapshot and prior_sql.get("metadata") == row:
            for key in ("dome", "premis", "mets"):
                verify(obj/previous[key]["path"], previous[key]["sha512"])
            print("[REUSE] Metadata source snapshot, SQL values and fixity verified")
            return 0
        for existing in metadata.glob("*.xml"): archive(existing)
        archive(docs/f"{job}_dome_sql_extraction.json")
        archive(docs/f"{job}_poc6_metadata_record.json")
        extraction=docs/f"{job}_dome_sql_extraction.json"; write_json(extraction,{"source_dms_job_id":args.dms_job_id,"job_id":job,"extracted_utc":now(),"metadata":row})
        dome=metadata/f"{job}_dome.xml"; write_xml(dome,build_dome(row,Path(args.xsd).name)); add_preservation_filepaths_to_dome(dome,obj)
        ok,errs=validate_xsd(dome,Path(args.xsd))
        if not ok: raise RuntimeError("DOME XSD validation failed:\n  "+"\n  ".join(errs))
        files=retained_files(obj,droid)
        registry={Path(f["staged_path"]).relative_to(obj).as_posix(): f for f in recs[1]["files"]}
        for c in recs[2]["conversions"]:
            registry[Path(c["master_path"]).relative_to(obj).as_posix()]={"file_id": c["master_file_id"], "source_file_id": c["source_file_id"]}
        for entry in recs[3]["validations"]:
            registry[Path(entry["validation_report_path"]).relative_to(obj).as_posix()]={"file_id":entry["validation_report_file_id"]}
        for entry in recs[4]["qa_entries"]:
            registry[Path(entry["qa_report_path"]).relative_to(obj).as_posix()]={"file_id":entry["qa_report_file_id"]}
        for entry in recs[5].get("evidence_files",[]):
            registry[entry["relative_path"]]={"file_id":entry["file_id"]}
        for f in files: f.update({k:v for k,v in registry.get(f["path"],{}).items() if k in {"file_id","original_path","source_file_id"}})
        provenance=[{"path":Path(f["staged_path"]).relative_to(obj).as_posix(), "file_id":f["file_id"], "original_path":f["original_path"], "sha512":f["sha512_staged"], "size":f["size_bytes"]} for f in recs[1]["files"] if f["role"] == "published_pdf_working_source"]
        files.append({"path":dome.relative_to(obj).as_posix(),"sha512":sha512(dome),"size":dome.stat().st_size,"mime":"application/xml","puid":"","format_name":"XML"})
        premis=metadata/f"{job}_premis.xml"; write_xml(premis,build_premis(recs[1].get("folder_uuid"),files+provenance))
        files2=files+[{"path":premis.relative_to(obj).as_posix(),"sha512":sha512(premis),"size":premis.stat().st_size,"mime":"application/xml","puid":"","format_name":"PREMIS"}]
        mets=metadata/f"{job}_mets.xml"; write_xml(mets,build_mets(job,recs[1].get("folder_uuid"),files2))
        record=docs/f"{job}_poc6_metadata_record.json"
        write_json(record,{"poc":6,"job_id":job,"folder_uuid":recs[1].get("folder_uuid"),"source_snapshot":{f["file_id"]:f["sha512_staged"] for f in recs[1]["files"]},"source_dms_job_id":args.dms_job_id,"created_utc":now(),"status":"STEP_6_METADATA_PROJECTED",
                           "dome":{"path":dome.relative_to(obj).as_posix(),"sha512":sha512(dome),"schema_validation":"PASS"},
                           "premis":{"path":premis.relative_to(obj).as_posix(),"sha512":sha512(premis)},
                           "mets":{"path":mets.relative_to(obj).as_posix(),"sha512":sha512(mets)}})
        print(f"[PASS] SQL -> {job}_dome.xml -> PREMIS -> METS")
        print(f"       JOB_ID: {job}")
        print(f"       DOME:   {dome}")
        print(f"       PREMIS: {premis}")
        print(f"       METS:   {mets}")
        print("Status: STEP_6_METADATA_PROJECTED")
        return 0
    except Exception as e:
        print(f"[FATAL] {e}",file=sys.stderr); return 10

if __name__=="__main__":
    raise SystemExit(main())
