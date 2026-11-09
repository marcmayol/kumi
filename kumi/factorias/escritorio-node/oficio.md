# Oficio: fábrica de escritorio con Node (Electron, Tauri)

Lo que sabe esta fábrica del stack, valga para el proyecto que valga. Lo propio de cada proyecto (versión, firma, instalador, publicación) no va aquí: va en el brain del proyecto y en su receta.

## Entorno

- Gestor de paquetes según el lockfile del repo: `package-lock.json` → `npm ci`; `pnpm-lock.yaml` → `pnpm install --frozen-lockfile`; `yarn.lock` → `yarn install --frozen-lockfile`. No cambies de gestor ni regeneres el lockfile entero.
- **Nunca `npm install` en una carpeta sin `package.json` propio**: npm sube por las carpetas padre hasta el primero que encuentra (a veces el de la carpeta personal) y lo modifica. Antes de instalar, `npm prefix` tiene que devolver el worktree.
- Nada global (`npm i -g`). Herramientas con `npx` del propio repo.
- C: va justo de espacio. Si una compilación de Tauri o un `electron-builder` va a dejar cientos de MB, que la salida y la caché de Cargo (`CARGO_TARGET_DIR`) vayan a D:.

## Compilar y probar

- Arranque de desarrollo: el script `start`/`dev` del `package.json`. Para comprobar que abre, lánzalo en segundo plano, espera a que aparezca la ventana y ciérralo tú (por su PID, no `taskkill /im electron.exe`, que se lleva otras apps Electron de {humano}).
- Tests: el script `test` si hace algo de verdad (en muchos repos es el `echo "Error: no test specified"` de plantilla: eso no es un test). Si hay `lint` o `typecheck`, pásalos.
- Empaquetar (`dist`, `dist:win`, `tauri build`) solo cuando la tarea toque empaquetado, icono, instalador o el proceso principal: tarda y pesa. Si lo haces, apunta el nombre y el tamaño del instalador.
- No toques `version` del `package.json` (ni la de `tauri.conf.json`/`Cargo.toml`): la sube la central al publicar.
- Electron se actualiza a menudo y rompe APIs: si subes la versión mayor de `electron`, es una tarea en sí misma, no un efecto secundario.

## Seguridad del renderer (Electron)

- `contextIsolation: true`, `nodeIntegration: false`, `sandbox: true` y nada de `webSecurity: false`. Si el proyecto ya lo tiene mal, no lo empeores y dilo en el documento.
- El `preload` expone lo mínimo con `contextBridge`: funciones concretas, nunca `ipcRenderer` entero ni `require`.
- Enlaces externos con `shell.openExternal` solo para `https:` y tras comprobar la URL; `setWindowOpenHandler` que deniegue por defecto.
- En Tauri, permisos (`capabilities`) por ventana y lo justo; no `csp: null`.

## Escritorio Windows

- Un cambio de interfaz no está hecho hasta verlo en pantalla. Lo verá la cadena `escritorio`; tú arranca la app y comprueba que abre sin errores en consola.
- La app tiene que verse bien al 100 % y al 150 % de escalado, y respetar el tema claro u oscuro de Windows si la interfaz es suya (`nativeTheme` en Electron).
- Icono: `build/icon.ico` con todos los tamaños (16, 24, 32, 48, 64, 128, 256). Dentro del `.ico`, DIB salvo el de 256 px, que va en PNG; con PNG en los pequeños sale ruido de colores sin ningún error.
- Datos del usuario en `app.getPath('userData')`, nunca junto al ejecutable.
- Si la app escucha en un puerto para la LAN, necesita regla de firewall (`profile=any`, acotada al ejecutable y al puerto, borrar antes de añadir) y sube a riesgo 2.
- Ficheros que lee otra herramienta, en UTF-8 sin BOM (ojo con PowerShell 5.1 y `Set-Content -Encoding utf8`).

## Publicación

- Lo publicado tiene que funcionar con solo instalarlo: nada de Node ni redistribuibles aparte.
- Ni `electron-builder --publish`, ni `gh release`, ni actualizadores automáticos desde la factoría: publicar lo hace la central con la receta del proyecto.
