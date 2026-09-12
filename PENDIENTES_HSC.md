# Pendientes acordados — revisión 11/09/2026

## Prioridad: aplicación general (cambios locales, sin publicar)

- Reportes: causa confirmada en Render para `UDA38_R 1`, 11/09 a las 13:18:31 CST y otras revisiones: `Generación manual interna falló: HTTP 302`. El monitor usaba `test_client()` sin sesión y era redirigido al acceso. Corregido mediante invocación interna del generador, sin abrir las rutas públicas. También se corrige el límite que contaba borradores y el estado de cola con errores. Falta publicar y verificar PDF real y registro HistorialPDF.
- Centro de avisos: bandeja administrativa compartida con filtros, contador, leído/sin leer y acciones; almacenamiento atómico local y respaldo/recuperación desde Drive, hasta 2,000 avisos. No es todavía una bandeja por técnico/cliente. Falta validación real de respaldo tras publicar.
- Avisos: facturas programadas conservan push y ahora historial; se añaden en bandeja pagos/complementos emitidos por HSC, respaldo de CFDI confirmado/fallido, respaldo de saldos fallido y errores de reportes. No se habilitó push masivo para todas las categorías nuevas.
- Recibidas: paginación sin detenerse por páginas pequeñas, deduplicación y advertencia de límite; canceladas y REP fuera de gastos, notas de crédito y descuentos restados, monedas separadas. Los gastos del balance en pesos excluyen otras monedas e informan la exclusión. Falta completar documentos no disponibles en Facturama y conciliar; no se presenta como balance fiscal completo ni como deducibilidad confirmada.
- Importación histórica de facturas: APLAZADA hasta contar con sus conceptos. No importar ni inventar conceptos.
- Vigilancia mensual: Render `HSC-Facturas-Programadas` ya está activo cada 10 minutos. Verificado el 11/09 a las 22:20:39 CST: HTTP 200, `ok:true`, `processed:[]`. Esto confirma el ejecutor, no un timbrado mensual real. No crear otro ejecutor ni modificar el aviso mensual por plantilla.
- Mejoras de vigilancia locales: última comprobación, próximas ejecuciones y errores en la bandeja; aviso visible al consultarla si la comprobación supera 40 minutos. Cron sale con error si HTTP 200 contiene facturas fallidas o respaldo sin confirmar. Conserva ID/UUID si falla el correo después de timbrar. Falta publicar web y cron y verificar una ejecución debida autorizada.
- Pruebas sin timbrar, enviar correos ni modificar la matriz real. La maqueta operativa previa permanece separada y sin publicar.
- Verificación local: 77 pruebas unitarias aprobadas (dependencias y servicios externos simulados donde corresponde), sintaxis de los scripts de Facturación y avisos validada. Bandeja probada en navegador con datos aislados: apertura, marcar leído y filtro Sin leer.

## Facturación recurrente

- Ya habilitado: programación mensual por plantilla, día y hora.
- Modos disponibles: recordatorio, confirmación antes de timbrar, timbrado automático y timbrado con envío automático.
- Incluye aviso de éxito/error, pausa, edición y protección mensual contra duplicados.
- Llaves y revisión periódica ya configuradas en Render. Pendiente publicar las mejoras descritas arriba.

## Panel de balance

- Facturado, cobrado y saldo pendiente por mes, año y cliente.
- Top de clientes, PUE contra PPD, vencimientos y comparativos.
- Excluir canceladas y no contar complementos como ventas nuevas.
- Cuando estén completas las recibidas: ingresos contra gastos e IVA trasladado contra pagado.

## Aplicación móvil y notificaciones

- PWA básica ya disponible con icono y sesión persistente.
- Centro de avisos local: ver alcance y pendientes de validación arriba.
- Push de facturas programadas configurado; conservar frecuencia mensual por plantilla.
- Después: preferencias por categoría y usuarios, más allá de la bandeja administrativa compartida.

## Sustitución gradual de AppSheet

- Mantener AppSheet durante la transición.
- Diseño operativo en pausa mientras se prioriza HSC general. La maqueta de clientes/equipos/rondas/reportes/agenda/invitaciones no está conectada; faltan permisos, invitaciones reales y validación de guardado/lectura de la matriz.
- Primera migración: reportes de trabajo y refrigeración con borradores, fotos y firmas.
- Después: cuentas creadas por HSC, permisos por técnico y modo sin conexión.
- Cancelar AppSheet únicamente después de una prueba paralela estable.
