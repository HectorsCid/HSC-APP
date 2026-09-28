# Actualización de las pruebas y validación completa

Fecha: 28 de septiembre de 2026. HEAD conservado: `5495b12f2903ff99a7466026c7cc1b818e4efce5`.

**Resultado: cero fallos nuevos y cero fallos pendientes en las baterías ejecutadas.** La batería completa pasa con **350 casos unittest + 41 funciones Python = 391 pruebas Python**, y **28 archivos de pruebas JavaScript**. No se cambiaron expectativas de la aplicación ni se eliminaron, omitieron o marcaron como aprobadas pruebas fallidas.

Este informe actualiza el resultado histórico de seis fallos previos que aparece en `sync-recovery-validation-2026-09-28.md`. Los cambios de aplicación del trabajo anterior siguen locales; esta tarea sólo modifica pruebas y documentación.

## Las seis pruebas desactualizadas

Los nombres de las primeras cinco pertenecen a `test_borradores.BorradoresTest`; la sexta a `test_mail_retirement.MailRetirementTests`.

| Prueba | Supuesto desactualizado | Actualización y comprobaciones conservadas o reforzadas |
| --- | --- | --- |
| `test_guardar_continuar_y_eliminar` | Enviaba un cliente inexistente en el catálogo. | Registra el cliente ficticio antes de guardar. Comprueba guardado, folio, nombre, partidas, comentarios, recuperación y eliminación del borrador. |
| `test_folio_se_asigna_hasta_guardar_borrador` | Esperaba reservar folio aunque el cliente fuera rechazado. | Registra el cliente ficticio. Comprueba que navegar y guardar datos no reserve folio, que guardar el borrador reserve uno solo y que quede persistido. |
| `test_acciones_conservan_cliente_y_condiciones_sin_guardar_datos` | La ruta rechazaba el cliente ficticio no registrado antes de preservar sus condiciones. | Registra el cliente. Comprueba atención, dirección, comentarios, partida, tiempo de entrega, anticipo y vigencia, incluyendo cambios antes de la vista previa. |
| `test_costos_internos_se_guardan_y_transfieren_solo_el_precio_final` | Buscaba el título retirado «Minicotizador interno». | Verifica «Costos internos», los tres botones actuales y los controles de búsqueda, filtro y desglose. Mantiene cálculos de 500 y 600, una sola partida pública, y añade recuperación completa del desglose desde el borrador. La plantilla pública del PDF muestra $600 y excluye nombres, nota del proveedor, claves e importes privados. |
| `test_paginas_muestran_los_nuevos_controles` | Esperaba un campo visible para el nombre del borrador, botones sin iconos y el orden anterior de acciones. | Comprueba el campo oculto alimentado por el diálogo y la conexión POST del botón de guardado. Verifica acciones principales PDF → Facturar → Enviar → Más acciones; menú Corregir → Duplicar → Archivos → Eliminar. Conserva enlaces, método POST, confirmación de borrado, conservación del PDF y catálogo fiscal. |
| `test_reader_removed_but_operational_worker_remains` | Ejecutaba la función extraída sin proporcionar `request`, aunque ahora consulta `request.endpoint`. | Usa Flask y solicitudes reales del cliente de pruebas, con `start_notifications` simulado. Verifica la inscripción del hook, inicio en página normal y API operativa, ausencia de inicio en cuatro endpoints de salud y archivos estáticos. Conserva la prohibición del lector retirado, navegación de correo y avisos de correo. |

El catálogo de clientes se aísla y restaura en cada caso. No se sustituye la validación de clientes de la aplicación. Los indicadores de sincronización usados por estas pruebas también se restauran al terminar.

## Comprobaciones adicionales

- `test_varios_calculos_internos_crean_partidas_independientes`: se añadió el registro del cliente ficticio para que su guardado explícito cumpla la misma regla del catálogo. Esta prueba ya pasaba; se conservaron todas sus comprobaciones de cálculos independientes.
- Nueva `test_cliente_inexistente_no_guarda_borrador_ni_reserva_folio`: comprueba el mensaje de rechazo, que no se llame al generador de folios y que no aparezca ningún borrador ni cliente seleccionado.
- `tests/report_audit_browser.cjs`, función `openReport`: la ejecución adicional encontró un timeout del guion, previo a las comprobaciones de colaboración. Consultaba `isVisible()` inmediatamente después de navegar y podía saltarse el cliente todavía no cargado, intentando pulsar un equipo oculto. Ahora espera un control visible de la pantalla inicial y selecciona las entidades ficticias por identificador. Se retrasa deliberadamente 400 ms la primera respuesta del catálogo para mantener esa carrera cubierta. Se conservan todas las comprobaciones de fotos, IndexedDB, modo sin conexión, pestañas, reinicio y finalización.

## Resultados ejecutados

Las baterías específicas son subconjuntos de la completa; sus cifras no deben sumarse a las 391 pruebas.

| Ejecución | Resultado final |
| --- | --- |
| Completa antes de actualizar: `python -B tests/run_full_validation.py` | 349 casos unittest + 41 funciones, 28 archivos CJS; reproducidos exactamente los seis fallos indicados arriba. |
| Completa después: `python -B tests/run_full_validation.py` | **350 casos unittest + 41 funciones Python aprobados**, 28 archivos CJS aprobados, **0 fallos**. |
| Reportes: `python -B tests/run_report_validation.py` | **102 casos unittest + 41 funciones Python aprobados**, 28 archivos CJS aprobados y comprobación Flask aprobada: dos pantallas de inicio sin conexión, 25 recursos cacheados, vinculación de cuenta y fallo de miniatura. |
| Recuperación: `test_sync_recovery` + `test_sync_recovery_api` | **17 pruebas aprobadas**, 0 fallos, 0 errores, 0 omitidas. Incluye referencia antigua, dos propietarios, solicitud activa, auditoría, conservación de pendientes y arranque de cola. |
| Recuperación de interfaz: `node --test test_sync_recovery.cjs` | **3 pruebas aprobadas**, 0 fallos, cancelaciones, omisiones o pendientes. |
| `node tests/sync_recovery_browser.cjs` | Aprobado en Chrome real con tamaños móviles de **320 y 390 px**: aviso y detalles, consentimiento, rechazo de referencia antigua, conservación, identidad del propietario, reanudación y resultado en la misma ventana. Sin errores JavaScript ni desbordamiento horizontal. |
| `node tests/report_audit_browser.cjs` | Aprobado tras corregir la espera del guion. Comprueba falta de espacio, bloqueo de finalización/cierre, reintento de respaldo en IndexedDB, **seis imágenes sintéticas de 24 MP**, conciliación entre dos pestañas sin conexión, recuperación después de recargar, reinicio real del servidor local y finalización con respuesta HTTP perdida. |
| Comparación con aplicación de `5495b12` y pruebas actualizadas | **12 pruebas aprobadas**, 0 fallos, 0 errores, 0 omitidas: todos los casos de `test_borradores` y `test_mail_retirement`. Confirma que estos cambios actualizan pruebas obsoletas sin necesitar cambios de aplicación. |
| `git diff --check` | Sin errores de espacios. |

La comparación publicada se ejecutó en una copia temporal: se restauraron desde Git los archivos de aplicación modificados respecto a `5495b12` y se conservaron las dos pruebas actualizadas. No se cambió el checkout. El ejecutor temporal está en `../.work/test_refresh_focus.py`; también se utilizó para las 17 pruebas específicas de recuperación, con conexiones de red bloqueadas antes de importar la aplicación.

[Salidas de las ejecuciones](validation/test-suite-refresh.txt). Se conserva también el fallo inicial del guion de navegador; no se presenta esa ejecución como aprobada.

## Archivos modificados en esta tarea

1. `test_borradores.py`.
2. `test_mail_retirement.py`.
3. `tests/report_audit_browser.cjs`.
4. `docs/test-suite-refresh-2026-09-28.md`.
5. `docs/validation/test-suite-refresh.txt`.

Los registros y el ejecutor auxiliar de comparación están en `../.work`, fuera del repositorio. La comparación SHA-256 de 233 archivos registrados al inicio sólo encontró cambios en los dos archivos de pruebas de la raíz: **ningún cambio adicional en código de aplicación, plantillas o recursos**. El tercer archivo de pruebas está dentro de `tests`, fuera de ese inventario. Los 682 cambios preexistentes de archivos `.pyc` de dependencias permanecen sin intervención.

## Alcance y límites

- Sin OAuth ni acceso a Google real en esta tarea. Las escrituras externas se simularon; la red saliente estuvo bloqueada y el navegador sólo pudo acceder al servidor local de prueba.
- Sin datos, credenciales ni bases de producción. Se utilizaron directorios y bases SQLite temporales, clientes ficticios y fotos generadas. **No se alteraron datos de producción.**
- No se repitió PostgreSQL en esta actualización exclusiva de pruebas; su ejecución real anterior está documentada en `sync-recovery-validation-2026-09-28.md` y su código de aplicación no cambió durante esta tarea.
- Chrome se ejecutó con dimensiones móviles; esto no sustituye una prueba en Safari/iPhone físico o una PWA instalada en un teléfono. La integración real con Google continúa sin validar, por indicación del usuario.
- **No se hizo commit ni se publicó.** Cero fallos en estas baterías no constituye una validación de los entornos externos que no se ejecutaron.
