import unittest
import xml.etree.ElementTree as ET
from io import BytesIO

from factura_pdf_hsc import build_invoice_pdf, parse_cfdi, PAGOS_NS
from test_facturama import FacturamaIntegrationTests


def sample_xml():
    root = ET.fromstring(FacturamaIntegrationTests.SAMPLE_PAYMENT_XML)
    root.set('Folio', 'P1')
    ET.SubElement(root, '{http://www.sat.gob.mx/cfd/4}Emisor', Rfc='AAA010101AAA', Nombre='HSC DEMOSTRACION', RegimenFiscal='601')
    ET.SubElement(root, '{http://www.sat.gob.mx/cfd/4}Receptor', Rfc='URE180429TM6', Nombre='CLIENTE DE PRUEBA', DomicilioFiscalReceptor='86991', RegimenFiscalReceptor='601', UsoCFDI='CP01')
    root.find(f'.//{{{PAGOS_NS}}}Pago').set('RfcEmisorCtaOrd', 'BBA830831LJ2')
    return ET.tostring(root)


class PaymentPdfTests(unittest.TestCase):
    def test_no_bank_rows_when_bank_not_selected(self):
        from pypdf import PdfReader
        root = ET.fromstring(sample_xml())
        del root.find(f'.//{{{PAGOS_NS}}}Pago').attrib['RfcEmisorCtaOrd']
        output = BytesIO()
        build_invoice_pdf(ET.tostring(root), output)
        text = '\n'.join(page.extract_text() for page in PdfReader(BytesIO(output.getvalue())).pages)
        self.assertNotIn('Institución bancaria de origen', text)
        self.assertNotIn('RFC del banco de origen', text)
        self.assertIn('Saldo pendiente', text)

    def test_bank_in_parsed_payment_and_pdf(self):
        from pypdf import PdfReader
        xml = sample_xml()
        self.assertEqual(parse_cfdi(xml)['payments'][0]['attributes']['RfcEmisorCtaOrd'], 'BBA830831LJ2')
        output = BytesIO()
        build_invoice_pdf(xml, output)
        text = '\n'.join(page.extract_text() for page in PdfReader(BytesIO(output.getvalue())).pages)
        self.assertIn('COMPLEMENTO DE PAGO', text)
        self.assertIn('BBVA', text)
        self.assertIn('Saldo pendiente', text)


if __name__ == '__main__':
    build_invoice_pdf(sample_xml(), 'output/pdf/complemento-hsc-prueba.pdf')
