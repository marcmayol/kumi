# Oficio: fábrica web

Lo que sabe esta fábrica de webs Astro y webs estáticas, valga para el proyecto que valga. Lo propio de cada proyecto va en su brain, en su receta y en las notas locales de esta instalación; aquí solo van las trampas que ya han mordido.

## Qué variante es

- Hay `astro.config.*`: **Astro**. Si no, **estática**: HTML, CSS y JS servidos tal cual (`index.html` en la raíz).
- Si el push a la rama principal despliega (GitHub Pages con Actions o publicando la rama, u otro hosting conectado al repo), nunca hagas push ni merge: eso lo hace la central después de su puerta de riesgo.
- La rama principal no siempre es `main`: mira la rama base de la tarea.

## Construir y previsualizar

**Astro**
- `npm ci`, que respeta el lockfile. No uses `npm install`: reescribe `package-lock.json`, y eso es nivel 2.
- `npm run build` y luego `npm run preview -- --host 127.0.0.1 --port <libre>`. Lo que se da por bueno es `dist/` servido con `preview`, que es lo que se publica; `npm run dev` compila al vuelo y no genera el sitemap ni demuestra que el build pasa.
- `dist/`, `.astro/` y `node_modules/` no se suben.
- No subas Astro ni añadas dependencias sin encargo. Mira la versión de Astro y la de Node que pide el proyecto (`engines`, el workflow de CI): lo que vale en una versión puede no valer en otra.
- Si el build falla con un mensaje propio del proyecto (un script que comprueba rutas, idiomas o enlaces), suele ser a propósito: arregla lo que pide, no el script.

**Estática**
- `python -m http.server <libre> --bind 127.0.0.1 --directory <repo>` y abre `http://127.0.0.1:<puerto>/`.
- Si la web se publica en una subcarpeta (`dominio/<repo>/`), en local se sirve en la raíz: las rutas relativas funcionan en los dos sitios; una ruta que empieza por `/` funciona en local y se rompe en producción. Usa siempre rutas relativas.

**Comprobación rápida en pantalla.** Un cambio visual no está hecho hasta verlo. Antes de entregar, haz una pasada con el recorrido de la cadena de test (`{kumi}/tests/web/navegar.py`, ver su oficio) sobre las páginas que tocaste y mira las capturas de móvil y escritorio. El recorrido completo lo hace la cadena de test; tú solo compruebas que lo tuyo se ve bien.

**Para el servidor al acabar.** Puede haber previews huérfanos de sesiones anteriores ocupando puertos. `navegar.py` levanta y para el suyo; si levantas uno a mano, apunta el PID y páralo (`taskkill /PID <pid> /T /F` en Windows, `kill <pid>` en el resto).

## Reglas comunes

- **Sistema de diseño**: si el repo tiene un `design.md` (o similar) en la raíz, léelo antes de tocar la interfaz y no cambies colores, tipografía ni acento sin encargo.
- **Accesibilidad**: `lang` en `<html>`, un solo `h1`, `alt` en cada imagen (vacío si es decorativa), etiquetas en los campos, foco visible, contraste AA, objetivos táctiles de 44 px en móvil (nunca por debajo de 24), `prefers-reduced-motion`. Que se pueda leer con la letra del móvil a 1,3 y 1,5.
- **Rendimiento**: que no baje la nota de Lighthouse móvil que ya tiene. Imágenes en `.webp` o `.avif` con `width` y `height`, poco JS y nada que bloquee el pintado.
- **Textos naturales**, en el idioma de la web. Si la web es bilingüe, cada texto nuevo va en los dos idiomas.
- **No guardes texto de interfaz como dato**: los textos van en el diccionario de idiomas del proyecto, no escritos a mano en los componentes.

## SEO que no se puede romper

- No cambies `site`, `base`, `trailingSlash`, el i18n ni los filtros del sitemap de `astro.config.*` sin encargo: cambian todas las URL de golpe.
- Si renombras una URL, la vieja va a las redirecciones del proyecto (`redirects.json`, `_redirects` o su equivalente). Slugs nuevos en ASCII y minúsculas.
- Cada página con su `canonical` y su pareja de idioma (`hreflang`).
- `robots.txt` y los `noindex` deciden qué se indexa. Un `noindex` que ya estaba suele estar a propósito: **no lo quites sin encargo**. Ojo: la puerta de riesgo ve lo que añades, no lo que borras.
- Si el dominio lo comparten varias webs (una principal y otros repos en subcarpetas), no crees páginas en las rutas de los otros: chocarían.
