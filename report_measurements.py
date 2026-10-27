"""Optional labels for current measurements; legacy reports stay unspecified."""
COMPONENT_OPTIONS = ('Compresor', 'Motor', 'Ventilador', 'Resistencia', 'Bomba', 'Otro')


def validate_components(payload):
    for key in ('a1_component', 'a2_component'):
        if key in payload and str(payload[key] or '').strip() not in ('', *COMPONENT_OPTIONS):
            raise ValueError('Selecciona un componente válido para el amperaje.')


def component_labels(payload):
    return {key: value if value in COMPONENT_OPTIONS else ''
            for key in ('a1_component', 'a2_component')
            for value in (str(payload.get(key) or '').strip(),)}
