# Sucursales guardadas para cotizaciones

## Uso

1. En **Editar cliente → Sucursales, lotes o proyectos**, agregar nombres como
   `Lote 86`, `Lote 83` y `Lote 81`, y guardar cambios.
2. Al cotizar, elegir el cliente y seleccionar su sucursal. **Sin sucursal**
   mantiene la cotización en el cliente general.
3. **Administrar sucursales** guarda los datos capturados de la cotización antes
   de abrir la ficha del cliente; las partidas permanecen en la captura actual.

La lista usa el mismo registro fiscal/RFC. No crea clientes duplicados ni
cuentas de usuario por sucursal. El respaldo, la carpeta de Drive, el PDF y el
historial por sucursal conservan el flujo existente.

## Compatibilidad y protección

- Los clientes anteriores, sin un catálogo explícito, recuperan los nombres del
  historial de cotizaciones y borradores. Al guardar la ficha se fija la lista.
- Quitar o cambiar un nombre no modifica documentos ni carpetas anteriores.
- Una cotización o borrador reabierto puede conservar una sucursal retirada; se
  identifica como **guardada en esta cotización**. Cambiar de cliente limpia la
  selección y no ofrece nombres retirados para nuevas capturas.
- Se rechazan duplicados (incluyendo diferencias sólo de mayúsculas/espacios),
  nombres de carpeta inválidos y valores ajenos al catálogo. La validación se
  ejecuta también en el servidor antes de modificar la captura.
- Formularios antiguos que no contienen el editor no borran la lista guardada.
- Cambiar datos fiscales desde facturación conserva las sucursales.

## Verificación local · 2026-09-23

- `test_quotation_branches.py`: catálogo, formularios, cambio de cliente,
  duplicados, persistencia del borrador, compatibilidad, actualización fiscal y
  rechazo de selecciones inválidas en todas las acciones de captura.
- `test_clientes_import.py`: regresión de datos fiscales/importación.
- Navegador real en entorno local aislado: agregar, quitar, guardar, seleccionar,
  cambiar cliente, conservar una sucursal retirada; escritorio y celular,
  claro/oscuro, sin desbordamiento horizontal ni errores JavaScript.
- La suite anterior `test_borradores.py` conserva dos expectativas visuales
  desactualizadas respecto a HEAD: busca `Minicotizador interno` y el enlace
  literal `>Corregir</a>` sin el icono añadido anteriormente. No se modificaron
  esas pruebas ni las pantallas correspondientes para este cambio.

Estas comprobaciones no acceden a producción ni generan documentos fiscales.
