"""Expediente comercial de pólizas, separado del CFDI y del registro de timbrado."""
import hashlib
import json
import re
from datetime import date, timedelta
from flask import Blueprint, current_app, jsonify, request
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
    for c in store.list_clients(client_id=key):
        if c["policy_active"]:
            ident = current_app.extensions["policy_identity"](c)
            rows.append(dict(id=c["id"], name=c["name"], rfc=ident["rfc"], photo=c["has_photo"], email=ident.get("email","")))
    return rows


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


def link(inv, c, name, period):
    if not name or len(name)>100 or not MONTH.fullmatch(period):
        raise PolicyError("Indica la póliza y el mes del servicio.")
    sql("""INSERT INTO billing_policy_invoices(invoice_id,client_id,rfc,policy,period) VALUES (?,?,?,?,?)
        ON CONFLICT(invoice_id) DO UPDATE SET policy=excluded.policy,period=excluded.period
        WHERE billing_policy_invoices.client_id=excluded.client_id AND billing_policy_invoices.rfc=excluded.rfc""",
        (inv,c["id"],c["rfc"],name,period),write=True)
    if not sql("SELECT 1 FROM billing_policy_invoices WHERE invoice_id=? AND client_id=? AND rfc=?", (inv,c["id"],c["rfc"])):
        raise PolicyError("Esta factura ya está vinculada a otro cliente.")


def tag_template(inv, template, rfc, period):
    if not template:
        return False
    for key, raw in sql("SELECT client_id,rules FROM billing_policy_rules"):
        cfg = json.loads(raw)
        name = cfg.get("templates",{}).get(template)
        if not name:
            continue
        candidates=clients(key)
        if not candidates or not rfc or candidates[0]["rfc"]!=rfc:
            continue
        start=date.fromisoformat(period+"-01")
        if cfg.get("previous"): period=(start-timedelta(days=1)).isoformat()[:7]
        link(inv,candidates[0],name,period)
        return True
    return False


@bp.get("")
def index():
    from email_tracking import delivery_status, read_email_deliveries
    rows=clients()
    tracked={(c["id"],c["rfc"]):c for c in rows}
    deliveries=read_email_deliveries()
    for key,rfc,inv in sql("SELECT client_id,rfc,invoice_id FROM billing_policy_invoices"):
        c=tracked.get((key,rfc))
        if c is not None and not delivery_status("factura",inv,deliveries).get("sent"):
            c["unconfirmed"]=c.get("unconfirmed",0)+1
    return jsonify(ok=True, clients=rows)


@bp.get("/<key>")
def detail(key):
    from facturacion_bp import _read_invoice_templates
    from email_tracking import delivery_status, read_email_deliveries
    c=client(key)
    links={i:(n,m) for i,n,m in sql("SELECT invoice_id,policy,period FROM billing_policy_invoices WHERE client_id=? AND rfc=?", (key,c["rfc"]))}
    deliveries=read_email_deliveries()
    try:
        rows=invoices(c)
    except RuntimeError as exc:
        return jsonify(ok=False,error=str(exc)),503
    for row in rows:
        row["policy"],row["period"]=links.get(row["id"],("",""))
        row["email_delivery"]=delivery_status("factura", row["id"], deliveries)
    templates=[dict(id=t["id"],name=t["name"]) for t in _read_invoice_templates()
               if c["rfc"] and str((t.get("receiver") or {}).get("tax_id") or "").upper()==c["rfc"]]
    return jsonify(ok=True,client=c,rules=rules(key),templates=templates,invoices=rows)


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
    sql("INSERT INTO billing_policy_rules VALUES (?,?) ON CONFLICT(client_id) DO UPDATE SET rules=excluded.rules",
        (key,json.dumps(cfg)),write=True)
    return jsonify(ok=True)


@bp.post("/<key>/link")
def manual_link(key):
    c=client(key)
    b=request.get_json() or {}
    inv=b.get("id")
    if not any(i["id"]==inv for i in invoices(c)):
        raise PolicyError("La factura no corresponde al RFC de este cliente.")
    if b.get("unlink") is True:
        sql("DELETE FROM billing_policy_invoices WHERE client_id=? AND invoice_id=?", (key,inv),write=True)
    else:
        link(inv,c,str(b.get("policy") or "").strip(),str(b.get("period") or ""))
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
    return jsonify(ok=True)


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
