# Prioridades acordadas — 20 septiembre 2026

Publicación autorizada el 20 de septiembre de 2026 para HectorsCid/HSC-APP, rama main, Render HSC-APP-3. Las ideas futuras siguientes no están autorizadas para activación automática.

## Prioridad inmediata

Reportes confiables en campo, cambios visibles localmente, conservar fotos/borradores y enviar sin duplicar al recuperar servicio. Las listas de «Mi jornada» son únicamente una ayuda de organización y acceso a equipos/rondas existentes. No cambian la lógica de reportes ni requieren completar una meta para trabajar.

## En preparación: Mi jornada sin incentivos

Lista por responsable, cliente y fecha prevista; equipos identificados por ID y ronda, orden libre, guardar sin conexión, reasignación administrativa, edición por responsable, mover de día, cerrar/reabrir y eliminar sólo la lista. Las abiertas persisten aunque llegue otro día.

Guardar identificadores estables, responsable, autor, versiones, fechas y revisiones para permitir reglas futuras. El progreso muestra reportes existentes de la ronda, NO prueba quién los hizo ni cuándo y NO es base suficiente para pagar un bono.

## Ideas pendientes, NO activar ni generar saldos todavía

- Cuenta por técnico: saldo acumulado que no desaparezca el domingo; semanas como historial, sueldo opcional, pagos parciales, anticipos, reembolsos y bonos separados. No desarrollar nómina fiscal sin otro alcance.
- Incentivo por productividad: reglas por cliente y escalones alcanzados. Ejemplos conversados, no política definitiva: hasta cinco equipos sin bono; seis a nueve $100; diez o más $400. Validar reportes, actor, fecha, evidencias y duplicados antes de cualquier cálculo; no penalizar automáticamente la sincronización tardía por falta de señal.
- Vacaciones, permisos y ausencias: solicitudes, aprobación y disponibilidad en ficha del técnico. Sin descontar sueldo ni asumir reglas de acumulación.
- Posible compensación por adelantar dinero propio para gasolina/piezas: ejemplo de 10%, todavía no aprobado como regla. Registrar por separado del gasto real y su reembolso; definir autorización, límites, qué gastos califican y evitar aplicarla a anticipos del patrón o tarjetas de la empresa.

La última indicación del usuario es recordar estas ideas, no implementarlas ahora. Prioridad: hacer reportes y que funcionen.

## Bitácora de reparaciones y remisiones — publicación autorizada

Solicitada después: administrador y técnicos registran intervenciones para clientes existentes o nuevos, equipo, síntoma, diagnóstico, trabajo realizado, piezas, mediciones finales (por ejemplo, 33 psi en baja), resultado y fotos antes/durante/después. Cada intervención tiene su propio registro y no reemplaza el historial por ronda. Listado cronológico por equipo y cliente, búsqueda y borrador offline. Fotos en lotes comprimidos/paginados, sin el límite de seis del reporte preventivo; definir límites por archivo y almacenamiento, sin prometer capacidad infinita.

Autorizada por el usuario con «avientate ese jale de los historiales de reparaciones» y después «Sube la bitácora y empieza con el panel». Implementación y alcance comprobado en `docs/repair-history-validation.md`. Nota de remisión de servicio imprimible vinculada a la captura finalizada, folio REM independiente, nombre de quien recibe y espacio para firma física. Sin importes, timbrado, firma electrónica, nómina ni avisos automáticos nuevos. El nuevo panel de pagos queda fuera de esta publicación.

## Criterio de salida de AppSheet aclarado por el propietario

El objetivo es cancelar licencias de la aplicación AppSheet por usuario; NO eliminar la hoja matriz ni las fotos en Drive. HSC debe cubrir el flujo usado en campo sin abrir AppSheet, conservando su integración por IDs con Sheets. No cancelar ni desconectar cuentas hasta validar el protocolo de salida y contar con autorización específica.
