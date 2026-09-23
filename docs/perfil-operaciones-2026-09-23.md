# Perfil de Operaciones: simplificación visual

## Cambios

- Cabecera compacta: identidad, estado de cuenta y acceso para editar el perfil.
- Se elimina el resumen de tareas, reportes y clientes, sin eliminar registros.
- Gastos/Pagos y Usuarios quedan juntos y antes de los ajustes. Sólo aparece
  el indicador de revisión cuando realmente hay gastos pendientes.
- Apariencia y Conexión se despliegan a petición; la conexión y sus pendientes
  permanecen visibles en la fila cerrada y en el aviso global existente.
- Herramientas de Google y diagnóstico dentro de Conexión, sólo para propietario.
- Notificaciones abre la bandeja existente. Cerrar sesión conserva las mismas
  comprobaciones de reportes, reparaciones y envíos pendientes.
- Iconos lineales, filas uniformes y controles táctiles. Los cuatro estilos
  existentes y los modos claro/oscuro siguen disponibles.
- El aviso de instalación no ocupa la cabecera de Perfil; los técnicos lo tienen
  dentro de Apariencia y sigue disponible en las demás pantallas.
- Hoja de estilos incluida en el caché seguro para el uso sin conexión. No se
  cambian las colas, IndexedDB ni los datos guardados del usuario.

## Verificación local

- Prueba nueva `test_operations_profile.cjs`: jerarquía, controles únicos,
  visibilidad por rol, notificaciones, contador de pendientes y cierre protegido.
- Actualizada `test_demo_permissions.cjs` para inspeccionar también la nueva
  plantilla parcial, manteniendo las comprobaciones previas.
- 21 suites JavaScript pasan. Dos fallos anteriores se reproducen contra HEAD:
  `test_agenda_faults.cjs` carece de `account` en su entorno simulado, y
  `test_worklists.cjs` espera una ruta de regreso que ya cambió anteriormente.
- Navegador real, datos ficticios locales: propietario y técnico; acceso a
  Gastos/Pagos, perfil, notificaciones, diagnóstico, estado sin conexión y temas.
  Los cuatro estilos se verificaron a 320, 390 y 1280 píxeles, sin desbordamientos
  ni errores JavaScript. También se revisaron capturas claras y oscuras.

No se modificaron saldos, cálculos, permisos del servidor ni registros de gastos.
Esta revisión no realiza cambios en producción.
