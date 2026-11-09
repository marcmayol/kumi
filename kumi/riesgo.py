"""Nivel de riesgo de un cambio, calculado por código sobre el diff. Nunca lo decide el agente.

Solo sirve para la puerta de salida: nivel 0-1 se puede publicar solo; nivel 2 lo aprueba una persona.

    0  textos, documentación              → se publica solo
    1  código normal                       → se publica solo (si los tests y la revisión pasan)
    2  datos del usuario, permisos, firma, secretos, CI, la propia receta de publicar
                                           → se para y espera aprobación humana

Reglas: factorias/riesgo.toml (comunes) + factorias/<nombre>/riesgo.toml (del stack).
Un fichero en una ruta de nivel 0 sigue subiendo a 2 si lo que añade casa con un patrón de nivel 2.
"""
from __future__ import annotations

import fnmatch
import re
import subprocess
import tomllib
from pathlib import Path

RAIZ = Path(__file__).resolve().parent


def _reglas(fabricas: str | list[str] | None) -> dict:
    """Reglas comunes + las de cada factoría que casa con el repo (en un repo mixto, todas cuentan)."""
    if isinstance(fabricas, str) or fabricas is None:
        fabricas = [fabricas] if fabricas else []
    juntas = {"nivel0": {"rutas": []}, "nivel1": {"rutas": []},
              "nivel2": {"rutas": [], "contenido": [], "borrado": []}}
    from .config import catalogo, nota_local
    locales = [nota_local("factorias", f"{x}.riesgo.toml") for x in ["comun", *fabricas]]
    for f in [catalogo("factorias", "riesgo.toml"), *(catalogo("factorias", x, "riesgo.toml") for x in fabricas),
              *(x for x in locales if x)]:
        if f.exists():
            r = tomllib.loads(f.read_text(encoding="utf-8"))
            for nivel, claves in juntas.items():
                for k in claves:
                    claves[k] += r.get(nivel, {}).get(k, [])
    return juntas


def _casa(ruta: str, patrones: list[str]) -> str | None:
    for p in patrones:
        if fnmatch.fnmatch(ruta, p) or fnmatch.fnmatch("/" + ruta, p):
            return p
    return None


def _git(obra: Path, *args: str) -> str:
    r = subprocess.run(["git", *args], cwd=obra, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout


def evaluar(obra: Path, base: str, fabrica: str | None) -> dict:
    """Compara la rama de `obra` con `base` (lo ya comprometido y lo que quede sin commitear)."""
    reglas = _reglas(fabrica)
    rutas = set(_git(obra, "diff", "--name-only", "--no-renames", f"{base}...HEAD").split())
    rutas |= set(_git(obra, "diff", "--name-only", "--no-renames", "HEAD").split())
    rutas |= set(_git(obra, "ls-files", "--others", "--exclude-standard").split())
    if not rutas:
        return {"nivel": 0, "motivos": ["sin cambios"], "ficheros": {}}

    contenido = [re.compile(p) for p in reglas["nivel2"]["contenido"]]
    borrado = [re.compile(p) for p in reglas["nivel2"]["borrado"]]
    nivel, motivos, ficheros = 0, [], {}
    for ruta in sorted(rutas):
        if p := _casa(ruta, reglas["nivel2"]["rutas"]):
            n, m = 2, f"{ruta}: ruta de nivel 2 ({p})"
        elif _casa(ruta, reglas["nivel1"]["rutas"]):
            n, m = 1, None                       # texto que cambia lo que hace algo (SKILL.md, agentes)
        elif _casa(ruta, reglas["nivel0"]["rutas"]):
            n, m = 0, None
        else:
            n, m = 1, None
        # Lo que se añade manda: datos o secretos en un fichero «inofensivo» siguen siendo nivel 2.
        if n < 2 and (contenido or borrado):
            try:
                diff = _git(obra, "diff", f"{base}", "--", ruta)
            except RuntimeError:
                diff = ""
            if not diff and (obra / ruta).is_file():
                diff = "\n".join("+" + l for l in (obra / ruta).read_text(encoding="utf-8", errors="replace").splitlines())
            añadidas = "\n".join(l[1:] for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++"))
            quitadas = "\n".join(l[1:] for l in diff.splitlines() if l.startswith("-") and not l.startswith("---"))
            for patron in contenido:
                if patron.search(añadidas):
                    n, m = 2, f"{ruta}: añade «{patron.pattern}»"
                    break
            else:
                # Quitar también arriesga: un `noindex` o una comprobación de permisos que desaparece.
                for patron in borrado:
                    if patron.search(quitadas):
                        n, m = 2, f"{ruta}: quita «{patron.pattern}»"
                        break
        ficheros[ruta] = n
        nivel = max(nivel, n)
        if m:
            motivos.append(m)
    if nivel == 1:
        motivos.append("código normal")
    elif nivel == 0:
        motivos.append("solo textos o documentación")
    return {"nivel": nivel, "motivos": motivos, "ficheros": ficheros}


if __name__ == "__main__":
    import argparse
    import json
    import sys

    for flujo in (sys.stdout, sys.stderr):
        flujo.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Nivel de riesgo de un cambio")
    ap.add_argument("obra", type=Path)
    ap.add_argument("--base", default="main")
    ap.add_argument("--fabrica", action="append", help="repetible: todas las factorías que casan")
    a = ap.parse_args()
    print(json.dumps(evaluar(a.obra, a.base, a.fabrica), ensure_ascii=False, indent=2))
