# Instalar Bubble

<p align="center">
  <img src="instalacion.gif" alt="Animación de la instalación en 5 pasos: bajar Bubble, doble clic en Iniciar.bat, la ventana «Preparar Bubble» descargando todo, conectar tu cuenta de Claude y listo para jugar" width="100%">
</p>

Son cinco pasos, y la mayoría los hace Bubble solo. La primera vez tarda unos diez minutos (depende de tu internet);
después abre en segundos, con el acceso directo del escritorio.

No hace falta saber programar ni tener nada instalado de antemano: si te falta algo, Bubble te lo ofrece.

<br>

## Antes de empezar

- **Windows 10 (versión 2004 o más nueva) u 11**, de 64 bits. Si tu PC tiene menos de cinco años, seguro está bien.
- **Una suscripción de Claude, Pro o Max.** Es lo que traduce. Bubble no te cobra nada ni te pide tarjetas.
- **Roblox**, el de roblox.com o el de la Microsoft Store: andan los dos.
- **Unos 1,5 GB libres** e internet.
- Si querés que te escuchen traducido: **auriculares con micrófono** (los de siempre sirven).

<br>

## 1. Bajá Bubble

<img src="instalacion/paso-1.png" alt="La carpeta de Bubble descomprimida, con Iniciar.bat marcado" width="100%">

Entrá a [la última versión](https://github.com/Fnqqew/bubble/releases/latest) y bajá **Source code (zip)**. Después,
clic derecho sobre el ZIP → **Extraer todo**.

> **Un consejo:** dejá la carpeta en un lugar fijo, como Documentos. El acceso directo que crea Bubble apunta ahí:
> si después la movés, volvé a abrir `Iniciar.bat` desde el lugar nuevo y el acceso directo se arregla solo.

<br>

## 2. Doble clic en `Iniciar.bat`

<img src="instalacion/paso-2.png" alt="La consola de Iniciar.bat preparando Bubble por primera vez" width="100%">

Se abre una ventanita negra que prepara todo. Dejala trabajar: cuando termina, se cierra sola y abre Bubble.

- **Si Windows te dice «Windows protegió su PC»:** es porque el archivo vino de internet. Tocá **Más información →
  Ejecutar de todas formas**. Es un archivo de texto: si querés ver qué hace, abrilo con el Bloc de notas.
- **Si te falta Python** (lo que hace andar a Bubble), te pregunta *«Lo instalo ahora [S,N]?»*: tocá **S** y
  **Enter**. Si tu Windows no lo puede instalar solo, te abre la página de Python: bajalo, instalalo y volvé a abrir
  `Iniciar.bat`.

<br>

## 3. Bubble se arma solo

<img src="instalacion/paso-3.png" alt="La ventana «Preparar Bubble» descargando el reconocimiento de voz y las voces" width="100%">

Se abre **Preparar Bubble** y baja lo que necesita para entender voces y hablar por vos: el reconocimiento de voz
(hasta ~500 MB), el que distingue quién habla (~30 MB) y las voces en inglés (~120 MB). Podés seguir usando la PC
mientras tanto.

Si a tu Windows le faltan unos componentes de Microsoft (Visual C++), aparece **Instalar componentes**: tocalo,
Windows te pide permiso y listo. Bubble nunca instala nada de Windows a escondidas.

<br>

## 4. Conectá tu Claude

<img src="instalacion/paso-4.png" alt="«Preparar Bubble» con los botones Iniciar sesión e Instalar micrófono virtual marcados" width="100%">

Esta parte necesita que estés vos, porque es tu cuenta:

1. **Instalar Claude Code** (si no lo tenés): se abre el instalador oficial de Anthropic en una ventana aparte.
   Esperá a que termine.
2. **Iniciar sesión**: se abre Claude Code; seguí sus pasos y te lleva al navegador. Entrá con tu cuenta de Claude
   (la misma de claude.ai) y aceptá. Eso es todo: Bubble traduce con tu suscripción, sin claves ni pagos aparte.
3. **Instalar micrófono virtual** (opcional, pero es lo que hace que los demás te escuchen traducido): Windows pide
   permiso de administrador y, en el instalador, tocás **Install Driver**. Si te pide reiniciar, reiniciá.

<br>

## 5. ¡A jugar!

<img src="instalacion/paso-5.png" alt="La ventana de Bubble lista, y traducciones en el chat del juego" width="100%">

Elegí tu idioma y abrí Roblox. Bubble encuentra el chat solo y traduce mientras jugás.

- **Abrí Bubble antes que Roblox.** Así Roblox ya usa el micrófono virtual cuando arranca. Si Roblox ya estaba
  abierto, Bubble te avisa qué tocar.
- **Apagá la traducción automática de Roblox** (Esc → Configuración → *Traducción automática del chat*). Si no,
  Bubble lee mensajes ya traducidos y se pierde la jerga original.
- Un tutorial corto te acompaña la primera vez. Si lo salteás, está en **Ajustes → Ver el tutorial**.

<br>

## Bubble se adapta a tu PC

Cada vez que abre, Bubble mira tu equipo y se acomoda solo, sin que toques nada:

- **Memoria.** Con poca memoria abre menos sesiones de Claude y usa el reconocimiento de voz más liviano, para no
  quitarle fluidez a Roblox.
- **Procesador y placa de video.** Captura la pantalla con la placa de video si puede, y lee el chat más o menos
  seguido según tu procesador.
- **Pantalla.** La ventana toma el tamaño justo para tu pantalla y tu escala de Windows (100 %, 125 %, 150 %…).
- **Micrófonos.** Encuentra el tuyo y el virtual, y te avisa si falta alguno.
- **Tu cuenta de Claude.** Revisa que esté la sesión iniciada y que el plan sea Pro o Max.
- **Internet.** Una vez por día mide qué tan rápido llegás a Claude y a la nube de Bubble Pro. Si tu conexión es
  lenta, la voz arranca con un poquito más de colchón para no cortarse.

Todo eso lo ves en **Pruebas → Tu equipo**, con avisos claros si algo impide que Bubble ande del todo.

<br>

## Si algo se traba

- **«No se pudo preparar Bubble»:** casi siempre es internet. Revisá la conexión y volvé a abrir `Iniciar.bat`:
  sigue desde donde quedó.
- **«Tu Python es de 32 bits»:** abrí `Iniciar.bat` de nuevo: si solo encuentra uno de 32 bits, te ofrece instalar
  el de 64.
- **«Tu cuenta de Claude es gratuita»:** Bubble necesita Claude Pro o Max para traducir.
- **No lee el chat y dice que falta un idioma para leer texto:** Configuración de Windows → **Hora e idioma →
  Idioma y región** → agregá **Inglés** (instala el lector de texto que usa Bubble).
- **Los demás no te escuchan traducido:** en Roblox, elegí **CABLE Output** como micrófono (Esc → Configuración →
  *Dispositivo de entrada*), o cerrá y volvé a abrir Roblox con Bubble abierto.
- **Nada de esto:** escribinos desde Bubble, en **Ajustes → Ayuda → Soporte…** (o el link **Soporte** de abajo de
  todo). Contá qué pasó, sumá una captura si podés, y le llega directo al creador, con los datos de tu PC para
  entender el problema más rápido.

La guía completa, con cada parte de Bubble explicada, está en [GUIA.md](GUIA.md).

<br>

## Desinstalar

**Ajustes → Desinstalar Bubble…** (o `Desinstalar.bat`). Elegís qué borrar: tu configuración, lo descargado, el
acceso directo, el micrófono virtual y la carpeta. Windows vuelve a usar tu micrófono de siempre, como si Bubble
nunca hubiera estado.
