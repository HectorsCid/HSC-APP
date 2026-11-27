import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask
import billing_policies as policies
import facturacion_bp as billing
from operaciones_store import OperationsStore


class PolicyFlow(unittest.TestCase):
    def test_billing_home_template_compiles(self):
        from jinja2 import Environment,FileSystemLoader
        env=Environment(loader=FileSystemLoader(Path(__file__).parent/"templates"))
        env.get_template("facturas_inicio.html")

    def setUp(self):
        folder=tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        app=Flask(__name__)
        app.testing=True
        store=OperationsStore(local_path=Path(folder.name)/"test.db")
        app.extensions.update(operations_store=store,policy_identity=lambda c:dict(rfc=c["billing_rfc"]))
        app.register_blueprint(policies.bp)
        app.register_blueprint(billing.facturacion_bp)
        ctx=app.app_context()
        ctx.push()
        self.addCleanup(ctx.pop)
        self.http=app.test_client()
        for key,active in [("a",1),("b",1),("z",0)]:
            policies.sql("INSERT INTO operations_clients(id,name,billing_rfc,policy_active,updated_at) VALUES (?,?,?,?,?)",
                         (key,key,key.upper(),active,""),write=True)
        self.mock("facturacion_bp._invoice_pdf_metadata",return_value={})
        self.mock("email_tracking.read_email_deliveries",return_value={})
        self.mock("facturacion_bp._read_invoice_templates",return_value=[dict(id="t",name="Chiller",receiver={"tax_id":"A"})])
        self.mock("facturacion_bp.billing_client_groups",return_value=([dict(rfc="A",invoices=[dict(id="inv"),dict(id="other")])],{}))
        self.base="/api/facturacion/polizas"
        self.http.post(self.base+"/a/rules",json=dict(templates=["t"],signed=True,oc=True,previous=True))
        self.assertFalse(policies.tag_template("inv","t","A","2026-01"))
        policies.link("inv",policies.client("a"),"Chiller","2025-12",queue=False)

    def mock(self,target,**kwargs):
        p=patch(target,**kwargs)
        self.addCleanup(p.stop)
        return p.start()

    def test_directory_history_and_identity(self):
        self.assertEqual([c["id"] for c in self.http.get(self.base).json["clients"]],["a"])
        rows=self.http.get(self.base+"/a").json["invoices"]
        self.assertEqual([(r["policy"],r["period"]) for r in rows],[("Chiller","2025-12"),("","")])
        self.assertFalse(policies.tag_template("other","t","B","2026-01"))
        self.assertEqual(self.http.post(self.base+"/b/link",json=dict(id="inv",policy="Otra",period="2026-01")).status_code,400)
        self.assertEqual(self.http.get(self.base+"/b/inv/packet").status_code,400)

    def test_send_requires_current_order_and_signed_pdf(self):
        self.mock("facturacion_bp.smtp_config",return_value={"configured":True})
        self.mock("facturacion_bp.authorized_to_send",return_value=True)
        self.mock("facturacion_bp._provider",return_value="facturapi")
        self.mock("facturacion_bp._fa_get_binary",side_effect=lambda path,mime:mime.encode())
        self.mock("facturacion_bp.email_thread_headers",return_value={})
        self.mock("facturacion_bp.record_email_delivery",return_value={})
        self.mock("facturacion_bp.trusted_device_token",return_value="test")
        send=self.mock("facturacion_bp.send_cfdi_email",return_value={"recipient":"test@example.org"})
        url=self.base+"/a/inv"
        def token(): return self.http.get(url+"/packet").json["packet"]["token"]
        def submit(current,signed=False):
            data=dict(policy_client="a",policy_token=current,email="test@example.org")
            if signed:data["signed_pdf"]=(io.BytesIO(b"%PDF-signed"),"Firmada.pdf")
            return self.http.post("/api/invoices/inv/email",data=data)
        initial=token()
        self.assertEqual(submit(initial).status_code,400)
        self.assertEqual(self.http.post(url+"/oc",data={"file":(io.BytesIO(b"%PDF-oc"),"OC.pdf")}).status_code,200)
        self.assertEqual(submit(initial,True).status_code,400)
        self.assertEqual(submit(token()).status_code,400)
        send.assert_not_called()
        self.assertEqual(submit(token(),True).status_code,200)
        args=send.call_args.kwargs
        self.assertEqual([a["filename"] for a in args["extra_attachments"]],["OC.pdf","Firmada.pdf"])
        self.assertTrue(args["pdf_bytes"] and args["xml_bytes"])

    def test_invoice_order_does_not_leak_to_other_month_or_invoice(self):
        self.http.post(self.base+"/a/inv/oc",data={"file":(io.BytesIO(b"%PDF-order"),"December.pdf"),"scope":"month"})
        policies.link("feb",policies.client("a"),"Chiller","2026-02",queue=False)
        self.assertEqual(policies.packet("a","feb")[0]["oc"],"")
        self.assertEqual(policies.packet("a","inv")[0]["oc"],"December.pdf")

    def test_relation_in_service_order_including_missing_months(self):
        import csv
        self.mock("facturacion_bp.billing_client_groups",return_value=([dict(rfc="A",invoices=[
            dict(id="inv",uuid="AAA",folio="1387",date="2026-03-18")])],{}))
        res=self.http.get(self.base+"/a/relacion.csv?desde=2025-11&hasta=2026-01")
        rows=list(csv.DictReader(io.StringIO(res.data.decode("utf-8-sig"))))
        self.assertEqual([r["Mes del servicio"] for r in rows],["2025-11","2025-12","2026-01"])
        self.assertEqual(rows[1]["Folio interno"],"1387")
        self.assertEqual(rows[1]["Folio fiscal"],"AAA")
        self.assertIn("Pendiente",rows[2]["Estado"])
        self.assertEqual(self.http.get(self.base+"/a/relacion.csv?desde=2026-02&hasta=2026-01").status_code,400)

    def test_annual_order_previous_service_year_and_two_separate_deliveries(self):
        from flask import current_app
        p=dict(key="v",client="Visscher",branch="Lote 86",name="Contrato 86",rfc="VCM1401063I9")
        current_app.extensions["commercial_policies"]=lambda:[p]
        self.mock("facturacion_bp._read_invoice_schedules",return_value=[dict(id="sch",template_id="t",policy_name=p["name"],policy_client="v")])
        self.mock("smtp_mailer.authorized_to_send",return_value=True)
        self.mock("facturacion_bp.authorized_to_send",return_value=True)
        self.mock("cfdi_drive.backup_policy_file")
        self.mock("billing_policies.current_invoice",return_value=("100",b"%PDF-orig",b"<xml/>"))
        sign=self.mock("smtp_mailer.send_email_with_attachments")
        final=self.mock("facturacion_bp.send_cfdi_email",return_value={"recipients":["final@example.org"]})
        self.mock("email_tracking.record_email_delivery")
        root=self.base+"/v"
        self.assertEqual(self.http.post(root+"/firmante",json=dict(policy=p["name"],signer="firma@example.org",recipient="final@example.org")).status_code,200)
        policies.sql("INSERT INTO billing_policy_rules VALUES (?,?)",("v",'{"previous":true}'),write=True)
        self.assertTrue(policies.tag_template("doc","t",p["rfc"],"2027-01","sch"))
        policies.deliver_ready();policies.deliver_ready()
        sign.assert_called_once()
        self.assertEqual(sign.call_args.kwargs["recipient"],"firma@example.org")
        self.assertEqual([x["data"] for x in sign.call_args.kwargs["attachments"]],[b"%PDF-orig"])
        def order(year):
            return self.http.post(root+"/oc-anual",data=dict(policy=p["name"],year=year,file=(io.BytesIO(b"%PDF-"+year.encode()),year+".pdf")))
        self.assertEqual(order("2027").status_code,200)
        self.assertFalse(policies.packet("v","doc")[0]["oc"])
        self.assertEqual(order("2026").status_code,200)
        def send():
            return self.http.post(root+"/doc/expediente",json={"token":policies.packet("v","doc")[0]["token"]})
        self.assertEqual(send().status_code,400)
        self.assertEqual(self.http.post(root+"/doc/signed",data={"file":(io.BytesIO(b"%PDF-signed"),"Firmada.pdf")}).status_code,200)
        policies.deliver_ready();final.assert_not_called()
        self.assertEqual(send().status_code,200)
        self.assertEqual(send().status_code,400)
        final.assert_called_once()
        args=final.call_args.kwargs
        self.assertEqual(args["recipient"],"final@example.org")
        self.assertEqual([args["pdf_bytes"],args["xml_bytes"]]+[a["data"] for a in args["extra_attachments"]],[b"%PDF-orig",b"<xml/>",b"%PDF-2026",b"%PDF-signed"])

    def test_automatic_waits_for_signature_and_order_and_only_sends_once(self):
        cfg=dict(templates=["t"],automatic=["Chiller"],signed=True,oc=True,email="admin@example.org")
        self.mock("smtp_mailer.authorized_to_send",return_value=True)
        self.assertEqual(self.http.post(self.base+"/a/rules",json=cfg).status_code,200)
        self.assertEqual(policies.sql("SELECT invoice_id FROM billing_policy_mail"),[])  # no retroactivos
        policies.link("new",policies.client("a"),"Chiller","2026-11",queue=True)
        send=self.mock("facturacion_bp.send_cfdi_email",return_value={"recipients":[cfg["email"]]})
        self.mock("facturacion_bp._fm_request",return_value={})
        self.mock("facturacion_bp._facturama_invoice_row",return_value=dict(id="new",customer_tax_id="A",type="I",cancellation_status="active",folio="123"))
        self.mock("facturacion_bp._decode_facturama_file",return_value=b"<xml/>")
        self.mock("facturacion_bp._facturama_printable_pdf",return_value=b"%PDF-")
        self.mock("email_tracking.record_email_delivery")
        policies.deliver_ready()
        self.http.post(self.base+"/a/new/signed",data={"file":(io.BytesIO(b"%PDF-signed"),"firmada.pdf")})
        policies.deliver_ready()
        send.assert_not_called()
        self.http.post(self.base+"/a/new/oc",data={"file":(io.BytesIO(b"%PDF-order"),"OC.pdf")})
        policies.deliver_ready()
        policies.deliver_ready()
        send.assert_called_once()
        self.assertEqual([x["filename"] for x in send.call_args.kwargs["extra_attachments"]],["OC.pdf","firmada.pdf"])
