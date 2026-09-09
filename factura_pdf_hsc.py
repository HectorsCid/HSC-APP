"""Representación impresa HSC de un CFDI 4.0 a partir de su XML timbrado."""

from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree as ET

from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    BaseDocTemplate,
    CondPageBreak,
    Frame,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)


NAVY = colors.HexColor("#10233F")
BLUE = colors.HexColor("#075A9C")
BLUE_DARK = colors.HexColor("#06436F")
INK = colors.HexColor("#111827")
MUTED = colors.HexColor("#6B7280")
LINE = colors.HexColor("#D8E0E8")
SOFT = colors.HexColor("#F4F6F8")
PALE_BLUE = colors.HexColor("#EAF4FB")
WHITE = colors.white

CFDI_NS = "http://www.sat.gob.mx/cfd/4"
TFD_NS = "http://www.sat.gob.mx/TimbreFiscalDigital"

PAYMENT_FORMS = {
    "01": "Efectivo", "02": "Cheque nominativo", "03": "Transferencia electrónica",
    "04": "Tarjeta de crédito", "28": "Tarjeta de débito", "99": "Por definir",
}
PAYMENT_METHODS = {"PUE": "Pago en una sola exhibición", "PPD": "Pago en parcialidades o diferido"}
CFDI_USES = {"G01": "Adquisición de mercancías", "G03": "Gastos en general", "S01": "Sin efectos fiscales"}


def _money(value) -> str:
    try:
        number = Decimal(str(value or "0"))
    except InvalidOperation:
        number = Decimal("0")
    return f"${number:,.2f}"


def _text(value, fallback="-") -> str:
    value = str(value or "").strip()
    return value or fallback


def parse_cfdi(xml_path: str | Path) -> dict:
    if isinstance(xml_path, (bytes, bytearray)):
        root = ET.fromstring(xml_path)
    else:
        root = ET.parse(xml_path).getroot()
    issuer = root.find(f"{{{CFDI_NS}}}Emisor")
    receiver = root.find(f"{{{CFDI_NS}}}Receptor")
    stamp = root.find(f".//{{{TFD_NS}}}TimbreFiscalDigital")
    concepts = []
    for node in root.findall(f".//{{{CFDI_NS}}}Concepto"):
        iva = Decimal("0")
        retained = Decimal("0")
        for tax in node.findall(f".//{{{CFDI_NS}}}Traslado"):
            try:
                iva += Decimal(tax.attrib.get("Importe", "0"))
            except InvalidOperation:
                pass
        for tax in node.findall(f".//{{{CFDI_NS}}}Retencion"):
            try:
                retained += Decimal(tax.attrib.get("Importe", "0"))
            except InvalidOperation:
                pass
        concepts.append({
            "code": node.attrib.get("ClaveProdServ", ""),
            "description": node.attrib.get("Descripcion", ""),
            "unit": node.attrib.get("Unidad") or node.attrib.get("ClaveUnidad", ""),
            "quantity": node.attrib.get("Cantidad", "0"),
            "unit_value": node.attrib.get("ValorUnitario", "0"),
            "amount": node.attrib.get("Importe", "0"),
            "discount": node.attrib.get("Descuento", "0"),
            "iva": str(iva),
            "retained": str(retained),
        })
    taxes = root.find(f"{{{CFDI_NS}}}Impuestos")
    return {
        "version": root.attrib.get("Version", "4.0"),
        "cfdi_type": root.attrib.get("TipoDeComprobante", "I"),
        "series": root.attrib.get("Serie", ""),
        "folio": root.attrib.get("Folio", ""),
        "date": root.attrib.get("Fecha", ""),
        "currency": root.attrib.get("Moneda", "MXN"),
        "subtotal": root.attrib.get("SubTotal", "0"),
        "discount": root.attrib.get("Descuento", "0"),
        "total": root.attrib.get("Total", "0"),
        "payment_form": root.attrib.get("FormaPago", ""),
        "payment_method": root.attrib.get("MetodoPago", ""),
        "expedition_zip": root.attrib.get("LugarExpedicion", ""),
        "certificate": root.attrib.get("NoCertificado", ""),
        "seal": root.attrib.get("Sello", ""),
        "issuer": dict(issuer.attrib) if issuer is not None else {},
        "receiver": dict(receiver.attrib) if receiver is not None else {},
        "stamp": dict(stamp.attrib) if stamp is not None else {},
        "tax_total": taxes.attrib.get("TotalImpuestosTrasladados", "0") if taxes is not None else "0",
        "retained_total": taxes.attrib.get("TotalImpuestosRetenidos", "0") if taxes is not None else "0",
        "concepts": concepts,
    }


def _styles():
    base = getSampleStyleSheet()
    return {
        "body": ParagraphStyle("Body", parent=base["BodyText"], fontName="Helvetica", fontSize=8.1, leading=10.2, textColor=INK),
        "small": ParagraphStyle("Small", parent=base["BodyText"], fontName="Helvetica", fontSize=6.7, leading=8.2, textColor=MUTED),
        "tiny": ParagraphStyle("Tiny", parent=base["BodyText"], fontName="Courier", fontSize=4.8, leading=5.8, textColor=MUTED, splitLongWords=True),
        "label": ParagraphStyle("Label", parent=base["BodyText"], fontName="Helvetica-Bold", fontSize=6.5, leading=8, textColor=BLUE, spaceAfter=2),
        "table_header": ParagraphStyle("TableHeader", parent=base["BodyText"], fontName="Helvetica-Bold", fontSize=6.5, leading=8, textColor=WHITE),
        "value": ParagraphStyle("Value", parent=base["BodyText"], fontName="Helvetica-Bold", fontSize=8.4, leading=10.3, textColor=INK),
        "client": ParagraphStyle("Client", parent=base["BodyText"], fontName="Helvetica-Bold", fontSize=11, leading=13, textColor=NAVY),
        "chip_label": ParagraphStyle("ChipLabel", parent=base["BodyText"], fontName="Helvetica-Bold", fontSize=6.1, leading=7, textColor=BLUE),
        "chip_value": ParagraphStyle("ChipValue", parent=base["BodyText"], fontName="Helvetica-Bold", fontSize=7.6, leading=9, textColor=NAVY),
        "right": ParagraphStyle("Right", parent=base["BodyText"], fontName="Helvetica", fontSize=8, leading=10, textColor=INK, alignment=TA_RIGHT),
        "right_bold": ParagraphStyle("RightBold", parent=base["BodyText"], fontName="Helvetica-Bold", fontSize=9, leading=11, textColor=NAVY, alignment=TA_RIGHT),
        "white_small": ParagraphStyle("WhiteSmall", parent=base["BodyText"], fontName="Helvetica", fontSize=7.5, leading=9.5, textColor=WHITE),
        "white_big": ParagraphStyle("WhiteBig", parent=base["BodyText"], fontName="Helvetica-Bold", fontSize=17, leading=19, textColor=WHITE, alignment=TA_RIGHT),
        "section": ParagraphStyle("Section", parent=base["BodyText"], fontName="Helvetica-Bold", fontSize=8, leading=10, textColor=NAVY, spaceAfter=5),
    }


def _p(value, style, markup=False):
    safe = _text(value)
    if not markup:
        safe = safe.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return Paragraph(safe, style)


def _qr(data: dict, size=30 * mm):
    uuid = data["stamp"].get("UUID", "")
    issuer = data["issuer"].get("Rfc", "")
    receiver = data["receiver"].get("Rfc", "")
    seal_tail = data.get("seal", "")[-8:]
    verification = (
        "https://verificacfdi.facturaelectronica.sat.gob.mx/default.aspx"
        f"?id={uuid}&re={issuer}&rr={receiver}&tt={data['total']}&fe={seal_tail}"
    )
    widget = QrCodeWidget(verification)
    bounds = widget.getBounds()
    scale = min(size / (bounds[2] - bounds[0]), size / (bounds[3] - bounds[1]))
    drawing = Drawing(size, size, transform=[scale, 0, 0, scale, 0, 0])
    drawing.add(widget)
    return drawing


class HscInvoiceDoc(BaseDocTemplate):
    def __init__(self, filename, data, **kwargs):
        super().__init__(filename, pagesize=A4, leftMargin=16 * mm, rightMargin=14 * mm,
                         topMargin=48 * mm, bottomMargin=19 * mm, **kwargs)
        self.data = data
        frame = Frame(self.leftMargin, self.bottomMargin, self.width, self.height, id="invoice")
        self.addPageTemplates(PageTemplate(id="hsc", frames=[frame], onPage=self._decorate))

    def _decorate(self, canvas, doc):
        width, height = A4
        canvas.saveState()
        logo_path = Path(__file__).resolve().parent / "static" / "img" / "logo2.png"
        if logo_path.exists():
            canvas.drawImage(ImageReader(str(logo_path)), 16 * mm, height - 39 * mm, 37 * mm, 31 * mm,
                             preserveAspectRatio=True, anchor="c", mask="auto")
        canvas.setFillColor(BLUE)
        canvas.rect(59 * mm, height - 31 * mm, 1.4 * mm, 19 * mm, stroke=0, fill=1)
        canvas.setFillColor(NAVY)
        canvas.setFont("Helvetica-Bold", 15)
        canvas.drawString(64 * mm, height - 22.5 * mm, "COMPROBANTE FISCAL")
        canvas.setFillColor(BLUE)
        canvas.setFont("Helvetica-Bold", 7)
        canvas.drawString(64 * mm, height - 28 * mm, "CFDI 4.0")
        folio_label = f"FOLIO {self.data['internal_folio']}"
        canvas.setFillColor(BLUE)
        canvas.roundRect(width - 58 * mm, height - 23 * mm, 44 * mm, 12 * mm, 3 * mm, stroke=0, fill=1)
        canvas.setFillColor(WHITE)
        canvas.setFont("Helvetica-Bold", 11)
        canvas.drawCentredString(width - 36 * mm, height - 18.7 * mm, folio_label)
        if doc.page == 1:
            order = self.data.get("order_number")
            if order:
                canvas.setFillColor(NAVY)
                canvas.setFont("Helvetica-Bold", 7.2)
                canvas.drawRightString(width - 14 * mm, height - 29.5 * mm, f"ORDEN DE COMPRA  {order}")
            canvas.setFillColor(MUTED)
            canvas.setFont("Helvetica", 7)
            canvas.drawRightString(width - 14 * mm, height - 34 * mm, f"Emisión  {_text(self.data['date']).replace('T', ' ')}")
        canvas.setStrokeColor(LINE)
        canvas.setLineWidth(1.2)
        canvas.line(16 * mm, height - 43 * mm, width - 14 * mm, height - 43 * mm)
        canvas.setLineWidth(.5)
        canvas.line(16 * mm, 13 * mm, width - 14 * mm, 13 * mm)
        canvas.setFillColor(MUTED)
        canvas.setFont("Helvetica", 6.2)
        canvas.drawString(16 * mm, 8 * mm, "Tel. 5527605496  -  hectorsc@hscrefrigeracion.com  -  www.hscrefrigeracion.com")
        canvas.drawRightString(width - 14 * mm, 8 * mm, f"Página {doc.page}")
        canvas.restoreState()


def build_invoice_pdf(xml_path: str | Path | bytes, output_path, internal_folio=None, order_number=None):
    data = parse_cfdi(xml_path)
    data["internal_folio"] = _text(internal_folio or data["folio"], "S/F")
    data["order_number"] = _text(order_number, "") if order_number else ""
    styles = _styles()
    if hasattr(output_path, "write"):
        output = output_path
    else:
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
    destination = output if hasattr(output, "write") else str(output)
    doc = HscInvoiceDoc(destination, data, title=f"Factura {data['series']} {data['folio']}", author="HSC")
    issuer = data["issuer"]
    receiver = data["receiver"]
    story = []

    issuer_table = Table([
        [_p("EMISOR", styles["label"]), _p(issuer.get("Nombre"), styles["value"]),
         _p(f"RFC {issuer.get('Rfc', '-')}  |  Régimen {issuer.get('RegimenFiscal', '-')}  |  Expedición {data['expedition_zip']}", styles["body"])],
    ], colWidths=[20 * mm, 65 * mm, 95 * mm])
    issuer_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2), ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 7),
    ]))
    story += [issuer_table, Spacer(1, 3.5 * mm)]

    recipient = Table([
        [_p("RECEPTOR", styles["label"]), "", ""],
        [_p(receiver.get("Nombre"), styles["client"]),
         _p(f"<b>RFC</b><br/>{receiver.get('Rfc', '-')}", styles["body"], markup=True),
         _p(f"<b>DOMICILIO FISCAL</b><br/>{receiver.get('DomicilioFiscalReceptor', '-')}", styles["body"], markup=True)],
        [_p(f"Uso CFDI: <b>{receiver.get('UsoCFDI', '-')} - {CFDI_USES.get(receiver.get('UsoCFDI', ''), 'Uso fiscal')}</b>", styles["small"], markup=True),
         _p(f"Régimen: <b>{receiver.get('RegimenFiscalReceptor', '-')}</b>", styles["small"], markup=True), ""],
    ], colWidths=[90 * mm, 48 * mm, 42 * mm])
    recipient.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), .7, LINE), ("ROUNDEDCORNERS", [7]),
        ("SPAN", (0, 0), (-1, 0)), ("SPAN", (0, 2), (0, 2)), ("SPAN", (2, 2), (2, 2)),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 2.8), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.8),
        ("BACKGROUND", (0, 0), (-1, 0), SOFT),
    ]))

    payment = Table([[
        [_p("MÉTODO DE PAGO", styles["chip_label"]), _p(f"{data['payment_method']}  {PAYMENT_METHODS.get(data['payment_method'], '')}", styles["chip_value"])],
        [_p("FORMA DE PAGO", styles["chip_label"]), _p(f"{data['payment_form']}  {PAYMENT_FORMS.get(data['payment_form'], '')}", styles["chip_value"])],
        [_p("MONEDA", styles["chip_label"]), _p(data["currency"], styles["chip_value"])],
    ]], colWidths=[55 * mm, 91 * mm, 34 * mm])
    payment.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), .5, LINE), ("ROUNDEDCORNERS", [8]),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#E7EAEE")),
        ("LINEBEFORE", (1, 0), (-1, -1), .5, LINE), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story += [recipient, Spacer(1, 3.2 * mm), payment, Spacer(1, 5 * mm), _p("CONCEPTOS FACTURADOS", styles["section"])]

    rows = [[_p("#", styles["table_header"]), _p("DESCRIPCIÓN", styles["table_header"]),
             _p("CANT.", styles["table_header"]), _p("PRECIO", styles["table_header"]), _p("IMPORTE", styles["table_header"])]]
    for index, item in enumerate(data["concepts"], 1):
        rows.append([
            _p(index, styles["small"]),
            _p(f"{item['description']}<br/><font color='#6B7280' size='6.5'>Clave SAT {item['code']}  ·  {item['unit']}</font>", styles["body"], markup=True),
            _p(item["quantity"], styles["right"]), _p(_money(item["unit_value"]), styles["right"]),
            _p(_money(item["amount"]), styles["right_bold"]),
        ])
    concepts = Table(
        rows,
        colWidths=[12 * mm, 92 * mm, 18 * mm, 28 * mm, 30 * mm],
        repeatRows=1,
        splitByRow=1,
        splitInRow=0,
    )
    concepts.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY), ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
        ("LINEBELOW", (0, 0), (-1, -1), .35, LINE), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, colors.HexColor('#FBFCFE')]),
    ]))
    story += [concepts, Spacer(1, 5 * mm)]

    totals_rows = [
        [_p("Subtotal", styles["body"]), _p(_money(data["subtotal"]), styles["right"])],
        [_p("IVA trasladado", styles["body"]), _p(_money(data["tax_total"]), styles["right"])],
    ]
    if Decimal(str(data["discount"] or "0")):
        totals_rows.insert(1, [_p("Descuento", styles["body"]), _p(f"-{_money(data['discount'])}", styles["right"])])
    if Decimal(str(data["retained_total"] or "0")):
        totals_rows.append([_p("Retenciones", styles["body"]), _p(f"-{_money(data['retained_total'])}", styles["right"])])
    totals_rows.append([_p("TOTAL", styles["value"]), _p(f"{_money(data['total'])} {data['currency']}", styles["right_bold"])])
    totals = Table(totals_rows, colWidths=[36 * mm, 37 * mm])
    totals.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), .7, LINE), ("ROUNDEDCORNERS", [7]),
        ("LINEABOVE", (0, -1), (-1, -1), .7, LINE), ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4), ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]))
    signature = Table([
        [_p("REVISIÓN DEL USUARIO", styles["section"]), "", ""],
        ["", "", ""],
        [_p("Nombre", styles["small"]), _p("Firma", styles["small"]), _p("Fecha", styles["small"])],
    ], colWidths=[45 * mm, 36 * mm, 18 * mm], rowHeights=[5 * mm, 12 * mm, 4 * mm])
    signature.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), .6, LINE), ("ROUNDEDCORNERS", [7]),
        ("SPAN", (0, 0), (-1, 0)),
        ("LINEBELOW", (0, 1), (-1, 1), .5, LINE),
        ("LINEBEFORE", (1, 1), (-1, -1), .5, LINE),
        ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
    ]))
    commercial = Table([[
        signature, "", totals
    ]], colWidths=[99 * mm, 6 * mm, 75 * mm])
    commercial.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    story += [commercial, Spacer(1, 4 * mm)]

    uuid = data["stamp"].get("UUID", "")
    fiscal_meta = [
        _p("TIMBRE FISCAL DIGITAL", styles["section"]),
        _p(f"<b>UUID</b><br/>{uuid}<br/><br/><b>Fecha de timbrado</b><br/>{data['stamp'].get('FechaTimbrado', '-')}<br/><br/><b>Certificado emisor</b><br/>{data['certificate']}<br/><b>Certificado SAT</b><br/>{data['stamp'].get('NoCertificadoSAT', '-')}", styles["small"], markup=True),
    ]
    fiscal = Table([[_qr(data, 26 * mm), fiscal_meta]], colWidths=[34 * mm, 139 * mm])
    fiscal.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), SOFT), ("BOX", (0, 0), (-1, -1), .6, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6), ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    original_chain = (
        f"||{data['stamp'].get('Version', '1.1')}|{uuid}|{data['stamp'].get('FechaTimbrado', '')}|"
        f"{data['stamp'].get('RfcProvCertif', '')}|{data['stamp'].get('SelloCFD', data['seal'])}|"
        f"{data['stamp'].get('NoCertificadoSAT', '')}||"
    )
    seals = Table([
        [_p("CADENA ORIGINAL DEL COMPLEMENTO DE CERTIFICACIÓN DIGITAL DEL SAT", styles["label"])], [_p(original_chain, styles["tiny"])],
        [_p("SELLO DIGITAL DEL CFDI", styles["label"])], [_p(data["seal"], styles["tiny"])],
        [_p("SELLO DIGITAL DEL SAT", styles["label"])], [_p(data["stamp"].get("SelloSAT"), styles["tiny"])],
    ], colWidths=[173 * mm])
    seals.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), .5, LINE), ("BACKGROUND", (0, 0), (-1, -1), WHITE),
        ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    # Reserva el alto completo: QR y sellos siempre comienzan donde ambos pueden caber.
    story += [CondPageBreak(74 * mm), fiscal, Spacer(1, 3 * mm), seals]
    doc.build(story)
    return output


def build_invoice_pdf_bytes(xml_bytes: bytes, internal_folio=None, order_number=None) -> bytes:
    """Genera la representación HSC directamente desde el XML timbrado."""
    buffer = BytesIO()
    build_invoice_pdf(xml_bytes, buffer, internal_folio, order_number)
    return buffer.getvalue()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("xml")
    parser.add_argument("output")
    parser.add_argument("--folio-interno", default="")
    parser.add_argument("--orden-compra", default="")
    args = parser.parse_args()
    print(build_invoice_pdf(args.xml, args.output, args.folio_interno, args.orden_compra))
