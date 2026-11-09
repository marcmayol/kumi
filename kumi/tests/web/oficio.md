# Oficio: test web

Cómo se prueba una web como la usaría una persona: construida igual que en producción, servida en local y recorrida con un navegador sin ventana en móvil y escritorio.

## Herramienta

`{kumi}/tests/web/navegar.py`, con Playwright para Python: `uv run --with playwright python {kumi}/tests/web/navegar.py ...`.

Por qué Python y no `npx playwright`: sirve igual para las webs estáticas, que no tienen Node; no toca el `package.json` ni el lockfile del repo (instalar Playwright en el repo es una dependencia nueva y nivel 2); los navegadores ya están en la caché de Playwright de la máquina; y el script lleva todas las comprobaciones, así que no hay que reescribirlas en cada prueba.

Qué hace:
- Levanta el servidor en un **puerto libre** y lo **para al acabar**, aunque falle (`--estatica <repo>` o `--astro <repo>` ya construido), o usa una URL dada (`--url`).
- Recorre las rutas de `--rutas` y descubre hasta `--rastrear` páginas propias más siguiendo enlaces.
- Cuatro configuraciones: `movil` (390x844), `movil-130` y `movil-150` (letra del sistema a 1,3 y 1,5) y `escritorio` (1440x900). Chrome en Android aplica la letra del sistema como zoom de página: a 1,5 la web ve 260 px de ancho CSS, y así se emula.
- En cada página: estado HTTP, errores de JavaScript y de consola, recursos que no cargan, imágenes rotas (baja la página despacio para que carguen las perezosas), scroll horizontal y elementos que se salen de la pantalla, texto que no cabe en su caja (`cortado`) separado del recortado a propósito con elipsis o `line-clamp`, imágenes sin `alt`, objetivos táctiles de menos de 24 px, `lang`, `h1`, `robots` y `canonical`.
- Comprueba los enlaces propios (y los de fuera con `--externos`; muchos sitios bloquean robots, así que un 403, 429 o 999 fuera es «no comprobable», no un fallo).
- **Nunca envía formularios de verdad**: los POST a otros dominios (el webhook de n8n) reciben un 200 falso y la analítica (Google Tag Manager y similares) se bloquea. Para probar un formulario, rellénalo y envíalo: el éxito que veas es el de la interfaz, no el del webhook.
- Deja en `--salida` `<config>/<nn>-<página>-pantalla.png` (lo que se ve al abrir), `-entera.png` (la página completa) e `informe.json`. Sale con código 1 si hay algo grave.

## Recorrido

1. **Construye** como en producción. Astro: `npm ci` y `npm run build` (si falla, hallazgo `alta` y terminas). Estática: nada que construir.
2. **Recorre**, con las rutas en `--rutas` **sin barra inicial** (`""` para la portada, `es/`, `blog/es/`): Git Bash convierte `/es/` en una ruta de Windows.
   - Astro: `uv run --with playwright python {kumi}/tests/web/navegar.py --astro . --rutas "" es/ <páginas del encargo> --salida <carpeta de trabajo>/evidencias`
   - Estática: lo mismo con `--estatica .`
   - Si la web se publica en una subcarpeta (`dominio/<repo>/`) y una ruta empieza por `/`, en local funcionará y en producción no: búscalo en el HTML que cambió (`src="/`, `href="/`) y, si lo hay, es hallazgo.
3. **Flujos del encargo** que no son solo mirar (un formulario, un filtro, un cambio de idioma o de tema): escribe un script corto en tu carpeta de trabajo con Playwright para Python, usa las mismas configuraciones y la misma interceptación que `navegar.py` (impórtalas: `sys.path.insert(0, r"{kumi}/tests/web")`; `from navegar import CONFIGS, ANALITICA`) y haz captura tras cada paso en `evidencias/<config>/`. Levanta el servidor con `navegar.py --astro/--estatica` en otra prueba o a mano en un puerto libre, apuntando el PID.
4. **Mira las capturas** (léelas como imagen), al menos las `-pantalla.png` de cada página en `movil-150` y `escritorio` y las `-entera.png` de lo que cambió. Busca lo que el informe no ve: texto encima de otro o sobre una foto que no deja leerlo, botones tapados, algo que solo se ve mal a 1,5, columnas descolocadas en escritorio, contraste pobre. Los avisos del informe (`cortado`, `desborda`) se confirman en la captura antes de darlos por hallazgo.
5. **Gravedad**: `alta` si una página no responde 200, hay un error de JavaScript, un enlace o recurso propio roto o un criterio del encargo no se cumple; `media` si algo no se lee o no se puede usar en alguna configuración (texto que se sale, scroll horizontal, botón inalcanzable); `baja` para imágenes sin `alt`, objetivos pequeños o detalles estéticos.

## Deja el entorno como estaba

- `navegar.py` para su servidor solo. Si levantaste uno a mano: `taskkill /PID <pid> /T /F` (con `/T`, que `uv` y `npm` arrancan procesos hijos) y comprueba que el puerto ya no escucha (`netstat -ano | grep :<puerto>`). No mates servidores que no arrancaste tú: hay previews huérfanos de otras sesiones.
- Las evidencias van en tu carpeta de trabajo, nunca en el repo. Al acabar, `git status` del worktree no debe enseñar nada nuevo fuera de lo ignorado (`dist/`, `.astro/` y `node_modules/` lo están).

## Prod tests

Comprobaciones contra la URL publicada después de desplegar. La central ejecuta cada línea de `prod_tests` de la receta del proyecto (en las recetas de esta instalación) desde la raíz del repo; si sale con código distinto de 0, abre una tarea de arreglo. Se ejecutan con `cmd`, no con Git Bash, así que ahí las rutas con barra inicial no se convierten, pero es mejor escribirlas igual sin ella.

```toml
prod_tests = [
  'uv run --with playwright python {kumi}/tests/web/navegar.py --url https://ejemplo.com/ --rutas es/ --configs movil escritorio --rastrear 5 --espera "Texto que tiene que estar" --reintentar 600 --salida %TEMP%\kumi-prod',
]
```

- `--espera` es el texto nuevo que demuestra que el despliegue llegó; con `--reintentar` lo pide cada 15 s, saltándose la caché, hasta ese tope de segundos. GitHub Pages tarda 1-2 minutos y su CDN cachea hasta 10, así que 600 es un tope razonable. Cuando la web se despliega con Actions la central ya espera al CI antes de los prod tests; cuando Pages publica la rama sin Actions no hay CI que esperar y `--reintentar` es lo único que cubre el retraso.
- Falla si la página no responde 200, si no aparece el texto, si hay errores de JavaScript o de consola, o si hay un enlace o recurso propio roto.
- En prod tampoco se envían formularios ni se cuentan visitas: la interceptación es la misma.
- Para comprobaciones que no son una página (un JSON de una API, `robots.txt`, el sitemap), una línea de `curl` vale: `curl -fsS https://ejemplo.com/api/v1/datos.json -o NUL` falla si no da 2xx. Para comprobar que una página sigue sin indexarse: `curl -fsS https://ejemplo.com/privada/ | findstr /C:"noindex"`.
