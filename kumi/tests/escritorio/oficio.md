# Oficio: test de escritorio (Windows)

Cómo se prueba una app de escritorio de Windows como la usaría una persona: arrancarla, mirarla, usarla y cerrarla. Vale para Python (tkinter, Qt, bandeja), Electron y Tauri, y para apps con instalador (Inno Setup, como `D:\Troba\escritorio` o DracPDF).

**Es el PC de {humano} y puede estar usándolo.** Interactúa con patrones de UI Automation (Invoke, Value, Toggle, Window), que no mueven el ratón ni roban el foco. Teclado o ratón simulados (`SendKeys`, `SendInput`) solo si no hay otro camino, en ráfagas cortas y apuntado en el informe. No cambies la escala, el tema ni ningún ajuste de Windows: es su configuración.

## Arrancar

- Desde el worktree, con la orden que diga el encargo o el oficio de la factoría (`uv run python -m <app>`, `npm start`, el `.exe` construido). Lánzala con `Start-Process -PassThru` y **guarda el PID**: todo lo demás va por ese PID.
- Espera a que tenga ventana (hasta 30 s, mirando `MainWindowHandle` o la ventana UIA del PID). Si es una app de bandeja sin ventana, búscala en la bandeja (árbol UIA de `Shell_TrayWnd` y del desbordamiento `NotifyIconOverflowWindow`) y abre su menú por UIA.
- Si se cierra sola o no aparece la ventana: hallazgo `alta` con la salida de consola (redirige stdout y stderr a `evidencias/arranque.txt`).
- Si la app escucha en un puerto, el primer arranque puede sacar el diálogo del firewall: no lo aceptes tú. Pruébala por `127.0.0.1` y apunta si la LAN queda como no comprobable.

## Herramienta: `ventana.ps1`

Guárdala en tu carpeta de trabajo **en UTF-8 con BOM** (PowerShell 5.1 lee como ANSI un `.ps1` sin BOM y estropea las tildes de los mensajes; aquí el BOM es lo correcto, al revés que en los ficheros que leen otras herramientas) y úsala con `powershell -NoProfile -ExecutionPolicy Bypass -File ventana.ps1 -ProcId <pid> -Salida <png> [-Pulsar "<nombre>"] [-Escribir "<nombre>=<texto>"] [-Cerrar]`. Captura la ventana en píxeles reales (aunque esté tapada), dice su DPI y lista los controles; pulsa o escribe por UIA; cierra con el patrón Window (como la X, no matando).

```powershell
param([int]$ProcId, [string]$Salida, [string]$Pulsar, [string]$Escribir, [switch]$Cerrar)
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes, System.Drawing
Add-Type @"
using System; using System.Runtime.InteropServices;
public static class W {
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr hdc, uint f);
  [DllImport("user32.dll")] public static extern bool SetProcessDpiAwarenessContext(IntPtr c);
  [DllImport("user32.dll")] public static extern uint GetDpiForWindow(IntPtr h);
  [DllImport("dwmapi.dll")] public static extern int DwmGetWindowAttribute(IntPtr h, int a, out RECT r, int s);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }
}
"@
[void][W]::SetProcessDpiAwarenessContext([IntPtr]-4)   # sin esto PowerShell 5.1 mide y captura a 96 dpi
$A = [System.Windows.Automation.AutomationElement]; $TS = [System.Windows.Automation.TreeScope]
$win = $A::RootElement.FindFirst($TS::Children, (New-Object System.Windows.Automation.PropertyCondition($A::ProcessIdProperty, $ProcId)))
if (-not $win) { throw "sin ventana para el PID $ProcId" }
function Buscar($nombre) {
  $e = $win.FindFirst($TS::Descendants, (New-Object System.Windows.Automation.PropertyCondition($A::NameProperty, $nombre)))
  if (-not $e) { throw "no encuentro «$nombre»" }; $e }
if ($Pulsar) { (Buscar $Pulsar).GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke(); Start-Sleep -Milliseconds 700 }
if ($Escribir) { $n, $t = $Escribir -split '=', 2
  (Buscar $n).GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern).SetValue($t); Start-Sleep -Milliseconds 400 }
if ($Salida) {
  $h = [IntPtr]$win.Current.NativeWindowHandle
  $r = New-Object W+RECT; [void][W]::DwmGetWindowAttribute($h, 9, [ref]$r, 16)   # marco real, sin sombra
  $bmp = New-Object System.Drawing.Bitmap(($r.R - $r.L), ($r.B - $r.T)); $g = [System.Drawing.Graphics]::FromImage($bmp)
  $hdc = $g.GetHdc(); [void][W]::PrintWindow($h, $hdc, 2); $g.ReleaseHdc($hdc)   # 2 = PW_RENDERFULLCONTENT (Electron, WebView2)
  $bmp.Save($Salida, [System.Drawing.Imaging.ImageFormat]::Png); $g.Dispose(); $bmp.Dispose()
  "titulo=$($win.Current.Name) dpi=$([W]::GetDpiForWindow($h)) tam=$($r.R - $r.L)x$($r.B - $r.T)"
  $win.FindAll($TS::Descendants, [System.Windows.Automation.Condition]::TrueCondition) | Select-Object -First 80 |
    % { "{0} | {1} | {2}" -f $_.Current.ControlType.ProgrammaticName.Replace('ControlType.', ''), $_.Current.Name, $_.Current.AutomationId } }
if ($Cerrar) { $win.GetCurrentPattern([System.Windows.Automation.WindowPattern]::Pattern).Close() }
```

- tkinter expone poco a UI Automation (a menudo solo la ventana y algún botón sin nombre). Si no hay controles con nombre, el recorrido se hace con teclado (Tab, Enter, atajos) sobre la ventana con foco, y se dice en el informe. Qt y Electron exponen bien el árbol.
- Una captura en negro o vacía suele ser una ventana minimizada o en otro escritorio virtual: restáurala por el patrón Window (`SetWindowVisualState`) antes de capturar.

## Matriz

La escala real de cada monitor se lee con el `dpi` que devuelve `ventana.ps1` (96 = 100 %, 144 = 150 %). Los monitores de {humano} están al 100 %. La otra escala se fuerza **solo para el proceso de la app**, nunca cambiando la de Windows:

| Stack | 100 % | 150 % | Tema oscuro / claro |
|---|---|---|---|
| Electron | `--force-device-scale-factor=1` | `--force-device-scale-factor=1.5` | `--force-dark-mode` fuerza oscuro; claro, si la app tiene su propio ajuste |
| Tauri (WebView2) | env `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--force-device-scale-factor=1` | ídem con `1.5` (solo el contenido web; el marco sigue al sistema) | el ajuste propio de la app |
| Qt (PySide, PyQt) | env `QT_SCREEN_SCALE_FACTORS=1` | env `QT_SCREEN_SCALE_FACTORS=1.5` | el ajuste propio de la app |
| tkinter y otros Win32 | escala real del monitor | sin forma fiable de forzarlo por proceso: **no comprobable**, dilo | el ajuste propio de la app |

- Comprueba que lo forzado se aplicó: el tamaño de la ventana o de la letra en la captura tiene que cambiar. Si no cambia, esa celda es «no comprobable», no «cumple».
- Tema: prueba el que tenga el sistema (mira `AppsUseLightTheme` en el registro, o el ajuste del escritorio en Linux y macOS). El otro solo se prueba si la app permite elegirlo; si no, «no comprobable sin cambiar su configuración».
- Configs mínimas: `100-oscuro` y `150-oscuro`; `100-claro` si la app tiene tema propio. Si la app tiene icono de bandeja, una captura de la bandeja en oscuro (un icono negro sobre la barra oscura no se ve).
- En cada config: arrancar, capturar tras cada paso a `evidencias/<config>/<nn>-<paso>.png`, recorrer lo que pide el encargo y la navegación principal, y **mirar las capturas** (texto cortado o encima de otro, controles fuera de la ventana, letra borrosa por escalado de mapa de bits, contraste ilegible en el tema, icono con ruido de colores o vacío). Redimensiona la ventana a un tamaño pequeño razonable (patrón Transform) y captura otra vez.
- Errores: la consola de la app (stdout y stderr redirigidos), y en Electron también el proceso de renderer (`--enable-logging`). Una traza de Python o un `Uncaught` es `alta` si rompe el paso, `media` si no.

## Cerrar

- Siempre por la propia app: `-Cerrar` (la X), o la opción «Salir» del menú de bandeja. Espera hasta 10 s a que el PID desaparezca.
- Si no se cierra: hallazgo `media` («no cierra limpiamente») y entonces `Stop-Process -Id <pid>` **y sus hijos** (Electron y WebView2 dejan procesos colgando: búscalos con `Get-CimInstance Win32_Process -Filter "ParentProcessId=<pid>"`). Nunca `taskkill /im`: se llevaría otras apps de {humano} (Claude Code Meter, Chrome, otras Electron).
- Comprueba que no queda icono fantasma en la bandeja ni el puerto escuchando (`Get-NetTCPConnection -OwningProcess`).

## Prod tests (tras publicar)

Los lanza la central desde la receta del proyecto, contra lo publicado y en una carpeta temporal:

1. `gh release download <tag> -R <repo> -p "*.exe" -p "*.msi" -D <temp>`. Si hay `docs/updates.json`, el sha256 del fichero tiene que ser el del manifiesto y la versión, la del tag.
2. Firma y dependencias: `Get-AuthenticodeSignature` (apunta si no está firmado; es un dato, no un fallo, salvo que la receta diga que va firmado). Que no pida Python, Node ni redistribuibles: lo que se publica funciona con solo instalarlo.
3. Instalador por usuario (Inno Setup sin admin): `<setup>.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /DIR=<temp>\app /LOG=<temp>\instalar.log`. Ejecutable portable: directamente desde `<temp>`. Un instalador que pide administrador no se instala solo: prod test «no comprobable» y aviso.
4. Arrancar el ejecutable instalado, esperar la ventana, una captura con `ventana.ps1`, comprobar que el título o «Acerca de» dice la versión publicada, y cerrar limpio.
5. Desinstalar con el `unins000.exe` de la carpeta (`/VERYSILENT`) y comprobar que la carpeta queda vacía.

Si un instalador nuevo se instala encima de la versión que {humano} usa a diario (misma AppId), **no lo instales**: prueba el portable o extrae, y apúntalo.

## Limpieza

- La app cerrada, sin procesos hijos ni icono en la bandeja; lo instalado en prod tests, desinstalado.
- Borra la carpeta temporal de descargas e instalación. Las capturas se quedan en `evidencias/`.
- Las variables de entorno de escala y tema solo en el proceso que lanzaste (`$env:` dentro de esa sesión o `Start-Process` con su propio entorno): nada en el entorno de usuario ni en el registro.
- Si la app guarda datos de usuario (`%APPDATA%\<app>`), y es la misma app que {humano} usa, arranca la prueba con un perfil aparte si la app lo permite (variable o argumento de carpeta de datos); si no, no borres nada suyo y apunta qué cambió.
