# Oficio: fábrica Rust

Lo que sabe esta fábrica del stack, valga para el proyecto que valga: binarios CLI, servidores MCP, servicios web y librerías con Cargo. Lo propio de cada proyecto (cómo publica, qué promete a versiones anteriores, sus variables de datos) va en el brain del proyecto, su `CONTRIBUTING`/`CLAUDE.md`, su receta y las notas locales de esta instalación. Si algo de aquí choca con lo que diga el repo, manda el repo.

## Antes de compilar: dónde y cuánto

Un `target/` de debug de un proyecto mediano ocupa ~12 GB, y varios agentes a la vez llenan un disco. Por eso:

- **Nunca compiles dentro del worktree.** Pon `CARGO_TARGET_DIR={compilacion}\<proyecto>` (`<proyecto>` = nombre de la carpeta del proyecto en minúsculas). Es la misma carpeta que usará después la cadena de test de Rust, que es quien la vacía; tú no la borres al acabar.
- Mira el espacio libre de ese disco antes de la primera compilación (`df -h`). Si tiene menos de 15 GB libres, primero borra los `target` que haya dejado Kumi (`{compilacion}\*` de tareas ya cerradas); si aun así no llega, `bloqueado` con el motivo y el espacio libre. No borres nada que no haya creado Kumi.
- **Un solo perfil.** Usa el que use la CI del repo (`.github/workflows/`). Mezclar `build` en debug con `test --release` dobla el disco.
- Limita los hilos (`CARGO_BUILD_JOBS` a unos tres cuartos de los núcleos) para que la máquina siga respondiendo: el enlazado con LTO y `codegen-units = 1` es lento. Lanza las compilaciones con timeout largo o en segundo plano, y no repitas la misma sin cambios.
- Siempre `--locked`, como la CI. Si Cargo quiere tocar `Cargo.lock`, es que cambian dependencias: eso es una decisión (ver abajo), no un efecto secundario.

## Compilar y comprobar

En este orden, con el perfil de la CI:

1. `cargo build --locked --all-targets` (con `--workspace` si el `Cargo.toml` raíz tiene `[workspace]` con miembros).
2. `cargo clippy --locked --all-targets -- -D warnings` si la CI del repo lo usa. Si no lo usa, al menos no añadas avisos nuevos: compara con los que salen en la rama base.
3. `cargo test --locked` (y `--workspace` si toca). Un arreglo lleva un test que falla sin él.
4. Si el proyecto tiene comprobaciones propias en la CI (probar el binario, scripts de `tools/`, ficheros dorados), pásalas también si tu cambio las toca.

Formato: si `rustfmt` no está instalado, no lo instales (es tocar la configuración de {humano}) ni reformatees ficheros enteros: un diff de formato tapa el cambio de verdad. Sigue el estilo del código de alrededor. Si está instalado, solo lo pasas si `cargo fmt --check` ya salía limpio en la base.

## Windows nativo

- En Windows (Git Bash, no WSL): rutas con `\` en lo que lee el programa, finales de línea CRLF en checkout (`.gitattributes`), y el bloqueo de ficheros: un `.exe` en marcha no se puede sobrescribir ni borrar.
- **No toques los binarios instalados de {humano}** (los del PATH, `~/.cargo/bin`): pueden ser los que usan sus sesiones y los agentes de Kumi ahora mismo. Nada de `cargo install`, ni de matar procesos por nombre: si arrancas un proceso, guarda su PID y para ese.
- Un `.cargo/config.toml` con `+crt-static` está para que el `.exe` no pida el redistribuible de Visual C++. No lo quites.
- Un binario que carga ONNX Runtime u otra librería con `load-dynamic` no arranca esa parte con el `.exe` solo.

## Cómo escribir Rust aquí

- **Sin dependencias nuevas sin motivo.** Antes de añadir un crate, mira si ya lo trae otra dependencia o si son veinte líneas. Si hace falta, versión publicada hace más de un mes, `default-features = false` cuando sobre lo demás, y el porqué en un comentario junto a la línea. Las de cifrado y red fijadas con `=x.y.z` lo están a propósito: no las «actualices».
- **MSRV**: si `Cargo.toml` tiene `rust-version`, el compilador instalado puede ser mucho más nuevo y no te avisará. No uses APIs ni sintaxis estabilizadas después de esa versión: mira el «since» en la documentación de lo que uses. No instales otro toolchain.
- **Edición**: en `edition = "2024"`, `std::env::set_var` es `unsafe` y los `impl Trait` capturan todo. Respeta la del crate.
- **Features**: si el crate tiene `[features]`, compila y prueba también con las que tocas (`--no-default-features`, `--features x`). En dependencias, no actives features de más.
- `unsafe` solo si no hay otra, con un comentario `// SAFETY:` que diga por qué es correcto. Sube la tarea a nivel 2.
- Nada de `unwrap()` en rutas que dependan de la entrada del usuario, de la red o del disco: errores con contexto (`anyhow::Context`) que digan qué fichero o qué orden falló.
- Escribe en el idioma del código de alrededor (nombres, comentarios, tests). Comentarios del porqué, no del qué.
- Los tests no tocan los datos reales: carpeta temporal propia por test, borrada también si el test falla, y las variables de entorno del proyecto apuntando a ella (abajo). Un test que deja carpetas en la carpeta temporal es un fallo.
- Secretos de mentira en los tests: partidos, que no parezcan enteros, o la protección de GitHub bloquea el push.

## Datos de verdad que no se tocan

Un binario que guarda datos suele tener una variable para mandarlos a otro sitio (`<APP>_HOME`, `<APP>_DATOS`, `CLAUDE_CONFIG_DIR` si escribe en la configuración de un agente). Úsala siempre que ejecutes el binario, **solo en el entorno de esa orden** (no hagas `export` en la sesión: herramientas de la propia sesión podrían leer la base equivocada). Si no la conoces, búscala en `src` (`grep -rn "env::var"`) antes de ejecutar nada, y si el binario abre un puerto, pásale uno libre.

## Compatibilidad: lo que leen otras versiones

Base de datos (esquema y migraciones), líneas de sincronización, formato de exportación y copias, ficheros de configuración que escribe `setup` en los agentes de otros: lo que guarda una versión lo lee otra. Dentro de una versión mayor, solo se añade (campo nuevo con `#[serde(default)]`, y la versión vieja tiene que conservarlo). Si tu cambio toca algo de esto, va en el `CHANGELOG` bajo **Compatibility** y es nivel 2.

## Lo que no decides tú

- Subir versión (`Cargo.toml`, `plugin.json`, `package.json` de npm), crear tags, `cargo publish`, Releases: lo hace la central con la receta del proyecto.
- Dependencias nuevas o cambiadas, cambio de MSRV o de edición, cambio de esquema o de formato que no sea solo añadir: `decision` con `[blocking]` si sin eso no hay tarea, `[review-after]` si has tomado la opción conservadora.

## Al acabar

- Detén los procesos que arrancaste (por PID) y borra las carpetas temporales que creaste.
- Deja `CARGO_TARGET_DIR` como está (lo reutiliza la cadena de test), pero apunta su ruta en `ajuste.md` / `para_test.md`, junto con el perfil, las órdenes que compilan y las órdenes del binario que hay que probar.
- Si añadiste carpetas `target/` dentro del worktree por error, bórralas.
