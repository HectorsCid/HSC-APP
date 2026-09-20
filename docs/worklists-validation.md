# Mi jornada — desarrollo local, sin publicar

Implementado: listas con responsable, cliente, fecha prevista, orden libre y ronda fija por elemento. Técnicos crean/editan sus propias listas; el administrador puede asignarlas. Las listas abiertas siguen disponibles cualquier día. Cerrar/reabrir o eliminar sólo afecta la lista, nunca equipos ni reportes. Acceso en Agenda → Mi jornada y retorno directo desde el equipo.

Persistencia: operations_worklists + operations_worklist_changes en base operativa, sin exportar a Sheets. Identificadores estables, revisiones optimistas, reintentos idempotentes y registro de autor/fechas/revisiones. IndexedDB conserva cambios locales, incluidos cambios de fecha y orden. Los conflictos quedan visibles para revisar y no detienen otros gastos o acciones de la cola. Partner no recibe estas listas.

No se calculan bonos, sueldos, vacaciones ni compensaciones. El progreso deriva de reportes completados por equipo/ronda, incluyendo los pendientes locales claramente etiquetados. Un reporte existente no acredita autoría, trabajo del día ni elegibilidad de incentivo.

## Verificado

- Pruebas de base: crear, reordenar, posponer, cerrar, eliminar, repetir solicitudes, detectar revisiones en conflicto, permisos/asignación, validación de equipos/clientes/rondas y ausencia de cambios a gastos/reportes/tareas.
- Pruebas JS: progreso por equipo/ronda sin duplicar reportes, overlay de pendientes y revisiones, navegación jerárquica y continuidad de otros envíos ante un conflicto.
- Navegador real sobre servidor local con datos sintéticos: crear dos equipos en orden inverso; abrir TEST2 R1 e iniciar su formulario normal; volver sin reabrir formularios; API en 503 → mover lista al día siguiente → recargar y conservar fecha/orden → restaurar API y confirmar envío.

No se probó en teléfono físico ni se publicó a Render. La caída simulada fue de API; no equivale a validar todos los escenarios de Safari o de arranque sin cobertura. Las pruebas locales no enviaron notificaciones a usuarios reales.
