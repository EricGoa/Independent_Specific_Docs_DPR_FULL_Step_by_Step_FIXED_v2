"""Verified SQL enrichment for the completed DOL/DOME crosswalk.
Reference: team-review/completed_crosswalk.json, Crosswalk sheet.
Raw IDs without DOME schema terms remain in extraction evidence only.
"""
import json
from pathlib import Path

def enrich(conn, row):
    cur=conn.cursor()
    def query(sql):
        cur.execute(sql,int(row['CatHeaderId']))
        names=[c[0] for c in cur.description]
        return [{name:r[i] for i,name in enumerate(names)} for r in cur.fetchall()]
    header=query("""
      SELECT CAST(h.DmsJobId AS varchar(30)) AS dms,
       CONVERT(varchar(10),h.CreateDate,23) AS document_date,
       CONVERT(varchar(10),h.DerestrictionDate,23) AS derestriction_date,
       h.TranslationRuleApplied AS translation_rule, h.RestrictionType AS restriction_id,
       action.ListName AS last_action,
       l.ListName AS restriction, a.ListName AS access,
       t.ListName AS derestriction_type, m.HName AS collection
      FROM dbo.CatalogueHeader h
      LEFT JOIN dbo.[List] action ON action.ID=h.LastDmsAction
      LEFT JOIN dbo.[List] l ON l.ID=h.RestrictionType
      LEFT JOIN dbo.[List] a ON a.ID=h.AccessType
      LEFT JOIN dbo.[List] t ON t.ID=h.DerestrictionType
      LEFT JOIN dbo.MetaCollectionList m ON m.ID=h.MetaCollection
      WHERE h.ID=?""")[0]
    details=query("""
      SELECT LCID, NbPages AS pages, CONVERT(varchar(10),PostingDate,23) AS issued,
       CatalogueStatus AS catalogue_status
      FROM dbo.CatalogueDetail WHERE CatHeaderID=? ORDER BY LCID,ID""")
    def unique(values):
        return sorted({str(v).strip() for v in values if v is not None and str(v).strip()})
    def column(sql,name):return unique(r[name] for r in query(sql))
    languages={1033:'en',1036:'fr',3082:'es'}
    fields={
      'jobID':[header['dms']],
      'documentDate':[header['document_date']],
      'language':unique(languages.get(r['LCID']) for r in details),
      'pages':[{'value':str(r['pages']),'language':languages.get(r['LCID'])}
               for r in details if r['pages'] is not None and r['LCID'] in languages],
      'dateIssued':[{'value':r['issued'],'language':languages.get(r['LCID'])}
               for r in details if r['issued'] and r['LCID'] in languages],
      'derestrictionDate':[header['derestriction_date']],
      'accessRights':[header['access']],
      'ACLInfo':[header['restriction']],
      'collection':[header['collection']],
    }
    numbers=column("""SELECT n.NotificationNumber AS value FROM dbo.CatHeaderNotification ch
      JOIN dbo.Notification n ON n.ID=ch.NotificationID WHERE ch.CatHeaderID=? AND n.Deleted=0""",'value')
    req=""" FROM dbo.CatHeaderNotification ch JOIN dbo.ReqNotification rn
        ON rn.NotificationID=ch.NotificationID """
    for name,col in [('requirementName','LongName'),('requirementShortName','ShortName')]:
        fields[name]=column("SELECT d."+col+" AS value"+req+
           "JOIN dbo.RequirementDetail d ON d.ReqHeaderID=rn.ReqHeaderID AND d.LCID=1033 WHERE ch.CatHeaderID=? AND d.Deleted=0",'value')
    fields['requirementMeasureType']=column("SELECT e.DName AS value"+req+"""
        JOIN dbo.ReqMeasure rm ON rm.ReqHeaderID=rn.ReqHeaderID
        JOIN dbo.MeasureElement e ON e.MeasureListID=rm.MeasureID AND e.LCID=1033
        WHERE ch.CatHeaderID=? AND e.Deleted=0""",'value')
    fields['notificationPeriodicity']=column("SELECT e.DName AS value"+req+"""
        JOIN dbo.RequirementHeader h ON h.ID=rn.ReqHeaderID
        JOIN dbo.PeriodicityElement e ON e.PeriodicityListID=h.Periodicity AND e.LCID=1033
        WHERE ch.CatHeaderID=? AND e.Deleted=0""",'value')
    fields['membersReqNotify']=column("SELECT n.Note AS value"+req+"""
        JOIN dbo.ReqNote n ON n.ReqHeaderID=rn.ReqHeaderID AND n.LCID=1033
        WHERE ch.CatHeaderID=? AND n.Deleted=0""",'value')
    fields['requirementTopic']=column("""SELECT e.DName AS value FROM dbo.CatHeaderNotification ch
        JOIN dbo.NotObjectiveNote n ON n.NotificationID=ch.NotificationID
        JOIN dbo.ObjectiveNoteElement e ON e.ObjectiveNoteListID=n.ObjectiveNoteListID AND e.LCID=1033
        WHERE ch.CatHeaderID=? AND e.Deleted=0""",'value')
    for name,role in [('countryTerritoryAffected','Affected'),('countryTerritoryNotifying','Notifying')]:
        fields[name]=column("""SELECT cn.Name AS value FROM dbo.CatHeaderNotification ch
         JOIN dbo.NotCountry nc ON nc.NotificationID=ch.NotificationID
         JOIN dbo.[List] l ON l.ID=nc.CountryRoleNot
         JOIN dbo.v_CountryNames cn ON cn.Code=nc.CountryCode AND cn.Lang=1
         WHERE ch.CatHeaderID=? AND l.ListName='"""+role+"'",'value')
    coverage=query("""SELECT l.ListName AS scheme,n.ClassificationCode AS code,
        n.ClassificationVersion AS version FROM dbo.CatHeaderNotification ch
        JOIN dbo.NotClassification n ON n.NotificationID=ch.NotificationID
        JOIN dbo.[List] l ON l.ID=n.ClassificationType WHERE ch.CatHeaderID=?
        ORDER BY l.ListName,n.ClassificationCode""")
    attachments=query("""SELECT n.FileName AS name,n.FileURL AS url,n.LCID FROM dbo.CatHeaderNotification ch
        JOIN dbo.NotAttachment n ON n.NotificationID=ch.NotificationID
        WHERE ch.CatHeaderID=? AND n.Deleted=0 ORDER BY n.ID""")
    # Keep actual database URLs. No filesystem paths are recast as web URLs.
    fields['notificationAttachment']=[{'value':a['name'] or '', 'url':a['url'],
        'language':languages.get(a['LCID'])} for a in attachments if a['url']]
    fields['tradeCoverage']=[{'scheme':r['scheme'],'code':r['code']} for r in coverage
                            if r['scheme'] in {'HS','ICS','Services','Environment'} and r['code']]
    row=dict(row)
    row['DOME crosswalk']=fields
    manifest=json.loads((Path(__file__).parent/'team-review/completed_crosswalk.json').read_text(encoding='utf-8'))
    row['Crosswalk provenance']={'sha512':manifest['sha512'],'sheet':'Crosswalk',
         'schema_unsupported':{'CRNid':numbers,'TraslationRule':header['translation_rule'],
                               'derestrictionType':header['derestriction_type']},
         'catalogue_status_by_language':details,
         'last_dms_action':header['last_action'],'restriction_type_id':header['restriction_id'],
         'trade_coverage_source':coverage,
         'policy':'CatalogueStatus is a per-language workflow state, not the editorial document status. Existing CatStatus editorial labels retained. No unverified vocabulary URIs invented.'}
    return row
