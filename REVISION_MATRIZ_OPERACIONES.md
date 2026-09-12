# Revisión de matriz para Operaciones

Fuente: Hoja Matriz, Google Sheets `15xLRRfR_Leidnd34Cpr3ERbpJ7AaMelMxMa-9B0d6kQ`, revisión visual y lectura de encabezados en navegador (septiembre 2026). Sin ediciones. Revisión estructural y muestras, NO auditoría exhaustiva de todas las filas, fórmulas ni de la configuración de AppSheet. Descarga local no disponible y sin credenciales locales de servicio; no se certifican unicidad ni integridad global.

## Tablas observadas

| Hoja | Evidencia inspeccionada | Uso / precaución |
|---|---|---|
| Clientes | A: ID_Cliente, B: NombreCliente, C: Direccion, D: Foto, E1 vacía, F: CorreoAutorizado, G: RondaSeleccionadaCliente, H: URL_Reportes | Vincular catálogo HSC por ID, no renombrar masivamente. No usar correos de compras como permisos. No escribir por posición saltando la columna vacía. |
| Equipos | A:N: ID_Equipo, ID_Cliente, NombreEquipo, Marca, Foto, Modelo, NoSerie, Estatus, Inventario, Ubicacion, Departamento, Responsable, NoContrato, Vigencia | Diferenciar Estatus del avance de ronda. La maqueta aún no tiene Inventario, Departamento, Responsable, NoContrato y Vigencia. Preservarlos al editar. |
| Reportes | Encabezados A1:AL1 leídos individualmente | Formulario y fotografías por reporte. No confundir filas asignadas a la hoja con reportes existentes. |
| PendientesDia | ID_Pendiente, NombreEmpresa, NombreCliente, Contacto, descripción, fechas, prioridad, estatus, notas; muestras visibles | Agenda existente no idéntica a maqueta. Verificar encabezados largos completos, responsables y relación por ID antes de guardar. |
| SeleccionRonda | A:C: ID, ID_Cliente, RondaSeleccionada; filas visibles con R 1/R 2/R 3 | Hay muestras con ID_Cliente vacío. No elegir arbitrariamente primera/última fila como ronda actual. Diferenciar consulta individual de cambio operativo compartido. |
| ReportesFalla | A:K: ID_ReporteFalla, ID_Cliente, ID_Equipo, Fecha, DescripcionFalla, Foto, Estado, ID_Reporte, TipoFalla, MostrarCliente, Origen | Muestras con MostrarCliente=TRUE y orígenes Cliente/Reporte técnico. La visibilidad de Partner requiere respetar esta tabla y verificar filtros AppSheet; no basta guardar un comentario en Reportes. |
| Cotizaciones | A:G: ID_Cotizacion, ID_Cliente, Fecha, NombreArchivo, ArchivoPDF, Estado, Folio | Módulo existente, no sustituir con catálogo/maqueta de Operaciones. |
| SaldosPendientes | A:J: ID_Saldo, ID_Cliente, Mes, Año, FechaFactura, Descripcion, Monto, Pagado, FechaPago, FacturaPDF | Hay muestra casi vacía; no tratarla como saldo real sin validación. |
| HistorialPDF | timestamp, cliente, folio, archivo_url, carpeta_url visibles | Hay muestras de cotizaciones. Se necesita distinguir tipo y clave; no asumir que todo el historial es mantenimiento. |
| Control_Procesamiento | A3 ultimo_folio y B3 valor numérico; A1 vacío | No interpretar como tabla con encabezados en fila 1 ni sobrescribir contador. |
| VisitasMantenimiento | A:L: ID_Visita, ID_Cliente, Modalidad, Ronda, FechaInicio, HoraInicio, FechaFin, HoraFin, TipoServicio, Estado, Tecnico, Notas | Muestra con visita confirmada e ID_Cliente vacío. Verificar vínculo y uso actual antes de obligarlo o derivarlo. |
| VisitaEquipos | Encabezados visibles en fila 8, NO fila 1; ID_VisitaEquipo, ID_Visita, ID_Equipo, trabajo/estado/notas | Lectores que asuman fila 1 fallarán. Confirmar rango de AppSheet antes de mover o leer/escribir. |

## Reportes: mapa exacto observado

`A ID_Reporte; B ID_Equipo; C ID_Cliente; D FechaInicio; E FechaFin; F PresionCto1; G PresionCto2; H TempCto1; I TempCto2; J Amperaje1; K Amperaje2; L ObsElectrico; M OBsElectrónico; N ObsMecanico; O Comentarios; P MtoCorrectivo; Q PartesUtilizadas; R NombreEquipo; S Marca; T Direccion; U Foto1; V Foto2; W Foto3; X Foto4; Y Foto5; Z Foto6; AA Ronda; AB Realizado; AC ClienteDebug; AD ID_Visita; AE Modelo; AF NoSerie; AG NoInventario; AH Ubicacion; AI Departamento; AJ Responsable; AK NoContrato; AL Vigencia`.

Mantener acento y capitalización en `OBsElectrónico`. Mapear por encabezado validado, no por posición fija. Hay valores R 2 y IDs con espacios como `HDI16_R 2`; normalizar para comparar sin cambiar claves existentes. Antes de nuevos ciclos, definir año/visita para evitar colisión de un ID repetido en la siguiente R1. No hay columna Año entre estos encabezados: no inferir unicidad anual.

## Fotografías

- `Reportes!U:Z` contiene rutas largas de evidencia dentro de carpetas de reporte. Ejemplos observados comienzan `/HSC/1. Refrigeración y Manto. industrial/.../04. Reportes/...`.
- `Clientes!D` usa rutas relativas `Clientes_Images/...`; `Equipos!E` usa `Equipos_Images/...`.
- Son referencias, no imágenes incrustadas. La app debe subir cada archivo y escribir una referencia por celda, en el orden confirmado por el usuario.
- `reportes_bp._pdf_photos` lee Foto1–Foto6. El código ya tiene caché en memoria de ruta→ID (30 min) y bytes (10 min); no es almacenamiento persistente de fotos.
- El formulario manual actual acepta varios `fotos` pero su flujo guarda JSON/archivos y respaldo en Drive, no equivale a insertar Reportes!U:Z.
- Mantener compatibilidad de rutas con AppSheet/Partner y probar lectura de una foto nueva desde ambos consumidores antes de habilitar cargas reales. No hacer públicas las fotos para resolver permisos.

## Riesgos y secuencia de conexión

1. Leer y confirmar claves/rangos y detectar duplicados, huérfanos y fórmulas sobre el conjunto completo; todavía pendiente. Revisar expresiones, acciones y filtros de AppSheet: no se deducen sólo de la hoja.
2. Reservar claves en servidor y proteger reintentos/concurrencia. El generador de ID consecutivo de la maqueta NO sirve por sí solo con dos técnicos.
3. Guardar borrador durable y evidencias con IDs de operación. Validar contenido/tamaño/orientación en servidor; comprimir copias, sin reemplazar originales inadvertidamente. HEIC requiere conversión o mensaje claro.
4. Confirmar todas las cargas, escribir referencias U:Z en la fila encontrada por ID_Reporte. Ante fallo parcial conservar pendientes/reintentar sin duplicar ni borrar fotos existentes.
5. Marcar Realizado sólo después del guardado completo. Evitar que el monitor PDF lea un reporte parcialmente cargado. Revisar disparadores AppSheet frente a inserciones externas.
6. Revisar autorización de escritura real: auth_google declara scopes drive y spreadsheets.readonly. No se comprobó la capacidad efectiva de escritura; no asumirla ni ampliar permisos durante esta revisión.
7. Vincular fallas en ReportesFalla y comprobar MostrarCliente/permisos. La notificación es un paso separado de que aparezca en Partner.

## Reglas de identidad y permisos acordadas

- Todas las relaciones nuevas usarán `ID_Cliente`, `ID_Equipo`, `ID_Reporte` e `ID_Visita`. Los nombres son datos visibles, nunca llaves de unión.
- El ID interno queda inmutable después del alta. Cambiar el nombre del cliente o equipo no rompe la relación.
- `ID de matriz` es un vínculo administrativo separado. Debe ser único y validarse contra la matriz antes de guardar; no se asignará automáticamente por parecido de nombre.
- Técnico por defecto: crear reportes, reportar fallas y, si se autoriza, agregar o editar equipos. No puede editar nombre/dirección del cliente ni vínculos con la matriz.
- Administrador: configura permisos por cuenta. El permiso especial `Administrar vínculo con la matriz` no vuelve editable el ID interno.
- La cuenta propietaria puede alternar entre modo técnico y modo administrador dentro de HSC Operaciones. La navegación de regreso permanece dentro de Operaciones; la salida al panel HSC será siempre una acción explícita.
- Primera conexión real: sólo lectura y sólo para IDs de clientes de prueba aprobados. No escribir clientes activos hasta validar duplicados, huérfanos, permisos y recorrido completo.

## Implementado ahora, sólo maqueta local

Selección múltiple hasta seis evidencias con previsualización, quitar, ordenar y separación por equipo/ronda. Rechazo del lote completo si excede límites, repite metadatos o una imagen no se decodifica. Las evidencias sólo viven en memoria de la pestaña; los borradores de texto NO las respaldan. Falta almacenamiento durable antes de uso real. Sin subida, escritura a la matriz ni publicación de estos últimos cambios.

También quedó maquetado el cambio de modo técnico/administrador en una sola cuenta, permisos configurables para técnicos, IDs internos protegidos y navegación por historial interno. No hay autenticación por roles ni persistencia de permisos todavía; la interfaz no sustituye validaciones del servidor.

Se agregó una conexión inicial de sólo lectura mediante `Clientes!A:H`, `Equipos!A:N` y `Reportes!A:AL` en una sola consulta por lote, con caché de tres minutos. La respuesta omite correos, rutas de fotos y observaciones: entrega IDs, nombres, datos básicos, rondas, estados y conteos. También calcula duplicados y huérfanos para detectar problemas antes de escribir. Las imágenes todavía no se descargan; sólo se informa si existe referencia y el total encontrado, evitando una llamada a Drive por tarjeta.

HSC Técnico y HSC Partner ya no comparten permisos en la invitación. Partner queda limitado a consultar sus equipos, reportes terminados y fallas visibles. No puede crear, modificar ni finalizar reportes.
