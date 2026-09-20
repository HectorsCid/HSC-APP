# Reparaciones — versión del 20 septiembre 2026

Publicación de esta bitácora autorizada por el propietario: «Sube la bitácora y empieza con el panel». El panel nuevo de pagos se desarrollará por separado y no forma parte de esta versión.

## Alcance

- Administrador y técnicos: accesos en Clientes y Agenda → Reparaciones y desde cada equipo → Historial de reparaciones.
- Cliente/equipo del catálogo por ID exacto, o nombre nuevo fuera de la póliza. Los externos reciben claves propias reutilizables desde la bitácora; no se crean filas de póliza ni se vinculan por parecido de nombres.
- Visitas independientes, búsqueda por cliente, equipo, serie o texto; historial filtrado por equipo y consulta paginada de 50 visitas. Una segunda visita es otro registro. Editar conserva folio y registra la revisión anterior.
- Síntoma, diagnóstico, trabajo, partes, resultado, estado final, recomendaciones, modelo/serie/ubicación, refrigerante, presión baja/alta (psi), temperatura (°C), amperaje (A), condiciones de medición y receptor.
- Cámara y galería múltiple, antes/durante/después y pie de foto. Sin seis espacios: compresión secuencial en dispositivo, límite de 2 MB por archivo recibido y 20 MP en servidor. La capacidad total depende del almacenamiento del teléfono/Drive, no es infinita.
- Remisión de servicio HTML imprimible, guardable como PDF mediante impresión del navegador. Folio consecutivo REM independiente. No es CFDI, recibo de pago ni firma electrónica. Incluye datos/mediciones y referencia al número de fotos; las fotos se consultan en la bitácora, no se incrustan en la remisión.

## Persistencia y protección

Esquema 17: `operations_repairs`, `operations_repair_changes`, `operations_repair_photos`, `operations_repair_sequence`. PostgreSQL en servidor, SQLite en pruebas. No exporta reparaciones a la hoja matriz, ni altera reportes/rondas, gastos, tareas o facturas. Fotos en Drive bajo `_Historial de reparaciones`, identificadas por reparación/foto y con nombres estables. La base guarda referencias, no blobs de fotos. Miniaturas independientes y enlace a la imagen completa comprimida.

Borradores y cola guardados en IndexedDB por cuenta; confirmación local antes de salir/finalizar. La cola respeta automático/Wi-Fi/sin conexión y permite envío manual. No requiere mantener abierta la captura; necesita que la app esté abierta/activa para enviar, no promete ejecución con el teléfono cerrado. Los borradores no finalizados son locales (no aparecen en otros teléfonos).

Cada envío conserva ID y mutation_id. Primero registra la visita, luego sube fotos de una en una, y sólo confirma el cierre cuando están todas. Reintentar no crea otra visita ni cambia folio. Una caída después de subir una imagen permite repetir el mismo ID sin otra evidencia lógica. Web Locks serializa envíos de esta cola en pestañas del mismo navegador cuando está disponible. Las fotos capturadas se conservan localmente; las de otras cuentas necesitan conexión para descargarse. No se deben borrar los datos del navegador si hay borradores o envíos pendientes.

Revisión optimista en servidor evita pisar modificaciones de otro dispositivo. Conflictos quedan visibles, con comparación y confirmación antes de aplicar la propuesta local como siguiente revisión; errores de validación permiten corregir la propuesta. Historial confirmado visible para el personal, edición sólo autor/administrador y con permiso createReports. Partner/no autenticados no acceden. No borra visitas ni fotos del servidor.

## Verificación realizada

- 16 pruebas Python específicas: folios, reintentos, concurrencia SQLite, relaciones cliente/equipo, externos sin póliza, edición/auditoría, permisos, datos obligatorios, fotos con hash/IDs, rollback de fallo de almacenamiento, finalización incompleta, búsqueda/paginación, HTML escapado, remisión y rechazo de contenido no imagen.
- Prueba JS de transporte con nueve fotos: orden de envío, fallo en una foto sin finalizar, repetición del mismo mutation_id y rechazo de una revisión posterior. Fallo simulado de escritura local bloquea salir del editor; lo mismo mientras se respalda una foto.
- Navegador real con servidor local y datos sintéticos: nueva tienda/equipo, registro a 33 psi, guardado y recuperación de borrador después de recargar; tres fotos + tres + una, siete imágenes comprimidas conservadas; servidor en 503 al finalizar → estado Pendiente de envío → recarga → siete fotos presentes → restauración del servidor → una visita finalizada; siete imágenes cargadas y remisión con los datos correctos. Diseño revisado a 390 px y escritorio.
- Suite general: 234 pruebas unittest, 33 funciones adicionales de store/matrix y 20 scripts de pruebas JS aprobados. `git diff --check` sin errores de espacios.

## Límites de esta validación

No se cargaron reparaciones de prueba en producción ni se enviaron correos/notificaciones. El almacenamiento de Drive se sustituyó por un almacén de pruebas; falta comprobar el recorrido con credenciales reales tras publicar. No sustituye la prueba física de cámara/iPhone, cierre forzado, cuota de almacenamiento, PostgreSQL real y recuperación de respaldos del protocolo de salida de AppSheet. Este cambio por sí solo no autoriza cancelar las licencias de AppSheet.
