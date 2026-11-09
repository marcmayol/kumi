# Oficio: fábrica Android

Lo que sabe esta fábrica del stack, valga para el proyecto que valga. Lo propio de cada proyecto (versión, firma, publicación) no va aquí: va en el brain del proyecto y en su receta.

## Compilar y probar

- Compila con el wrapper del repo: `./gradlew assembleDebug` (en Windows, `gradlew.bat`). Tests unitarios: `./gradlew testDebugUnitTest`. Si hay `lint` configurado, también.
- Las tareas de Gradle tardan: lánzalas con timeout largo y no repitas la misma compilación sin cambios.
- No toques versionCode ni versionName: los sube la central al publicar.

## Interfaz

- Un cambio de interfaz no está hecho hasta verlo en pantalla. La compilación y los tests no bastan.
- Textos en `res/values*/strings.xml`, nunca escritos a mano en el código, y con todas las traducciones que tenga la app.
- El texto que se enseña no se guarda en la base de datos: se guarda un código y se traduce al pintar.
- Lo que el usuario escribe y vive solo en `remember` se pierde al girar o al matar el proceso: `rememberSaveable` o ViewModel.
