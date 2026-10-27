# Revisión de estabilidad y consumo de HSC

Fecha: 26 de octubre de 2026. Código revisado: `63938df`.

## Alcance y límites

Revisión local de servidor web, operaciones, Partner, fotos, PDF, facturación,
sincronización, avisos, almacenamiento del navegador y despliegue. No se cambió
código funcional ni se publicó nada como parte de esta revisión. Las pruebas
utilizaron datos locales y servicios simulados; no enviaron correos ni timbraron.

No es una prueba de carga de producción, una auditoría de seguridad exhaustiva
ni una medición del consumo actual de Render. Las variables reales de Render
pueden diferir de `render.yaml`. Los registros anteriores prueban que hubo OOM,
pero todavía no identifican una causa única. El pico proporcionado de 449.3 MB
dejaba aproximadamente 62.7 MB hasta el límite informado de 512 MB.

## Resultado de pruebas

- 517 pruebas descubiertas por unittest: 516 correctas y un error.
- El error está en `test_matrix_auto_sync.py:68`: ejecuta una función extraída
  del código sin proporcionar `trace_memory`, el decorador añadido al diagnóstico.
  La prueba debe adaptarse; este resultado no demuestra por sí solo un fallo
  de la sincronización en ejecución.
- 39 archivos de pruebas de interfaz JavaScript: todos correctos.
- Las funciones de pruebas con estilo pytest no descubiertas por unittest no
  están incluidas en las 517. No se hizo una validación visual de todas las
  pantallas en todos los dispositivos.

## Hallazgos y prioridades

### 1. Alta: lecturas completas para mostrar información parcial

`operaciones_store.py:696` (`snapshot`) consulta clientes, equipos, reportes,
fallas, tareas, gastos, listas y estadísticas de toda la operación.
`app.py:4077` prepara esa información y después `_scope_operaciones_payload`
filtra lo visible para un cliente. El filtrado final existe: el hallazgo es
trabajo innecesario y crecimiento con el historial, no una demostración de que
se entregue toda la base al cliente.

Además, `_entity_snapshot` en `operaciones_sync.py:40` obtiene otro snapshot
completo por operación saliente. Varias rutas de tareas y gastos hacen lo mismo
para localizar un solo registro.

Propuesta: consultas por empresa/ID y periodo; resúmenes SQL; páginas para
históricos; cargar detalles al abrirlos. Evitar reemplazar esto por una caché
global sin límite, porque trasladaría el problema a memoria permanente.

### 2. Alta: actualización que reconstruye vistas sin cambios

`templates/app_operativa_demo.html:1247` actualiza cada 30 segundos y al volver
a la pestaña. `loadMatrixData` termina en `repaintPendingState` y
`applyMatrixBootstrap`, que repintan las vistas. `renderPartnerDashboard` vuelve
a crear logo y listas de imágenes, incluidas la vista previa y la lista completa.
`drainFieldActions` también repinta y solicita datos cuando procesa una cola
no vacía, incluso si las acciones quedan bloqueadas o fallan. Si la cola está
vacía ya tiene una salida anticipada. La petición compartida evita algunas
solicitudes simultáneas, pero no evita todos los repintados redundantes.

`static/operations_offline.js:9` clona el payload completo mediante JSON y el
flujo lo vuelve a serializar a localStorage. Esto afecta sobre todo al navegador.

Propuesta: versión de datos por empresa, no repintar cuando no cambió nada,
actualizar tarjetas afectadas y no cargar imágenes de vistas ocultas. Un ETag
debe comprobarse antes de reconstruir toda la respuesta para ahorrar también
trabajo del servidor, no solamente transferencia.

### 3. Alta: documentos Partner ligados a un recorrido pesado de Facturama

`facturacion_bp.py:1978` (`billing_client_groups`) tiene caché de 180 segundos,
pero al vencer consulta pendientes, un periodo reciente y hasta 50 páginas de
histórico. Mantiene un candado durante buena parte de las consultas externas.
`_fm_request` tiene un tiempo máximo por solicitud de 60 segundos; no equivale
a un límite global de todo el recorrido.

`notification_delivery.py:315` inicia un recorrido de documentos cada cinco
minutos para empresas con usuarios cliente. Comparte el proceso y el origen
de datos con las consultas interactivas.

Propuesta: índice durable de documentos, actualización incremental y con límite
total de tiempo; devolver el último estado confirmado indicando su fecha si el
proveedor no responde. Consultar inmediatamente al proveedor cuando una acción
fiscal lo requiera: no inventar vigencias ni saldos basándose en un dato viejo.

### 4. Alta: descarga de originales antes de obtener turno de procesamiento

`app.py:5288` busca primero la miniatura persistida, lo cual es correcto. Cuando
no existe, descarga el original, obtiene sus bytes y sólo después intenta
`prepare_photo(... wait_timeout=0.25)`. El bloqueo del procesador protege la
decodificación, pero no toda la descarga/preparación anterior. Se pueden
conservar originales en solicitudes diferentes mientras coinciden otros trabajos.

La interfaz admite cuatro cargas de imagen concurrentes, frente a dos hilos
HTTP configurados en Procfile. Con un PDF ocupando el turno, las miniaturas
nuevas pueden fallar temporalmente y agotar los reintentos de la interfaz.

Propuesta: generar la miniatura al guardar/importar una foto, agrupar solicitudes
de la misma miniatura y acotar también descargas/preparación. Si no hay turno,
responder de manera recuperable antes de descargar un original grande. No
eliminar los límites de resolución para que carguen imágenes: esos límites
protegen la memoria.

### 5. Alta: varios trabajos pesados comparten los 512 MB

Hay protecciones individuales, pero Drive inicial, importación de matriz,
documentos, fotos y generación de PDF pueden coincidir. Los PDF usan un proceso
temporal; es positivo para liberar memoria al finalizar, pero ese proceso sigue
contando dentro del mismo contenedor de Render.

`_retry_operations_sync_on_activity` puede programar sincronización con lecturas
normales de la app. El worker puede importar la matriz antes de procesar salida.

Propuesta: presupuesto de concurrencia para tareas costosas; priorizar atención
interactiva, usar una cola durable y posponer trabajo no urgente. Como segunda
etapa, evaluar un worker separado para documentos si la medición justifica su
costo. No aumentar workers de Gunicorn indiscriminadamente: además de RAM,
existen candados y archivos JSON cuya coordinación actual es por proceso.

### 6. Media: conexiones de base de datos sin un límite explícito en el código

`operaciones_store.py:163` abre una conexión PostgreSQL por contexto. No fija
`connect_timeout` allí; podría venir en la URL real, que no se inspeccionó.
Tampoco se observa un plazo general de consultas en esa función.

Propuesta: límites de conexión y consultas, recuperación controlada y métricas de
latencia. Después medir si conviene un pool pequeño y acotado. Un pool demasiado
grande también consume recursos: no agregarlo como solución automática.

### 7. Media: copias locales de fotos y colas del dispositivo

El servidor ya persiste miniaturas de 360x360 y entrega cabeceras privadas de
caché. El service worker no conserva explícitamente las fotos de operaciones.
Los nodos reconstruidos vuelven a pasar por la cola visual aunque el navegador
pueda reutilizar una respuesta HTTP.

Propuesta: primero evitar repintado; después una caché local limitada de
miniaturas, con cuenta/empresa/versión en su identidad, aislamiento al cambiar
de usuario y limpieza al cerrar sesión. No guardar errores, respuestas de login
ni originales de alta resolución. Manejar falta de espacio sin borrar evidencia
o trabajo pendiente. Revisar `getAll()` en las colas para no recuperar fotos si
únicamente se necesita un contador o metadatos.

### 8. Media: avisos con JSON completo y respaldo remoto en el camino crítico

`notification_center.py:22-44` lee/escribe el estado completo y `_write` intenta
respaldarlo en Drive. Hay retención de 2,000 avisos y 2,000 trabajos push: no es
una lista infinitamente creciente, pero cada actualización puede mover todo el
documento bajo candados.

Propuesta: guardar eventos/transiciones pequeños en SQL y respaldar en lotes
fuera de la petición. Mantener reintentos e idempotencia para evitar duplicados.

### 9. Media: fotografías de reportes en el repositorio y carpeta estática

Git contiene 5,446 archivos de `venv311` (aproximadamente 172.4 MiB) y 66 fotos
en `static/diag_uploads` (aproximadamente 9 MiB). Es tamaño de archivos, no RAM
residente ni prueba de que todo ello se cargue en memoria al arrancar.

Propuesta: quitar el entorno virtual del seguimiento en una tarea separada y
conservar dependencias reproducibles. Migrar fotos generadas a almacenamiento
durable con rutas autenticadas, verificando antes respaldos y referencias.
No borrar archivos para aligerar el despliegue sin un plan de migración.
La configuración permite servir imágenes estáticas sin login; debe revisarse
la privacidad de evidencia alojada en esa carpeta. No se probaron URLs privadas
en producción durante esta revisión.

### 10. Media: pruebas, métricas y mantenimiento

Actualizar la prueba aislada del decorador y ejecutar una suite reproducible en
cada publicación. Incorporar escenarios de varias sesiones, proveedor lento,
PDF simultáneo con fotos y dispositivo con almacenamiento lleno.

El diagnóstico actual tiene buffers y retención limitados; no es una solución
al OOM. Su muestreo puede perder picos breves y su periodo temporal se reinicia
con el proceso. Definir métricas ligeras de memoria del contenedor, tiempo por
ruta, conexiones, cola y fallos; no registrar fotos, credenciales ni datos
sensibles en los eventos. Los ahorros propuestos todavía no están medidos.

## Protecciones que conviene conservar

- PDF en procesos temporales y límite de una transformación pesada a la vez.
- Límites de bytes y píxeles al procesar fotografías.
- Caché de originales limitada a 24 MiB, con máximo de 6 MiB por entrada.
- Miniaturas persistidas, evitando reprocesar el original en cada visita.
- Evidencia y colas durables, revisiones y protección contra reintentos duplicados.
- Separación por cuenta/empresa y comprobación de propiedad en las fotos.
- Validación del archivo PDF y manejo de renderizador ocupado.

## Orden sugerido de trabajo

1. Actualización sin repintados innecesarios y carga local limitada de miniaturas.
2. Consultas por empresa/registro, versión de datos y paginación de históricos.
3. Índice incremental de documentos y recuperación frente a proveedores lentos.
4. Coordinación de trabajos pesados, generación anticipada de miniaturas y límites
   de base de datos.
5. Pruebas de concurrencia y medición en un entorno equivalente a producción.
6. Limpieza del repositorio y migración segura de archivos; decidir capacidad
   de servidor según los picos medidos, no sólo según el consumo en reposo.

Si vuelven a ocurrir OOM mientras se optimiza, un aumento temporal de capacidad
puede aportar margen, pero no corrige por sí mismo consultas o concurrencia
innecesarias. No se cambió el plan de Render.

## Referencias técnicas

### Avance local del 27 de octubre

Primera etapa preparada, todavía sin publicar: comparación de datos para evitar
repintados idénticos; historial acotado de URLs de fotos vistas; descarga de
miniaturas nuevas dentro del turno de procesamiento; carga visual secuencial y
reintentos con espera creciente. Se añadió selección durable de componente por
amperaje y su presentación en el PDF. Las fotos nuevas de reporte usan un máximo
de 1920 píxeles y JPEG de calidad 90; la reducción JPEG respeta la proporción para
evitar una decodificación mayor que la necesaria. Se mantienen límites de bytes,
píxeles y procesamiento serial. Esto no garantiza un ahorro concreto de RAM del
contenedor: se debe medir en producción.

Validación: 526 pruebas unittest, 27 funciones de pruebas del almacén y 40 suites
JavaScript correctas. PDF sintético de tres páginas renderizado e inspeccionado.
El test aislado del decorador se actualizó. El resto de propuestas sigue pendiente.

Segunda etapa del 27 de octubre: consultas SQL por empresa antes de materializar
Partner, búsqueda del técnico por nombre/ID/marca/modelo/serie/ubicación,
consultas ligeras de clientes y catálogo durable de facturación por cuenta. El
histórico inicial se descarga por páginas con puntos de reanudación; después
se consultan novedades, solicitudes pendientes y una ventana histórica rotativa.
Actualizar solicita una revisión completa en segundo plano. El resultado anterior
se conserva hasta confirmar una publicación completa. Se muestran fecha de
actualización y avisos de información guardada. Las acciones de timbrar, cancelar
y enviar correo no pasan a depender de ese catálogo de presentación.

Validación de ambas etapas: 540 unittest, 27 pruebas adicionales del almacén y
41 suites JavaScript correctas. Publicación autorizada por el usuario; consumo
real y tiempos del proveedor deben observarse después del despliegue.

- https://render.com/docs/troubleshooting-deploys
- https://render.com/tutorials/when-deploys-go-wrong/health-checks-and-crashes
- https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/Conditional_requests
- https://developer.mozilla.org/en-US/docs/Web/API/Storage_API/Storage_quotas_and_eviction_criteria
- https://www.psycopg.org/psycopg3/docs/advanced/pool.html
