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
        self.assertTrue(policies.tag_template("inv","t","A","2026-01"))

    def mock(self,target,**kwargs):
        p=patch(target,**kwargs)
        self.addCleanup(p.stop)
        return p.start()

    def test_directory_history_and_identity(self):
        self.assertEqual([c["id"] for c in self.http.get(self.base).json["clients"]],["a","b"])
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
        policies.tag_template("feb","t","A","2026-03")
        self.assertEqual(policies.packet("a","feb")[0]["oc"],"")
        self.assertEqual(policies.packet("a","inv")[0]["oc"],"December.pdf")
