# Oficio: factoría de plugins de Claude Code

Lo que sabe esta factoría de los plugins y mods de Claude Code, valga para el plugin que valga. Lo propio de cada uno (qué hace, sus decisiones) está en el brain del proyecto.

Un plugin no rompe un proyecto: rompe **todas las sesiones** de quien lo tenga instalado, y la primera es la de {humano}. Un hook que tarda o que lanza una excepción se nota en cada mensaje.

## Qué hay en un plugin

- `.claude-plugin/plugin.json`: `name` (minúsculas, cifras y guiones; no puede empezar por `claude-` ni ser `claude` o `claude-code`), `version`, `description`, `author`, `homepage`, `repository`, `license`, `keywords`; `userConfig` si tiene opciones y `types` si guarda valores en `$.state`.
- `skills/<nombre>/SKILL.md`: el `description` del frontmatter decide cuándo se usa. Di qué hace y cuándo («Úsala cuando…»).
- `commands/`, `agents/`: Markdown con frontmatter.
- `hooks/hooks.json`: o hooks clásicos (`"hooks": {"PreToolUse": [...]}`) o un mod: `{"modules": ["./register.ts"]}`, un módulo TypeScript que corre dentro de Claude Code.
- `.mcp.json`: servidores MCP por stdio.
- `.claude-plugin/marketplace.json`: `name`, `owner` y `plugins[]` (`name`, `source`, `description`, `version`, `category`).

Los repos de plugins suelen ser de dos tipos: **un marketplace con varios plugins** en `plugins/<nombre>` (fuente relativa) y otros enlazados por `url` o `github` sin `version`; y **un repo, un plugin** con su propio `marketplace.json` y `"source": "./"`.

Nombres de herramientas: las que registra un mod son `mcp__<plugin>__<herramienta>`; las de un servidor de `.mcp.json`, `mcp__plugin_<plugin>_<servidor>__<herramienta>`. Pasar de uno a otro **renombra la herramienta** y rompe lo que la nombra: `--allowedTools` de scripts que lanzan sesiones, skills de otros plugins, permisos. Si cambia un nombre, búscalo en los repos de {humano} y avisa.

## Sin dependencias para quien instala

**Instalar y que funcione**. Nada de uv, Python, Node, npx, redistribuibles de Visual C++ ni un segundo comando manual, tampoco en Mac. «Sin dependencias» en el README no vale si no se ha comprobado sobre lo publicado.

Por orden de preferencia:

1. **Mod**: módulo TypeScript en `hooks/`. Lo ejecuta Claude Code; no necesita Node. Las herramientas se registran con `$.tool.register` en `session.start` y se responden en `on('tool.call', { tool })`. Un plugin en Python con `uv run` no arranca en un PC sin uv.
2. **Binario Rust que el mod baja en el primer uso** de la Release de GitHub de su misma versión, con SHA-256 comprobado. Estático: Windows con `+crt-static` (sin `VCRUNTIME140.dll` en la tabla de imports), Linux musl, Mac solo `libSystem`. Se comprueba en CI sobre el artefacto publicado.
3. Skills, comandos y agentes: texto, sin nada que instalar.

Un plugin que ya arranca su MCP con `uv run` o con Node es deuda: no añadas más así, y migrarlo es decisión de {humano} (va a la cola, no lo hagas por tu cuenta).

## Mods (módulo de hooks)

- La API es temprana y cambia entre versiones de Claude Code. La referencia es el `claude-code.d.ts` que escribe la skill `plugin-authoring` al cargarse (su ruta cambia con cada versión) o el `.claude-plugin/types/` que el motor deja junto al mod. Búscalo con grep, no lo copies a mano.
- Sin DOM y sin Node: todo pasa por `$`. Importaciones estáticas de ficheros del plugin; un `import()` dinámico impide que cargue.
- Un hook que lanza una excepción se salta en silencio (solo queda en `claude --debug`). Un mod «que no hace nada» casi siempre está ahí. Un hook que vigila (deniega herramientas o prompts) se escribe con `.catch(...)` para decidir qué pasa si falla.
- Una recarga ejecuta `register` y `session.start` otra vez: las variables del módulo empiezan de cero, `$.state` y `$.store` se conservan. El código nuevo tiene que entender los valores viejos.
- `classic.SessionStart` puede llegar antes que `session.start`: lo que preparan los dos se comparte en una sola promesa.
- Desde qué versión de Claude Code funciona va en el README (la 2.1.282 necesitaba `CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1`; la 2.1.310 ya los carga sola).

## Hooks: coste en cada evento

- `prompt.submit`, `classic.UserPromptSubmit`, `tool.call`, `ui.render` y `turn.*` corren en cada mensaje, herramienta o repintado. Tienen que responder en decenas de milisegundos. Lo lento va en segundo plano (`$.clock.after`, un proceso sin esperar) y avisa con `$.ui.toast` al acabar.
- `SessionEnd` tiene 1,5 s en total: pon `timeoutMs` (unos 1200).
- Un error del plugin nunca rompe la sesión: `try/catch` y devuelve lo de `next(e)`. Solo un hook que vigila deniega, y con motivo.
- Lo que escribe el plugin va a `$.store` o a `${CLAUDE_PLUGIN_DATA}`, nunca a `~/.claude` ni a la configuración del usuario. `${CLAUDE_PLUGIN_ROOT}` cambia con cada versión.

## Multiplataforma, Windows primero

- En un mod: `$.process.run([argv])`, sin shell. Windows se detecta con `$.env.get('OS') === 'Windows_NT'` y la carpeta personal es `USERPROFILE`.
- Windows trae lo justo: `%SystemRoot%\System32\tar.exe` (bsdtar, abre zip; el `tar` de Git que va antes en el PATH no), `curl.exe`, `certutil -hashfile <f> SHA256`, `cmd /c mkdir` con barras invertidas.
- `curl.exe` (Schannel) falla detrás de proxies que no dejan comprobar la revocación (`CRYPT_E_NO_REVOCATION_CHECK`): reintenta con `--ssl-revoke-best-effort` y, si no, con `Invoke-WebRequest`. El SHA-256 se comprueba igual.
- Hooks clásicos: en Windows el comando va por Git Bash, o por PowerShell si no hay Git Bash. Usa la forma `command` + `args` (sin shell) con `${CLAUDE_PLUGIN_ROOT}`; en forma de shell, el marcador entre comillas dobles. Nada de `jq`, `sed -i` ni utilidades que Windows no trae.
- Rutas de componentes con `/`: una ruta con `\` solo carga en Windows.

## Servidores MCP

- Por stdio. En stdout solo va JSON-RPC: un `print` o un log en stdout rompe el protocolo. Los logs, a stderr.
- `initialize` responde enseguida; lo pesado (modelos, descargas) se hace después.
- Un error de una herramienta vuelve como resultado con `isError`, sin tirar el proceso.
- Dependencias fijadas: `mcp` 2.x renombró FastMCP y rompió el arranque de los servidores que no fijaban `mcp<2`.

## Versionado

- `MAYOR.MENOR.PARCHE`: arreglos y retoques suben el parche (modo ajuste); una funcionalidad nueva, el menor (modo ASDD); un cambio en cómo se usa, el mayor.
- **Excepción a «no subas versión»**: en esta factoría la subes tú, en la rama, porque la central solo sabe tocar un fichero y aquí van varios a la vez: `version` de `plugin.json` y la de su entrada en `marketplace.json` (si la tiene), con el mismo número. En repos con binario también `Cargo.toml` y los instaladores (una CI puede comparar el tag con todos). Compruébalo con `claude plugin tag --dry-run <carpeta del plugin>`, que valida que `plugin.json` y el marketplace coinciden.
- Si `plugin.json` fija la versión y no la subes, nadie recibe el cambio: `claude plugin update` contesta que ya está en la última.
- Un mod que baja el binario de la Release `v<versión>` no puede publicarse antes de que esa Release exista: la primera sesión de todos los usuarios fallaría al instalar. Primero el tag y la Release (su CI), después el `plugin.json` que la nombra.

## Textos

- Plugins públicos: README.md en inglés (el principal) y README.es.md. Lo que lee Claude (descripciones de herramientas, skills) va en inglés salvo que el plugin sea para un idioma concreto.
- Textos naturales, que no suenen a IA: sin rayas como inciso, sin «revolutionize» ni «landscape».
- El README dice cómo se instala con una línea (`/plugin install <plugin> --marketplace <dueño>/<repo>`) y nada más que hacer después.

## Comprobar antes de entregar

- `claude plugin validate <carpeta>` en cada plugin tocado y en la raíz del marketplace. Los avisos que ya salían antes del cambio (sin icono, nombre parecido a uno de Anthropic) no bloquean; uno nuevo, sí se mira.
- Mods con `*.test.ts`: `claude plugin test <carpeta>`. Si hay `tsconfig.json`: `tsc -p <carpeta>`.
- Python heredado: sus tests (`uv run pytest`). Rust: lo que diga su factoría.
- Cargarlo en una sesión de verdad sin instalarlo: la cadena de test `plugin-claude` explica cómo. Tú puedes usar lo mismo para autocomprobar.
- **Nunca** `claude plugin install`, `update`, `marketplace add` ni `enable` contra la configuración real de {humano}, ni edites `~/.claude` o `~/.claude.json`. Si hace falta instalar, con `CLAUDE_CONFIG_DIR` temporal.

## Trampas

- Un marketplace añadido **como carpeta local** carga sus plugins de fuente relativa desde la carpeta tal cual: el merge en el repo principal **es** el despliegue en esa máquina, sin subir versión. Por eso la factoría trabaja en su worktree y no toca la copia principal.
- `--plugin-dir` con el mismo nombre que un plugin instalado lo sustituye sin avisar (solo lo dice `--debug`). Dos plugins instalados con el mismo nombre: solo cargan los hooks de uno.
- `npm install` en una carpeta sin `package.json` sube por las carpetas padre hasta encontrar uno (a veces el de la carpeta personal) y lo cambia. Crea antes `{"private": true}` y comprueba `npm prefix`. `node --test` en la carpeta personal recoge tests de toda ella.
- Compila fuera del worktree (`CARGO_TARGET_DIR={compilacion}\<proyecto>`) y borra al acabar lo temporal (configuraciones de prueba, `target/`, carpetas temporales).
