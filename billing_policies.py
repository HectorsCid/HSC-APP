"""Expediente comercial de pólizas, separado del CFDI y del registro de timbrado."""
import hashlib
import json
import re
import csv
import io
import time
from datetime import date, timedelta
from flask import Blueprint, current_app, jsonify, request, Response
from werkzeug.utils import secure_filename

bp = Blueprint("billing_policies", __name__, url_prefix="/api/facturacion/polizas")
MONTH = re.compile(r"[0-9]{4}-(?:0[1-9]|1[0-2])")
MAX_PDF = 8 * 1024 * 1024


class PolicyError(ValueError):
    pass


@bp.errorhandler(PolicyError)
def invalid(exc):
    return jsonify(ok=False, error=str(exc)), 400


def sql(query, params=(), *, write=False):
    store = current_app.extensions["operations_store"]
    if not store.enabled:
        raise PolicyError("No está conectada la base de Operaciones.")
    store.initialize()
    with store.connection() as conn:
        cursor = conn.execute(query.replace("?", store.placeholder), params)
        return [] if write else cursor.fetchall()


def clients(key=None):
    store = current_app.extensions["operations_store"]
    if not store.enabled:
        raise PolicyError("No está conectada la base de Operaciones.")
    rows = []
    configured=commercial()
    for c in store.list_clients(client_id=key):
        ident = current_app.extensions["policy_identity"](c)
        owned=[p for p in configured if p["key"]==c["id"] and p["rfc"]==ident["rfc"]]
        if c["policy_active"] or owned:
            rows.append(dict(id=c["id"], name=c["name"], rfc=ident["rfc"], photo=c["has_photo"], email=ident.get("email","")))
    for p in configured:
        if (not key or key==p["key"]) and not any(row["id"]==p["key"] for row in rows):
            rows.append(dict(id=p["key"], name=p["client"],rfc=p["rfc"],photo=False,email=""))
    return rows


def commercial():
    rows=current_app.extensions.get("commercial_policies",lambda:[])()
    return [dict(p,key=p["key"] or "fiscal-"+p["rfc"]) for p in rows if p["rfc"]]


def assigned_policy(key,rfc,name):
    match=[p for p in commercial() if p["key"]==key and p["rfc"]==rfc and p["name"]==name]
    if len(match)!=1:
        raise PolicyError("La póliza no está configurada para este cliente y lote.")
    return match[0]


def client(key):
    rows = clients(key)
    if not rows:
        raise PolicyError("El cliente no tiene una póliza activa.")
    return rows[0]


def rules(key):
    rows = sql("SELECT rules FROM billing_policy_rules WHERE client_id=?", (key,))
    return json.loads(rows[0][0]) if rows else {}


def invoices(c):
    from facturacion_bp import billing_client_groups
    if not c["rfc"]:
        return []
    groups, _ = billing_client_groups(customer_tax_id=c["rfc"])
    return [i for g in groups if g["rfc"] == c["rfc"] for i in g["invoices"]]


def link(inv, c, name, period, *, queue=True):
    if not name or len(name)>100 or not MONTH.fullmatch(period):
        raise PolicyError("Indica la póliza y el mes del servicio.")
    sql("""INSERT INTO billing_policy_invoices(invoice_id,client_id,rfc,policy,period) VALUES (?,?,?,?,?)
        ON CONFLICT(invoice_id) DO UPDATE SET policy=excluded.policy,period=excluded.period
        WHERE billing_policy_invoices.client_id=excluded.client_id AND billing_policy_invoices.rfc=excluded.rfc""",
        (inv,c["id"],c["rfc"],name,period),write=True)
    if not sql("SELECT 1 FROM billing_policy_invoices WHERE invoice_id=? AND client_id=? AND rfc=?", (inv,c["id"],c["rfc"])):
        raise PolicyError("Esta factura ya está vinculada a otro cliente.")
    if queue and name in rules(c["id"]).get("automatic",[]):
        sql("""INSERT INTO billing_policy_mail(invoice_id,client_id,policy,state,due) VALUES (?,?,?,'waiting',0)
            ON CONFLICT(invoice_id) DO UPDATE SET client_id=excluded.client_id,policy=excluded.policy,state='waiting',due=0,note=''
            WHERE billing_policy_mail.state IN ('waiting','manual')""",(inv,c["id"],name),write=True)


def tag_template(inv, template, rfc, period, schedule_id=""):
    if schedule_id:
        from facturacion_bp import _read_invoice_schedules
        sch=next((s for s in _read_invoice_schedules() if s["id"]==schedule_id),None)
        if not sch or sch["template_id"]!=template:
            raise PolicyError("La programación no corresponde a esta plantilla.")
        if sch.get("policy_name"):
            p=assigned_policy(sch.get("policy_client"),rfc,sch["policy_name"])
            if rules(p["key"]).get("previous"):
                period=(date.fromisoformat(period+"-01")-timedelta(days=1)).isoformat()[:7]
            link(inv,client(p["key"]),p["name"],period,queue=False)
            return True
    # A legacy template is not a contract. Keep historical invoice links intact.
    return False


@bp.get("")
def index():
    from email_tracking import delivery_status, read_email_deliveries
    rows=clients()
    tracked={(c["id"],c["rfc"]):c for c in rows}
    deliveries=read_email_deliveries()
    contracts=commercial()
    for c in rows:
        c["policy_count"]=sum(p["key"]==c["id"] and p["rfc"]==c["rfc"] for p in contracts)
    for key,rfc,inv in sql("SELECT client_id,rfc,invoice_id FROM billing_policy_invoices"):
        c=tracked.get((key,rfc))
        if c is not None:
            c["history"]=True
            if not delivery_status("factura",inv,deliveries).get("sent"):
                c["unconfirmed"]=c.get("unconfirmed",0)+1
    return jsonify(ok=True, clients=[c for c in rows if c["policy_count"] or c.get("history")])


@bp.get("/<key>")
def detail(key):
    from facturacion_bp import _read_invoice_templates
    from email_tracking import delivery_status, read_email_deliveries
    c=client(key)
    links={i:(n,m) for i,n,m in sql("SELECT invoice_id,policy,period FROM billing_policy_invoices WHERE client_id=? AND rfc=?", (key,c["rfc"]))}
    sql("UPDATE billing_policy_mail SET state='uncertain',note='Verifica Enviados antes de reenviar.' WHERE client_id=? AND state='sending' AND due<?",
        (key,int(time.time())-1800),write=True)
    deliveries=read_email_deliveries()
    try:
        rows=invoices(c)
    except RuntimeError as exc:
        return jsonify(ok=False,error=str(exc)),503
    jobs={i:(s,n) for i,s,n in sql("SELECT invoice_id,state,note FROM billing_policy_mail WHERE client_id=?",(key,))}
    for row in rows:
        row["policy"],row["period"]=links.get(row["id"],("",""))
        row["email_delivery"]=delivery_status("factura", row["id"], deliveries)
        row["auto_state"],row["auto_note"]=jobs.get(row["id"],("",""))
    templates=[dict(id=t["id"],name=t["name"]) for t in _read_invoice_templates()
               if c["rfc"] and str((t.get("receiver") or {}).get("tax_id") or "").upper()==c["rfc"]]
    return jsonify(ok=True,client=c,rules=rules(key),templates=templates,invoices=rows,configured=[p for p in commercial() if p['key']==key and p['rfc']==c['rfc']])


@bp.post("/<key>/rules")
def save_rules(key):
    from facturacion_bp import _read_invoice_templates
    c=client(key)
    body=request.get_json() or {}
    ids=body.get("templates") or []
    templates={t["id"]:t["name"] for t in _read_invoice_templates() if t["id"] in ids and c["rfc"] and (t.get("receiver") or {}).get("tax_id","").upper()==c["rfc"]}
    if set(templates)!=set(ids):
        raise PolicyError("Revisa las plantillas seleccionadas.")
    for other, raw in sql("SELECT client_id,rules FROM billing_policy_rules"):
        if other!=key and set(templates).intersection(json.loads(raw).get("templates",{})):
            raise PolicyError("Una plantilla ya está asignada a otra póliza.")
    cfg={k:str(body.get(k) or "").strip()[:4000] for k in ("email","cc","subject","message")}
    cfg.update({k:body.get(k) is True for k in ("signed","oc","previous")},templates=templates)
    names=set(templates.values())|{row[0] for row in sql("SELECT DISTINCT policy FROM billing_policy_invoices WHERE client_id=?",(key,))}
    auto=body.get("automatic") or []
    names.update(p['name'] for p in commercial() if p['key']==key and p['rfc']==c['rfc'])
    if not isinstance(auto,list) or any(not isinstance(n,str) or n not in names for n in auto):
        raise PolicyError("Selecciona una póliza válida para el envío automático.")
    if auto:
        from smtp_mailer import authorized_to_send,parse_recipients,parse_optional_recipients
        if not authorized_to_send(body.get("send_key"), request.cookies.get("hsc_mail_trusted","")):
            return jsonify(ok=False,error="Necesitas la clave de envío para autorizar el correo automático."),403
        try:
            parse_recipients(cfg["email"] or c["email"])
            parse_optional_recipients(cfg["cc"])
        except ValueError as exc:
            raise PolicyError(str(exc)) from exc
    cfg["automatic"]=sorted(set(auto))
    sql("INSERT INTO billing_policy_rules VALUES (?,?) ON CONFLICT(client_id) DO UPDATE SET rules=excluded.rules",
        (key,json.dumps(cfg)),write=True)
    for (name,) in sql("SELECT DISTINCT policy FROM billing_policy_mail WHERE client_id=? AND state='waiting'",(key,)):
        enabled=name in auto
        sql("UPDATE billing_policy_mail SET state=?,due=0,note=? WHERE client_id=? AND policy=? AND state='waiting'",
            ("waiting" if enabled else "manual","" if enabled else "Envío manual",key,name),write=True)
    return jsonify(ok=True)


@bp.post("/<key>/link")
def manual_link(key):
    c=client(key)
    b=request.get_json() or {}
    inv=b.get("id")
    if not any(i["id"]==inv for i in invoices(c)):
        raise PolicyError("La factura no corresponde al RFC de este cliente.")
    if b.get("unlink") is not True:
        name=str(b.get("policy") or "").strip()
        if not sql("SELECT 1 FROM billing_policy_invoices WHERE client_id=? AND rfc=? AND invoice_id=? AND policy=?", (key,c['rfc'],inv,name)):
            assigned_policy(key,c['rfc'],name)
    if sql("SELECT 1 FROM billing_policy_mail WHERE invoice_id=? AND state='sending'",(inv,)):
        raise PolicyError("El correo está en proceso. Espera antes de editar el vínculo.")
    sql("UPDATE billing_policy_mail SET state='manual',note='Envío manual' WHERE invoice_id=? AND client_id=? AND state='waiting'",(inv,key),write=True)
    if b.get("unlink") is True:
        sql("DELETE FROM billing_policy_invoices WHERE client_id=? AND invoice_id=?", (key,inv),write=True)
    else:
        link(inv,c,str(b.get("policy") or "").strip(),str(b.get("period") or ""),queue=b.get("queue") is True)
    return jsonify(ok=True)


def context(key, inv):
    c=client(key)
    rows=sql("SELECT policy,period FROM billing_policy_invoices WHERE invoice_id=? AND client_id=? AND rfc=?",(inv,key,c["rfc"]))
    if not rows:
        raise PolicyError("La factura ya no está vinculada a esta póliza.")
    return c, *rows[0]


def read_pdf(upload):
    if not upload:
        raise PolicyError("Selecciona un PDF.")
    data=upload.stream.read(MAX_PDF+1)
    if len(data)>MAX_PDF or not data.startswith(b"%PDF-"):
        raise PolicyError("Selecciona un PDF válido de hasta 8 MB.")
    return data


@bp.post("/<key>/<inv>/oc")
def save_order(key,inv):
    _,name,period=context(key,inv)
    upload=request.files.get("file")
    data=read_pdf(upload)
    scope=request.form.get("scope","invoice")
    if scope=="year": period=period[:4]
    elif scope=="invoice": period="invoice:"+inv
    elif scope!="month": raise PolicyError("Alcance de OC no válido.")
    sql("""INSERT INTO billing_policy_orders VALUES (?,?,?,?,?,?) ON CONFLICT(client_id,policy,period)
        DO UPDATE SET filename=excluded.filename,digest=excluded.digest,content=excluded.content""",
        (key,name,period,secure_filename(upload.filename) or "OC.pdf",hashlib.sha256(data).hexdigest(),data),write=True)
    sql("UPDATE billing_policy_mail SET due=0 WHERE client_id=? AND policy=? AND state='waiting'",(key,name),write=True)
    return jsonify(ok=True)


@bp.post("/<key>/<inv>/signed")
def save_signed(key,inv):
    context(key,inv)
    u=request.files.get("file")
    content=read_pdf(u)
    sql("""INSERT INTO billing_policy_signed VALUES (?,?,?,?) ON CONFLICT(invoice_id)
        DO UPDATE SET filename=excluded.filename,content=excluded.content WHERE billing_policy_signed.client_id=excluded.client_id""",
        (inv,key,secure_filename(u.filename) or "Firmada.pdf",content),write=True)
    sql("UPDATE billing_policy_mail SET due=0 WHERE invoice_id=? AND state='waiting'",(inv,),write=True)
    return jsonify(ok=True)


@bp.get("/<key>/relacion.csv")
def export_relation(key):
    from email_tracking import read_email_deliveries,delivery_status
    c=client(key)
    first,last=request.args.get("desde",""),request.args.get("hasta","")
    if not MONTH.fullmatch(first) or not MONTH.fullmatch(last) or first>last or first.startswith("0000"):
        raise PolicyError("Selecciona un periodo válido.")
    def idx(v): return int(v[:4])*12+int(v[5:])-1
    if idx(last)-idx(first)>=120: raise PolicyError("Selecciona hasta diez años.")
    links=sql("SELECT invoice_id,policy,period FROM billing_policy_invoices WHERE client_id=? AND rfc=? AND period BETWEEN ? AND ? ORDER BY period,policy",(key,c["rfc"],first,last))
    selected=request.args.get("poliza","")
    if selected:
        known=any(p["key"]==key and p["rfc"]==c["rfc"] and p["name"]==selected for p in commercial())
        if not known and not sql("SELECT 1 FROM billing_policy_invoices WHERE client_id=? AND rfc=? AND policy=? LIMIT 1",(key,c["rfc"],selected)):
            raise PolicyError("La póliza no corresponde al cliente.")
        links=[link for link in links if link[1]==selected]
    docs={r["id"]:r for r in invoices(c)} if links else {}
    if any(i not in docs for i,_,_ in links): raise PolicyError("El historial aún no confirma todas las facturas vinculadas. Actualízalo antes de descargar.")
    names=sorted({n for _,n,_ in links}|
                 {p["name"] for p in commercial() if p["key"]==key and p["rfc"]==c["rfc"]}) or [""]
    groups={}
    if selected: names=[selected]
    for i,n,m in links: groups.setdefault((m,n),[]).append(docs[i])
    statuses=read_email_deliveries() if links else {}
    out=io.StringIO()
    writer=csv.writer(out)
    def row(*values):
        writer.writerow(["'"+str(v) if str(v).lstrip().startswith(("=","+","-","@")) else v for v in values])
    row("Cliente","Mes del servicio","Póliza","Fecha de emisión","Folio interno","Folio fiscal","Estado","Último envío registrado")
    for ix in range(idx(first),idx(last)+1):
        m=f"{ix//12:04d}-{ix%12+1:02d}"
        for n in names:
            covered=False
            for d in sorted(groups.get((m,n),[]),key=lambda d:(d.get("date") or "",str(d.get("folio") or ""))):
                delivery=delivery_status("factura",d["id"],statuses)
                invalid=d.get("cancellation_status") in ("pending","canceled") or d.get("active") is False
                covered|=not invalid
                state=(d.get("status_label") or "Cancelación") if invalid else ("Enviada" if delivery["sent"] else "Sin envío registrado")
                row(c["name"],m,n,(d.get("date") or "")[:10],d.get("folio"),d.get("uuid"),state,delivery["last_sent_at"])
            if not covered: row(c["name"],m,n,"","","","Pendiente · sin factura vigente vinculada","")
    filename=secure_filename(f"Relacion-{c['name']}-{first}-{last}.csv")
    return Response("\ufeff"+out.getvalue(),content_type="text/csv; charset=utf-8",headers={"Content-Disposition":f'attachment; filename="{filename}"',"Cache-Control":"no-store"})


def packet(key,inv,download=False):
    from facturacion_bp import _invoice_pdf_metadata, list_invoice_support_documents, download_invoice_support_documents
    c,name,month=context(key,inv)
    cfg=rules(key)
    info=dict(email=cfg.get("email") or c["email"],cc=cfg.get("cc",""),subject=cfg.get("subject",""),message=cfg.get("message",""),
              policy=name,period=month,signed=cfg.get("signed",False),required=cfg.get("oc",False),oc="")
    cols="filename,digest,period"+(",content" if download else "")
    rows=sql(f"SELECT {cols} FROM billing_policy_orders WHERE client_id=? AND policy=? AND period IN (?,?,?) ORDER BY period DESC",(key,name,"invoice:"+inv,month,month[:4]))
    extra=[]
    marker=""
    if not rows or rows[0][2] != "invoice:"+inv:
        meta=_invoice_pdf_metadata(inv)
        folder=meta.get("drive_folder_name") or meta.get("internal_folio")
        if folder and meta.get("client_name"):
            docs=[d for d in list_invoice_support_documents(meta["client_name"],folder)
                  if (d.get("appProperties") or {}).get("hscCategory")=="orden_compra"]
            if len(docs)==1:
                doc=docs[0]; info["oc"]=doc["name"]; marker=doc["id"]
                if download:
                    extra=download_invoice_support_documents(meta["client_name"],folder,[marker])
                    if len(extra)!=1: raise PolicyError("No se pudo recuperar la OC de esta factura.")
            elif len(docs)>1:
                info["required"]=True
                info["warning"]="Hay varias OC en el expediente. Vincula la correcta solo a esta factura antes de enviar."
    if rows and not info["oc"] and not info.get("warning"):
        row=rows[0]
        info["oc"]=row[0]; marker=row[1]
        if download: extra=[dict(data=bytes(row[3]),filename=row[0],content_type="application/pdf")]
    info["token"]=hashlib.sha256(json.dumps([key,inv,info,marker],sort_keys=True).encode()).hexdigest()
    return info,extra


@bp.get("/<key>/<inv>/packet")
def prepare(key,inv):
    info,_=packet(key,inv)
    return jsonify(ok=True,packet=info)


def deliver_ready():
    """Una consulta indexada en reposo; como máximo una preparación por ciclo."""
    import facturacion_bp as billing
    from email_tracking import delivery_status,record_email_delivery,email_thread_headers
    now=int(time.time())
    candidates=sql("SELECT invoice_id,client_id,policy,due FROM billing_policy_mail WHERE state='waiting' AND due<=? ORDER BY due,invoice_id LIMIT 1",(now,))
    if not candidates:return
    inv,key,name,old=candidates[0]
    lease=now+900
    def update(stmt,params):
        store=current_app.extensions["operations_store"]
        with store.connection() as conn:
            return conn.execute(stmt.replace("?",store.placeholder),params).rowcount==1
    if not update("UPDATE billing_policy_mail SET due=? WHERE invoice_id=? AND due=? AND state='waiting'",(lease,inv,old)):return
    def end(state,note,delay=0):
        return update("UPDATE billing_policy_mail SET state=?,note=?,due=? WHERE invoice_id=? AND due=? AND state='waiting'",
                      (state,note,now+delay,inv,lease))
    try:
        c,policy,period=context(key,inv)
        cfg=rules(key)
        if policy!=name or name not in cfg.get("automatic",[]):return end("manual","Envío manual")
        if delivery_status("factura",inv)["sent"]:return end("sent","Ya enviada")
        scheduled=current_app.extensions.get("scheduled_email_queue")
        if scheduled and any(j.get("state") in ("pending","sending","sent","uncertain") for j in scheduled.list("factura",inv)):
            return end("manual","Hay otro envío programado o previo. Revisa su estado.")
        if cfg.get("signed") and not sql("SELECT 1 FROM billing_policy_signed WHERE invoice_id=? AND client_id=?",(inv,key)):
            return end("waiting","Falta la factura firmada",86400)
        info,extras=packet(key,inv,download=True)
        if info["required"] and not info["oc"]:return end("waiting","Falta vincular la OC correcta",3600)
        if info["signed"]:
            files=sql("SELECT filename,content FROM billing_policy_signed WHERE invoice_id=? AND client_id=?",(inv,key))
            if not files:return end("waiting","Falta la factura firmada",86400)
            extras.append(dict(filename=files[0][0],data=bytes(files[0][1]),content_type="application/pdf"))
        if sum(len(a["data"]) for a in extras)>15*1024*1024:return end("manual","Los adjuntos superan 15 MB. Revisa el envío.")
        doc=billing._facturama_invoice_row(billing._fm_request("GET",f"/api/cfdi/{inv}",params={"type":"issued"}))
        if doc["id"]!=inv or not c["rfc"] or doc["customer_tax_id"]!=c["rfc"] or doc["type"]!="I" or doc["cancellation_status"]!="active":
            return end("manual","Verifica el RFC y la vigencia de la factura.")
        xml=billing._decode_facturama_file(billing._fm_request("GET",f"/Cfdi/xml/issued/{inv}"))
        pdf=billing._facturama_printable_pdf(xml,inv)
        if not xml or not pdf:raise PolicyError("Faltan PDF o XML")
        folio=doc["folio"] or doc["uuid"]
        def fill(s):return s.replace("{folio}",folio).replace("{poliza}",name).replace("{periodo}",period)
        fields=dict(recipient=info["email"],cc=info["cc"],subject=fill(info["subject"] or "Factura {folio} · {poliza} · {periodo}"),
                    body=fill(info["message"] or "Buen día, adjunto la factura de {poliza} correspondiente a {periodo}."),
                    folio=folio,pdf_bytes=pdf,xml_bytes=xml,extra_attachments=extras,**email_thread_headers("factura",inv))
        if delivery_status("factura",inv)["sent"]:return end("sent","Ya enviada")
        if not end("sending","Procesando envío; verificar antes de reenviar."):return
    except Exception:
        current_app.logger.exception("No se pudo preparar el correo de póliza %s",inv)
        end("waiting","No se pudo preparar el expediente. Se reintentará.",1800)
        return
    try:
        result=billing.send_cfdi_email(**fields)
        record_email_delivery("factura",inv,recipients=result.get("recipients"),cc=result.get("cc"),folio=folio,message_id=result.get("message_id"))
        state,note="sent","Enviada"
    except Exception:
        current_app.logger.exception("Envío de póliza sin confirmación: %s",inv)
        state,note="uncertain","No se confirmó el envío. Revisa Enviados antes de reenviar."
    sql("UPDATE billing_policy_mail SET state=?,note=? WHERE invoice_id=? AND state='sending'",(state,note,inv),write=True)
