# Pagos a técnicos — publicación autorizada, 20 septiembre 2026

## Publicación

La bitácora quedó publicada por separado en `597cc80` (Render: health 200, shell v23, JS de reparaciones 200). Después de revisar los pendientes del panel, el usuario autorizó «Ciérralo y subelo». Esta entrega corresponde al panel con shell v24; no configura sueldos ni registra pagos reales.

## Qué cubre

- Perfil → Pagos a técnicos, sólo administrador. Técnicos con saldo cero también aparecen, incluidas cuentas suspendidas con deuda; clientes no reciben información de salarios.
- Tarjetas por persona con todo el adeudo, sin filtro de fechas que esconda semanas anteriores. Los pendientes y aprobados reembolsables cuentan; rechazados, no reembolsables y liquidados no cuentan. No modifica los gastos preexistentes al leerlos.
- Detalle semanal, domingo a sábado, 12 semanas por página; el total incluye también las semanas fuera de la página. Ver comprobante/revisar/liquidar/eliminar usa el flujo de gastos existente. Las modificaciones locales del gasto se reflejan con advertencia de confirmación pendiente.
- Sueldo semanal optativo, inicialmente desactivado. La configuración establece desde qué semana empieza; importe cero detiene sueldos futuros desde esa semana, nunca borra deuda anterior. No permite reescribir semanas cerradas. El sábado el sueldo entra al adeudo; antes aparece como previsión, separado del saldo actual.
- Registrar un pago de sueldo no transfiere dinero ni liquida gastos. Pagos parciales, fecha/referencia y anulación con motivo. La anulación conserva el registro original y devuelve el importe al adeudo.
- Centavos enteros, validación de finitud/precisión/importes, transacción por cuenta, control de revisión, UUID de movimiento y huella del contenido. Reintentar un mismo movimiento no lo duplica. Cambiar su contenido reutilizando la misma clave se rechaza.
- Un envío de salario sin confirmar se conserva en IndexedDB por cuenta, antes de contactar al servidor. Sobrevive recargas y cierre/reapertura de pestañas; permite reintentar el mismo identificador. Transacciones locales impiden que dos pestañas sobrescriban un movimiento pendiente; limpieza condicionada por ID y errores de espacio fallan de manera conservadora. No se muestra como pagado sin respuesta confirmada. Consultas fallidas señalan que el saldo visible puede ser antiguo; no sustituyen saldos por cero. No se encolan pagos de sueldo offline como si estuvieran confirmados.
- Desactivar el sueldo con cero también elimina de la programación todos los cambios posteriores ya planeados; los conserva en la auditoría. No reactiva un aumento anterior de manera implícita. Requiere configuración explícita posterior para reactivarlo; nunca elimina el adeudo ya generado.
- Esquema 18: cuentas, pagos salariales e historial de cambios en la base operativa, no Sheets ni bootstrap. GET no crea movimientos. API exclusivamente administrativa y `private, no-store`.
- No permite eliminar definitivamente una cuenta con historial salarial; se suspende el acceso y se desactiva por separado su sueldo futuro. Suspender acceso no se interpreta como baja laboral ni borra deuda.

## Validación

- 16 pruebas de dominio/API en `test_payments.py`: domingo, acumulación, año nuevo, paginación, salarios optativos, cambios futuros, pagos parciales, reintentos, dos escritores concurrentes SQLite, anulación y auditoría, acceso por rol, bloqueo de borrado de historial e importes inválidos; además desactivación con aumentos futuros programados y reactivación explícita.
- `test_payments.cjs`: acumulado sin filtro semanal, estados del gasto, centavos, límites de semana y acceso/navegación.
- `test_payment_journal.cjs`: reapertura con mismo identificador, aislamiento por cuenta, dos pestañas concurrentes, escrituras sin espacio, limpieza fallida y registro dañado. Modelo transaccional complementado con prueba real del navegador.
- Suite general: 250 pruebas unittest y 22 scripts `test_*.cjs` correctos; `git diff --check` sin errores.
- Nuevo cierre completo de pestaña con IndexedDB real: pago ficticio de $100 guardado por servidor pero confirmación perdida; cierre de pestaña, apertura de otra nueva, recuperación del movimiento y reintento. Saldo $2,370 antes y después del reintento (inicial $2,470); no se duplica el pago.
- Navegador real sobre `tests/offline_fixture.py`, base temporal y cuentas sintéticas, sin Google/Render: tarjetas de dos técnicos, total inicial $2,470 (gastos $470, salario $2,000), domingo con deuda anterior, pago parcial $500 deja $1,970, pérdida de confirmación de otro pago de $100 + recarga + reintento deja $1,870 (no $1,770). Prueba de cambio salarial $2,500 para semana nueva conserva adeudo salarial anterior de $2,000. Liquidación de gasto local se refleja de inmediato y su envío se confirmó tras corregir el adaptador de la fixture. Sin movimientos reales.
- Revisión visual a 390×844 y escritorio: tarjetas, resumen y formulario; sin desborde horizontal. Confirmación integrada mediante casilla y botón, sin depender de cuadros nativos del navegador.

## Límites explícitos

No hay nómina fiscal, anticipos, bonos, préstamos, prorrateos por ausencias, vacaciones, avisos automáticos de sueldo ni transferencias. Las notificaciones de gastos existentes se conservan. No se asignaron importes de sueldo reales. No hay una liquidación conjunta automática de sueldo + gastos; se mantienen separados para no descontar dos veces. El dueño debe elegir fecha y sueldo y registrar lo efectivamente pagado.

El historial salarial se consulta en línea; no se promete disponibilidad de sus datos después de cerrar la pestaña o recargar sin servidor. IndexedDB conserva el intento pendiente al cerrar, pero no protege contra borrar datos del navegador, modo privado o pérdida del dispositivo. No se solicita ni recomienda borrar datos. Saldo en otra pantalla requiere actualizar/reabrir; un intento de escritura con revisión antigua se bloquea. No hay PostgreSQL ni dispositivo físico conectado en este entorno: concurrencia validada con SQLite y navegador real, no equivale a una prueba física de iPhone/Android. Tras publicar, verificar de forma no mutante que la consulta de cuentas funciona con la base de Render; no generar movimientos reales para probar.
