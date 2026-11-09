# Changelog

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
