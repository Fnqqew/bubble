# Imágenes del README

Scripts que generan `docs/demo.gif`, `docs/como-funciona.png` y `docs/interfaz.png` con el código real de Bubble
(píldoras, burbujas, barra para escribir y subtítulos). Las ventanas se capturan con PrintWindow, transparentes y
sin tomar el foco: nunca sale lo que hay detrás en tu pantalla.

Desde la raíz del proyecto, con una carpeta de trabajo `$T` (y `imageio-ffmpeg` instalado en `$T/pylib`):

```bash
python=.venv/Scripts/python.exe
mkdir -p "$T/media/compose"
$python tools/media/capture_compose.py "$T/media/compose" tools/media   # la barra, en cada etapa
$python tools/media/shot_main.py "$T/main_raw.png" tools/media          # la ventana principal
PYTHONPATH="$T/pylib" $python tools/media/make_demo.py "$T" "$T/out"   # GIF (y MP4) + cuadros fijos
$python tools/media/make_stills.py "$T" "$T/out"                       # cómo funciona + interfaz
cp "$T/out/demo.gif" "$T/out/como-funciona.png" "$T/out/interfaz.png" docs/
```

La imagen de Bubble Pro (`docs/pro.png`: Basic y Pro lado a lado, la barra en Pro y el aviso del juego):

```bash
mkdir -p "$T/pro/compose_pro"
$python tools/media/shot_main.py inicio "$T/pro" oscuro          # Basic
$python tools/media/shot_main.py pro "$T/pro" oscuro pro         # la página ✦ Pro, con Pro activo
$python tools/media/capture_compose.py "$T/pro/compose_pro" tools/media pro
$python tools/media/make_pro.py "$T/pro" "$T/out" && cp "$T/out/pro.png" docs/
```

La guía de instalación (`docs/instalacion.gif` y `docs/instalacion/paso-N.png`), con la ventana «Preparar Bubble» real
en cada etapa (no instala nada):

```bash
mkdir -p "$T/install"
$python tools/media/shot_install.py "$T/install" tools/media              # «Preparar Bubble» en cada etapa
$python tools/media/shot_main.py inicio "$T/install" oscuro             # la ventana lista para jugar
$python tools/media/make_install.py "$T/install" "$T/install/out"
cp "$T/install/out/instalacion.gif" docs/ && cp "$T/install/out/instalacion/"paso-*.png docs/instalacion/
```
