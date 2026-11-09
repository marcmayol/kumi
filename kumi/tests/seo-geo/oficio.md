# Oficio: test seo-geo

Cómo se comprueba, sin nadie delante, que una web sigue igual de bien o mejor para buscadores, para las IA que citan y para los agentes. La regla de esta cadena: **se compara la rama con `main`** (la rama base de la tarea), y **todo empeoramiento es un hallazgo**, aunque el encargo no hable de SEO. Lo que ya estaba mal en `main` y sigue igual no es hallazgo: apúntalo aparte como «heredado».

## Herramientas

- **verify-seo-for-agents**: `%USERPROFILE%\.verify-seo\bin\verify-seo.exe` (o `mcp__verify-seo-for-agents__scan_site` si la sesión lo tiene). Es la prueba principal de preparación para agentes. Escanea `localhost`.
- **claude-seo**: ruta en `registro.json` → `plugins` → `claude-seo@…` → `ruta`. Scripts con `"<ruta>/scripts/claude-seo" run <script>.py`. Contra el servidor local, cada orden lleva delante `CLAUDE_SEO_LOCAL_TARGETS=127.0.0.1:<puerto>`; no sigue redirecciones, así que pide las URL con su barra final. Lo que usa Playwright (`render_page.py`, capturas) no funciona contra local: eso queda para los prod tests.
- **humanizar** (opcional): `mcp__humanizar__humanizar` para los textos nuevos, si la sesión lo tiene.
- **Lighthouse**: `npx --yes lighthouse <url> --output=json --output-path=<fichero> --chrome-flags="--headless=new" --quiet` (Chrome está instalado).
- **Enlaces**: `npx --yes linkinator <url> --recurse --format json` (solo enlaces internos: `--skip` para lo que no sea `127.0.0.1`).

## 1. Dos builds: antes y después

- La rama base está en `tarea.json` de la tarea (la carpeta padre de la tuya), campo `base`.
- **Después** = la obra (el worktree de la rama). **Antes** = `git -C <obra> archive <base> | tar -x -C <tu carpeta>/antes`. Así no tocas el repo ni sus worktrees.
- En cada una: `npm ci` (si hay `package-lock.json`) y `npm run build`. Astro deja la web en `dist/`. HTML sin build: la raíz tal cual.
- Si el build de **después** falla: hallazgo `alta` y terminas. Si falla el de **antes**, sigue solo con el de después y dilo.
- Sirve cada una en su puerto libre: `npx astro preview --host 127.0.0.1 --port <p>` (Astro) o `python -m http.server <p> --bind 127.0.0.1` (estático). Apunta los PID para pararlos al final.

## 2. Qué se compara

Guarda cada salida en `evidencias/antes/` y `evidencias/despues/` con el mismo nombre. Lo determinista se mide una vez; lo que tiene ruido (Lighthouse, puntuaciones de los agentes) se mide **intercalado**, antes, después, antes, después, tres veces cada uno, y se compara la mediana. Nunca por bloques.

| Qué | Cómo | Empeora si… | Gravedad |
|---|---|---|---|
| Preparación para agentes | `verify-seo scan http://127.0.0.1:<p>/ --json --profile content` en los dos (`apiApp` si es una app o una API; el mismo perfil y los mismos `--checks` en los dos lados) | baja el nivel o `score.overall`, o un check pasa de `pass` a `fail` | `alta` si es `robotsTxt`, `sitemap`, `robotsTxtAiRules` o `contentSignals`; `media` el resto |
| Agentes, detalle | `agentic_check.py <url> --json` (claude-seo) | un check empeora (robots por bot de IA, Content-Signal, llms.txt, Markdown, `.well-known`, WebMCP, `http-404`) | `media` (`alta` si deja a los buscadores fuera) |
| URL de la web | lista de páginas HTML de `dist/` y URL de los sitemaps (`sitemap_discovery.py` o leyendo el XML) en los dos | desaparece una URL sin redirección; una URL nueva no está en el sitemap; el sitemap lista una página `noindex` o una redirección | `alta` si desaparece una URL; `media` el resto |
| Indexación por página | `parse_html.py <html> --url <url de producción> --json` sobre cada HTML de los dos builds | `noindex` nuevo, canonical que apunta a otra URL, title o description que faltan o se duplican, H1 que falta, hreflang sin vuelta o sin `x-default` | `alta` los tres primeros; `media` el resto |
| Deriva SEO de las páginas tocadas y la portada | `seo-drift`: sirve **antes** en el puerto P, `drift_baseline.py <url> --skip-cwv`; para, sirve **después** en el mismo P, `drift_compare.py <url> --skip-cwv --baseline-id <id>` | regla CRITICAL o WARNING | CRITICAL `alta`, WARNING `media`, INFO no cuenta |
| Datos estructurados | agente `claude-seo:seo-schema` sobre los HTML afectados, más el JSON-LD que extrae `parse_html.py` | JSON que no parsea, tipo retirado (`HowTo`, `FAQPage` para resultados enriquecidos), falta un campo obligatorio, o el schema dice algo que no está en la página | `media`; `alta` si hay datos inventados (valoraciones, reseñas, precios, fechas, autores que no salen en ningún sitio) |
| Enlaces internos | `linkinator` contra los dos servidores | aparece un enlace interno roto | `media`; `alta` si está en la portada o la navegación. Los que apuntan a otras webs del mismo dominio que no se sirven en local (otros repos publicados en subcarpetas) no cuentan si ya estaban en antes |
| Core Web Vitals (laboratorio) y categorías | Lighthouse en la portada y las páginas tocadas, intercalado | baja el rendimiento más de 5 puntos, sube el LCP más de un 20 %, el CLS pasa de 0,1, baja la categoría SEO o la de accesibilidad | `media` |
| Navegación de agentes (Lighthouse) | si la versión de Lighthouse trae la categoría de navegación agéntica, `lighthouse_agentic.py --from-json <informe>` | baja la fracción X/N (nunca la pases a porcentaje) | `media` |
| Técnico, GEO y contenido | agentes `claude-seo:seo-technical`, `claude-seo:seo-geo` y `claude-seo:seo-content` sobre los HTML de antes y de después de las páginas tocadas (dales las rutas de los ficheros; puntúan de 0 a 100) | la puntuación baja más de 10 puntos con un motivo concreto, o aparece un problema con evidencia (contenido que solo existe con JS, respuesta enterrada, fecha o autor que se pierden) | `media` |
| Textos nuevos | los textos que añade el diff (title, description, alt, llms.txt, párrafos), leídos con ojo de editor (o con la herramienta de humanizar de la instalación, si la hay) | avisos de relleno de IA, «no es solo X, es Y», rayas, muro de hashtags. «Poco concreto» en un texto corto no cuenta | `baja`; `alta` si para «concretar» se inventó una cifra o un dato |
| Diseño | `git diff <base>...HEAD --stat`: CSS, maquetación o componentes visuales | la factoría seo-geo tocó el diseño | `media` (fuera de su alcance). Que se vea bien es de la cadena `web` |
| Idiomas | lo nuevo en un idioma existe en todos los que tiene la web | falta en alguno | `media` |

Los checks que no se pueden pasar en el alojamiento (en GitHub Pages: `linkHeaders`, `markdownNegotiation`, cualquier cabecera propia) ni los que no aplican (OAuth, MCP, pagos en una web que no los tiene) no son hallazgo: van en el informe como «no aplica» con el motivo.

Si la tarea es una auditoría de toda la web, añade `claude-seo:seo-audit` sobre los dos lados y compara la puntuación global; en el resto de tareas no hace falta (recorre hasta 500 páginas y lanza muchos agentes).

## 3. Criterios del encargo

Cada criterio que pida la tarea (un schema concreto, un llms.txt, una description) se comprueba en el HTML o el fichero generado de **después**, con la salida como evidencia: cumple, no cumple o no comprobable.

## 4. Deja el entorno como estaba

Para los servidores que arrancaste por su PID (nunca `taskkill /IM node.exe`: puede haber otros de {humano}). Borra `antes/` y los `node_modules` que creaste en tu carpeta; no toques la obra salvo su `dist/`.

## Prod tests (contra la web publicada)

Tras desplegar, la central ejecuta las órdenes de `prod_tests` de la receta del proyecto (`{recetas}/<repo>.toml`). Se lanzan con `cmd` en Windows (sin `grep`: `findstr`), fallan con código distinto de 0, y GitHub Pages tarda un poco en servir lo nuevo, así que van con reintentos. Solo se prueba lo que la web tiene hoy: un check que nunca pasó no puede ser prod test.

Plantilla (cambia el dominio y el nivel mínimo, que es el que tiene la web hoy con el perfil `content`):

```toml
prod_tests = [
  'curl -fsS --retry 6 --retry-delay 20 --retry-all-errors -o NUL https://DOMINIO/',
  'curl -fsS --retry 6 --retry-delay 20 --retry-all-errors https://DOMINIO/robots.txt | findstr /i /c:"Sitemap:"',
  'curl -fsS --retry 6 --retry-delay 20 --retry-all-errors -o NUL https://DOMINIO/sitemap-index.xml',
  'curl -fsS --retry 6 --retry-delay 20 --retry-all-errors -o NUL https://DOMINIO/llms.txt',
  'curl -fsS https://DOMINIO/ | findstr /i "noindex" && exit 1 || exit 0',
  '"%USERPROFILE%\.verify-seo\bin\verify-seo.exe" scan https://DOMINIO --profile content --min-level N',
]
```

Fija `--min-level` con lo que la web ya tiene publicado (si aún no tiene robots ni sitemap, nivel 0 y solo la portada). El estado de cada web de esta instalación está en sus notas locales.

Lo que no cabe en una orden con código de salida (Lighthouse, accesibilidad para agentes con `agent_ux_check.py`, `render_page.py`) se pasa contra la URL publicada cuando una cadena de test o una tarea de SEO lo pida: ahí sí funcionan, porque no es local.
