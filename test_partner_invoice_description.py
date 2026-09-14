import ast
from pathlib import Path
from functools import lru_cache
from xml.etree import ElementTree as ET
import unittest
from unittest.mock import Mock


class InvoiceDescriptionTests(unittest.TestCase):
    def test_xml_concepts_cached_and_deduplicated(self):
        tree = ast.parse(Path('facturacion_bp.py').read_text(encoding='utf-8-sig'))
        names = {'_invoice_concept_description', 'partner_invoice_description'}
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
        request = Mock(return_value=b'<Comprobante><Conceptos><Concepto Descripcion="Cambio de compresor"/><Concepto Descripcion="Carga de refrigerante"/><Concepto Descripcion="Cambio de compresor"/></Conceptos></Comprobante>')
        ns = {'ET':ET, 'lru_cache':lru_cache, '_fm_request':request, '_decode_facturama_file':lambda v:v,
              '_pick':lambda obj,*keys:next((obj[k] for k in keys if k in obj),None)}
        exec(compile(ast.Module(body=functions,type_ignores=[]), 'facturacion_bp.py','exec'),ns)
        for _ in range(2):
            self.assertEqual(ns['partner_invoice_description']('invoice-1'), 'Cambio de compresor · Carga de refrigerante')
        request.assert_called_once_with('GET','/Cfdi/xml/issued/invoice-1')

    def test_description_route_checks_client_before_reading_invoice(self):
        tree=ast.parse(Path('app.py').read_text(encoding='utf-8-sig'))
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='api_operaciones_partner_invoice_description')
        fn.decorator_list=[]
        def abort(code):
            raise PermissionError(code)
        ns={'_partner_documents_client':lambda:({'id':'client-A'},None), '_partner_document_allowed':lambda *args:False,'abort':abort}
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'app.py','exec'),ns)
        with self.assertRaises(PermissionError):
            ns['api_operaciones_partner_invoice_description']('invoice-B')


if __name__=='__main__':
    unittest.main()
