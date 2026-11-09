# Oficio: fábrica Python

Lo que sabe esta fábrica del stack, valga para el proyecto que valga: apps de escritorio Windows (tkinter, Qt, bandeja), scripts, servicios y librerías. Lo propio de cada proyecto (versión, empaquetado, publicación) no va aquí: va en el brain del proyecto y en su receta.

## Qué cadenas de test le tocan

- Siempre `python` y `cyber`.
- **Si el repo es una app de escritorio** (abre ventanas, vive en la bandeja, tiene instalador `.iss` o `.spec` de PyInstaller), el encargado añade `escritorio`. La señal: imports de `tkinter`, `PySide6`, `PyQt5/6`, `pystray`, `customtkinter`, `wx` o `webview`, ficheros `.pyw`, `.ico`, `.iss` o `.spec`. Está propuesto en `factoria.toml` (`tests_si`), pero Kumi aún no lo lee.
- Si tocas interfaz y la tarea no lleva `escritorio`, dilo en `ajuste.md` o en `para_test.md`: sin esa cadena nadie la va a mirar en pantalla.

## Entorno

- Usa el entorno del repo, no el Python global. Si hay `uv.lock` o `pyproject.toml` con `[tool.uv]`: `uv sync` y `uv run …`. Si hay `.venv` o un script de activar (`activar.ps1`), ese. Si solo hay `requirements*.txt`: `uv venv` y `uv pip install -r …` dentro del worktree. **Nunca** `pip install` en el Python del sistema ni `--user`.
- Scripts sueltos con cabecera `# /// script` (PEP 723): `uv run script.py`, y las dependencias nuevas se añaden a esa cabecera, no a un requirements aparte.
- Algunos repos tienen el entorno pesado fuera del árbol (un entorno virtual o los modelos de Hugging Face en otro disco, enlazados con un junction o un symlink). No lo dupliques en el disco del sistema, que va justo de espacio: lo pesado, a D:.
- Antes de cargar modelos o datasets grandes, mira la RAM libre (`Get-CimInstance Win32_OperatingSystem | % FreePhysicalMemory`) y deja margen. No lances entrenamientos ni procesos largos de GPU: eso necesita el sí de {humano}.

## Compilar y probar

- Tests: `uv run pytest -q` (o el que diga el `pyproject.toml`). Si el repo configura `ruff` o `mypy` (en `pyproject.toml`, `.pre-commit-config.yaml` o la CI), pásalos también y no metas avisos nuevos. Si no los usa, no los introduzcas.
- Los repos sin tests (bancos de prueba, scripts) se comprueban **ejecutando** lo que tocaste con una entrada pequeña y real, y apuntando la salida.
- Si cambias una función que usan otros módulos, busca quién la llama (`rg`) y prueba también eso.
- No toques la versión (`__version__`, `version` del `pyproject.toml`): la sube la central al publicar.
- Nada de `print` de depuración ni rutas absolutas de la máquina de {humano} metidas en el código.

## Escritorio Windows

- Un cambio de interfaz no está hecho hasta verlo en pantalla. Lo verá la cadena `escritorio`; tú al menos arranca la app un momento y comprueba que abre sin traza.
- Escalado: la app tiene que verse bien al 100 % y al 150 %. tkinter necesita `SetProcessDpiAwareness` (o el manifiesto) para no verse borroso, y entonces los tamaños fijos en píxeles se quedan pequeños: medidas relativas a la fuente o multiplicadas por el factor de escala.
- Tema: Windows 11 pone la barra de tareas oscura de fábrica. Un icono de bandeja negro ahí no se ve: lee `SystemUsesLightTheme` en `HKCU\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize`.
- Iconos `.ico` generados por código: las imágenes van como **DIB**, salvo la de 256 px, que va en **PNG**. Con PNG en los tamaños pequeños sale ruido de colores, sin ningún error.
- A 16 px no se distinguen estados por opacidad: usa forma (una marca en la esquina).
- Una app que **escucha en un puerto** para la LAN no funciona sin regla de firewall, y el síntoma engaña (el ping va, el TCP muere en timeout). Regla en el instalador acotada al ejecutable y al puerto, `profile=any`, borrar antes de añadir. Esto sube a riesgo 2.
- Rutas del usuario con `%APPDATA%`/`%LOCALAPPDATA%` (`os.environ` o `platformdirs`), nunca junto al ejecutable: en `Program Files` no se puede escribir.
- Ficheros que lee otra herramienta, en UTF-8 **sin BOM**. En PowerShell 5.1, `Set-Content -Encoding utf8` mete BOM: usa `[IO.File]::WriteAllText($ruta, $texto, [Text.UTF8Encoding]::new($false))`. Y `-replace` no distingue mayúsculas: `-creplace`.
- En Python, abre los ficheros de texto con `encoding="utf-8"` explícito: en Windows el valor por defecto es cp1252.

## Empaquetado y publicación

- Si el proyecto tiene su ritual (`scripts/publicar_release.py`, `build_exe.ps1`, `.iss`), úsalo solo para comprobar que se construye; publicar lo hace la central con la receta.
- Lo que se publica tiene que funcionar con solo instalarlo: el `.exe` no puede pedir Python, uv ni redistribuibles que el usuario no tenga.
- Nada de PyPI, GitHub Releases ni `gh` desde la factoría.
