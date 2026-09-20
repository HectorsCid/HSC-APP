# Auditoría para dejar de pagar AppSheet — 20 septiembre 2026

## Dictamen actual

**Todavía no autorizar la cancelación de las licencias.** No se encontró evidencia suficiente para afirmar paridad operativa en los teléfonos reales. Esto no impide publicar protecciones comprobadas, pero aprobar pruebas de código no equivale a aprobar una jornada de campo.

El alcance acordado es sustituir la **aplicación AppSheet**, no eliminar Google Sheets/hoja matriz ni Drive. No se borraron clientes, reportes, fotos, cuentas ni la matriz. Tampoco se canceló ningún servicio. Bonos, vacaciones, incentivos y bitácora de reparaciones son etapas separadas.

## Correcciones de protección aplicadas

| Riesgo encontrado | Protección | Evidencia |
|---|---|---|
| Atrás o navegación podían abandonar el formulario aunque fallara el almacenamiento | Primero respaldar texto y fotos; si falla, permanecer en el formulario. También al cerrar sesión y usando el gesto de atrás | Pruebas JS de cuota agotada, fotos no respaldadas y cámara ocupada |
| Texto escrito podía depender de tocar «Guardar borrador» | Respaldo local durante escritura, por cuenta/equipo/ronda | Navegador real: 33 psi y observación sobreviven atrás, recarga y reapertura |
| Respuesta perdida tras editar: el borrador temporal desaparecía y no se reconocía el folio confirmado | Recibo persistente que relaciona envío, propietario y folio definitivo; reintento reconoce confirmación | Pruebas Python y JS de edición y repetición |
| Reinicio entre guardar reporte y preparar sincronización | Reporte, recibo y cola de salida se confirman en una transacción | Fallo inyectado en cola revierte también finalización y recibo, conservando borrador |
| Dos técnicos finalizando el mismo equipo/ronda | Serialización de finalización y comprobación antes de confirmar; segundo borrador se conserva | Dos hilos contra SQLite: un terminado, una advertencia y una operación de salida. Rama PostgreSQL revisada; concurrencia real allí pendiente |
| Guardado tardío podía reabrir un reporte terminado como borrador | Escritura condicionada a que siga siendo borrador no completado | Prueba de intento de sobrescritura |
| Evidencia de un borrador de otra cuenta podía modificarse conociendo su ID | Validación de propietario en consulta y mutaciones de evidencia | Pruebas de rechazo 403/404. Compatibilidad de borradores antiguos sin propietario preservada |
| Recuperación volvía a subir fotos innecesariamente y retenía URLs temporales | Consultar confirmación antes de reiniciar fotografías; liberar URLs tras intento; coordinar pestañas mediante Web Locks donde esté disponible | Pruebas JS; no se afirma coordinación entre dispositivos mediante Web Locks |
| Google lento podía bloquear lecturas de campo detrás de un candado global | Cuando la base tiene datos, lectura directa sin esperar importación; refresco inicia sincronización en segundo plano | Prueba del endpoint sin servicios Google ni candado de caché disponibles |
| Error al precargar un reporte existente abría una edición vacía | Copia completa por cuenta cuando ya se consultó; sin original o copia, no abrir edición vacía | Pruebas online, copia offline, reporte no descargado y otra cuenta |

La copia local de un reporte existente debe haberse descargado antes de editarlo sin conexión. Los reportes únicos externos al formulario por ronda no se certificaron para trabajo sin conexión.

## Validación realizada

- 218 pruebas `unittest` de la aplicación pasaron después de corregir aislamiento y expectativas obsoletas de pruebas. Incluyen acceso por rol, reportes, facturación simulada, cancelaciones simuladas, correos, notificaciones, PWA, PDF, tareas, gastos, matriz y jornadas.
- 33 pruebas funcionales adicionales de almacenamiento/matriz pasaron usando bases temporales.
- Todos los `test_*.cjs` pasaron: recuperación offline, fotos, navegación, permisos visuales, calendario, jornadas y nuevas protecciones.
- Pruebas de facturación usan respuestas simuladas: **no** se timbró, canceló ni envió correo de producción.
- Se corrigieron pruebas antiguas, no se relajaron reglas de producto: técnico con sesión identificada; cachés fiscales aisladas; listado pendiente distinto del listado vigente; avisos ahora en cola transaccional en lugar de envío inmediato.
- Prueba visual en navegador de escritorio con `tests/offline_fixture.py`, cuenta y equipo sintéticos. No reemplaza Safari/iPhone o Android instalados.
- Despliegue de jornadas `7cf2cfc`: módulo público de Render respondió HTTP 200. Segunda tanda identificada como `report-safety-20260920`, service worker v22.

## Pendientes que sí condicionan cancelar AppSheet

1. **Prueba de campo completa en ambos teléfonos.** Primera carga conectada; modo avión; crear reporte con seis fotos; modificar nombre/foto de equipo; salir y cerrar completamente; volver a abrir sin señal. Deben verse los cambios y la cola sin borrar datos del navegador.
2. **Corte durante subida y reanudación.** Restablecer red, interrumpir entre fotos y durante confirmación, reiniciar la app. Un solo folio y cada posición una vez, sin texto perdido; al confirmar, desaparecer el borrador/pendiente correcto.
3. **Dos técnicos y dueño.** Registrar trabajo simultáneo con usuarios reales. Comprobar por ID de equipo, cliente y ronda los datos de HSC, matriz y fotos de Drive; el dueño debe ver lo confirmado, sin depender de abrir AppSheet.
4. **Entregable completo.** Generar el PDF desde esos reportes, comprobar datos y seis fotos, y confirmar el aviso correspondiente. El proceso actual conserva dependencias de Sheets/Drive; cancelar AppSheet no las elimina.
5. **Recuperación de respaldo comprobada.** Verificar retención y respaldo de PostgreSQL en el servicio contratado y restaurar una copia en entorno aislado; contrastar conteos/relaciones/fotos. No se verificó una restauración real ni se presume que un respaldo exista por estar en Render.
6. **Jornadas reales representativas sin usar AppSheet.** Registrar incidencias y discrepancias, comprobar recuperación en ambos teléfonos y revisar memoria/reinicios del servidor durante carga real. No prometer número de usuarios ni RAM suficiente sin métricas.

La recomendación de cancelar requiere cerrar estos puntos con evidencia. Durante el ensayo, AppSheet se conserva como contingencia, evitando editar el mismo reporte en las dos aplicaciones a la vez. La baja de licencias requiere aprobación del propietario; no implica borrar la app, sus datos ni sus fuentes.

## Riesgos adicionales identificados, sin encubrirlos

- El centro de avisos aún usa archivo local con respaldo en Drive; sus lecturas/escrituras remotas pueden esperar a Google. No se migró apresuradamente en esta tanda.
- No hay prueba de carga/recuperación en PostgreSQL de producción ni de actualización de la PWA instalada con cola real. Las pruebas concurrentes de esta tanda utilizaron SQLite local.
- No se hizo restauración integral de toda la información del negocio ni pruebas físicas de pérdida de dispositivo. Lo que nunca se envió sólo existe en ese dispositivo.
- La ejecución en segundo plano depende del navegador/SO: cerrar la app no garantiza subir mientras está cerrada. Reabrir debe reanudar sin pérdida.
- La comparación con AppSheet se limita a los flujos de trabajo utilizados por HSC, no a todas las capacidades de esa plataforma.

## Referencia de comparación

Los criterios de cola local, reintento sin duplicar y arranque offline se contrastaron con la documentación oficial: [Offline and Sync](https://support.google.com/appsheet/answer/10107724?hl=en), [What if I lose connectivity?](https://support.google.com/appsheet/answer/10105762?hl=en), [Errors and retry](https://support.google.com/appsheet/answer/10104788?hl=en). No son certificación de HSC.
