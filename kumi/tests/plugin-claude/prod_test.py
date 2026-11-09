"""Prod test de un plugin de Claude Code: lo instala desde el marketplace remoto como lo haría un usuario,
en una configuración temporal, y comprueba que está, que su versión es la del repo y que el manifiesto
instalado valida. Con --sesion abre además una sesión `claude -p` con solo ese plugin.

No toca tu instalación de Claude Code: todo va a un CLAUDE_CONFIG_DIR temporal (KUMI_TMP o la carpeta temporal
del sistema), que se borra al acabar, y comprueba que tu settings.json y tus registros de plugins siguen igual.

    uv run --no-project python prod_test.py [repo] [--marketplace dueño/repo] [--plugin nombre ...] [--sesion]

Sale con 1 si algo falla. La sesión necesita CLAUDE_CODE_OAUTH_TOKEN o ANTHROPIC_API_KEY (una
configuración nueva no tiene login); sin ellos se da por no comprobable y no cuenta como fallo.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REAL = Path.home() / ".claude"
VIGILADOS = [REAL / "settings.json", REAL / "plugins" / "installed_plugins.json", REAL / "plugins" / "known_marketplaces.json"]
fallos: list[str] = []


def ok(bien: bool, que: str) -> bool:
    print(f"{'ok ' if bien else 'MAL'}  {que}", flush=True)
    if not bien:
        fallos.append(que)
    return bien


def claude(args: list[str], env: dict, cwd: Path, timeout: int = 300) -> subprocess.CompletedProcess:
    exe = shutil.which("claude") or "claude"
    return subprocess.run([exe, *args], env=env, cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def fuente_remota(repo: Path) -> str:
    url = subprocess.run(["git", "-C", str(repo), "remote", "get-url", "origin"], capture_output=True, text=True).stdout.strip()
    m = re.search(r"github\.com[:/]([^/]+/[^/]+?)(\.git)?$", url)
    if not url:
        sys.exit("el repo no tiene remoto origin: pasa --marketplace")
    return m.group(1) if m else url


def leer(f: Path) -> dict:
    return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}


def esperados(repo: Path, propio: bool, pedidos: list[str]) -> dict[str, str | None]:
    """Plugin → versión que debería instalarse según el repo."""
    mk = leer(repo / ".claude-plugin" / "marketplace.json")
    raiz = leer(repo / ".claude-plugin" / "plugin.json")
    out: dict[str, str | None] = {}
    for e in mk.get("plugins", []) if propio else []:
        src = e.get("source")
        if isinstance(src, str) and src.startswith("./") and (not pedidos or e["name"] in pedidos):
            out[e["name"]] = leer(repo / src / ".claude-plugin" / "plugin.json").get("version") or e.get("version")
    if not out and raiz.get("name"):
        out[raiz["name"]] = raiz.get("version")
    for p in pedidos:
        out.setdefault(p, None)
    return out


def huella_real() -> dict:
    return {str(f): (f.stat().st_mtime_ns, f.stat().st_size) if f.exists() else None for f in VIGILADOS}


def borrar(carpeta: Path) -> None:
    def quitar_solo_lectura(func, ruta, _):
        os.chmod(ruta, stat.S_IWRITE)
        func(ruta)
    shutil.rmtree(carpeta, onerror=quitar_solo_lectura)


def main() -> None:
    for flujo in (sys.stdout, sys.stderr):
        flujo.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("repo", nargs="?", default=".", type=Path)
    ap.add_argument("--marketplace", help="fuente del marketplace (dueño/repo, URL git); por defecto, el origin del repo")
    ap.add_argument("--plugin", action="append", default=[], help="plugin a instalar (repetible)")
    ap.add_argument("--sesion", action="store_true", help="abrir también una sesión claude -p con el plugin")
    a = ap.parse_args()
    repo = a.repo.resolve()
    propio = a.marketplace is None
    if propio and not (repo / ".claude-plugin" / "marketplace.json").exists():
        sys.exit("el repo no tiene marketplace propio: pasa --marketplace (p. ej. dueño/repo-marketplace)")
    fuente = a.marketplace or fuente_remota(repo)
    plugins = esperados(repo, propio, a.plugin)
    if not plugins:
        sys.exit("no sé qué plugin probar: pasa --plugin")

    base = os.environ.get("KUMI_TMP") or None
    tmp = Path(tempfile.mkdtemp(prefix="kumi-prod-", dir=base))
    cfg, casa, ws = tmp / "cfg", tmp / "casa", tmp / "ws"
    for d in (cfg, casa, ws):
        d.mkdir()
    env = {k: v for k, v in os.environ.items()
           if k not in ("CLAUDE_CODE_PLUGIN_DIRS", "CLAUDE_CODE_PLUGIN_SEED_DIR", "CLAUDE_CODE_PLUGIN_CACHE_DIR")}
    env.update(CLAUDE_CONFIG_DIR=str(cfg), CLAUDE_CODE_PLUGIN_CACHE_DIR=str(cfg / "plugins"), DISABLE_AUTOUPDATER="1",
               CLAUDE_CODE_DISABLE_OFFICIAL_MARKETPLACE_AUTOINSTALL="1")
    antes = huella_real()
    print(f"marketplace {fuente} · plugins {plugins} · temporal {tmp}", flush=True)
    try:
        r = claude(["plugin", "marketplace", "add", fuente], env, ws)
        if not ok(r.returncode == 0, f"marketplace add {fuente}"):
            print(r.stdout[-800:], r.stderr[-800:])
            return
        nombre_mk = next(iter(leer(cfg / "plugins" / "known_marketplaces.json")), None)
        for p in plugins:
            r = claude(["plugin", "install", f"{p}@{nombre_mk}", "--json"], env, ws)
            ok(r.returncode == 0 and '"outcome":"ok"' in r.stdout.replace(" ", ""), f"install {p}@{nombre_mk}")
        instalados = {x["id"].split("@")[0]: x for x in json.loads(claude(["plugin", "list", "--json"], env, ws).stdout or "[]")}
        for p, version in plugins.items():
            x = instalados.get(p)
            if not ok(x is not None, f"{p} aparece en plugin list"):
                continue
            if version:
                ok(x.get("version") == version, f"{p}: versión publicada {x.get('version')} = versión del repo {version}")
            r = claude(["plugin", "validate", x["installPath"]], env, ws)
            ok(r.returncode == 0, f"{p}: el manifiesto instalado valida")

        if a.sesion:
            if not (env.get("CLAUDE_CODE_OAUTH_TOKEN") or env.get("ANTHROPIC_API_KEY")):
                print("--   sesión: no comprobable (sin CLAUDE_CODE_OAUTH_TOKEN ni ANTHROPIC_API_KEY)", flush=True)
            else:
                env_s = {**env, "HOME": str(casa), "USERPROFILE": str(casa)}
                t0 = time.monotonic()
                r = claude(["-p", "Responde solo OK", "--output-format", "stream-json", "--verbose"], env_s, ws, timeout=600)
                segundos = time.monotonic() - t0
                init, final = {}, {}
                for linea in r.stdout.splitlines():
                    try:
                        d = json.loads(linea)
                    except json.JSONDecodeError:
                        continue
                    if d.get("type") == "system" and d.get("subtype") == "init":
                        init = d
                    elif d.get("type") == "result":
                        final = d
                cargados = {x.get("name") for x in init.get("plugins", [])}
                for p in plugins:
                    ok(p in cargados, f"{p}: cargado en la sesión")
                caidos = [m["name"] for m in init.get("mcp_servers", []) if m.get("status") not in ("connected", None)]
                ok(not caidos, f"servidores MCP conectados ({caidos or 'todos'})")
                ok(final.get("is_error") is False, f"la sesión responde ({final.get('result', '')!r}, {segundos:.1f} s)")
    finally:
        ok(huella_real() == antes, "la configuración real de Claude Code sigue igual")
        borrar(tmp)
    sys.exit(1 if fallos else 0)


if __name__ == "__main__":
    main()
