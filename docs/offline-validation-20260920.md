# Validación de trabajo local — 20 septiembre 2026

Cambios: solicitudes con timeout (incluida la lectura del cuerpo), diagnóstico separado de sesión/servidor/conexión, cola persistente para nombres/fotos/clientes/equipos, superposición de pendientes sobre cada respuesta remota, protección de borrador local frente a respuesta tardía, actualización de service worker sin borrar colas.

La preferencia explícita «sin conexión» se conserva hasta que el usuario la cambie. El modo Wi-Fi tampoco autoriza datos móviles automáticamente si el navegador no identifica la red. Automático intenta contactar el servidor aunque navigator.onLine informe false.

## Evidencia obtenida

- Todos los test_*.cjs pasaron, incluido test_offline_recovery.cjs (sintaxis del JS inline, superposición local, errores 401/503, timeout y recuperación).
- 33 pruebas unittest de readiness, sincronización de matriz y avisos de tareas pasaron.
- Navegador real sobre tests/offline_fixture.py, sólo datos sintéticos y servidor loopback:
  - API devolviendo 503: cambiar nombre y guardar lo refleja de inmediato.
  - Recargar todavía en 503 conserva nombre y aviso de envío pendiente.
  - Restaurar API envía automáticamente; aparece «Guardar equipo: confirmado» y cero pendientes.
  - Agregar PNG de ejemplo en 503 conserva foto cargada (naturalWidth=192), también tras recargar; la imagen procede de Blob local recuperado de IndexedDB.

## Límites

No equivale a validación en iPhone/Safari ni a paridad completa con AppSheet. La prueba de navegador simuló caída de API, no un dispositivo físicamente sin cobertura. El navegador puede limitar almacenamiento, ejecución en segundo plano o identificación de Wi-Fi. La app necesita una primera carga con conexión. Las acciones fuera de los formularios operativos probados pueden seguir requiriendo conexión.

No borrar datos de navegación para recuperar conexión: elimina respaldos locales. Tampoco confundir «guardado en dispositivo» con «confirmado por servidor».
