"""Catálogo de sucursales de cotización, sin duplicar la ficha fiscal."""
import re
import unicodedata


def branch_key(value):
    return ' '.join(unicodedata.normalize('NFKC', str(value or '')).split()).casefold()


def validate_branches(values):
    """Valida nombres nuevos; nunca renombra carpetas ni documentos históricos."""
    result, seen = [], set()
    for value in values:
        name = ' '.join(unicodedata.normalize('NFC', str(value or '')).split())
        if not name:
            continue
        if len(name) > 100:
            raise ValueError('Cada sucursal puede tener hasta 100 caracteres.')
        if name in {'.', '..'} or re.search(r'[<>:"/\\|?*\x00-\x1f]', name) or name.endswith('.'):
            raise ValueError(f'La sucursal «{name}» contiene caracteres no válidos para su carpeta.')
        key = branch_key(name)
        if key in seen:
            raise ValueError(f'La sucursal «{name}» está repetida. Déjala una sola vez.')
        seen.add(key)
        result.append(name)
    if len(result) > 100:
        raise ValueError('Puedes guardar hasta 100 sucursales por cliente.')
    return result


def catalog_branches(client, historic_names=()):
    # Una lista vacía explícita no debe revivir sucursales retiradas.
    names = client.get('sucursales', historic_names)
    if not isinstance(names, (list, tuple)):
        names = []
    result, seen = [], set()
    for value in names:
        if not isinstance(value, str):
            continue
        name = value.strip()
        key = branch_key(name)
        if key and key not in seen:
            result.append(name)
            seen.add(key)
    return result


def select_branch(value, choices, *, previous_client='', current_client='', previous_branch=''):
    name = str(value or '').strip()
    if not name:
        return ''
    # Conservar el nombre histórico sólo dentro de la misma cotización/cliente.
    if current_client == previous_client and name == previous_branch:
        return name
    for choice in choices:
        if branch_key(choice) == branch_key(name):
            return choice
    raise ValueError('Elige una sucursal de la lista. Para agregar otra, guárdala primero en Editar cliente → Sucursales.')
