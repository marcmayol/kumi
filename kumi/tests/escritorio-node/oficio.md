# Oficio: test escritorio-node (Electron, Tauri)

La factoría de test propia de las apps de escritorio hechas con Node. Prueba lo que es de este stack: que se instala, que pasa sus tests, que **se empaqueta** para Windows y que el ejecutable empaquetado arranca. Recorrer la ventana como una persona lo hace además la cadena común `escritorio`; aquí no lo repitas.

## 1. Dependencias limpias

- `npm ci` (o el gestor que fije el lockfile: `pnpm-lock.yaml`, `yarn.lock`). Si el lockfile no casa con `package.json`, hallazgo `alta`.
- Dependencias nuevas o actualizadas en el diff: compruébalas en `npm audit --omit=dev` y apúntalas. Una versión mayor nueva de Electron o de Tauri es un cambio de plataforma: avisa en el informe aunque compile.

## 2. Tests del repo

- `npm test` si existe de verdad. La plantilla `echo "Error: no test specified" && exit 1` **no es un fallo de la tarea**: anótala como «el repo no tiene tests» (`baja`) y sigue.
- Lint o comprobación de tipos si el repo los tiene (`npm run lint`, `tsc --noEmit`).

## 3. Empaquetado de Windows

- Empaqueta sin firmar y en una carpeta temporal, nunca encima de una release: electron-builder con `--win --dir` y `CSC_IDENTITY_AUTO_DISCOVERY=false`; Tauri con `npm run tauri build -- --no-bundle` o el equivalente.
- Si no empaqueta, hallazgo `alta` con las últimas líneas del error.
- Compara el tamaño con el empaquetado de la rama base si te da tiempo: si crece más de un 20 % sin motivo en el diff, `media`.

## 4. El ejecutable empaquetado arranca

- Arranca el `.exe` empaquetado (no `npm start`: lo que se publica es el empaquetado), espera a que tenga ventana principal (UI Automation o `Get-Process … MainWindowHandle`) y ciérralo **por su PID**, nunca por nombre.
- Si se cierra solo, se queda sin ventana o escribe errores en la consola, `alta`.
- Electron: comprueba en `main` que siguen `contextIsolation: true`, `nodeIntegration: false` y `sandbox` como estaban. Si el diff los relaja, `alta`.

## 5. Limpieza

Borra la carpeta de empaquetado temporal y `node_modules` si los creaste tú en el worktree. No dejes procesos de la app abiertos.

## Prod tests

Tras publicar: descarga el instalador o el ejecutable de la Release a una carpeta temporal, comprueba su tamaño y su hash si la Release los publica, arráncalo, espera a la ventana y ciérralo por PID. **No lo instales encima de la versión que {humano} usa a diario.**
