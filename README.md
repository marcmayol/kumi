# Kumi

**English** · [Español](README.es.md)

Kumi takes software tasks to production with AI coding agents. It picks the factory that builds the project and runs the change through that factory's test chains. It computes the risk with code, not with a model. Safe changes ship on their own; risky ones wait for a human yes. Then it deploys, listens to CI, checks production and rolls back if something breaks.

```
task → central (manager + planner) → factories → test factories → supervisor
     → commit → risk gate ─level 2→ human PR → deploy → CI → prod tests ─fail→ rollback + new task
```

Every task works in its own git worktree, so your checkout is untouched until the change is approved.

**Everything runs in parallel.** Tasks run side by side, each in its own process, and the test chains of a task run at the same time. Factories in a chain still go in order, since each one builds on the previous one. Before starting an agent, Kumi checks free disk and RAM and waits if the machine is full:

```toml
[recursos]
max_paralelo = 4       # tasks at once
min_disco_gb = 15      # free where the worktrees live
min_sistema_gb = 8     # free on the system disk
min_ram_gb = 6
```

> **Status: alpha (0.2).** In daily use on one setup: Windows, Claude Code, Android apps, websites, Rust and Python. Linux and macOS should work but are not tested yet. The agent instructions (trades, modes) and the CLI messages are in Spanish for now.

## Install

You need Python 3.11+, git and at least one agent engine (Claude Code, Codex CLI, Gemini CLI, OpenCode or Aider).

```
uv tool install git+https://github.com/marcmayol/kumi
kumi init
```

`kumi init` creates `~/.kumi/kumi.toml` with the minimum, puts `kumi.ejemplo.toml` next to it with every option, and tells you which engines it found and what is missing. Use `KUMI_HOME` for another folder.

## Use

```
kumi tarea --proyecto ~/projects/my-repo "what you want done"
kumi nuevo "an app that…"              # a project that does not exist yet
kumi estado                            # tasks in progress
kumi aprobar <id>                      # human PR: ship it
kumi rechazar <id> "what to change"    # human PR: new task with your comment
kumi config                            # who does each role, what is missing, today's spend
```

## Any engine and model per role

Each step of the pipeline is a **role**, and `kumi.toml` says who plays it:

| Role | What it does | Kind |
|---|---|---|
| `encargado` | decides which factories a task needs | query |
| `recepcion` | decides which project an incoming request belongs to | query |
| `planificador` | designs new factories (always with their test factory) | agent |
| `factoria` | writes the code | agent |
| `test` | tests the change for real: builds, runs, browses | agent |
| `revisor` | reviews risky changes with another model | agent |
| `aprendiz` | proposes trade rules from what you keep asking for | agent |
| `oficina` | tasks that are not about a repo | agent |

Agent engines: **Claude Code, Codex CLI, Gemini CLI, OpenCode, Aider**, or your own command. Queries can also use plain APIs: **Anthropic, OpenAI, Google, OpenRouter, Ollama, LM Studio**.

```toml
[papeles]
encargado = ["anthropic:claude-sonnet-5-5"]
factoria  = ["codex:gpt-5-codex", "claude-code"]     # a list: if the first one fails, the next
test      = ["claude-code:opus"]
revisor   = ["gemini-cli"]

[papeles_por_nombre]
"factoria:android" = ["claude-code"]
```

Per project: `kumi perfil --proyecto <repo> --motor factoria=opencode:ollama/qwen3-coder`.

Kumi talks to agents through files: it leaves the instructions in a folder and reads back a `resultado.json`. Any engine that can work in a folder will do. On top of that:

- **Fallback.** If an engine is missing, crashes or runs out of quota without a result, the next one in the list takes over. On supervisor retries, the engine that already succeeded goes first.
- **Capabilities.** Each factory declares what it needs (`necesita = ["shell", "ficheros", "mcp"]`), and Kumi never hands it to an engine that lacks it.
- **Contract.** `resultado.json` is validated; if it is malformed, the same engine fixes it.
- **Blind spots.** If the same model family builds and tests, Kumi warns you.

## Risk

Code computes the risk from the diff, using paths and patterns per stack, right after the factory. At level 2 (auth, payments, secrets, CI, migrations…) the change also goes through the reinforcement test factories (`[riesgo] nivel2_tests`, `revision` by default). The human approval comes last, with everything green, so a person only sees what is ready for production.

A task that contradicts a decision already recorded in the project memory is no longer a quick fix: it switches to spec mode.

## Spend caps

```toml
[gasto]
por_tarea_usd = 15     # a task plus its supervisor retries and requested changes
por_dia_usd = 60       # every agent, per day
```

Past a cap, the task stops and notifies you. Raise the cap and run `kumi reanudar <id>` to continue. Only what the engine reports counts: Claude Code reports the cost of each session; other engines do not always.

## Channels

Where work comes in and where Kumi talks: notices, questions and human PR approvals.

| Channel | Kumi → you | You → Kumi |
|---|---|---|
| desktop (Windows, macOS, Linux) | ✓ | |
| Telegram (`kumi escuchar`) | ✓ with Approve / Request changes buttons | ✓ `/tarea`, `/aprobar`, `/rechazar`, `/estado` |
| Discord (webhook), ntfy, email | ✓ | |
| Notion (`kumi recepcion`) | ✓ | ✓ |

`[enrutado]` routes each message type to its channels. Only people listed in `aprobadores` can approve.

### Notion

`kumi recepcion` makes one pass over two Notion databases, tasks and ideas: it launches every task that is due and returns at once. Schedule it hourly with cron, launchd or the Windows Task Scheduler. Tasks need:

- an `Estado` status property with «Sin empezar», «En progreso» and «Listo»;
- a `humano` select property. Only tasks set to `no` are done; the rest are logged as skipped, with the reason.

The token goes in `NOTION_TOKEN` or is saved with `kumi recepcion --token`.

## The catalog

Factories ship for Android, web, Rust, Python, Alexa, Node desktop apps, Claude Code plugins and SEO/GEO, each with its own test factory, plus shared chains: `cyber`, `escritorio` (desktop) and `revision`. A factory is a folder with an `oficio.md` (how to work that stack), a `factoria.toml` (when it matches a repo, which tests it goes through, how it ships) and a `riesgo.toml`.

Your own stuff lives apart and upgrades never overwrite it:

- **Your chains** in `~/.kumi/factorias/` and `~/.kumi/tests/`. The planner creates them when a project matches no factory, and they win over built-in ones with the same name.
- **Local notes** (`[rutas] notas`): what only holds on your machine or for your projects. `<notes>/factorias/web.md` is appended to the web factory's trade, and `<notes>/recetas/<repo>.toml` says how each project ships.

## Memory

Per-project memory uses [Omoikane](https://github.com/marcmayol/omoikane): project profiles (which tests each project needs), the decision queue and trade rules. Without it (`[memoria] motor = "ninguna"`) Kumi works the same but remembers nothing between tasks.

## License

MIT
