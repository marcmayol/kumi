# Changelog

## 0.3.0 (2026-11-12)

- Traces: each task is an OpenTelemetry trace (task → steps → what each agent does). Every agent gets `TRACEPARENT`; Claude Code hangs its model calls, tools and subagents under the step that launched it. Child tasks stay in their parent's trace. With `[trazas] endpoint`, Kumi sends its spans over OTLP/HTTP (no new dependencies) and turns on Claude Code's telemetry towards the same receiver.
- Time cap per agent: `[recursos] tope_agente_min` (240 by default, 0 = no cap). An agent that hangs used to block its task forever; now, when it passes the cap, Kumi kills it together with everything it launched (builds, emulators, subagents), notes it in `salida.jsonl` and the step falls back to the next engine or fails. The longest real run seen so far was 57 min.
- Turn count fixed: a Claude Code session can emit several `result` events (when background tasks wake it up). The cost is cumulative, but turns are per stretch: they were taken from the last one only (35 instead of 154 in a real Android run). Now they are added up.

## 0.2.0 (2026-11-09)

- Everything runs in parallel: `kumi recepcion` launches every task and idea that is due, each in its own process, up to `[recursos] max_paralelo`, and returns at once; the next pass knows which ones are still running.
- The test chains of a task run at the same time (each one still waits for its slot, e.g. a single emulator).
- Before starting an agent or launching a task, Kumi checks free disk and RAM (`[recursos] min_disco_gb`, `min_sistema_gb`, `min_ram_gb`) and waits until there is room.
- `kumi recepcion` logs the tasks it skips because of the `humano` field, once per edit, with the reason.

## 0.1.0 (2026-11-09)

First public release.

- Installable package: `uv tool install git+https://github.com/marcmayol/kumi`, command `kumi`.
- `kumi init` creates `~/.kumi` with a minimal `kumi.toml` and reports missing engines.
- Working data (tasks, worktrees, registry, your own chains) lives in `~/.kumi` (`KUMI_HOME`), apart from the built-in catalog.
- Spend caps per task and per day (`[gasto]`), `kumi gasto`.
- Any engine and model per role, with fallback: Claude Code, Codex CLI, Gemini CLI, OpenCode, Aider, or your own command.
- Two-way channels: desktop, Telegram, Discord, ntfy, email, Notion (`kumi recepcion`).
- Risk computed by code right after the factory; human approval last, with everything green.
- Catalog: Android, web, Rust, Python, Alexa, Node desktop, Claude Code plugins, SEO/GEO, plus cyber, desktop and review test chains.
