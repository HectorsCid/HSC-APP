import unittest
from unittest.mock import patch
from flask import Flask
from request_diagnostics import install


class RequestDiagnosticsTests(unittest.TestCase):
    def test_error_logs_route_without_sensitive_details(self):
        app=Flask(__name__)
        app.testing=True
        install(app)
        @app.post('/photo/<ident>')
        def photo(ident):
            return 'error',400
        with patch.object(app.logger,'warning') as logger:
            response=app.test_client().post('/photo/private-name?token=secret',data='sensitive body')
        self.assertIn('X-HSC-Request-ID',response.headers)
        self.assertEqual(logger.call_args.args[3],'/photo/<ident>')
        self.assertNotIn('secret',str(logger.call_args))
        self.assertNotIn('sensitive body',str(logger.call_args))
        self.assertNotIn('private-name',str(logger.call_args))
