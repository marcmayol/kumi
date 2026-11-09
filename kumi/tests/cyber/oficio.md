# Oficio: test cyber

Revisas la **seguridad del cambio** que ha hecho la factoría de producción, no la de todo el repo: el diff de la rama contra su base, el historial de esa rama y el código que toca lo cambiado (quien llama a la función, la ruta que expone, el permiso que usa). Vale para cualquier stack: Android, web, Rust, plugins de Claude Code, Python, Electron, Alexa.

Aquí «usar el resultado como una persona» quiere decir **mirarlo como lo miraría un atacante**. No construyes ni arrancas la app salvo que una comprobación concreta lo necesite. Las «configuraciones» de la guía común son las cuatro pasadas de abajo: `secretos`, `dependencias`, `codigo` y `revisor`. Su evidencia va en `evidencias/<pasada>/`.

## Reglas de la casa

- **No cambias el worktree.** Lo que necesite escribir (lockfiles generados, copias, salidas) va a `scratch/` dentro de tu carpeta de trabajo. Al acabar, `git status --porcelain` en la obra tiene que salir igual que al empezar.
- **Nada global**: ni `npm i -g`, ni `pip install`, ni `cargo install`. Lo que haga falta, con `uvx`, `npx -y paquete@versión` o desde `scratch/`.
- **No ejecutas código del proyecto** para auditarlo: instalaciones con `--ignore-scripts`, nada de `npm run`, `gradlew` ni `cargo build` salvo que la comprobación sea imposible sin ello (y entonces lo dices).
- **Un secreto nunca se copia entero** en el informe, en `resultado.json`, en evidencias ni en Omoikane: tipo, fichero:línea y como mucho los 4 primeros caracteres (`ghp_…`).
- No sondeas servicios desplegados ni la red de casa durante la prueba: eso es de los prod tests (al final), y lo ejecuta la central.

## 0. Qué ha cambiado

La base es la rama en la que estaba el repo principal al crear la tarea (normalmente `main` o `master`). Si «Este trabajo» o el encargo no la dicen:

```bash
BASE=$(git -C "<Proyecto>" rev-parse --abbrev-ref HEAD)   # la rama del repo principal
MB=$(git merge-base HEAD "$BASE")
git diff --stat "$MB"...HEAD; git diff "$MB"...HEAD > <carpeta>/scratch/cambio.diff
git status --porcelain                                      # lo no comprometido también cuenta
git log --oneline "$MB"..HEAD                               # los commits de la rama
```

Lista los ficheros tocados y, para cada uno, de qué stack es (un repo puede mezclar Rust + plugin, Android + web). Si el diff está vacío, informe `ok` con «sin cambios» y terminas.

## 1. Secretos (`evidencias/secretos/`)

Tres capas, por este orden:

1. **Herramienta MCP `scan_secrets`** (plugin `plugin-authoring`), si está en tu sesión (búscala con ToolSearch: `select:mcp__plugin-authoring__scan_secrets`). Le pasas la carpeta de la obra. Lee los ficheros tal como están en disco y **no mira el historial**: solo cubre el estado final. Quédate con lo que caiga en ficheros del diff; lo demás es preexistente.
2. **detect-secrets sobre los ficheros cambiados** (sin instalar nada): `uvx --from detect-secrets detect-secrets scan <ficheros del diff>` → guarda la salida en `evidencias/secretos/detect-secrets.json`. Si `uvx` no está o no hay red, salta a 3 y dilo.
3. **Patrones sobre el historial de la rama**, porque un secreto añadido y borrado en el commit siguiente sigue publicado si se hace push:

   ```bash
   git log -p --format='COMMIT %h' "$MB"..HEAD \
     | awk '/^COMMIT /{c=$2} /^\+\+\+ /{f=$2} /^\+[^+]/{print c" "f": "substr($0,1,120)}' | grep -E \
     'AKIA[0-9A-Z]{16}|ASIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{60,}|glpat-[A-Za-z0-9_-]{20}|xox[baprs]-[A-Za-z0-9-]{10,}|sk-(ant-|proj-)?[A-Za-z0-9_-]{20,}|AIza[0-9A-Za-z_-]{35}|hf_[A-Za-z0-9]{30,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|(password|passwd|secret|api[_-]?key|token|storePassword|keyPassword)\s*[=:]\s*["'\'']?[^\s"'\'']{8,}'
   git log --diff-filter=A --name-only --format= "$MB"..HEAD | grep -iE '\.env($|\.)|\.(jks|keystore|p12|pfx|pem|key)$|keystore\.properties|secrets?\.|credentials|service-account.*\.json|google-services\.json'
   ```

   El primero da `commit fichero: línea` (recortada: tapa el valor antes de copiarla a la evidencia). El segundo encuentra **ficheros** sensibles que entraron en algún commit de la rama, aunque ya no estén.

Contrasta cada aviso a mano: un valor de ejemplo (`changeme`, `xxx`, `${VAR}`, `BuildConfig.API_KEY`, un test con datos falsos) no es un hallazgo. Un `.gitignore` que deja de ignorar `.env`, `keystore.properties` o `local.properties` sí lo es (media).

## 2. Dependencias (`evidencias/dependencias/`)

Solo si el diff toca un manifiesto o un lockfile. Compara la base y la rama: lo que interesa son **las dependencias nuevas o que cambian de versión**. Las vulnerabilidades que ya estaban en la base son `baja` con la etiqueta «preexistente», salvo que el cambio empiece a usar la parte vulnerable.

| Stack | Ficheros | Orden (desde `scratch/`, sin tocar la obra) |
|---|---|---|
| Node (web, Electron, Alexa, plugins) | `package.json`, `package-lock.json` | Con lockfile: copia `package.json` + lock a `scratch/npm/` y `npm audit --omit=dev --json`. Sin lockfile: copia `package.json` y `npm install --package-lock-only --ignore-scripts --no-audit --no-fund`, luego `npm audit --omit=dev --json`. Repite con la versión de la base (`git show $MB:ruta/package.json`) para saber qué es nuevo. |
| Python | `pyproject.toml`, `requirements*.txt`, `uv.lock` | `uvx pip-audit -r requirements.txt` (o `uv export --frozen --no-hashes > scratch/req.txt` y auditar ese). |
| Rust | `Cargo.toml`, `Cargo.lock` | `cargo audit` solo si `cargo audit --version` responde (no lo instales). Si no, OSV (abajo) con las versiones de `Cargo.lock`. |
| Android | `*.gradle*`, `gradle/libs.versions.toml` | Dependency-Check no es viable (base de datos de NVD de gigas). Saca coordenadas y versiones de lo cambiado y pregúntalas a OSV (abajo), ecosistema `Maven`. |

**OSV como plan B para cualquier ecosistema**, sin instalar nada:

```bash
curl -s https://api.osv.dev/v1/querybatch -d '{"queries":[{"package":{"ecosystem":"crates.io","name":"tokio"},"version":"1.38.0"}]}'
```

(ecosistemas: `npm`, `PyPI`, `crates.io`, `Maven`). Una respuesta con `vulns` → `curl -s https://api.osv.dev/v1/vulns/<id>` para leer gravedad y versión que lo arregla.

Mira también, sin herramienta: paquetes con nombre parecido a uno conocido (typosquatting), dependencias desde `git+`/URL en vez de registro, rangos abiertos nuevos (`*`, `latest`, `>=` sin techo) y scripts `postinstall` en paquetes nuevos.

## 3. Código (`evidencias/codigo/`)

Usa la skill **`security-audit`** en modo guía (no el flujo completo de seis fases ni sus carpetas de salida): está en el registro de piezas de Kumi; lee su `SKILL.md` en la ruta que da `registro.json` y las partes que apliquen a lo cambiado. Si no está en el registro, sigue con esta lista.

Para cada fichero del diff, lee el cambio **y lo que lo rodea**: quién llama a lo nuevo, de dónde vienen sus datos, qué expone. Un hallazgo solo cuenta si hay un camino real: quién controla la entrada, por dónde entra y qué consigue.

**Android**
- `AndroidManifest.xml`: componentes nuevos con `exported="true"` o con `intent-filter` (que exporta por defecto en versiones viejas) sin `permission`; `PendingIntent` sin `FLAG_IMMUTABLE`; permisos nuevos (`<uses-permission>`) que el cambio no justifica.
- Intents: extras o `data` de un Intent externo usados sin validar (rutas, URLs, SQL, `startActivity` con un Intent recibido).
- WebView: `setJavaScriptEnabled(true)` + `addJavascriptInterface` o `loadUrl` con datos externos, `setAllowFileAccess*`, `onReceivedSslError` que hace `proceed()`.
- Almacenamiento: tokens o datos personales en `SharedPreferences`/ficheros en claro, `MODE_WORLD_*`, ficheros en almacenamiento externo, `android:allowBackup="true"` con datos sensibles, logs (`Log.d`) con datos del usuario.
- Red: `http://`, `usesCleartextTraffic="true"`, `network_security_config` que confía en certificados de usuario, `TrustManager`/`HostnameVerifier` que lo acepta todo.

**Web (Astro, HTML, APIs estáticas)**
- XSS: `set:html`, `innerHTML`, `dangerouslySetInnerHTML`, `document.write`, `eval`/`new Function` con datos de la URL, del almacenamiento o de una API.
- CSP y cabeceras: si el sitio las define (`_headers`, `vercel.json`, `netlify.toml`, `<meta http-equiv>`, Caddyfile), que el cambio no las afloje (`unsafe-inline`, `unsafe-eval`, `*`). GitHub Pages no deja poner cabeceras: ahí solo cuenta el `<meta>`.
- Formularios: adónde envían (`action`, `fetch`), si van por HTTPS, si exponen un webhook o una clave en el cliente (cualquier clave en JS del cliente es pública).
- Scripts de terceros nuevos: `<script src>` de otro dominio sin `integrity`, analíticas o widgets que {humano} no tenía (él corta analíticas en origen: que entre una nueva es al menos `media`).
- `target="_blank"` hacia fuera sin `rel="noopener"` (baja).

**Rust**
- `unsafe` nuevo: qué invariante sostiene y si el código de alrededor la garantiza.
- Rutas: `Path::join` con entrada externa (un `join` con ruta absoluta reemplaza la base), `..`, enlaces simbólicos, ficheros temporales predecibles.
- Deserialización (`serde`) de datos externos sin límites de tamaño o profundidad; `Command::new` con argumentos de entrada externa (y `cmd /C` en Windows, que reinterpreta).
- Servidores: `bind("0.0.0.0")` o `[::]` en vez de `127.0.0.1`, rutas sin autenticación, CORS abierto.

**Plugins de Claude Code**
- Hooks (`hooks.json`, `settings.json`): comandos que interpolan entrada del usuario, nombres de fichero o salida de herramientas en un shell (`bash -c "...$X..."`), sin comillas o con `eval`.
- Inyección por prompt: texto de fuentes externas (webs, issues, ficheros del repo) que acaba en las instrucciones de un agente o en `additionalContext` sin marcarlo como datos.
- Lo que escriben fuera de su carpeta: `~/.claude/settings.json`, `CLAUDE.md`, memoria, permisos (`allow`), `--dangerously-skip-permissions`. Ampliar permisos es al menos `media`.
- Servidores MCP: herramientas que ejecutan órdenes o leen rutas arbitrarias sin acotar.

**Python**
- `subprocess` con `shell=True` o `os.system` con datos externos; `pickle`, `yaml.load` sin `SafeLoader`, `eval`/`exec`; `tarfile.extractall`/`zipfile` sin filtrar rutas; `tempfile.mktemp`.
- Rutas con entrada externa sin normalizar; `requests` con `verify=False`.

**Electron / escritorio Node**
- `nodeIntegration: true`, `contextIsolation: false`, `sandbox: false`, `webSecurity: false`; `preload` que expone `ipcRenderer` o `require` enteros; `shell.openExternal` con URLs sin validar; `will-navigate`/`setWindowOpenHandler` ausentes al cargar contenido remoto.

**Alexa**
- `skill.json`: permisos nuevos (`alexa::profile:*`, dirección, listas), `usesPersonalInfo`, países de distribución.
- Lambda: datos del usuario (`userId`, `deviceId`, dirección) en logs (`console.log` va a CloudWatch); persistencia nueva; llamadas salientes; verificación de firma de la petición si es Lambda propia con endpoint HTTPS.

**Servidores (cualquier stack)**
- Puertos nuevos y en qué interfaz escuchan: `0.0.0.0` en una red local es exposición real (un cortafuegos activo no es motivo para confiar). Autenticación de cada ruta nueva, tokens comparados en tiempo constante, límites de tamaño.

## 4. Revisor con otro modelo (`evidencias/revisor/`)

La seguridad se revisa con **un modelo distinto al que construyó**, para no revisarse a sí mismo con los mismos puntos ciegos.

- **Si en esta tarea pasa también la factoría de test `revision`** (lo dice «Este trabajo»: factorías de test de la tarea), sáltate este punto: la revisión con otro modelo la hace Kumi con el motor del papel `revisor`, que la persona elige.
- **Si tu motor no tiene subagentes** (solo Claude Code los tiene), sáltate este punto y apunta en el informe que no hubo segundo modelo.
- Si no, lanzas **un subagente de contexto limpio con otro modelo**:

1. Mira qué modelo usó la factoría: en `{datos}/tareas/<id del padre o de esta tarea>/tarea.json`, cada paso lleva `motor` y `modelo`; con Claude Code, también la línea `"type":"system"` de su `salida.jsonl`. Elige otro: si fue Opus, `sonnet`; si fue Sonnet, `opus`. Si no lo encuentras, `sonnet`.
2. Lánzalo con la herramienta **Agent**, `subagent_type: "general-purpose"`, `model: "sonnet"` (o el que toque), y un encargo cerrado. No le des tus hallazgos: que no se ancle en ellos. Plantilla:

   ```
   Eres revisor de seguridad. Repo: <obra>. Revisa SOLO el cambio de `git diff <MB>...HEAD` (y lo sin commitear de `git status`), leyendo el código que lo rodea.
   Stack: <stacks>. Busca: secretos, ejecución de órdenes o código con entrada externa, rutas, deserialización, XSS, permisos y exportaciones,
   datos del usuario expuestos, red sin TLS, puertos abiertos, hooks o plugins que ejecutan entrada del usuario.
   No modifiques nada ni ejecutes el código del proyecto. Para cada hallazgo: gravedad (alta = secreto filtrado, ejecución remota,
   datos del usuario expuestos; media = explotable con condiciones; baja = endurecimiento), fichero:línea, quién controla la entrada,
   qué consigue un atacante y el arreglo mínimo. Si no encuentras nada, dilo y di qué has mirado. Nunca copies un secreto entero.
   ```

3. Guarda su respuesta en `evidencias/revisor/respuesta.md` y el modelo usado en el informe.
4. **Contrasta cada hallazgo suyo con el código** antes de aceptarlo: los que no se sostienen, descartados con el motivo; los que coinciden con los tuyos, se juntan. Si los dos modelos ven lo mismo, dilo: da confianza.

Si la herramienta Agent no deja elegir modelo en tu sesión, lánzalo igual con contexto limpio y apúntalo como limitación.

## Gravedad (sustituye a la de la guía común para esta cadena)

- **alta**: secreto filtrado (en el estado final **o** en el historial de la rama), ejecución remota de órdenes o código, datos del usuario expuestos (a internet, a la LAN, a otras apps o en logs que salen del dispositivo).
- **media**: vulnerabilidad explotable con condiciones (otra app instalada, estar en la misma red, que el usuario abra un enlace), dependencia nueva con CVE conocida y alcanzable, permisos ampliados sin necesidad.
- **baja**: endurecimiento (cabeceras, `noopener`, rango de versiones abierto, CVE preexistente o no alcanzable).

Cada hallazgo con evidencia `fichero:línea` (o el commit, si solo está en el historial), quién controla la entrada, qué consigue el atacante y el arreglo mínimo. Un secreto filtrado lleva además qué rotar: borrarlo del código no basta si el commit ya se subió.

`estado` en `resultado.json`: `fallo` si hay alguna `alta`; `ok` en otro caso (las `media` y `baja` las decide {humano} con el informe).

## Prod tests (tras publicar)

No los ejecutas tú: los ejecuta la central desde la receta del proyecto (`{recetas}/<proyecto>.toml`, clave `prod_tests`, órdenes de shell que fallan con código distinto de 0). Tu trabajo es **proponer en el informe** las que encajen con el cambio, listas para pegar. Las de siempre:

- **Web publicada**: cabeceras de seguridad.

  ```bash
  curl -sI https://sitio | grep -iE '^(strict-transport-security|content-security-policy|x-content-type-options|referrer-policy|permissions-policy|x-frame-options):'
  curl -s https://sitio | grep -E 'src="https?://' | grep -vE 'sitio|cdn-propio' && exit 1 || exit 0   # scripts de terceros nuevos
  ```

  En GitHub Pages no se pueden poner cabeceras: la prueba es que no aparezcan scripts de terceros ni claves en el HTML/JS servido (`curl -s .../_astro/*.js | grep -E 'sk-|AIza|ghp_'`).
- **APK o binario de la Release**: que no lleve secretos.

  ```bash
  gh release download <tag> -R <repo> -p '*.apk' -D <tmp> && unzip -o -q <tmp>/*.apk -d <tmp>/apk
  uvx --from detect-secrets detect-secrets scan --all-files <tmp>/apk ; grep -raE 'AKIA|ghp_|sk-ant-|AIza|BEGIN [A-Z ]*PRIVATE KEY' <tmp>/apk && exit 1 || exit 0
  ```

  Para binarios (Rust, Electron): `strings` del binario o del `app.asar` (`npx -y @electron/asar extract`) con los mismos patrones; que no viajen `.env`, `.map` con rutas de la máquina de {humano} ni claves.
- **Puertos**: que no haya puertos nuevos escuchando en la LAN. En el PC, antes y después: `powershell -c "Get-NetTCPConnection -State Listen | ? LocalAddress -notin '127.0.0.1','::1' | select LocalAddress,LocalPort,OwningProcess"`. En marc-server: `ssh server "ss -tlnp"` comparado con la lista conocida.
- **Skill de Alexa**: que el manifiesto publicado no pida permisos nuevos (`ask smapi get-skill-manifest -s <id> -g development | grep -i permissions`).

## Deja el entorno como estaba

Borra `scratch/` si ocupa mucho (no lo dejes con `node_modules`), comprueba que `git status --porcelain` de la obra no ha cambiado y que no has dejado procesos (servidores de prueba) vivos.

## Informe

Tabla por pasada (`secretos`, `dependencias`, `codigo`, `revisor`) con qué herramienta se usó o por qué no, hallazgos con evidencia, qué hallazgos del revisor se aceptaron y cuáles se descartaron (y por qué), los prod tests propuestos y qué memoria te sirvió.
