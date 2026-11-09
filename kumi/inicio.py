"""`kumi init`: deja una instalación lista para usar.

Crea la carpeta de datos (~/.kumi o KUMI_HOME) con un kumi.toml mínimo y la copia de referencia con todas las
opciones, comprueba qué hace falta (git, un motor de agentes, Omoikane si se quiere memoria) y termina con el
diagnóstico de `kumi config`.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .config import CASA, RAIZ

MINIMO = """# Configuración de Kumi. Todas las opciones, comentadas: kumi.ejemplo.toml (en esta misma carpeta).
# `kumi config` dice qué motor hace cada papel y qué falta.

[persona]
nombre = "{nombre}"                # quien aprueba: sale en los avisos y en las instrucciones de los agentes
idioma = "es"

[rutas]
proyectos = "{proyectos}"          # dónde crea Kumi los proyectos nuevos

[memoria]
motor = "{memoria}"                # "omoikane" (perfiles por proyecto, cola de decisiones) o "ninguna"

[gasto]                            # en dólares; 0 = sin tope
por_tarea_usd = 15
por_dia_usd = 60

[papeles]                          # una lista por papel: si el primero falla o no está, el siguiente
factoria = ["claude-code"]
test     = ["claude-code"]
revisor  = ["claude-code:opus"]

[canales.escritorio]

[enrutado]
aviso     = ["escritorio"]
pr_humana = ["escritorio"]
pregunta  = ["escritorio"]
"""


def _pregunta(texto: str, defecto: str) -> str:
    try:
        r = input(f"{texto} [{defecto}]: ").strip()
    except EOFError:
        r = ""
    return r or defecto


def init(nombre: str | None, proyectos: str | None, sin_preguntar: bool) -> None:
    CASA.mkdir(parents=True, exist_ok=True)
    print(f"Carpeta de Kumi: {CASA}")
    ejemplo = CASA / "kumi.ejemplo.toml"
    shutil.copyfile(RAIZ / "kumi.ejemplo.toml", ejemplo)
    toml = CASA / "kumi.toml"
    if toml.exists():
        print(f"Ya hay configuración en {toml}; no la toco. Referencia actualizada: {ejemplo}")
    else:
        hay_omoi = bool(shutil.which("omoi"))
        if not sin_preguntar:
            nombre = nombre or _pregunta("¿Quién aprueba lo que se publica?", "yo")
            proyectos = proyectos or _pregunta("¿Dónde van los proyectos nuevos?", str(Path.home() / "proyectos"))
        toml.write_text(MINIMO.format(nombre=nombre or "yo",
                                      proyectos=Path(proyectos or Path.home() / "proyectos").as_posix(),
                                      memoria="omoikane" if hay_omoi else "ninguna"), encoding="utf-8")
        print(f"Creado {toml}")
    print()
    faltan = []
    if not shutil.which("git"):
        faltan.append("git (obligatorio: cada tarea trabaja en un worktree)")
    if not shutil.which("gh"):
        faltan.append("gh, la CLI de GitHub (para escuchar la CI y publicar releases; opcional)")
    agentes = [m for m in ("claude", "codex", "gemini", "opencode", "aider") if shutil.which(m)]
    if not agentes:
        faltan.append("un motor de agentes: Claude Code (claude), Codex, Gemini CLI, OpenCode o Aider")
    if not shutil.which("omoi"):
        print("Omoikane (omoi) no está: Kumi funciona sin memoria. Para perfiles por proyecto y cola de decisiones:\n"
              "  https://github.com/marcmayol/omoikane\n")
    print("Motores encontrados: " + (", ".join(agentes) or "ninguno"))
    for f in faltan:
        print(f"  Falta {f}")
    print()
    import sys
    sys.stdout.flush()
    subprocess.run([sys.executable, "-m", "kumi", "config"])
    print("\nPrimera tarea:\n  kumi tarea --proyecto <carpeta de un repo git> \"lo que quieres que haga\"")
