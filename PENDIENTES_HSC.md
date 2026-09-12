# Pendientes acordados — revisión 11/09/2026

## Operaciones: pólizas y rondas (último cambio local, sin publicar)

- Fallas Partner: ya se pueden abrir y marcar como atendidas, con fecha, tipo de usuario y observaciones de cierre; el cambio queda en la base operativa y en cola para sincronizar con Sheets. Falta publicar y validar el cierre con una falla de prueba en Render.
- Activar/desactivar equipos ya actualiza la base operativa y conserva el historial; falta publicar y validar con un equipo de prueba.
- Los reportes sin evidencias ya pueden finalizarse en la base operativa, marcar el equipo realizado y quedar en cola para Google. Si hay fotos, se bloquea el cierre para no perderlas hasta conectar su subida permanente.

- Revisión estructural de las 12 pestañas reales y mapa completo Reportes!A1:AL1 documentados en REVISION_MATRIZ_OPERACIONES.md. Sin modificación de Sheets; falta auditoría global de claves/fórmulas y reglas de AppSheet.
- Evidencias múltiples locales: hasta seis fotos ordenables con miniaturas, eliminación y límites. Pruebas de lógica en test_demo_evidence.cjs (decodificación simulada). Fotos en memoria por equipo/ronda, no respaldadas con borrador de texto. Falta probar carga real en teléfono, persistencia, subida Drive y mapeo validado U:Z antes de conectar.
- Navegación y permisos maquetados localmente: cuenta propietaria alterna entre técnico/administrador sin salir de Operaciones; pestañas principales reinician el historial y los detalles usan flecha interna. Permisos por técnico para reportes, fallas, altas/edición de equipos, datos de cliente y vínculo matriz. IDs internos inmutables. Falta persistencia y cumplimiento obligatorio en servidor.
- Conexión inicial de Operaciones a Matriz preparada en sólo lectura y con caché: clientes, equipos, rondas y reportes por ID; omite correos, observaciones y rutas de imagen. Cuenta referencias de fotos sin descargarlas. Falta publicar y verificar con credenciales de Render, revisar duplicados/huérfanos devueltos y limitar datos por usuario cuando existan cuentas reales.
- Invitación Partner corregida: sólo consulta equipos, reportes terminados y fallas visibles; no comparte permisos de creación del técnico.

- Agenda: retirado interruptor de notificaciones del técnico; queda aviso de configuración por administración. No se pueden impedir bloqueos de notificaciones desde el sistema operativo.
- Alta de equipos por cantidad (1–100) en maqueta: registros independientes, IDs libres, nombres numerados y datos comunes; series individuales sin duplicarlas en el lote. Probado crear 3 equipos HDI7–HDI9 conservando los 6 existentes. Falta implementación de lote real en matriz con protección de reintentos/concurrencia y permisos.

- Nuevas pantallas en maqueta: agregar desde catálogo o crear cliente, editar nombre/dirección/póliza/vínculo matriz/foto; agregar y editar equipo (tipo, ubicación, marca, modelo, serie, notas y foto), desactivar/reactivar sin quitar reportes. Datos, permisos y fotos nuevos sólo en memoria de la pestaña, se pierden al recargar. Sin APIs ni permisos reales implementados.
- Validaciones de ejemplo: nombres normalizados y vínculos matriz duplicados en clientes, serie duplicada dentro del cliente, imágenes JPG/PNG/WebP hasta 5 MB. IDs existentes no editables. Confirmación al abandonar formularios con cambios.
- Verificado en navegador estrecho: alta de cliente ficticio, alta/edición/desactivación de equipo, R4, conteos por cliente y ocultamiento al quitar póliza. Sintaxis JavaScript y diff correctos. Falta probar carga de foto en dispositivos reales y conectar almacenamiento persistente/permisos antes de uso operativo.

- Maqueta con selector Ronda 1 a 4 en cliente y equipo; avance, historial y borradores por ronda.
- Agregar/Editar cliente incluye `tiene_poliza`, booleano guardado en el catálogo único existente. Por defecto desmarcado; no inferir póliza de facturas ni de aparecer en AppSheet.
- Al conectar Operaciones con datos reales, filtrar en servidor por `tiene_poliza is True` y por los permisos del técnico. No duplicar catálogo ni exponer datos fiscales/contactos innecesarios.
- Desactivar póliza oculta al cliente de la lista operativa sin borrar equipos ni reportes; no concede ni revoca cuentas automáticamente.
- Falta enlazar por identificador estable con los equipos/reportes de la matriz y aplicar ese filtro real; la maqueta sigue usando ejemplos aislados.
- Publicación previa confirmada: mejoras generales y maqueta aislada en Render, commit `7edd9da`. Las menciones anteriores a pendiente de publicación más abajo corresponden al estado previo; siguen pendientes las verificaciones reales indicadas.

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

# Transición de Operaciones a base robusta

- [x] Esquema inicial para clientes, equipos, reportes, evidencias y cola de sincronización.
- [x] Desarrollo local sin costo mediante SQLite; PostgreSQL se activa sólo con `OPERACIONES_DATABASE_URL`.
- [x] Importación transaccional desde la Hoja Matriz conservando IDs.
- [x] Lectura desde base después de la primera importación, sin consultar Google en cada apertura.
- [ ] Crear PostgreSQL administrado en Render cuando se apruebe el cargo aproximado de USD 6.25/mes.
- [ ] Importar y validar UVM en Render antes de habilitar escrituras.
- [x] Guardar altas de clientes/equipos y borradores de texto en la base operativa.
- [x] Conservar evidencias sin conexión en el dispositivo, separadas por equipo y ronda.
- [x] Preparar migración integral de Clientes, Equipos y Reportes hasta columna ZZ, conservando IDs, columnas adicionales y posiciones Foto1-Foto6.
- [x] Bloquear la migración si existen IDs duplicados o relaciones huérfanas y comparar los conteos contra la base.
- [x] Proteger en servidor las escrituras operativas por rol: administrador o técnico según la acción; cliente en consulta restringida.
- [ ] Finalizar reportes reales en PostgreSQL después de conectar almacenamiento de evidencias.
- [ ] Crear trabajador de salida para subir PDF/evidencias a Drive y actualizar Sheets por lotes.
- [ ] Incorporar miniaturas persistentes y caché offline en el teléfono.
