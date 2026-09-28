# Recuperación operativa de Google Sheets

Fecha: 28 de septiembre de 2026. Base del repositorio: `5495b12f2903ff99a7466026c7cc1b818e4efce5`.

Implementación local, sin commit, publicación ni cambios a permisos de técnicos. Este informe describe la ampliación de recuperación; los cambios anteriores de colaboración permanecen en el directorio de trabajo.

## Resultado funcional

| Problema anterior | Comportamiento nuevo | Evidencia |
| --- | --- | --- |
| Una entrega incierta bloqueaba los siguientes envíos sin una recuperación visual. | El diagnóstico del propietario muestra **Sincronización pausada**, reporte/entidad, operación, fecha y hora local, revisión, último fallo y número exacto de cambios pendientes. | `test_uncertain_has_frozen_entity_revision_reason_and_exact_count`, prueba de navegador. Se comprueba el conteo con 71 pendientes, sin el límite anterior de 50. |
| Una consulta a Google fallida ocultaba también el estado local. | El diagnóstico conserva el estado de la entrega y muestra por separado el fallo de consulta a Google. | `test_diagnostic_remains_visible_when_google_read_fails`. |
| La recuperación identificaba sólo la operación. Una confirmación vieja podía liberar otro intento de esa misma operación. | Cada intento tiene un identificador nuevo. La ruta exige operación + intento + confirmación explícita, bajo el mismo bloqueo que usa el escritor. | `test_stale_reference_cannot_release_new_attempt_of_same_operation`; navegador: otro propietario libera la operación y crea un intento nuevo antes de confirmar la ventana vieja. |
| Dos propietarios podían confirmar la misma recuperación. | Una sola confirmación tiene efecto; la otra recibe conflicto. Una solicitud activa no puede liberarse. | `test_two_owners_exactly_one_can_resume`, `test_active_request_cannot_be_released`, repetidas en PostgreSQL real. |
| No quedaba constancia de quién recuperó la cola. | Cambio de estado y registro de recuperación se confirman en una transacción. Identidad tomada de la sesión, nunca del cuerpo enviado por el navegador. | `test_confirm_route_audits_session_actor_and_restarts_queue`; tabla `operations_sheet_recoveries`. |
| Reanudar mientras había otro trabajador podía perder la señal de arranque. | Se programa un intento posterior. Los lotes restantes continúan y los fallos seguros se reintentan con pausa. | `test_busy_worker_schedules_followup_instead_of_losing_resume`, `test_worker_automatically_schedules_safe_failure_retry`. |
| Algunos fallos seguros se trataban como inciertos. | Errores locales antes de HTTP, lecturas fallidas tras una escritura confirmada y rechazos HTTP definitivos permiten reintento automático. Sólo queda pausada una escritura enviada sin confirmación. | `test_local_failure_before_http_never_requires_manual_confirmation`, `test_read_failure_after_acknowledged_write_retries_automatically`, `test_definite_rejection_recovers_automatically`. |
| Un reinicio antes de enviar podía dejar una pausa innecesaria. | Se distinguen preparación, envío y confirmación. Una preparación abandonada se libera automáticamente. Una entrega antigua sin información suficiente permanece pausada. | `test_orphan_prepared_intent_recovers_automatically_without_losing_changes`, `test_old_schema_intent_gets_stable_reference_and_stays_paused`; muerte real de proceso en PostgreSQL. |

La ventana **Revisar y reanudar** explica que debe usarse cuando ya no haya una solicitud anterior ejecutándose. El botón de confirmación requiere marcar esa declaración. Muestra el resultado, quién confirmó y el avance de la cola sin cerrar la ventana. Una referencia rechazada o una respuesta perdida exige consultar de nuevo el estado; no repite la liberación a ciegas.

La recuperación no borra ni modifica los contenidos de la cola. La prueba compara los pendientes antes y después de liberar. El trabajador normal envía después la revisión vigente y confirma cada salida.

## Pruebas ejecutadas

Antes de la implementación se ejecutaron ocho pruebas de recuperación: siete fallaron por las carencias originales y la prueba de rechazo definitivo ya pasaba. Se añadió también la regresión de interfaz antes de crear el módulo. Durante la revisión surgieron dos casos de pausa innecesaria; ambas reproducciones fallaron primero y pasan tras la corrección.

| Batería | Resultado |
| --- | --- |
| `python -B tests/run_report_validation.py` | **102 casos unittest + 41 funciones Python aprobados**, 28 archivos de pruebas JavaScript aprobados y smoke test Flask aprobado. |
| `python -B tests/run_full_validation.py` | **390 pruebas Python: 384 aprobadas y 6 fallos previos**; 28 archivos JavaScript aprobados. |
| `python -B tests/run_full_validation.py --baseline` | Commit original: 354 pruebas Python y **los mismos seis fallos**; 26 archivos JavaScript. Ningún fallo nuevo en la batería completa. |
| `node tests/sync_recovery_browser.cjs` | Chrome real, ventanas de **320 y 390 px**, consentimiento, datos visibles, referencia antigua, conservación de pendientes, reanudación y éxito en la misma ventana. Sin errores JavaScript ni desbordamiento horizontal; confirmación visible. |
| PostgreSQL real | **10 regresiones aprobadas**, más migraciones y pruebas de procesos/reinicio descritas abajo. |
| `git diff --check` | Sin errores de espacios. |

Los seis fallos preexistentes son cinco pruebas de `test_borradores.BorradoresTest` (`acciones_conservan_cliente_y_condiciones_sin_guardar_datos`, `costos_internos_se_guardan_y_transfieren_solo_el_precio_final`, `folio_se_asigna_hasta_guardar_borrador`, `guardar_continuar_y_eliminar`, `paginas_muestran_los_nuevos_controles`) y `test_mail_retirement.MailRetirementTests.test_reader_removed_but_operational_worker_remains`.

### PostgreSQL y migraciones

Se usó **PostgreSQL 17.11** portátil y **psycopg 3.3.6**, con un clúster nuevo en una carpeta temporal, usuario de prueba, puerto aleatorio y escucha exclusivamente en `127.0.0.1`. El programa no acepta una URL de una base existente y verifica que `data_directory` es su propio directorio temporal. Se detuvo y eliminó el clúster al terminar.

Se comprobó:

1. Migración del esquema **20 del commit publicado al 21**, conservando reporte y cola y añadiendo las columnas de reservas.
2. Migración **21 → 22**, conservando una entrega incierta antigua, su reporte y sus pendientes. El esquema 22 añade metadatos de intento y auditoría.
3. Inicialización concurrente desde tres procesos.
4. Serialización de tres procesos con bloqueos advisory reales.
5. Bloqueo `FOR UPDATE`: una modificación competidora recibe timeout mientras el escritor conserva la fila.
6. Muerte forzada del escritor: liberación del bloqueo y rollback de su transacción.
7. Reinicio real de PostgreSQL: persiste la pausa incierta, sigue pendiente el reporte y se puede recuperar con auditoría.
8. Muerte antes de HTTP: recuperación automática, conservando la cola.

[Salida de PostgreSQL](validation/sync-recovery-postgres.txt) · [Captura del diálogo móvil](validation/sync-recovery-mobile.png).

Comando local usado (los binarios, dependencias y copia previa del esquema 21 están fuera del repositorio, en `../.work`):

```powershell
$env:PYTHONPATH = '../.work/pg-deps'
python -B tests/run_postgres_recovery_validation.py --bin-dir '../.work/pg-runtime/pgsql/bin' --schema21-source '../.work/schema21-source/operaciones_store.py'
```

## Archivos de esta ampliación

Aplicación:

- `app.py`: diagnóstico, confirmación con identidad de sesión, conteo exacto, arranque y reintentos de cola.
- `operaciones_store.py`: esquema 22, metadatos de entrega y tabla de auditoría.
- `sheet_delivery.py` (nuevo): estados durables, clasificación segura, referencia por intento, recuperación atómica.
- `operaciones_sync.py`: seguimiento de la escritura HTTP pendiente de confirmación y preparación bajo bloqueo.
- `templates/app_operativa_demo.html`: aviso, ventana y conexión del diagnóstico.
- `static/sync_recovery.js` y `static/sync_recovery.css` (nuevos): interacción accesible y presentación móvil.
- `static/service-worker.js`: incorpora los recursos nuevos y cambia la versión del caché a 42.

Pruebas:

- Nuevos: `tests/test_sync_recovery.py`, `tests/test_sync_recovery_api.py`, `test_sync_recovery.cjs`, `tests/sync_recovery_fixture.py`, `tests/sync_recovery_browser.cjs`, `tests/run_postgres_recovery_validation.py`, `tests/run_google_recovery_validation.py`.
- Actualizados: `tests/test_audit_regressions.py` (la recuperación ahora exige intento y actor), `tests/run_report_validation.py`, `tests/run_full_validation.py`.
- Evidencia: este informe, `docs/validation/sync-recovery-postgres.txt` y `docs/validation/sync-recovery-mobile.png`.

Los demás cambios ya presentes pertenecen al trabajo anterior. No se tocaron los 682 archivos de caché de dependencias que ya aparecían modificados al iniciar.

## Límites pendientes

- **Prueba real de Google suspendida por indicación del usuario.** El permiso `drive.file` fue autorizado en el navegador, pero no se obtuvo un token válido; no se creó la carpeta ni la hoja de prueba y no se ejecutaron escrituras reales. El script `tests/run_google_recovery_validation.py` queda preparado, pero aún no validado contra Google. Las pruebas aprobadas de sincronización usan Google simulado.
- **Safari/iPhone físico y PWA instalada en teléfono no probados en esta ampliación.** La presentación y el flujo se comprobaron en Chrome real con tamaño y entrada táctil de celular.
- Google no ofrece aquí una confirmación consultable de una solicitud cuyo resultado se perdió. Esa incertidumbre sigue requiriendo que el propietario confirme que ya no hay una solicitud anterior ejecutándose. No se libera por antigüedad solamente.
- Los reintentos automáticos necesitan un proceso del servidor vivo. Tras reiniciar, la actividad normal de la aplicación vuelve a activar la cola persistida. No se añadió un servicio externo permanente de tareas.
- Las entregas heredadas del esquema 21 no siempre permiten reconstruir la revisión histórica. El panel lo indica; no inventa una revisión.
- Al publicar en el futuro, hay que evitar que convivan escritores antiguos que no implementan estos bloqueos con los nuevos. No se simuló Render ni un despliegue real.

**No se alteraron datos de producción, no se cambió el commit y no se publicó nada.** El trabajo permanece local y revisable. La prueba real de Google queda como validación pendiente antes de considerar comprobada la integración externa completa.
