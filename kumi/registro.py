"""El registro de Kumi: qué piezas hay para montar cadenas y qué cadenas hay ya construidas.

Piezas: skills, plugins (con sus skills, agentes y MCP), agentes sueltos y servidores MCP del usuario.
Cadenas: factorías de producción (factorias/*/factoria.toml) y cadenas de test (tests/*/test.toml).

Se regenera solo antes de cada tarea; a mano: `kumi registro`.
"""
from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from .config import CFG, catalogo_glob

CLAUDE = Path.home() / ".claude"


def _frontmatter(md: Path) -> dict:
    try:
        texto = md.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    m = re.match(r"---\s*\n(.*?)\n---", texto, re.S)
    datos = {}
    if m:
        for linea in m.group(1).splitlines():
            if ":" in linea and not linea.startswith(" "):
                k, v = linea.split(":", 1)
                datos[k.strip()] = v.strip().strip('"').strip("'")
    return datos


def _skills(carpeta: Path, origen: str) -> list[dict]:
    out = []
    for md in sorted(carpeta.glob("*/SKILL.md")):
        fm = _frontmatter(md)
        out.append({"nombre": fm.get("name", md.parent.name), "origen": origen,
                    "descripcion": fm.get("description", "")[:300], "ruta": str(md)})
    return out


def _agentes(carpeta: Path, origen: str) -> list[dict]:
    out = []
    for md in sorted(carpeta.glob("*.md")):
        fm = _frontmatter(md)
        out.append({"nombre": fm.get("name", md.stem), "origen": origen,
                    "descripcion": fm.get("description", "")[:300], "ruta": str(md)})
    return out


def _mcp_de(fichero: Path, origen: str) -> list[dict]:
    try:
        datos = json.loads(fichero.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    servidores = datos.get("mcpServers", datos if origen != "usuario" else {})
    return [{"nombre": n, "origen": origen} for n in servidores if isinstance(servidores.get(n), dict)]


def piezas() -> dict:
    p = {"skills": _skills(CLAUDE / "skills", "usuario"),
         "agentes": _agentes(CLAUDE / "agents", "usuario"),
         "mcp": _mcp_de(Path.home() / ".claude.json", "usuario"),
         "plugins": []}
    try:
        instalados = json.loads((CLAUDE / "plugins" / "installed_plugins.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        instalados = {}
    for nombre, versiones in instalados.get("plugins", {}).items():
        if not versiones:
            continue
        ruta = Path(versiones[0].get("installPath", ""))
        manifiesto = ruta / ".claude-plugin" / "plugin.json"
        desc = ""
        if manifiesto.exists():
            try:
                desc = json.loads(manifiesto.read_text(encoding="utf-8")).get("description", "")
            except json.JSONDecodeError:
                pass
        p["plugins"].append({"nombre": nombre, "descripcion": desc[:300], "ruta": str(ruta)})
        p["skills"] += _skills(ruta / "skills", nombre)
        p["agentes"] += _agentes(ruta / "agents", nombre)
        if (ruta / ".mcp.json").exists():
            p["mcp"] += _mcp_de(ruta / ".mcp.json", nombre)
    return p


def cadenas() -> dict:
    def leer(patron: str) -> list[dict]:
        out = []
        for f in catalogo_glob(patron):
            c = tomllib.loads(f.read_text(encoding="utf-8"))
            c["carpeta"] = str(f.parent)
            # Lo que el motor tiene que saber hacer (motores.SERIE[...]["capacidades"]). Por defecto, un agente
            # que ejecuta órdenes y toca ficheros: compilar y probar no se hace de otra manera.
            c.setdefault("necesita", ["shell", "ficheros"])
            out.append(c)
        return out
    return {"factorias": leer("factorias/*/factoria.toml"), "tests": leer("tests/*/test.toml")}


def publicar_oficios(factorias: list[dict]) -> None:
    """La ruta por defecto de cada factoría, como capa de OFICIO de `megafactoria-ruta` en Omoikane 1.8.

    Solo nombres de factorías y tests (nada de rutas ni proyectos: el control de fugas lo rechazaría).
    Regenerarla con el mismo contenido no crea versiones nuevas.
    """
    import shutil
    import subprocess
    if CFG.memoria != "omoikane" or not shutil.which("omoi"):
        return
    for f in factorias:
        if f.get("provisional"):
            continue
        data = {"tests": [f["nombre"]] + [x for x in f.get("tests", []) if x != f["nombre"]],
                "encadena_con": f.get("encadena_con", [])}
        subprocess.run(["omoi", "remember", "--kind", "fact", "--trade", f["nombre"], "--key", "megafactoria-ruta",
                        "--title", f"Megafactoría: ruta por defecto de la factoría {f['nombre']}",
                        "--data", json.dumps(data, ensure_ascii=False), "--dir", str(CFG.datos), "--source", "kumi",
                        "--tag", "megafactoria",
                        f"Factoría {f['nombre']}: pasa por las factorías de test {', '.join(data['tests'])}"
                        + (f"; se puede encadenar con {', '.join(data['encadena_con'])}" if data["encadena_con"] else "") + "."],
                       capture_output=True, text=True, encoding="utf-8")


def construir() -> dict:
    r = {"piezas": piezas(), **cadenas()}
    publicar_oficios(r["factorias"])
    CFG.datos.mkdir(parents=True, exist_ok=True)
    (CFG.datos / "registro.json").write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
    return r


if __name__ == "__main__":
    r = construir()
    p = r["piezas"]
    print(f"{len(p['skills'])} skills, {len(p['plugins'])} plugins, {len(p['agentes'])} agentes, "
          f"{len(p['mcp'])} MCP · {len(r['factorias'])} factorías, {len(r['tests'])} cadenas de test")
