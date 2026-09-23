# Medición local del incidente de memoria del 23 de septiembre de 2026

## Alcance y límites

Render informó que la instancia `rbml5` excedió 512 MB. Los registros muestran
40 peticiones de miniaturas (dos tandas de 20) antes del reinicio, actividad de
gastos y pagos, y el nuevo arranque a las 16:36:35 UTC. No incluyen memoria por
operación ni distinguen miniaturas generadas de las servidas desde caché.

Esta medición **no es una medición de Render ni prueba la causa exacta de aquel
reinicio**. Se ejecutó en Windows/Python 3.11 con procesos nuevos por caso y
datos sintéticos. La consulta de diagnóstico de producción desde el navegador
de esta sesión fue bloqueada. No se cambiaron pagos ni datos de producción.

Se midió memoria residente del proceso (MiB), con muestreo cada 5 ms y el máximo
histórico reportado por Windows. No equivale al consumo total de un contenedor
Linux: faltan, entre otros, el proceso maestro de Gunicorn, cachés del sistema,
los datos reales, OAuth y PostgreSQL remoto. No se impuso un límite de 512 MB.

## Resultados

| Caso | Inicio (MiB) | Pico residente (MiB) | Detalle |
|---|---:|---:|---|
| Estado de salud | 113.71 | 114.39 | 500 consultas |
| Consulta/configuración de pagos | 113.42 | 115.20 | 100 ciclos, 300 solicitudes |
| Consulta/registro de pagos de prueba | 115.12 | 116.35 | 100 ciclos, 300 solicitudes |
| Importación de matriz | 118.60 | 131.25 | 20 ciclos; 12 clientes, 600 equipos, 1800 reportes |
| Clientes de Google | 113.94 | 123.20 | 3 hilos, 3 clientes por hilo, 5 ciclos |
| Miniaturas anteriores, 12 MP | 114.38 | 305.99 | 20 generadas + 20 desde caché, 2 hilos |
| Miniaturas corregidas, 12 MP | 113.80 | 118.80 | Misma carga y misma imagen |
| Miniaturas anteriores, 24 MP | 114.54 | 487.52 | 20 generadas + 20 desde caché, 2 hilos |
| Miniaturas corregidas, 24 MP | 114.16 | 119.75 | Misma carga y misma imagen |

En la importación se midieron deserialización, transformación, importación en
SQLite temporal y lectura del estado. No se consultó Sheets ni se exportó la
cola a Google. Por separado se midió la construcción real de clientes Drive y
Sheets sin ejecutar llamadas externas, utilizando credenciales anónimas.

Los datos de Pagos fueron 10 técnicos y 100 gastos de prueba en una base temporal.
La configuración y el registro de pagos se probaron por separado. Las
solicitudes respondieron 200 y las modificaciones sólo afectaron esa base.

Las fotografías fueron JPEG sintéticos de 4000×3000 y 6000×4000 píxeles. Su
generación ocurrió en el proceso padre, fuera de la medición. El código anterior
se extrajo de `_serve_operations_thumbnail` en `HEAD`; el corregido es la copia
local pendiente de publicar. Ambas pruebas cargaron los mismos módulos de la
app y usaron almacenamiento temporal real de miniaturas. No se conoce la
resolución de las fotografías solicitadas durante el incidente real.

## Interpretación

- Está demostrado un pico evitable en la conversión de miniaturas. Reducir la
  imagen antes de copiarla/rotarla y coordinar el procesamiento reduce mucho el
  consumo adicional en estas pruebas.
- No se reprodujo un gran pico en Pagos con esta carga. Esto no descarta otros
  volúmenes de datos ni interacciones con tareas en segundo plano.
- La prueba de matriz no representa toda la sincronización de producción.
- Las peticiones de miniaturas del registro real podrían ser aciertos de caché.
  No es posible atribuir retroactivamente el OOM a esas conversiones con los
  registros actuales.
- Para confirmar el comportamiento de producción hacen falta memoria del
  contenedor, operaciones activas y estado de caché durante cada intervalo.
  Ningún cambio fue publicado como parte de esta medición.

Resultados sin resumir: `memory-experiment-2026-09-23.json` y
`memory-stress-2026-09-23.json`. Reproductor: `tests/measure_incident_memory.py`.
