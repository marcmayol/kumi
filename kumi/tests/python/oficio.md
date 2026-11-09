# Oficio: test Python

Cómo se prueba código Python como lo usaría una persona: su batería de tests, sus comprobadores y, sobre todo, **ejecutar** lo que cambió con una entrada real. La interfaz de escritorio no es de esta cadena: es de `escritorio`.

## Entorno

- Todo dentro del worktree y con el gestor del repo: `uv.lock` → `uv sync` y `uv run …`; `requirements*.txt` → `uv venv .venv-kumi` y `uv pip install -r …` en ese venv; script con cabecera `# /// script` → `uv run script.py`. **Nunca** `pip install` en el Python del sistema ni `--user`.
- Si el repo tiene el entorno pesado fuera (un entorno o unos modelos en otro disco), úsalo tal cual: no lo dupliques en el disco del sistema.
- Antes de algo que cargue modelos o datos grandes, mira la RAM libre (`Get-CimInstance Win32_OperatingSystem | % FreePhysicalMemory`) y deja margen holgado. No entrenes ni lances procesos largos de GPU.
- Python de Windows: si un script falla con `UnicodeEncodeError` al imprimir, prueba con `PYTHONUTF8=1` y apúntalo como hallazgo (`baja` si solo es la consola, `media` si rompe un fichero).

## Matriz

| Config | Qué se ejecuta |
|---|---|
| tests | `uv run pytest -q` (o lo que diga `pyproject.toml`); guarda la salida en `evidencias/tests/pytest.txt` |
| estilo | `ruff check` y `mypy` **solo si el repo los configura** (`pyproject.toml`, `.pre-commit-config.yaml`, CI). Un aviso nuevo en lo que cambió es `baja`; uno que el repo trata como error, `media` |
| uso | cada script, orden o función pública que tocó la factoría, ejecutado con una entrada pequeña y real (`--help` y un caso normal como mínimo); salida en `evidencias/uso/<nn>-<paso>.txt` |
| limpio | si hay `pyproject.toml` empaquetable: `uv build` en una carpeta temporal y, en un venv nuevo fuera del worktree, instalar la rueda e importar el paquete (detecta ficheros que faltan en el paquete) |

- Compara con la rama base: un test que ya fallaba en `main` no es culpa de este cambio; dilo, pero no como `alta`.
- Un test que falla a veces sí y a veces no: repítelo hasta 3 veces y apúntalo como inestable con las tres salidas.
- Si la tarea toca algo que escucha en un puerto, prueba con `127.0.0.1` y avisa (`media`) si escucha en `0.0.0.0` sin que el encargo lo pida.

## Prod tests (tras publicar)

Los lanza la central desde la receta del proyecto, contra lo ya publicado, en una carpeta temporal y sin tocar el repo:

- **Release de GitHub con `.exe` o instalador**: `gh release download <tag> -R <repo> -p "*.exe" -D <temp>`; comprueba que el sha256 coincide con el de `docs/updates.json` si existe; arranca el ejecutable con `--version` o `--help` si lo admite, o la app en segundo plano y confirma que sigue viva a los 10 s y crea su ventana (si es de escritorio, que lo haga la cadena `escritorio`).
- **PyPI**: en un directorio vacío, `uv run --no-project --with <paquete>==<versión> python -c "import <modulo>; print(<modulo>.__version__)"` y el punto de entrada con `--help`. La versión tiene que ser la publicada, no una de caché (`--refresh`).
- **Script o servicio desplegado**: la orden de salud que diga la receta (una petición a su endpoint, una ejecución en seco).

Si algo no arranca, falla el prod test y la salida va al informe.

## Limpieza

- Borra los venv y carpetas temporales que creaste fuera del worktree (`uv build`, instalación limpia, descargas de prod tests).
- Mata solo los procesos que arrancaste tú, por su PID (nunca `taskkill /im python.exe`: {humano} tiene otros Python corriendo).
- No dejes ficheros nuevos en el repo principal ni cachés en C: (`__pycache__`, `.pytest_cache` dentro del worktree no importan).
