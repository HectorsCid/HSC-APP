"""Instituciones bancarias nacionales identificadas por su RFC fiscal."""
import re

BANK_NAMES = {'BBA830831LJ2': 'BBVA México'}

def payer_bank_rfc(value, payment_form):
    rfc = str(value or '').strip().upper()
    if not rfc:
        return ''
    if str(payment_form).zfill(2) not in {'02', '03', '04', '28', '29'}:
        raise ValueError('El banco de origen sólo se puede indicar para cheque, transferencia o tarjeta.')
    if not re.fullmatch(r'[A-Z&Ñ]{3}\d{6}[A-Z0-9]{3}', rfc):
        raise ValueError('Revisa el RFC de 12 caracteres del banco de origen.')
    return rfc
