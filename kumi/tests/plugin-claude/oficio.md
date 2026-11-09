# Oficio: test de plugins de Claude Code

Cómo se prueba un plugin o mod como lo viviría quien lo instala: que valide, que cargue en una sesión de verdad, que sus skills, hooks y herramientas hagan lo que dicen, y que no rompa ni ralentice una sesión normal.

**Regla de oro: nunca se toca la instalación de {humano}.** Nada de `claude plugin install`, `update`, `enable`, `disable` ni `marketplace add` sin `CLAUDE_CONFIG_DIR` temporal, y nunca se edita `~/.claude`, `~/.claude.json` ni sus settings. Antes de empezar apunta fecha y tamaño de `~/.claude/settings.json`, `~/.claude/plugins/installed_plugins.json` y `~/.claude/plugins/known_marketplaces.json`; al acabar tienen que seguir igual (si no, hallazgo `alta`).

Todo lo temporal va a una carpeta `kumi-test-<tarea>` en la carpeta temporal del sistema, con `cfg`, `casa` y `ws` dentro.

## Tres formas de abrir una sesión con el plugin

Una configuración nueva no tiene login. Elige según lo que haya:

**A. Aislada del todo** (si hay `CLAUDE_CODE_OAUTH_TOKEN` o `ANTHROPIC_API_KEY` en el entorno). Es la buena: nada del plugin cae en la máquina de {humano}.

```bash
T=/d/kumi-test-<tarea>
export CLAUDE_CONFIG_DIR="$T/cfg" CLAUDE_CODE_PLUGIN_CACHE_DIR="$T/cfg/plugins" HOME="$T/casa" USERPROFILE="$T/casa" \
       DISABLE_AUTOUPDATER=1 CLAUDE_CODE_DISABLE_OFFICIAL_MARKETPLACE_AUTOINSTALL=1
unset CLAUDE_CODE_PLUGIN_DIRS CLAUDE_CODE_PLUGIN_SEED_DIR
cd "$T/ws" && claude plugin marketplace add <obra> && claude plugin install <plugin>@<marketplace>
claude -p "<prompt>" --output-format stream-json --verbose
```

Instalar desde la obra como marketplace local prueba también el camino de instalación. Pon las variables en la misma orden que lanza `claude`: algunas sesiones limpian `CLAUDE_CONFIG_DIR` del entorno de los subprocesos.

**B. Con el login de {humano}, sin sus ajustes** (si no hay credencial aparte). Lee su configuración pero no carga sus plugins, hooks ni MCP:

```bash
cd "$T/ws" && claude -p "<prompt>" --setting-sources project --strict-mcp-config \
  --plugin-dir "<obra>/<carpeta del plugin>" --output-format stream-json --verbose
```

Comprobado el 7-nov-2026: carga solo el plugin (`<nombre>@inline`) y los integrados de Claude Code. Deja la conversación en `~/.claude/projects` y lo que el plugin guarde en `$.store` o en la carpeta personal de {humano}. Por eso, en plugins que escriben fuera (Omoikane en su base y en `~/.omoikane`, Aloud y verify-seo con su binario) usa sus variables para apuntar a otro sitio (`OMOIKANE_BIN`, `VERIFY_SEO_BIN`, `HUMANIZAR_NO_LOG=1`…) o, si no las hay, deja esa parte como **no comprobable** y dilo.

**C. `claude plugin eval`** (si el plugin trae `evals/`). Crea su propia casa y configuración temporales con la credencial de {humano}. Gasta modelo: `claude plugin eval <carpeta> --trust-plugin --no-publish --ablation none --runs 1 --json <T>/eval.json`. Sin `--no-publish` sube el informe a claude.ai. No uses `--scaffold`, `--mocks off` ni `--allow-real-servers` salvo que el encargo lo pida.

## 1. Estático

- Cada `plugin.json`, `marketplace.json`, `hooks.json` y `.mcp.json` es JSON válido.
- `claude plugin validate <carpeta>` en cada plugin tocado y en la raíz del marketplace: código 0. Avisos ya conocidos (sin icono; «reads as one of Anthropic's own» del meter) no cuentan; uno nuevo es `baja`.
- `claude plugin tag --dry-run <carpeta del plugin>`: `plugin.json` y su entrada del marketplace coinciden. Si el plugin cambió respecto a la base y la versión no, hallazgo `alta` (nadie recibiría el cambio). Arreglo, parche; funcionalidad, menor.
- **Sin dependencias para quien instala**: ningún `.mcp.json`, hook ni `$.process.run` nuevo que llame a `uv`, `python`, `node`, `npx`, `bun` o `pip` (`alta`). En binarios Rust de Windows, ni rastro de `VCRUNTIME140.dll` en el `.exe` publicado.
- Rutas de componentes y comandos de hooks: sin `\` (solo cargarían en Windows) y con `${CLAUDE_PLUGIN_ROOT}` entre comillas en forma de shell.

## 2. Tests del propio plugin

- Mods con `*.test.ts`: `claude plugin test <carpeta>`. Con `tsconfig.json`: `tsc -p <carpeta>`.
- Python heredado: `uv run pytest` en el repo. Rust: la cadena `rust`.
- Después, `git status` del repo sin ficheros nuevos.

## 3. Servidor MCP por stdio

Arráncalo como lo hace `.mcp.json` (mismo comando, `CLAUDE_PLUGIN_ROOT` apuntando a la carpeta) y mándale tres líneas por stdin:

```
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"kumi","version":"0"}}}
{"jsonrpc":"2.0","method":"notifications/initialized"}
{"jsonrpc":"2.0","id":2,"method":"tools/list"}
```

Cada línea de stdout tiene que ser JSON (un log en stdout es `alta`); `initialize` en menos de 5 s; `tools/list` con las herramientas que el encargo espera y su `inputSchema`. Si hay una herramienta de solo lectura, llámala con `tools/call`. Al cerrar stdin el proceso tiene que terminar: si no, mátalo por PID, nunca por nombre (`taskkill /IM python.exe` mataría los de {humano}).

Las herramientas de un mod (`$.tool.register`) no tienen proceso propio: se comprueban en la sesión.

## 4. En una sesión de verdad

Con A, B o C. Del evento `init` del stream:

- `plugins`: el plugin está, con la versión de la obra.
- `mcp_servers`: los suyos en `connected`.
- `tools`: sus herramientas (`mcp__<plugin>__<h>` si es un mod, `mcp__plugin_<plugin>_<servidor>__<h>` si viene de `.mcp.json`).
- `slash_commands`: sus comandos y skills (`<plugin>:<skill>`).

Después, un prompt por cada cosa del encargo: una petición con las palabras de una persona que debería disparar la skill (mira en el stream el `tool_use` de `Skill` o de su herramienta), un comando, una llamada a herramienta. Para los hooks, un prompt que provoque su evento y la comprobación de su efecto; con `--debug-file <T>/debug.log` busca líneas `<plugin>: … hook skipped` o `module … did not load`. Para dar permiso a una herramienta concreta sin abrir más: `--allowedTools "mcp__…"`.

## 5. No rompe ni ralentiza

- Sesión normal con el plugin: «Responde solo OK» y otra que use una herramienta (`--allowedTools "Bash(git --version)"`, «ejecuta git --version y dime la versión»). Si alguna falla o el plugin deniega algo que no le toca: `alta`.
- Tiempo: la misma orden con el plugin y sin él (sin `--plugin-dir`), **intercalado** A, B, A, B, tres parejas, `duration_ms` del evento `result`. Si la mediana con el plugin pasa de 1,5 s más o un 30 % más: `media`. Apunta las seis cifras en el informe.
- Mac y Linux no los hay aquí. Si el repo tiene CI con esos sistemas, mira su última ejecución (`gh run list`, solo lectura) y cítala; si no, dilo como no comprobable.

## Prod tests (tras publicar)

```bash
uv run --no-project python {kumi}/tests/plugin-claude/prod_test.py <repo> --sesion
# repos sin marketplace propio (los publica otro repo marketplace):
uv run --no-project python {kumi}/tests/plugin-claude/prod_test.py <repo> --marketplace <dueño>/<repo-marketplace> --sesion
```

Añade el marketplace remoto (GitHub) a una configuración temporal, instala sus plugins de fuente relativa (o el del repo), comprueba que la versión instalada es la del repo y que el manifiesto instalado valida y, con credencial aparte, abre una sesión aislada (forma A) en una casa vacía. En un mod que baja su binario, esa sesión es la prueba de que el usuario nuevo lo recibe de la Release. Sale con 1 si algo falla y comprueba que la configuración de {humano} no ha cambiado. Si el encargo pide más (una skill, una herramienta), repítelo con la forma A sobre la copia instalada.

Ojo: si en la máquina de {humano} el marketplace está añadido como carpeta local, su sesión ya usaba el código desde el merge. El prod test es lo que ve **otra** persona.

## Dejarlo como estaba

- Borra tu `kumi-test-<tarea>` y cualquier `kumi-prod-*` que quede tuyo en la carpeta temporal. Los ficheros de git son de solo lectura en Windows: quítales el atributo antes de borrar.
- Mata por PID los servidores MCP o procesos que arrancaste.
- Comprueba las tres fechas y tamaños del principio. `git status` del repo limpio.
