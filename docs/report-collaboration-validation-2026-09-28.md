# Reportes colaborativos: correcciones y validación local

Fecha: 28 de septiembre de 2026. Base revisada: `2705a65`.
Cambios locales, sin commit, push ni despliegue. No se escribieron datos de producción ni archivos de Drive/Sheets. Los cambios previos en archivos `.pyc` del entorno virtual se conservaron.

## Hallazgos y solución implementada

| Prioridad | Problema reproducido | Corrección local y evidencia |
|---|---|---|
| P0 | Un envío offline sin ID podía crear otro borrador después de la finalización y subir una imagen al mismo nombre de Drive, antes de detectar el duplicado. | Rechazo transaccional de creación tardía por equipo/ronda. Nombre de imagen ligado al borrador, operación y contenido. `test_report_conflicts.py`, `test_media_memory.py`. |
| P0 | La confirmación de otro técnico podía aceptarse como confirmación propia y borrar texto/fotos que no llegaron. | Recibo por `submission_id`, huella del contenido y lista de fotos; se confirma en la misma transacción que el reporte y su cola de Sheets. El cliente conserva los pendientes sin ese recibo. `test_report_safety.py`, `test_collaboration_recovery.cjs`. |
| P1 | Un guardado avanzaba la referencia del servidor sin actualizar el formulario, y el siguiente envío reintroducía valores antiguos. | Comparación por campo con su valor base, conciliación del formulario y conflictos explícitos. Campos distintos se combinan; cambios rivales en el mismo campo requieren elección. Prueba de navegador con dos técnicos: presión 73 frente a 74, ambos valores visibles en el conflicto. |
| P1 | Respuestas fuera de orden podían devolver una fecha anterior o borrar texto escrito mientras se creaba el borrador. | Un guardado en vuelo por reporte y nuevo envío de lo escrito durante la espera. Pruebas controladas 28→29→30 de septiembre y escritura durante creación. |
| P1 | Defaults de fecha/notas de un formulario abierto offline podían sobrescribir campos remotos que el usuario no había editado. | Base local persistida junto a los datos; sólo diferencias reales se envían. Notas vacías de otro técnico no se rellenan automáticamente. Fechas heredadas se recuperan también de las columnas del reporte. |
| P1 | Era posible finalizar con reservas de fotos aún incompletas; una foto tardía quedaba fuera de la sincronización o de una edición eliminada. | Finalización bloqueada mientras haya cargas sin confirmar o falten fotos del envío; reserva, confirmación y finalización coordinan sus bloqueos. Las seis posiciones permanecen idempotentes. |
| P1 | El modo sin conexión funcionaba con una pestaña abierta, pero no al arrancar la aplicación sin servidor. | Caché de una plantilla neutra sin identidad, restauración de la última cuenta local y datos por cuenta. Las API conservan la autenticación y vinculan la solicitud a la cuenta activa. Se probó recargando el navegador con el servidor apagado. |
| P1 | Texto y fotos de un borrador sin finalizar no siempre se reanudaban; “Enviar ahora” dependía de pulsar indirectamente un botón oculto. | Guardado automático de campos, recuperación de fotos desde IndexedDB y función de sincronización compartida por ambos botones. Verificado: presión 82 y una foto llegan al servidor manteniendo `state=draft`. |
| P1 | La limpieza de un envío podía borrar fotos o texto posteriores en otra pestaña. | Sólo se retiran las fotos incluidas en el recibo; cualquier dato posterior se conserva para revisión. Prueba con foto confirmada y foto adicional, más texto nuevo. |
| P2 | URLs de fotos y vistas de borradores permanecían en memoria; un PNG de 24 MP elevaba el consumo y la miniatura fallida devolvía el original completo. | Liberación de URLs, elementos de imagen, canvas y conexiones a IndexedDB; inspección de dimensiones antes de decodificar en el teléfono; límite del servidor de 12 MP después de reducción JPEG; miniatura fallida devuelve 503 pequeño con reintento. |
| P1 | Reintentar un recibo antiguo podía volver a aplicar una descripción de falla anterior a una edición posterior. | Los efectos sobre la falla verifican la revisión del reporte y guardan la falla con su salida a Sheets en una transacción. Prueba de recibo antiguo frente a una edición nueva. |

P0: riesgo directo de pérdida o sobrescritura irreversible de evidencia. P1: integridad o continuidad del trabajo. P2: recursos y presentación.

## Pruebas reproducibles

Desde la raíz de HSC-APP:

```powershell
python -B tests/run_report_validation.py
```

La batería usa bases temporales, bloquea conexiones de red antes de cargar Flask y evita generar bytecode. Incluye 107 pruebas Python (66 `unittest` y 41 funciones), 26 archivos de pruebas JavaScript y una prueba adicional de la aplicación real: dos plantillas offline, 22 recursos de caché, aislamiento entre cuentas y respuesta pequeña ante saturación de miniaturas.

Se actualizaron fixtures y aserciones que habían quedado desfasadas respecto de la interfaz actual, el protocolo con valores base y el proceso aislado de PDF. Los casos nuevos ejercitan el código real del editor con respuestas demoradas y los métodos transaccionales del almacén; no se limitaron a buscar texto en los archivos.

Para repetir la comprobación visual con datos sintéticos:

```powershell
python -B tests/report_browser_fixture.py
```

Abrir `http://127.0.0.1:8767/hsc-tecnico/` y `http://localhost:8767/hsc-tecnico/`. Son cuentas de prueba distintas con almacenamiento local separado y la misma base temporal. La identidad administrativa del fixture permite navegar todo el catálogo; la prueba específica de cambio de cuenta sí activa la verificación real de sesión. Drive/Sheets/notificaciones de salida están sustituidos o bloqueados. No usar este fixture como servidor real.

Resultados de navegador:

1. Ambos técnicos vieron 29/09/2026 como inicio y fin, y presión 72 después de editar campos distintos.
2. Dos cambios sobre presión produjeron el conflicto «tu valor 74 / compartido 73» conservando la propuesta local.
3. Se seleccionaron seis JPEG sintéticos de 4000×3000. El otro técnico vio seis posiciones distintas y su autor. El reporte final mostró seis fotos y la fecha correcta.
4. Un borrador creado con envíos pausados conservó texto y fotografía; el envío manual lo guardó sin finalizarlo.
5. Con el proceso Flask apagado, la recarga restauró la ronda 2, presión 83 y dos fotos locales, una aún pendiente. El aviso mostró un envío pendiente.

## Memoria medida

Mediciones de Windows en procesos nuevos, seis intentos secuenciales. Las imágenes se generaron antes, en otro proceso. No son métricas del contenedor de Render ni del heap de un teléfono.

| Caso | Antes | Pico del proceso | Después | Resultado |
|---|---:|---:|---:|---|
| Seis JPEG de 12 MP | 39,37 MiB | 66,88 MiB | 41,34 MiB | 6 aceptados y reducidos |
| Seis PNG de 24 MP | 38,84 MiB | 40,36 MiB | 40,07 MiB | 6 rechazados antes de decodificar |

El mismo caso PNG alcanzó aproximadamente 251,7 MiB en el diagnóstico previo. La reducción del pico se obtiene rechazando esa entrada grande en el servidor, no procesándola con la misma resolución. El teléfono admite hasta 24 MP tras inspeccionar la cabecera y envía una copia reducida de hasta 1600 píxeles de lado; entradas mayores piden una copia de menor resolución.

## Límites y preparación para una futura publicación

- La concurrencia ejecutada usa SQLite y navegador de escritorio. Falta ejecutar el mismo protocolo con varios procesos PostgreSQL y Safari/Chrome en teléfonos reales, incluyendo suspensión de la app y presión de memoria.
- No se validó escritura real en Google. Se verificaron nombres inmutables, referencias, idempotencia y cola con sustitutos locales. La integración real requiere un entorno de prueba autorizado.
- La migración de esquema 20 agrega la tabla de recibos. La nueva API rechaza finalizaciones sin identificador de envío y conserva el pendiente; las pestañas antiguas necesitarán recargarse. Esto requiere coordinar servidor, interfaz y service worker al publicar.
- El modo offline necesita al menos una carga previa de esta versión, una sesión local recordada y almacenamiento del navegador disponible. El cierre de sesión borra la identidad offline activa, conservando pendientes por cuenta.
- Las reservas de fotos sin confirmar mantienen bloqueada la finalización para evitar pérdida silenciosa. Una carga interrumpida necesita reintento/recuperación; no se considera confirmada sólo por haber terminado el resto del reporte.

No se realizó ninguna publicación.
