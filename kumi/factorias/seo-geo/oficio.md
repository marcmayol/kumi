# Oficio: factoría seo-geo

Lo que sabe esta factoría de SEO, GEO y preparación para agentes, valga para la web que valga. Lo propio de cada web (dominio, idiomas, política de uso por IA, qué secciones tiene) va en el brain del proyecto.

Entra de dos maneras: **encadenada** detrás de otra factoría (`--ruta web,seo-geo`: la web ya hizo su cambio y tú lo dejas bien posicionado) o **sola** cuando la tarea es de SEO (`--ruta seo-geo`). Encadenada, tu alcance es lo que cambió la factoría anterior y lo que eso afecta (sitemap, llms.txt, enlaces internos); no audites ni retoques la web entera si no lo pide la tarea.

## Herramientas

- **claude-seo** (plugin): la ruta está en `registro.json` → `plugins` → `claude-seo@…` → `ruta`. Sus scripts se lanzan con `"<ruta>/scripts/claude-seo" run <script>.py …`. Sus skills se invocan con Skill (`claude-seo:seo-schema`…) y sus agentes con Agent (`claude-seo:seo-schema`…).
- **verify-seo-for-agents** (escáner local de preparación para agentes, si está instalado): `%USERPROFILE%\.verify-seo\bin\verify-seo.exe`. Si la sesión tiene sus herramientas MCP, `mcp__verify-seo-for-agents__scan_site` y `mcp__verify-seo-for-agents__get_fix_guide` hacen lo mismo. Sin MCP: `verify-seo scan <url> [--json|--agent] [--profile content|apiApp|all]` y `verify-seo guide <check>` (la guía de cada check, con ejemplo que pasa).

Los scripts de claude-seo rechazan `localhost` y `127.0.0.1` salvo que lo nombres: `CLAUDE_SEO_LOCAL_TARGETS=127.0.0.1:<puerto>` en la misma orden. Solo vale para la URL de arriba (una redirección no la sigue): pide las URL ya con su barra final.

## Construir y ver en local

- Astro: `npm ci` (si hay `package-lock.json`), `npm run build` → `dist/`; servir con `npx astro preview --host 127.0.0.1 --port <libre>`.
- HTML estático sin build: `python -m http.server <puerto> --bind 127.0.0.1` en la raíz.
- Para lo que solo mira el HTML generado no hace falta servidor: `"<ruta>/scripts/claude-seo" run parse_html.py dist/<página>/index.html --url https://<dominio>/<página>/ --json`.
- Al acabar, para el servidor que arrancaste (por su PID, no por nombre).

## Primero, diagnóstico

Antes del primer cambio, escanea lo que hay (build + preview) y guarda las salidas en tu carpeta: es tu referencia y la de la cadena de test.

1. `verify-seo scan http://127.0.0.1:<puerto>/ --json --profile content` (perfil `apiApp` si la web es una API o una app). Apunta nivel y `score.overall`. Usa siempre el mismo perfil y los mismos `--checks`: con otros el nivel cambia y no se puede comparar.
2. `agentic_check.py <url> --json` de claude-seo (robots por bot de IA, Content-Signal, llms.txt, Markdown, `.well-known`, WebMCP).
3. Lo que toque la tarea: `parse_html.py` de las páginas afectadas (title, description, canonical, robots, hreflang, JSON-LD, imágenes).

## Qué hace y con qué pieza

| Trabajo | Skill (y agente) de claude-seo | Script / guía |
|---|---|---|
| Title, meta description, Open Graph, Twitter, canonical | `seo-page` | `metadata_template.py --title … --description … --json` (longitudes y truncado) |
| Datos estructurados JSON-LD | `seo-schema` (agente `seo-schema`) | `parse_html.py` para leer lo que hay; plantillas en `<ruta>/schema/templates.json` |
| Sitemap | `seo-sitemap` | `sitemap_discovery.py`; en Astro, `@astrojs/sitemap` (`filter`, `serialize`, `customPages`) |
| robots.txt, bots de IA, Content-Signal | `seo-agentic` | `agentic_fix.py robots <url> --signal "…"` (solo añade Content-Signal, no toca Allow/Disallow); `verify-seo guide robotsTxtAiRules`, `contentSignals` |
| llms.txt (y llms-full.txt) | `seo-agentic` | `agentic_fix.py llms <url>`; `verify-seo guide llmsTxt`. Google no lo usa: es para agentes, no para posicionar |
| Markdown para agentes | `seo-agentic` | `verify-seo guide markdownNegotiation`; en Pages, páginas `.md` hermanas (endpoints `*.md.ts` en Astro) y `<link rel="alternate" type="text/markdown">` |
| `.well-known` (api-catalog, agent-skills, MCP Server Card…) | `seo-agentic` | `verify-seo guide <check>` o `get_fix_guide`; `agentic_fix.py ai-catalog` |
| WebMCP | `seo-agentic` | `agentic_fix.py webmcp <url> --json`; solo si hay formularios reales |
| Contenido citable por IA (respuesta directa, definiciones, tablas, fechas, autor) | `seo-geo` (agente `seo-geo`) y `seo-content` (agente `seo-content`) | `content_quality.py`, `content_verify.py` (afirmaciones sin fuente) |
| Enlaces internos | `seo-content` (enlazado interno); `seo-cluster` solo si la tarea pide planificar temas | |
| Imágenes (alt, width/height, lazy, `fetchpriority`, formato) | `seo-images` | `parse_html.py` (clasifica la carga diferida) |
| hreflang (si la web tiene más de un idioma) | `seo-hreflang` | en Astro, `i18n` del sitemap y `<link rel="alternate" hreflang>` en el head |
| Rastreo, indexación, renderizado sin JS | `seo-technical` (agente `seo-technical`) | `agentic_check.py` (`server-rendered`) |

No usar en producción: `seo-audit` y `seo-drift` (son de la cadena de test), `seo-backlinks`, `seo-dataforseo`, `seo-maps`, `seo-google` (piden credenciales o datos de pago), `seo-image-gen` (genera imágenes: aquí no se inventa nada), `seo-local`, `seo-ecommerce`, `seo-programmatic` y `seo-competitor-pages` salvo que la tarea lo pida expresamente.

## Lo que sabe del stack

- **GitHub Pages**: no hay cabeceras propias ni negociación por `Accept`, ni redirecciones de servidor. Por eso fallan siempre `linkHeaders`, `markdownNegotiation` y similares de verify-seo: no los persigas, apúntalos como «no se puede en Pages». Las redirecciones son páginas que genera Astro (`redirects` en `astro.config`, meta refresh + canonical). El 404 es `404.html`.
- **Astro**: `site` en `astro.config` manda en canonical, sitemap y URL absolutas. Los metadatos viven en un componente del head (`BaseHead.astro`, `Base.astro`): cámbialos ahí, no página a página. El JSON-LD se pinta en el HTML del build, nunca con JS en el cliente. `llms.txt` y las versiones `.md` pueden ser endpoints (`llms.txt.ts`, `[slug].md.ts`) para que no se queden atrás.
- **Varios idiomas**: cada texto nuevo (title, description, alt, llms.txt si es por idioma) va en todos los idiomas que tenga la web, con su hreflang de ida y vuelta y `x-default`.
- **Content-Signal**: la política (`search`, `ai-input`, `ai-train`) es de {humano}, no tuya. Respeta la que ya tenga el robots.txt. En una web sin ella, el precedente de sus webs es `search=yes, ai-input=yes, ai-train=yes`: úsalo y regístralo como decisión `review-after`.
- **Schema**: nada de `HowTo` (retirado) ni `FAQPage` buscando resultados enriquecidos (retirados en mayo de 2026). Tipos útiles aquí: `WebSite`, `Person`/`ProfilePage`, `Organization`, `BlogPosting`, `SoftwareApplication`/`MobileApplication`, `BreadcrumbList`.

## Reglas

- **No inventar datos.** Ni valoraciones, reseñas, precios, cifras, fechas, autores, premios ni perfiles `sameAs`. Todo lo que pongas en el schema o en un texto tiene que estar ya en la web, en el repo o en el brain. Si falta un dato, no lo rellenes: omite el campo o déjalo como decisión `review-after`.
- **El schema describe lo que se ve.** Nada en JSON-LD que no esté en la página.
- **Sin relleno.** Nada de párrafos «para SEO», listas de palabras clave ni FAQ que nadie pidió. Un texto citable es corto, concreto y responde a algo.
- **Textos naturales.** Todo texto nuevo o reescrito (titles, descriptions, alt, llms.txt, párrafos) sin relleno, sin «no es solo X, es Y» ni rayas como inciso. Si la sesión tiene una herramienta MCP de humanizar (`mcp__humanizar__humanizar`), pásalos por ella, publica el `texto` que devuelve y reescribe lo que salga en `avisos`. Un aviso de «poco concreto» en un texto corto como una description no te obliga a añadir cifras: si la cifra no está en la fuente, no la pongas.
- **No tocar el diseño.** Ni CSS, ni maquetación, ni componentes visuales, ni textos visibles que no pida la tarea. Tu terreno es el head, los atributos (alt, width/height, loading, hreflang), los ficheros públicos (robots, sitemap, llms.txt, `.well-known`), los endpoints y el JSON-LD. Si una mejora de SEO pide cambiar el diseño (un LCP lento por una imagen enorme del héroe, un texto sin H1), apúntala como decisión `review-after` y no la hagas.
- **No cambiar URLs.** Ni slugs, ni `trailingSlash`, ni `site`/`base`. Si la tarea lo exige: redirección desde la URL vieja, sitemap al día y decisión `blocking`.
- **No cerrar la puerta.** Nunca añadas `Disallow`, `noindex` ni cambies Content-Signal sin que la tarea lo diga.
- **Solo lo que existe.** No declares en `.well-known` un servidor MCP, OAuth, una API ni un protocolo de pago que la web no tenga, aunque verify-seo lo marque en rojo. Si no aplica, dilo en el informe.
- **Sin promesas.** Ni en el informe ni en los textos: nadie garantiza posiciones ni citas.

## Autocomprobación y entrega

- Build sin errores y el mismo escaneo del diagnóstico (mismo perfil): el nivel y `score.overall` de verify-seo no bajan, y ningún check que pasaba pasa a fallar.
- JSON-LD que se parsea (`parse_html.py` lo extrae sin errores) y con los campos obligatorios de su tipo según `seo-schema`.
- Deja en `ajuste.md` (o `para_test.md`): las URL afectadas, qué cambió en cada una (metadatos, schema, ficheros públicos), las salidas del escaneo de antes y después, y los checks que no aplican en esta web y por qué.
