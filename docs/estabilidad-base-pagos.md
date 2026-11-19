# Refuerzo de base de datos, fotos y pagos

## Alcance

- PostgreSQL: conexión limitada a 10 segundos, consulta a 30 segundos y espera de bloqueo a 5 segundos. Un error revierte la transacción y cierra la conexión. No se limita una sesión inactiva porque los bloqueos de operación protegen también llamadas externas.
- Subida de foto de cliente/equipo: consulta puntual por ID. Conserva la comprobación de acceso existente; ya no carga el historial completo para localizar la entidad.
- Índice de pagos: pasa a una fila persistente `billing_payment_index` en la base operativa. Las modificaciones se serializan entre procesos, incluyendo creación normal, rápida, múltiple, reconciliación y retirada de REP.
- Cuando otra operación posee el bloqueo, una nueva emisión devuelve conflicto antes de iniciarse. No se presenta un timeout posterior del proveedor como si la operación no hubiera empezado.

## Migración y respaldo

La primera lectura importa el índice anterior, dando preferencia al archivo local válido no vacío y después al respaldo de Drive, como en el flujo anterior. Si falla la lectura remota, no se confirma una migración vacía. Después de migrar, la base es la fuente del índice; el archivo local y Drive son respaldos. No se timbra ni se envía correo durante esta migración.

El índice sigue siendo un documento completo, no filas individuales por REP. La protección depende de mantener todas sus escrituras bajo `payment_index_store.mutation`; las escrituras sin ese bloqueo se rechazan. El bloqueo global prioriza consistencia y puede pedir esperar si coinciden pagos distintos.

## Verificación y límites

Pruebas aisladas: importación inicial, recuperación después de fallo de respaldo, actualizaciones concurrentes sin pérdida, rechazo de escritura sin bloqueo, conflicto sin iniciar operación, propagación correcta de timeout posterior y consulta de foto sin snapshot. Los límites de PostgreSQL se comprueban con un conector simulado; la concurrencia del índice se prueba con SQLite temporal. No se han realizado pagos, correos ni timbrados reales.

Esto no demuestra la causa exacta del healthcheck fallido del 18 de noviembre. Siguen siendo necesarias mediciones controladas de carga y correlación de registros del servidor. No modifica ni elimina las colas locales de los teléfonos, ni sustituye el diagnóstico de sus envíos pendientes. Otras rutas todavía usan snapshots; esta reducción está enfocada a las fotos.
