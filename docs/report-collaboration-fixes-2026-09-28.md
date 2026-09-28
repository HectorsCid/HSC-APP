# Correcciones de reportes colaborativos

Base revisada: `5495b12f2903ff99a7466026c7cc1b818e4efce5`. Cambios locales, sin commit ni publicación. No se cambiaron los permisos generales de los técnicos.

## Qué cambió y cómo se comprobó

| Problema | Antes | Ahora | Evidencia reproducible |
| --- | --- | --- | --- |
| Foto anunciada como protegida sin confirmación local | Un aborto de IndexedDB conservaba la vista, pero podía anunciar respaldo y permitir salir. La preparación de un lote precedía a su respaldo. | Cada foto se comprime y escribe antes de preparar la siguiente. Sólo `transaction.oncomplete` permite marcarla respaldada. Un fallo conserva la vista, muestra **SIN RESPALDO**, permite reintentar o descartar conscientemente y bloquea finalizar/navegar normalmente. | `test_audit_regressions.cjs`: aborto por cuota, permisos denegados, recuperación y suspensión de la sexta compresión. Navegador: cero blobs tras el fallo; un blob después del reintento; seis después del lote. |
| Trabajo antiguo de Sheets | Un plan atrasado podía escribir valores anteriores y confirmar sincronización sobre una revisión nueva. | Bloqueo entre procesos por hoja, validación del identificador de salida y revisión, bloqueo de la entidad durante el envío y confirmación SQL en la misma transacción. Se recalculan las posiciones de las filas bajo el bloqueo. | `test_obsolete_sheet_plan_makes_zero_external_writes`: plan con 120, revisión nueva con 999; el antiguo no realiza ninguna escritura. `test_two_sheet_workers_send_one_current_revision`: dos trabajadores, un solo envío. |
| Duplicados en Drive | Buscar y crear en solicitudes simultáneas podía generar carpetas o archivos diferentes. Una respuesta perdida o una posición reasignada podía producir otro archivo. | Registro SQL de identidad canónica y bloqueo compartido. El ID se reserva antes de crear. La identidad de una foto depende de la carga/contenido, independientemente del espacio Foto 1–6. Se reutilizan el ID y el nombre originales. | Dos hilos crean exactamente una carpeta y un archivo. Pérdida de respuesta después de crear + reapertura del almacén: un solo objeto. Reasignar Foto 1 a Foto 2 conserva exactamente las mismas referencias. |
| Reservas abandonadas | Una reserva sin archivo podía impedir finalizar indefinidamente después de reiniciar. | Cada intento tiene token, vencimiento de 120 segundos y renovación cada 20 segundos. Se liberan reservas vencidas al consultar, reservar o finalizar. Las reservas antiguas sin token vencen después de 15 minutos. El dueño puede cancelar su intento activo. | Reserva de 24 horas + nueva instancia del almacén: permite finalizar. Un intento vencido no puede completar ni eliminar la reserva que lo reemplazó. Renovación, cancelación, cuenta ajena y seis cargas simultáneas probadas. |
| Dos pestañas sobrescribían borradores | Ambas reescribían el mismo índice local. Una confirmación o descarte podía retirar trabajo nuevo. | Registros separados por cuenta, reporte y editor; propuestas por campo; coordinación por BroadcastChannel y eventos de almacenamiento. Los campos distintos se concilian y los valores incompatibles se presentan para elección. El borrado exige la versión efectivamente observada/confirmada. | Pestañas A/B guardan 70 y 80 en campos distintos; ambos reaparecen sin conexión. Cambios incompatibles del mismo campo permanecen hasta elegir. Una confirmación tardía conserva la rama nueva; descartar una ventana conserva las fotos de la otra. |
| Reintentos de otra foto/cuenta | La misma mutación podía confirmarse con otra referencia; una pestaña antigua podía depender de la sesión posterior. | Registro persistente de hash y autor de cada mutación; confirmación inmutable; identidad de cuenta capturada al abrir la página. Las escrituras operativas requieren `X-HSC-Account` y rechazan una sesión distinta. | Cambiar los bytes o el autor de la mutación produce conflicto; el original sigue intacto. Modificar la identidad del DOM no cambia la cabecera del envío. Cuenta B rechaza el pendiente de A; B conserva su permiso normal para crear reportes. |

Los nueve casos iniciales se ejecutaron antes de modificar la implementación: todos fallaron. Se añadieron casos durante la revisión, incluyendo una reproducción que fallaba al reasignar el espacio de una foto, un contador de pendientes que aún leía el índice antiguo y un reintento de evidencia antigua sin hash verificable. Los casos específicos suman ahora **19 pruebas Python y 10 JavaScript**, todos aprobados.

## Pruebas ejecutadas

Desde la raíz de HSC-APP:

```text
python -B tests/run_audit_validation.py
python -B tests/run_report_validation.py
python -B tests/run_full_validation.py
python -B tests/run_full_validation.py --baseline
node tests/report_audit_browser.cjs
git diff --check
```

- Batería de reportes: **85 casos unittest + 41 funciones Python**, todos aprobados; **27 archivos CJS**, todos aprobados. Smoke de Flask: dos vistas offline neutrales, 23 recursos precargados, vinculación de cuenta y fallo controlado de miniaturas.
- Batería completa: **332 casos unittest + 41 funciones = 373 pruebas Python**; **367 aprobadas y seis fallos preexistentes**. Los 27 archivos CJS pasan. No se afirma que toda la batería esté en verde.
- Comparación con la implementación original de `5495b12`, en el mismo entorno aislado: **313 casos unittest + 41 funciones**, con los mismos seis fallos Python; sus 26 archivos CJS pasan.
- Tres procesos independientes comparten el bloqueo de un recurso: 30 incrementos controlados, ninguno perdido. Tres procesos inicializan simultáneamente una base nueva. Un proceso se termina forzosamente mientras mantiene la intención de envío a Sheets: el bloqueo del sistema se libera, la intención persistente sigue impidiendo escrituras y la recuperación explícita permite continuar.
- Chrome real en Windows, viewport táctil de 390 × 844, perfil temporal, service worker e IndexedDB reales: cuota agotada inyectada, reintento, seis imágenes sintéticas de **6000 × 4000 (24 MP)**, dos pestañas offline, reapertura offline, reinicio del proceso Flask conservando sólo la base ficticia y reconexión. Se comprueba que llegan las seis fotos y los valores 70/80 al servidor ficticio.
- Se interrumpe la respuesta HTTP después de que Flask acepta la finalización. El cliente recupera el resultado usando el mismo `submission_id`; el reporte queda finalizado con sus seis fotos. No se confirma ni limpia el pendiente usando otra identidad.

Los seis fallos existentes son:

```text
test_borradores.BorradoresTest.test_acciones_conservan_cliente_y_condiciones_sin_guardar_datos
test_borradores.BorradoresTest.test_costos_internos_se_guardan_y_transfieren_solo_el_precio_final
test_borradores.BorradoresTest.test_folio_se_asigna_hasta_guardar_borrador
test_borradores.BorradoresTest.test_guardar_continuar_y_eliminar
test_borradores.BorradoresTest.test_paginas_muestran_los_nuevos_controles
test_mail_retirement.MailRetirementTests.test_reader_removed_but_operational_worker_remains
```

Corresponden a expectativas de cotizaciones/borradores y a un contexto incompleto de una prueba del trabajador de avisos. Se conservan sin modificar: no son regresiones introducidas por este cambio. Las dependencias faltantes de Windows se resolvieron para la ejecución usando bibliotecas ya disponibles, sin modificar requirements ni el entorno del proyecto.

## Límites y recuperación operativa

1. **PostgreSQL real, Google real y Render no se probaron.** Se implementaron bloqueos consultivos de sesión, bloqueo transaccional de migraciones y orden entidad → salida en PostgreSQL. Las carreras ejecutadas usan SQLite temporal, bloqueos de archivo entre procesos y simulaciones de Google. Antes de publicar, falta ejecutar el equivalente en PostgreSQL y un Drive/Sheet exclusivos de pruebas, incluyendo desconexión de la conexión que mantiene el bloqueo. No se usó la base publicada como laboratorio.
2. **Una respuesta incierta de Sheets pausa las escrituras de toda esa hoja.** La intención se confirma en SQL antes del HTTP; un timeout o muerte del proceso no se transforma en autorización automática para escribir otra revisión. Esto sacrifica continuidad de sincronización para proteger los datos. El reporte y la salida pendiente siguen almacenados. `GET /api/operaciones/sync-status` y el diagnóstico del propietario muestran `sheet_delivery` con referencia, estado y fecha. Tras comprobar que la petición anterior terminó y que no queda un trabajador antiguo escribiendo, un administrador puede llamar `POST /api/operaciones/sync/resolve-uncertain` con `operation_id` y `confirm_no_request_in_flight: true`, además de su cabecera de cuenta. La confirmación es obligatoria y se verifica que la referencia no haya cambiado. **No debe usarse como botón de reintento automático.**
3. **Otros escritores externos no respetan los bloqueos SQL de HSC.** Ediciones directas en Sheets, AppSheet, scripts externos o procesos con el código antiguo quedan fuera de esta coordinación. Un futuro despliegue debe retirar los trabajadores antiguos y dejar terminar sus solicitudes antes de habilitar nuevos envíos. Una pestaña antigua sin identidad de cuenta recibe 401 conservando el respaldo; debe actualizarse. No se publicaron cambios de servidor ni de PWA en esta tarea.
4. **Drive conserva duplicados existentes.** Cuando los permisos efectivos visibles de dos carpetas coinciden, los hijos se trasladan a la canónica y la carpeta duplicada se conserva con otro nombre. Los IDs y archivos no se eliminan. Si los accesos difieren o no se pueden verificar, no se trasladan los hijos: el registro los marca en `access_review` y la lectura de rutas intenta también esas carpetas. Se requiere revisión autorizada para conciliar esos accesos. Las nuevas escrituras requieren la base operativa persistente; no se vuelve al comportamiento inseguro si falta esa base.
5. **Reservas y archivos huérfanos son distintos.** Vencer/cancelar una reserva libera el espacio SQL y rechaza confirmaciones tardías, pero no elimina un archivo que Drive haya alcanzado a recibir. Un reintento de la misma foto reutiliza su identidad. Un registro antiguo sin hash sólo se reconoce automáticamente si su ruta inmutable permite verificar los mismos bytes; de lo contrario se detiene para revisión y conserva el original.
6. **No se puede impedir que el sistema operativo mate el navegador.** Los cierres/navegación normales avisan o se bloquean según permite el navegador. Si el sistema mata el proceso mientras IndexedDB sigue fallando, una foto que sólo estaba en RAM puede perderse. La interfaz lo declara. IndexedDB confirmado tampoco equivale a respaldo remoto permanente: borrar datos del navegador, desinstalar o la expulsión del almacenamiento pueden retirarlo.
7. **No se validó hardware móvil real.** Chrome Android, PWA instalada y Safari/iOS requieren prueba en dispositivos. La emulación táctil de Chrome no demuestra la política de suspensión, almacenamiento o memoria de esos sistemas. Las seis imágenes grandes prueban el flujo y la compresión progresiva; no constituyen una medición de pico de RAM en teléfonos o Render.
8. **Se conservan metadatos de conciliación y envíos retirados.** No se purgan automáticamente todas las ramas de pestañas antiguas ni los marcadores que evitan resucitar un envío. El crecimiento de largo plazo del almacenamiento local necesita una política de compactación con pruebas; un agotamiento se trata como fallo de respaldo, no como éxito. No se eliminó información pendiente para ganar espacio.

Para fundamentar la estrategia de IDs se verificó la documentación oficial de [creación de archivos de Drive](https://developers.google.com/workspace/drive/api/guides/create-file): admite IDs pregenerados para archivos y carpetas y el reintento de una creación aceptada devuelve conflicto sin duplicar el objeto. La [documentación de límites de Sheets](https://developers.google.com/workspace/sheets/api/limits) describe los timeouts; no convierte una respuesta perdida en confirmación verificable. Por eso la recuperación de una entrega incierta es explícita.

## Archivos modificados o añadidos

Implementación:

```text
app.py
operaciones_store.py
operaciones_sync.py
reportes_bp.py
drive_registry.py                 nuevo
operation_locks.py                nuevo
photo_uploads.py                  nuevo
static/operations_session.js
static/service-worker.js
static/report_local_store.js      nuevo
templates/app_operativa_demo.html
```

Pruebas y ejecución aislada:

```text
test_background_actions.cjs
test_demo_evidence.cjs
test_operaciones_sync.py
test_audit_regressions.cjs         nuevo
tests/test_audit_regressions.py    nuevo
tests/run_audit_validation.py      nuevo
tests/run_full_validation.py       nuevo
tests/report_audit_browser.cjs     nuevo
tests/report_browser_fixture.py
tests/run_report_validation.py
docs/report-collaboration-fixes-2026-09-28.md
```

La migración local incrementa el esquema a 21 y añade tablas de identidades Drive, intenciones Sheets y mutaciones de fotos, más columnas de reserva temporal. No cambia cuentas ni permisos generales.

## Aislamiento

Las pruebas usan bases temporales, cuentas y fotografías ficticias. El cargador retira credenciales de Google/base y deshabilita conexiones salientes antes de importar Flask. La batería completa corre en una copia temporal del código sin bases, archivos de negocio, credenciales ni cargas de usuarios; los subprocesos heredan el bloqueo de red. El navegador sólo accede al servidor de pruebas en loopback. Los servicios de Google se sustituyen por simulaciones que permiten registrar y contar cada creación/escritura.

**No se consultaron ni alteraron reportes, fotografías, carpetas, hojas o bases de producción. No hubo commit, push ni despliegue.** HEAD sigue en `5495b12`. Las 682 entradas de cachés Python modificadas que ya existían al iniciar continúan con el mismo estado; no se revirtieron cambios ajenos.
