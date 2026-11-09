# Kumi

[English](README.md) · **Español**

Kumi recibe tareas de software y las lleva hasta producción con agentes de IA. Decide qué factoría trabaja el proyecto y pasa el cambio por sus factorías de test. Calcula el riesgo con código, no con un modelo. Publica sola lo que es seguro y pide un sí a una persona para lo que no. Después despliega, escucha la CI, comprueba producción y vuelve atrás si algo falla.

```
tarea → central (encargado + planificador) → factorías → factorías de test → supervisor
      → commit → puerta de riesgo ─nivel 2→ PR humana → despliegue → CI → prod tests ─fallo→ rollback + tarea nueva
```

Cada tarea trabaja en su propio worktree de git, así que tu copia del repo no se toca hasta que el cambio está aprobado.

**Todo va en paralelo.** Las tareas corren a la vez, cada una en su proceso, y las cadenas de test de una tarea también. Las factorías de una misma ruta siguen en orden, porque cada una trabaja sobre lo que dejó la anterior. Antes de arrancar un agente, Kumi mira el disco y la RAM libres, y si el equipo va lleno, espera:

```toml
[recursos]
max_paralelo = 4       # tareas a la vez
min_disco_gb = 15      # libres donde están las obras
min_sistema_gb = 8     # libres en el disco del sistema
min_ram_gb = 6
```

> **Estado: alfa (0.2).** Se usa a diario en una instalación con Windows, Claude Code y Android, webs, Rust y Python. Linux y macOS deberían funcionar, pero aún no están probados. Los textos de los agentes (oficios, modos) están en español.

## Instalar

Hace falta Python 3.11 o más, git y al menos un motor de agentes (Claude Code, Codex CLI, Gemini CLI, OpenCode o Aider).

```
uv tool install git+https://github.com/marcmayol/kumi
kumi init
```

`kumi init` crea `~/.kumi/kumi.toml` con lo mínimo, deja al lado `kumi.ejemplo.toml` con todas las opciones y te dice qué motores tienes y qué falta. Otra carpeta: variable `KUMI_HOME`.

## Usar

```
kumi tarea --proyecto ~/proyectos/mi-repo "lo que quieres que haga"
kumi nuevo "una app que…"              # un proyecto que aún no existe
kumi estado                            # tareas en curso
kumi aprobar <id>                      # PR humana: publica
kumi rechazar <id> "qué cambiar"       # PR humana: tarea nueva con tu comentario
kumi config                            # quién hace cada papel, qué falta, cuánto se ha gastado hoy
```

## Cada papel, con el motor y el modelo que quieras

Cada punto del recorrido es un **papel**, y en `kumi.toml` dices quién lo hace:

| Papel | Qué hace | Tipo |
|---|---|---|
| `encargado` | decide qué factorías levanta cada tarea | consulta |
| `recepcion` | decide de qué proyecto es lo que entra | consulta |
| `planificador` | diseña factorías nuevas (siempre con su factoría de test) | agente |
| `factoria` | escribe el código | agente |
| `test` | prueba el cambio de verdad: compila, ejecuta, navega | agente |
| `revisor` | revisa con otro modelo los cambios de riesgo | agente |
| `aprendiz` | propone reglas de oficio a partir de lo que pides | agente |
| `oficina` | tareas que no son de un repo | agente |

Motores agente: **Claude Code, Codex CLI, Gemini CLI, OpenCode, Aider** o una orden propia. Para las consultas valen también las API directas: **Anthropic, OpenAI, Google, OpenRouter, Ollama, LM Studio**.

```toml
[papeles]
encargado = ["anthropic:claude-sonnet-5-5"]
factoria  = ["codex:gpt-5-codex", "claude-code"]     # una lista: si el primero falla, el siguiente
test      = ["claude-code:opus"]
revisor   = ["gemini-cli"]

[papeles_por_nombre]
"factoria:android" = ["claude-code"]
```

Y por proyecto: `kumi perfil --proyecto <repo> --motor factoria=opencode:ollama/qwen3-coder`.

Kumi habla con los agentes por ficheros: les deja las instrucciones en una carpeta y lee su `resultado.json`. Por eso vale cualquier motor que sepa trabajar en una carpeta. Además:

- **Respaldo.** Si un motor no está instalado, se cae o se queda sin cuota sin dejar resultado, entra el siguiente de la lista. En las vueltas del supervisor se empieza por el motor que ya acertó.
- **Capacidades.** Cada factoría declara lo que necesita (`necesita = ["shell", "ficheros", "mcp"]`) y Kumi no se la da a un motor que no lo tiene.
- **Contrato.** El `resultado.json` se valida y, si viene mal, el mismo motor lo corrige.
- **Puntos ciegos.** Si la misma familia de modelos construye y prueba, Kumi avisa.

## Riesgo

El riesgo lo calcula código sobre el diff, con rutas y patrones de cada stack. Se calcula justo después de la factoría. Si sale nivel 2 (auth, pagos, secretos, CI, migraciones…), el cambio pasa además por las factorías de test de refuerzo (`[riesgo] nivel2_tests`, por defecto `revision`). La aprobación humana va al final, con todo en verde, así que a la persona solo le llega lo que ya está listo para producción.

Si una tarea contradice una decisión ya registrada en la memoria del proyecto, deja de ser un ajuste y pasa a modo spec.

## Topes de gasto

```toml
[gasto]
por_tarea_usd = 15     # la tarea con sus vueltas del supervisor y los cambios pedidos
por_dia_usd = 60       # todos los agentes en un día
```

Si se pasa un tope, la tarea se para y te avisa. Para seguir, sube el tope y ejecuta `kumi reanudar <id>`. Solo cuenta lo que declara el motor: Claude Code da el coste de cada sesión, pero otros motores no siempre lo dan.

## Trazas

Cada tarea es una traza de OpenTelemetry: un span por la tarea, uno por cada paso (factoría, test, revisor…) y, debajo, lo que hace el agente de ese paso. Kumi le pasa a cada agente `TRACEPARENT`, el estándar W3C Trace Context. Claude Code lo lee en `-p` y cuelga ahí sus llamadas al modelo (con tokens, caché y reintentos), sus herramientas y sus subagentes. Las tareas que nacen de otra (vueltas del supervisor, rechazos, fallos en producción) siguen en la traza de su madre.

```toml
[trazas]
endpoint = "https://otel.midominio.com"              # receptor OTLP/HTTP: Collector, Jaeger, Phoenix, Tempo…
cabeceras = { authorization = "Bearer $KUMI_OTLP_TOKEN" }
contenido = false                                    # true = también prompts y detalles de herramientas
servicio = "kumi"                                    # service.name; cambia por instancia si tienes varias
```

Sin `endpoint` no se manda nada, pero cada agente recibe su `TRACEPARENT` y la tarea guarda su `trace_id` (`kumi ver <id>`). Con `endpoint`, Kumi manda sus spans por OTLP/HTTP, sin dependencias, y activa la telemetría de Claude Code hacia el mismo sitio con los atributos `kumi.tarea`, `kumi.paso`, `kumi.papel` y `kumi.nombre`. Si el receptor no contesta, avisa una vez y sigue: una traza nunca para una tarea. Omoikane (1.9 o más) apunta en cada memoria el `trace_id` y el `span_id` del paso que la escribió.

## Canales

Por dónde entra el trabajo y por dónde habla Kumi: avisos, preguntas y aprobación de PR humanas.

| Canal | Kumi → tú | Tú → Kumi |
|---|---|---|
| escritorio (Windows, macOS, Linux) | ✓ | |
| Telegram (`kumi escuchar`) | ✓ con botones Aprobar / Pedir cambios | ✓ `/tarea`, `/aprobar`, `/rechazar`, `/estado` |
| Discord (webhook), ntfy, correo | ✓ | |
| Notion (`kumi recepcion`) | ✓ | ✓ |

`[enrutado]` decide qué tipo de mensaje va por cada canal. Solo aprueba quien está en `aprobadores`.

### Notion

`kumi recepcion` hace una pasada por dos bases de Notion, una de tareas y otra de ideas. Lanza todas las que tocan y termina enseguida. Prográmala cada hora con el programador de tu sistema (cron, launchd o el Programador de tareas de Windows). Las tareas necesitan:

- un campo `Estado` de tipo status, con «Sin empezar», «En progreso» y «Listo»;
- un campo `humano` de tipo select. Solo se hacen las que tienen `no`. Las demás se anotan en el registro como saltadas, con el motivo.

El token va en `NOTION_TOKEN` o se guarda con `kumi recepcion --token`.

## El catálogo

El catálogo trae factorías para Android, web, Rust, Python, Alexa, aplicaciones de escritorio con Node, plugins de Claude Code y SEO/GEO. Cada una tiene su factoría de test, y además hay cadenas comunes: `cyber`, `escritorio` y `revision`. Cada factoría es una carpeta con su `oficio.md` (cómo se trabaja ese stack), su `factoria.toml` (cuándo casa con un repo, por qué tests pasa y cómo se publica) y su `riesgo.toml`.

Lo tuyo va aparte, y las actualizaciones no lo pisan:

- **Cadenas propias** en `~/.kumi/factorias/` y `~/.kumi/tests/`. Las crea el planificador cuando un proyecto no casa con ninguna, y mandan sobre las de serie con el mismo nombre.
- **Notas locales** (`[rutas] notas`): lo que solo vale en tu máquina o para tus proyectos. `<notas>/factorias/web.md` se suma al oficio de la factoría web, y `<notas>/recetas/<repo>.toml` dice cómo se publica cada proyecto.

## Memoria

La memoria por proyecto usa [Omoikane](https://github.com/marcmayol/omoikane). Ahí se guardan los perfiles (qué tests necesita cada proyecto), la cola de decisiones y las reglas de oficio. Sin Omoikane (`[memoria] motor = "ninguna"`), Kumi funciona igual, pero no recuerda nada entre tareas.

## Licencia

MIT
