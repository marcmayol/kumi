# Oficio: test Rust

Cómo se prueba un proyecto Rust como lo usaría una persona: compilado como lo compila la CI, y el binario de verdad ejecutado con sus órdenes principales en un entorno aislado, sin tocar los datos ni los programas instalados de {humano}.

## Preparar

- **`CARGO_TARGET_DIR`**: el mismo que usó la factoría, `{compilacion}\<proyecto>` (`<proyecto>` = carpeta del proyecto en minúsculas), o el que diga su `ajuste.md` / `para_test.md`. Así no se recompilan las dependencias. Nunca compiles dentro del worktree.
- Antes de compilar, mira el espacio libre de ese disco (`df -h`). Con menos de 15 GB libres, borra los `{compilacion}\*` de tareas ya cerradas; si aun así no llega, `bloqueado` con el motivo.
- `CARGO_BUILD_JOBS` a unos tres cuartos de los núcleos, siempre `--locked`, el perfil de la CI del repo y timeout largo.
- Una carpeta temporal para todo: `PRUEBA=<tu carpeta de trabajo>/sandbox`. Ahí van la base, los datos, las configuraciones de mentira y las copias de los binarios.
- Anota cuántas entradas hay en `%TEMP%` (`ls "$TEMP" | wc -l`) para ver al final si los tests dejan basura.

## 1. Lo que corre la CI

Copia las órdenes de `.github/workflows/` del repo y pásalas tal cual (sin los pasos de publicar): normalmente `cargo build --release --locked --all-targets`, `cargo clippy --locked --all-targets -- -D warnings` si está, `cargo test --locked` (con `--workspace` si es un workspace), y lo propio del proyecto (scripts de `tools/`, ficheros dorados). Si no hay CI, `cargo build`, `cargo clippy --all-targets` (los avisos nuevos frente a la base son hallazgo `baja`) y `cargo test`.

- Tests que se saltan solos por falta de algo (un modelo, un binario viejo para comparar): dilo en el informe como no comprobado. Si el cambio toca el esquema, la sincronización o la exportación y hay un test de compatibilidad que necesita una versión publicada, bájala de su Release a `$PRUEBA`, comprueba su SHA-256 y pásasela.
- Si el cambio toca rutas o carpetas temporales, pasa también los tests con `TEMP` y `TMP` apuntando al nombre corto 8.3 de una carpeta de nombre largo (como el runner de GitHub, `C:/Users/RUNNER~1/…`): una versión ya se publicó con la CI de Windows rota por eso.
- Al acabar los tests, vuelve a contar `%TEMP%`: carpetas nuevas del proyecto que quedan = hallazgo `media`.

## 2. El binario de verdad

El ejecutable es `$CARGO_TARGET_DIR/release/<bin>.exe` (el nombre está en `[[bin]]` del `Cargo.toml`). **Cópialo a `$PRUEBA/bin/`** y ejecuta la copia: así no queda bloqueado el de `target/` si hay que recompilar.

**Nunca** ejecutes el del PATH (es el instalado de {humano}), ni `cargo install`, ni `cargo run` sin aislar, ni `taskkill /IM <bin>.exe` (mataría el que usan sus sesiones). Lo que arranques, por PID.

Aísla siempre, **solo en el entorno de cada orden** (prefijo `VAR=… orden` o un subshell; nada de `export`, que las herramientas de tu propia sesión leerían otra base), con las variables de datos del proyecto apuntando a `$PRUEBA` (las conocidas están en las notas de esta instalación) y `CLAUDE_CONFIG_DIR=$PRUEBA/claude` si escribe en la configuración de un agente.

Proyecto sin variables conocidas: `grep -rn "env::var" src` y busca las que deciden dónde guarda y qué configura antes de ejecutar nada. Si no hay manera de aislarlo, no lo ejecutes contra los datos reales: no comprobable, y dilo.

Recorrido, siempre:

1. `--version` y `--help`: arrancan, la versión es la de `Cargo.toml`.
2. Las órdenes principales con datos de prueba, como en la CI (crear, buscar, listar, borrar lo que el programa maneje), y lo que toque el encargo, entero. Nunca contra un sitio o servicio de terceros: uno servido en local.
3. Errores como los haría una persona: orden inexistente, fichero que no existe, entrada vacía. Mensaje claro y código de salida distinto de 0, sin `panicked at`.
4. Si el encargo toca `setup`: ejecútalo con todo aislado y **mira lo que escribió** en `$PRUEBA/claude` y `$PRUEBA/agentes`; luego comprueba que el `settings.json` real de {humano} (`~/.claude/settings.json`) no ha cambiado (fecha de modificación antes y después).

Guarda cada orden con su salida en `evidencias/binario/<nn>-<orden>.txt`.

### Servidores MCP

Por stdio (`<bin> mcp` o lo que diga el README): arráncalo con el entorno aislado y háblale JSON-RPC, un mensaje por línea. Un script en Python en tu carpeta de trabajo vale:

```python
import json, subprocess, sys
p = subprocess.Popen(sys.argv[1:], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8")
def enviar(m): p.stdin.write(json.dumps(m) + "\n"); p.stdin.flush()
enviar({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18",
       "capabilities": {}, "clientInfo": {"name": "kumi-test", "version": "0"}}})
print(p.stdout.readline())
enviar({"jsonrpc": "2.0", "method": "notifications/initialized"})
enviar({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
print(p.stdout.readline())
# …y un tools/call de cada herramienta que toque el encargo, con argumentos reales.
p.stdin.close(); p.wait(timeout=10)
```

Comprueba: `initialize` responde con `serverInfo` y la versión correcta; `tools/list` da las herramientas esperadas (si el proyecto fija cuántas tiene, una de más es hallazgo `alta`), cada una con descripción y `inputSchema`; un `tools/call` con argumentos válidos y otro con argumentos malos (error limpio, el servidor sigue vivo); nada que no sea JSON-RPC sale por stdout (un `println!` de depuración rompe el protocolo); al cerrar stdin el proceso termina solo.

Por HTTP (`/mcp` streamable): arranca el servidor en `127.0.0.1` y un puerto libre, espera a que responda, y haz `initialize` + `tools/list` con `POST` a `/mcp` (`Accept: application/json, text/event-stream`, y la cabecera `Mcp-Session-Id` que devuelva el primero). Si pide clave, créala dentro de `$PRUEBA`. Para el servidor por su PID al acabar.

### Servicios web

Arranca en un puerto libre de `127.0.0.1`, recorre con `curl` las rutas que toca el encargo (códigos y cuerpos), y si tiene interfaz web pásale la cadena de test web si existe, o captura con el navegador. Comprueba que solo escucha donde debe (`netstat -ano | grep <puerto>`).

### Librerías

`cargo test --doc` (los ejemplos de la documentación compilan y pasan), `cargo doc --no-deps` sin avisos de enlaces rotos, y un ejemplo mínimo que use la API pública como un tercero (`examples/` del repo, o uno tuyo en `$PRUEBA` con dependencia `path = …` y el mismo `CARGO_TARGET_DIR`). Si el cambio rompe la API pública (firma cambiada, tipo quitado) y la versión no sube el número que toca, hallazgo `alta`.

## 3. Comparar con la base

Cuando el encargo diga que no cambia la salida, toque rendimiento, búsqueda o recuerdo, o cambie algo que se ejecuta a menudo (un hook que salta en cada mensaje tiene que seguir en decenas de milisegundos):

1. `git worktree add $PRUEBA/base <rama base>`, compílalo con el **mismo** `CARGO_TARGET_DIR` (solo se recompila el crate) y copia el binario a `$PRUEBA/bin/antes.exe`; el de la rama, a `despues.exe`.
2. Las mismas órdenes con los dos, cada uno con su propia carpeta de datos, y `diff` de las salidas. Diferencias que el encargo no pide: hallazgo.
3. Tiempos **intercalados** (A, B, A, B…, al menos 20 de cada), nunca un bloque detrás de otro: las cachés y la temperatura falsean el segundo. Mediana y P90 de cada uno. Empeorar más de un 10 % sin que el encargo lo justifique es hallazgo `media`.
4. Si el proyecto trae su propio evaluador (un `examples/eval` con casos), pásalo con los dos binarios y lista lo que arregla y lo que rompe.
5. `git worktree remove $PRUEBA/base` al acabar.

## Prod tests (tras publicar)

Los lanza la central con la receta del proyecto, contra lo ya publicado. Comprueban que lo que se ha publicado se baja y arranca, no el código:

- **GitHub Releases**: `gh release view v<versión> --repo <dueño>/<repo> --json assets` tiene todos los paquetes que publica su workflow, con `SHA256SUMS`. Baja el de Windows (`<bin>-x86_64-pc-windows-msvc.zip`) a una carpeta temporal nueva, comprueba su suma contra `SHA256SUMS`, descomprime y `<bin>.exe --version` debe decir esa versión, y una orden de diagnóstico con datos en la carpeta temporal si la tiene.
- **crates.io**: `curl -s https://crates.io/api/v1/crates/<crate> -A kumi` y `.crate.max_version` es la versión publicada. Para probar que instala: `cargo install <crate> --version <v> --locked --root <temporal> --target-dir <temporal>/t` y `<temporal>/bin/<bin>.exe --version`. Nunca sin `--root`: pisaría el de `~/.cargo/bin`.
- **npm** (si el binario se publica también ahí): `npm view <paquete> version` y el paquete de la plataforma con la misma versión; `npx` en una carpeta temporal **con su propio `package.json`** (sin él, npm sube por las carpetas padre y puede cambiar el de la carpeta personal).
- Borra la carpeta temporal al acabar, también si falla.

## 4. Dejarlo todo como estaba

- Para por PID cada proceso que arrancaste y comprueba que no queda ninguno (`tasklist | grep <bin>` debe mostrar solo los de {humano}, que ya estaban antes).
- Borra `$PRUEBA` y los worktrees de comparación (`git worktree prune`).
- **Vacía el `CARGO_TARGET_DIR` de este proyecto** con `CARGO_TARGET_DIR=<ruta> cargo clean` (si otra cadena lo necesita, lo recompila). Si `cargo clean` falla porque un `.exe` está en uso, para ese proceso y repite.
- Si en `%TEMP%` quedan carpetas nuevas del proyecto que dejaron los tests, bórralas después de apuntarlas como hallazgo.
- Apunta en el informe los GB libres del disco de `{compilacion}` antes y después.
