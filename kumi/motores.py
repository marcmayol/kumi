"""Motores de Kumi: quién ejecuta cada papel.

Dos clases de trabajo:
- **Agente** (`ejecutar`): una sesión que trabaja en una carpeta con herramientas y deja `resultado.json`.
  Claude Code, Codex CLI, Gemini CLI, OpenCode, Aider, o cualquier orden propia (`tipo = "comando"`).
- **Consulta** (`consultar`): una pregunta corta sin herramientas que devuelve texto. Además de los agentes,
  sirve una API directa: Anthropic, o cualquiera compatible con OpenAI (OpenAI, OpenRouter, Google, Ollama,
  LM Studio…).

Kumi habla con los agentes por ficheros, no por API: las instrucciones van a `instrucciones.md` en la
carpeta del paso, y el agente deja `informe.md` y `resultado.json`. Por eso vale cualquier motor que sepa
trabajar en una carpeta.

Sin dependencias: solo la biblioteca estándar.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .config import CFG

# Qué sabe hacer cada motor de serie. Una factoría declara lo que necesita (`necesita = [...]` en su toml) y
# Kumi no se la da a un motor que no lo tiene.
#   shell: ejecutar órdenes (compilar, adb, navegador headless con scripts)
#   ficheros: leer y escribir el repo
#   mcp: servidores MCP
#   skills / subagentes: piezas propias de Claude Code
#   web: buscar o leer páginas
SERIE = {
    "claude-code": {"tipo": "agente", "familia": "anthropic", "orden": "claude",
                    "capacidades": ["shell", "ficheros", "mcp", "skills", "subagentes", "web"]},
    "codex": {"tipo": "agente", "familia": "openai", "orden": "codex",
              "capacidades": ["shell", "ficheros", "mcp", "web"]},
    "gemini-cli": {"tipo": "agente", "familia": "google", "orden": "gemini",
                   "capacidades": ["shell", "ficheros", "mcp", "web"]},
    "opencode": {"tipo": "agente", "familia": None, "orden": "opencode",
                 "capacidades": ["shell", "ficheros", "mcp"]},
    "aider": {"tipo": "agente", "familia": None, "orden": "aider", "capacidades": ["ficheros"]},
    "anthropic": {"tipo": "api", "familia": "anthropic", "url": "https://api.anthropic.com/v1",
                  "clave_env": "ANTHROPIC_API_KEY"},
    "openai": {"tipo": "openai", "familia": "openai", "url": "https://api.openai.com/v1", "clave_env": "OPENAI_API_KEY"},
    "openrouter": {"tipo": "openai", "familia": None, "url": "https://openrouter.ai/api/v1",
                   "clave_env": "OPENROUTER_API_KEY"},
    "google": {"tipo": "openai", "familia": "google", "url": "https://generativelanguage.googleapis.com/v1beta/openai",
               "clave_env": "GEMINI_API_KEY"},
    "ollama": {"tipo": "openai", "familia": "local", "url": "http://localhost:11434/v1", "clave_env": None},
    "lmstudio": {"tipo": "openai", "familia": "local", "url": "http://localhost:1234/v1", "clave_env": None},
}

MENSAJE_FICHERO = ("Tus instrucciones completas están en el fichero {instrucciones}. Léelo entero y síguelo al pie "
                   "de la letra. El repo en el que trabajas es la carpeta actual ({cwd}): los cambios de código van ahí. "
                   "La carpeta de las instrucciones es solo para tus documentos: al terminar deja allí informe.md y "
                   "resultado.json.")


def motor(nombre: str) -> dict:
    """La definición de un motor: la de serie, con lo que la configuración añada o cambie."""
    base = dict(SERIE.get(nombre, {}))
    base.update(CFG.motores.get(nombre, {}))
    base.setdefault("tipo", "comando" if base.get("orden_plantilla") else "agente")
    base["nombre"] = nombre
    return base


def entorno(m: dict) -> dict:
    """Variables de entorno propias del motor (`env` en [motores.<nombre>]), p. ej. OPENCODE_CONFIG."""
    return {k: os.path.expandvars(os.path.expanduser(str(v))) for k, v in (m.get("env") or {}).items()}


def instalado(m: dict) -> bool:
    if m["tipo"] in ("api", "openai"):
        return not m.get("clave_env") or bool(os.environ.get(m["clave_env"]))
    if m["tipo"] == "comando":
        primera = (m.get("orden_plantilla") or "").split()
        return bool(primera) and shutil.which(primera[0]) is not None
    return shutil.which(m.get("orden", m["nombre"])) is not None


def familia(e: dict) -> str:
    """Familia de modelos de una elección, para avisar si quien prueba es el mismo que quien construye."""
    modelo = (e.get("modelo") or "").lower()
    for clave, fam in (("claude", "anthropic"), ("sonnet", "anthropic"), ("opus", "anthropic"), ("haiku", "anthropic"),
                       ("fable", "anthropic"), ("anthropic/", "anthropic"), ("gpt", "openai"), ("codex", "openai"),
                       ("openai/", "openai"), ("o3", "openai"), ("o4", "openai"), ("gemini", "google"),
                       ("google/", "google"), ("qwen", "qwen"), ("llama", "meta"), ("mistral", "mistral"),
                       ("deepseek", "deepseek"), ("gemma", "google")):
        if clave in modelo:
            return fam
    return motor(e["motor"]).get("familia") or e["motor"]


def efectiva(elecciones: list[dict], agente: bool = True) -> dict:
    """La elección que de verdad se usará: la primera instalada (y que sea agente si hace falta)."""
    for e in elecciones:
        m = motor(e["motor"])
        if instalado(m) and (not agente or m["tipo"] not in ("api", "openai")):
            return e
    return elecciones[0]


def describir(e: dict) -> str:
    return e["motor"] + (f" · {e['modelo']}" if e.get("modelo") else "")


# --- Agentes ---------------------------------------------------------------------------------

@dataclass
class Ejecucion:
    codigo: int
    eleccion: dict
    usd: float | None = None
    turnos: int | None = None
    error: str = ""            # fallo de infraestructura (no instalado, se cayó sin dejar nada)


def _orden_agente(m: dict, e: dict, instrucciones: Path, salida_final: Path,
                  cwd: str | None = None) -> tuple[list[str] | str, str | None]:
    """La orden de cada motor y lo que se le pasa por la entrada estándar."""
    modelo = e.get("modelo")
    exe = shutil.which(m.get("orden", m["nombre"])) or m.get("orden", m["nombre"])
    corto = MENSAJE_FICHERO.format(instrucciones=instrucciones, cwd=cwd or instrucciones.parent)
    extra = list(m.get("argumentos", []))
    n = m["nombre"] if m["nombre"] in SERIE else m.get("base", m["nombre"])
    if m["tipo"] == "comando":
        orden = m["orden_plantilla"].format(instrucciones=instrucciones, modelo=modelo or "", mensaje=corto,
                                            salida=salida_final)
        return orden, None
    if n == "claude-code":
        return [exe, "-p", "--dangerously-skip-permissions", "--output-format", "stream-json", "--verbose",
                *(["--model", modelo] if modelo else []), *extra], instrucciones.read_text(encoding="utf-8")
    if n == "codex":
        return [exe, "exec", "--dangerously-bypass-approvals-and-sandbox", "--skip-git-repo-check", "--json",
                "-o", str(salida_final), *(["-m", modelo] if modelo else []), *extra, corto], None
    if n == "gemini-cli":
        return [exe, "--yolo", "--output-format", "json", *(["-m", modelo] if modelo else []), *extra, "-p", corto], None
    if n == "opencode":
        return [exe, "run", "--dangerously-skip-permissions", "--format", "json",
                *(["-m", modelo] if modelo else []), *extra, corto], None
    if n == "aider":
        return [exe, "--yes-always", "--no-auto-commits", "--no-pretty", "--message-file", str(instrucciones),
                *(["--model", modelo] if modelo else []), *extra], None
    return [exe, *extra, corto], None


def coste(salida: Path) -> tuple[float | None, int | None]:
    """Coste y turnos si el motor los da (Claude Code: total_cost_usd; OpenCode: cost por paso).

    Una sesión de Claude Code puede dar varios `result` (cuando sus tareas en segundo plano la despiertan):
    el coste viene acumulado, así que vale el último; los turnos son de cada tramo, así que se suman."""
    usd, turnos, suma = None, None, 0.0
    try:
        for linea in salida.read_text(encoding="utf-8", errors="replace").splitlines():
            if not linea.startswith("{"):
                continue
            try:
                ev = json.loads(linea)
            except json.JSONDecodeError:
                continue
            if ev.get("type") == "result" and "total_cost_usd" in ev:
                usd = ev.get("total_cost_usd")
                if isinstance(ev.get("num_turns"), int):
                    turnos = (turnos or 0) + ev["num_turns"]
            elif isinstance(ev.get("part"), dict) and isinstance(ev["part"].get("cost"), (int, float)):
                suma += ev["part"]["cost"]
    except FileNotFoundError:
        pass
    if usd is None and suma:
        usd = round(suma, 4)
    return usd, turnos


def ejecutar(elecciones: list[dict], prompt: str, cwd: str, env: dict, carpeta: Path,
             necesita: list[str] = (), timeout: int | None = None) -> Ejecucion:
    """Lanza el papel con la primera elección que pueda hacerlo; si se cae sin dejar resultado, la siguiente."""
    instrucciones = carpeta / "instrucciones.md"
    instrucciones.write_text(prompt, encoding="utf-8")
    descartes = []
    ultima = None
    for e in elecciones:
        m = motor(e["motor"])
        if m["tipo"] in ("api", "openai"):
            descartes.append(f"{describir(e)}: es una API sin herramientas, no puede hacer de agente")
            continue
        if not instalado(m):
            descartes.append(f"{describir(e)}: no está instalado")
            continue
        faltan = [c for c in necesita if c not in m.get("capacidades", [])]
        if faltan:
            descartes.append(f"{describir(e)}: le falta {', '.join(faltan)}")
            continue
        salida = carpeta / "salida.jsonl"
        final = carpeta / "respuesta_final.txt"
        orden, entrada = _orden_agente(m, e, instrucciones, final, cwd)
        with open(salida, "a", encoding="utf-8") as s:
            s.write(json.dumps({"kumi": "motor", "motor": e["motor"], "modelo": e.get("modelo")}) + "\n")
            s.flush()
            try:
                r = subprocess.run(orden, input=entrada, cwd=cwd, env={**env, **entorno(m)}, stdout=s,
                                   stderr=subprocess.STDOUT,
                                   text=True, encoding="utf-8", errors="replace", shell=isinstance(orden, str),
                                   timeout=timeout)
                codigo = r.returncode
            except subprocess.TimeoutExpired:
                codigo = -9
        usd, turnos = coste(salida)
        ultima = Ejecucion(codigo, e, usd, turnos)
        res_f = carpeta / "resultado.json"
        if res_f.exists() and leer_resultado(res_f)[0] is None and e is not elecciones[-1]:
            # Ni siquiera es JSON: se aparta y lo intenta el siguiente motor de la lista.
            res_f.replace(carpeta / f"resultado.invalido.{e['motor']}.json")
            descartes.append(f"{describir(e)}: dejó un resultado.json que no es JSON")
            continue
        if res_f.exists():
            if descartes:
                ultima.error = "; ".join(descartes)
            return ultima
        descartes.append(f"{describir(e)}: terminó ({codigo}) sin dejar resultado.json")
    if ultima is None:
        return Ejecucion(-1, elecciones[0] if elecciones else {"motor": "?"}, error="; ".join(descartes) or "sin motores")
    ultima.error = "; ".join(descartes)
    return ultima


def ejecutar_texto(elecciones: list[dict], prompt: str, cwd: str, env: dict, timeout: int | None = None) -> tuple[int, str]:
    """Un agente con herramientas que devuelve su respuesta final como texto (la oficina: trabajo que no es de
    un repo). Primera elección instalada; si se cae sin decir nada, la siguiente."""
    ultimo = (-1, "")
    for e in elecciones:
        m = motor(e["motor"])
        if m["tipo"] in ("api", "openai") or not instalado(m):
            continue
        n = m["nombre"] if m["nombre"] in SERIE else m.get("base", m["nombre"])
        with tempfile.TemporaryDirectory(prefix="kumi-oficina-") as tmp:
            ins, final = Path(tmp) / "instrucciones.md", Path(tmp) / "respuesta.txt"
            ins.write_text(prompt, encoding="utf-8")
            if n == "claude-code":
                exe = shutil.which("claude") or "claude"
                orden, entrada = [exe, "-p", "--dangerously-skip-permissions",
                                  *(["--model", e["modelo"]] if e.get("modelo") else [])], prompt
            else:
                orden, entrada = _orden_agente(m, e, ins, final)
                if n == "codex":
                    orden = [x for x in orden if x != "--json"]
                if n in ("gemini-cli", "opencode"):
                    orden = [x for x in orden if x not in ("--output-format", "json", "--format")]
            try:
                r = subprocess.run(orden, input=entrada, cwd=cwd, env={**env, **entorno(m)}, capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", timeout=timeout, shell=isinstance(orden, str))
                texto = final.read_text(encoding="utf-8") if final.exists() else (r.stdout or "")
                ultimo = (r.returncode, texto)
            except subprocess.TimeoutExpired:
                ultimo = (-9, "")
        if ultimo[1].strip():
            return ultimo
    return ultimo


# --- Consultas ---------------------------------------------------------------------------------

def _post(url: str, cuerpo: dict, cabeceras: dict, timeout: int = 180) -> dict:
    req = urllib.request.Request(url, data=json.dumps(cuerpo).encode(), method="POST",
                                 headers={"content-type": "application/json", **cabeceras})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _consulta(e: dict, prompt: str, cwd: str | None) -> str:
    m = motor(e["motor"])
    modelo = e.get("modelo")
    if not instalado(m):
        raise RuntimeError("no está instalado o falta su clave")
    clave = os.environ.get(m["clave_env"], "") if m.get("clave_env") else ""
    if m["tipo"] == "api":                         # Anthropic Messages API
        d = _post(f"{m['url']}/messages", {"model": modelo or "claude-sonnet-5-5", "max_tokens": 4096,
                                            "messages": [{"role": "user", "content": prompt}]},
                  {"x-api-key": clave, "anthropic-version": "2023-06-01"})
        return "".join(b.get("text", "") for b in d.get("content", []))
    if m["tipo"] == "openai":                      # cualquier API compatible con OpenAI
        d = _post(f"{m['url'].rstrip('/')}/chat/completions",
                  {"model": modelo, "messages": [{"role": "user", "content": prompt}]},
                  {"authorization": f"Bearer {clave}"} if clave else {})
        return d["choices"][0]["message"]["content"] or ""
    n = m["nombre"] if m["nombre"] in SERIE else m.get("base", m["nombre"])
    exe = shutil.which(m.get("orden", m["nombre"])) or m.get("orden", m["nombre"])
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", **entorno(m)}
    if n == "claude-code":
        r = subprocess.run([exe, "-p", "--output-format", "json", *(["--model", modelo] if modelo else []),
                            "--max-turns", "1", "--tools", ""], input=prompt, cwd=cwd, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", env=env)
        try:
            return json.loads(r.stdout).get("result", "")
        except json.JSONDecodeError:
            return r.stdout
    # El resto de agentes: en una carpeta vacía, con las instrucciones en un fichero.
    with tempfile.TemporaryDirectory(prefix="kumi-consulta-") as tmp:
        ins, final = Path(tmp) / "instrucciones.md", Path(tmp) / "respuesta.txt"
        ins.write_text(prompt + "\n\nResponde solo con el texto pedido. No ejecutes nada ni crees ficheros.",
                       encoding="utf-8")
        orden, entrada = _orden_agente(m, e, ins, final)
        if n == "codex":
            orden = [x for x in orden if x != "--json"]
        if n in ("gemini-cli", "opencode"):
            orden = [x for x in orden if x not in ("--output-format", "json", "--format")]
        r = subprocess.run(orden, input=entrada, cwd=tmp, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=env, shell=isinstance(orden, str))
        return final.read_text(encoding="utf-8") if final.exists() else r.stdout


def consultar(elecciones: list[dict], prompt: str, cwd: str | None = None) -> str:
    """Pregunta corta: prueba cada elección hasta que una conteste algo."""
    errores = []
    for e in elecciones:
        try:
            texto = _consulta(e, prompt, cwd)
            if texto and texto.strip():
                return texto
            errores.append(f"{describir(e)}: respuesta vacía")
        except Exception as ex:  # noqa: BLE001 — cualquier fallo de un proveedor pasa al siguiente
            errores.append(f"{describir(e)}: {ex}")
    print("[kumi] ninguna consulta contestó: " + "; ".join(errores), file=__import__("sys").stderr)
    return ""


# --- El contrato --------------------------------------------------------------------------------

ESTADOS = {"ok", "fallo", "bloqueado", "ascender"}
GRAVEDADES = {"alta", "media", "baja"}


def validar_resultado(res) -> tuple[dict, list[str]]:
    """Comprueba y normaliza un resultado.json. Devuelve (resultado limpio, problemas)."""
    problemas = []
    if not isinstance(res, dict):
        return {"estado": "fallo", "resumen": "resultado.json no es un objeto"}, ["no es un objeto JSON"]
    r = dict(res)
    est = str(r.get("estado", "")).strip().lower()
    if est not in ESTADOS:
        problemas.append(f"`estado` debe ser uno de {sorted(ESTADOS)} (vino {r.get('estado')!r})")
    r["estado"] = est
    if not isinstance(r.get("resumen"), str) or not r["resumen"].strip():
        problemas.append("falta `resumen` (una frase)")
        r["resumen"] = str(r.get("resumen") or "")
    hall = r.get("hallazgos", [])
    if not isinstance(hall, list):
        problemas.append("`hallazgos` debe ser una lista")
        hall = []
    limpios = []
    for h in hall:
        if isinstance(h, str):
            h = {"gravedad": "media", "descripcion": h}
        if not isinstance(h, dict):
            continue
        g = str(h.get("gravedad", "")).strip().lower()
        if g not in GRAVEDADES:
            problemas.append(f"un hallazgo tiene `gravedad` {h.get('gravedad')!r} (alta, media o baja)")
            g = "media"
        limpios.append({**h, "gravedad": g})
    r["hallazgos"] = limpios
    try:
        r["bloqueantes"] = int(r.get("bloqueantes") or 0)
    except (TypeError, ValueError):
        problemas.append("`bloqueantes` debe ser un número")
        r["bloqueantes"] = 0
    if r.get("tipo_cambio") not in (None, "arreglo", "funcionalidad"):
        problemas.append("`tipo_cambio` debe ser arreglo o funcionalidad")
        r["tipo_cambio"] = None
    return r, problemas


def leer_resultado(f: Path) -> tuple[dict | None, list[str]]:
    if not f.exists():
        return None, ["no existe resultado.json"]
    texto = f.read_text(encoding="utf-8", errors="replace")
    try:
        datos = json.loads(texto)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", texto, re.S)     # algún modelo lo envuelve en ```json
        try:
            datos = json.loads(m.group(0)) if m else None
        except json.JSONDecodeError:
            datos = None
        if datos is None:
            return None, ["resultado.json no es JSON válido"]
    return validar_resultado(datos)
