# Autobuses Asturias

Consulta de horarios de autobús interurbano en Asturias (Consorcio de Transportes de Asturias y ALSA) y del trayecto Oviedo–Madrid de ALSA. Web instalable (PWA) en GitHub Pages, hermana de [Trenes en Vivo](https://github.com/mobilityastur/trenes).

## Cómo funciona

- Cada noche, `.github/workflows/datos.yml` ejecuta `scripts/generar.py`, que descarga del [NAP](https://nap.transportes.gob.es) los GTFS de ALSA (fichero 1133) y del CTA (fichero 1178).
- De ALSA se quedan las líneas dentro de Asturias y las que unen Oviedo con Madrid. Las paradas cercanas de ambas fuentes se unifican y los viajes repetidos se eliminan (gana el del Consorcio).
- Genera `datos/horarios.json` con los próximos 52 días y publica la web. La app calcula las salidas en el propio móvil.
- Son horarios programados: no hay datos públicos de tiempo real.

## Puesta en marcha

1. Settings → Pages → Source: **GitHub Actions**.
2. Settings → Secrets and variables → Actions → secreto **`NAP_API_KEY`** con la clave de tu cuenta del NAP.
3. Actions → «Actualizar horarios de autobús» → **Run workflow**. El resumen de la ejecución muestra cuántos viajes ha cargado de cada fuente.

Datos: Punto de Acceso Nacional de Transporte Multimodal, Ministerio de Transportes y Movilidad Sostenible ([licencia](https://nap.transportes.gob.es/licencia-datos)).
