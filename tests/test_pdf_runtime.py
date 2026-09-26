"""Pruebas locales del aislamiento de los tres flujos de impresion."""

from pathlib import Path
import tempfile
import unittest

from pdf_runtime import render_invoice_pdf_bytes, render_pdf_bytes, render_pdf_file


CFDI_FIXTURE = b'''<?xml version="1.0" encoding="UTF-8"?>
<cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/4"
 Version="4.0" Serie="HSC" Folio="TEST" Fecha="2026-09-25T12:00:00"
 Moneda="MXN" SubTotal="100.00" Total="116.00" TipoDeComprobante="I"
 FormaPago="03" MetodoPago="PUE" LugarExpedicion="76116" NoCertificado="TEST" Sello="TESTSEAL">
 <cfdi:Emisor Rfc="SICH930611DV7" Nombre="HECTOR SILVA CID" RegimenFiscal="612"/>
 <cfdi:Receptor Rfc="XAXX010101000" Nombre="CLIENTE DE PRUEBA"
  DomicilioFiscalReceptor="76116" RegimenFiscalReceptor="616" UsoCFDI="G03"/>
 <cfdi:Conceptos>
  <cfdi:Concepto ClaveProdServ="72151200" Cantidad="1" ClaveUnidad="E48"
   Unidad="Servicio" Descripcion="Mantenimiento de prueba" ValorUnitario="100.00" Importe="100.00">
   <cfdi:Impuestos><cfdi:Traslados>
    <cfdi:Traslado Base="100.00" Impuesto="002" TipoFactor="Tasa" TasaOCuota="0.160000" Importe="16.00"/>
   </cfdi:Traslados></cfdi:Impuestos>
  </cfdi:Concepto>
 </cfdi:Conceptos>
 <cfdi:Impuestos TotalImpuestosTrasladados="16.00"/>
</cfdi:Comprobante>'''


class PdfRuntimeTests(unittest.TestCase):
    def assert_pdf(self, data):
        self.assertTrue(data.startswith(b"%PDF-"))
        self.assertGreater(len(data), 500)

    def test_quotation_or_report_html_to_bytes_with_local_static_asset(self):
        html = '''<html><body>
        <img src="https://hsc.invalid/static/img/logo2.png" style="width:120px">
        <h1>Documento HSC</h1><p>Cotizacion o reporte de prueba.</p>
        </body></html>'''
        self.assert_pdf(render_pdf_bytes(html, base_url=Path.cwd()))

    def test_quotation_html_to_atomic_file(self):
        with tempfile.TemporaryDirectory(prefix="hsc-pdf-test-") as folder:
            target = Path(folder) / "cotizacion.pdf"
            render_pdf_file("<h1>Cotizacion HSC</h1>", target, base_url=Path.cwd())
            self.assert_pdf(target.read_bytes())

    def test_invoice_pdf_is_generated_in_isolated_process(self):
        pdf = render_invoice_pdf_bytes(
            CFDI_FIXTURE,
            internal_folio="1375",
            order_number="OC-PRUEBA",
            quote_folio="1616",
        )
        self.assert_pdf(pdf)


if __name__ == "__main__":
    unittest.main()
