# Instalar Bubble

<p align="center">
  <img src="instalacion.gif" alt="Animación de la instalación en 5 pasos: bajar Bubble, doble clic en Iniciar.bat, la ventana «Preparar Bubble» descargando todo, conectar tu cuenta de Claude y listo para jugar" width="100%">
</p>

La instalación tiene cinco pasos, y la mayoría los realiza Bubble automáticamente. La primera vez demora unos diez
minutos, según tu conexión; después abre en segundos desde el acceso directo del escritorio.

No hace falta saber programar ni tener nada instalado de antemano: si falta algo, Bubble ofrece instalarlo.

<br>

## Antes de empezar

- **Windows 10 (versión 2004 o posterior) u 11**, de 64 bits. Cualquier PC de menos de cinco años cumple este
  requisito.
- **Una suscripción de Claude, Pro o Max.** Es la que realiza las traducciones. Bubble no cobra nada ni pide datos de
  pago. Si todavía no tenés una, Bubble Pro puede traducir con el crédito de regalo de Deepgram (ver el paso 4).
- **Roblox**, tanto el de roblox.com como el de la Microsoft Store.
- **Alrededor de 1,5 GB libres** y conexión a internet.
- Para que te escuchen con la voz traducida: **auriculares con micrófono**.

> [!IMPORTANT]
> **Lo más importante es el micrófono.** Bubble te entiende tan bien como te escucha. Con un micrófono de auriculares
> o uno USB cerca de la boca, el resultado es mucho mejor que con el de la notebook. Podés probarlo en
> **Inicio → Probar mi micrófono**.

<br>

## 1. Descargá Bubble

<img src="instalacion/paso-1.png" alt="La carpeta de Bubble descomprimida, con Iniciar.bat marcado" width="100%">

Ingresá a [la última versión](https://github.com/Fnqqew/bubble/releases/latest) y descargá **Source code (zip)**.
Después, hacé clic derecho sobre el archivo ZIP → **Extraer todo**.

> **Recomendación:** guardá la carpeta en un lugar fijo, como Documentos. El acceso directo que crea Bubble apunta a
> esa ubicación; si más adelante la movés, abrí `Iniciar.bat` desde el lugar nuevo y el acceso directo se corrige
> automáticamente.

<br>

## 2. Hacé doble clic en `Iniciar.bat`

<img src="instalacion/paso-2.png" alt="La consola de Iniciar.bat preparando Bubble por primera vez" width="100%">

Se abre una ventana de consola que prepara todo. Cuando termina, se cierra sola y abre Bubble.

- **Si Windows muestra «Windows protegió su PC»:** ocurre porque el archivo se descargó de internet. Tocá **Más
  información → Ejecutar de todas formas**. Es un archivo de texto; si querés revisar lo que hace, podés abrirlo con el
  Bloc de notas.
- **Si falta Python** (el lenguaje con el que funciona Bubble), aparece la pregunta *«Lo instalo ahora [S,N]?»*:
  presioná **S** y **Enter**. Si Windows no puede instalarlo automáticamente, se abre la página de Python para
  descargarlo; después de instalarlo, volvé a abrir `Iniciar.bat`.

<br>

## 3. Bubble se prepara

<img src="instalacion/paso-3.png" alt="La ventana «Preparar Bubble» descargando el reconocimiento de voz y las voces" width="100%">

Se abre **Preparar Bubble** y descarga lo necesario para reconocer voces y hablar por vos: el reconocimiento de voz
(hasta unos 500 MB), el modelo que distingue quién habla (unos 30 MB) y las voces en inglés (unos 120 MB). Podés seguir
usando la PC mientras tanto.

Si a Windows le faltan componentes de Microsoft (Visual C++), aparece el botón **Instalar componentes**. Al tocarlo,
Windows pide permiso y los instala. Bubble nunca instala nada en Windows sin tu autorización.

<br>

## 4. Conectá tu cuenta de Claude

<img src="instalacion/paso-4.png" alt="«Preparar Bubble» con los botones Iniciar sesión e Instalar micrófono virtual marcados" width="100%">

Este paso requiere tu intervención, porque se trata de tu cuenta:

1. **Instalar Claude Code** (si no lo tenés): se abre el instalador oficial de Anthropic en una ventana aparte.
   Esperá a que termine.
2. **Iniciar sesión**: se abre Claude Code, que te lleva al navegador. Ingresá con tu cuenta de Claude (la misma de
   claude.ai) y aceptá. A partir de ahí, Bubble traduce con tu suscripción, sin claves ni pagos adicionales.
3. **Instalar micrófono virtual** (opcional, pero necesario para que los demás te escuchen con la voz traducida):
   Windows pide permiso de administrador y, en el instalador, tocás **Install Driver**. Si pide reiniciar, reiniciá.

**¿No tenés Claude, o tu cuenta es la gratuita?** Bubble lo informa y ofrece dos opciones:

- **Bubble Pro con créditos gratuitos:** creás una cuenta en Deepgram (200 US$ de regalo, sin tarjeta), copiás la
  clave y la pegás en Bubble. Así la traducción funciona sin Claude, aunque consume crédito (unos 0,075 US$ por
  minuto con mensajes; la conexión se cierra sola cuando el chat está inactivo), y Bubble lo recuerda. Mientras no
  conectes Claude, Pro queda activado y Basic aparece bloqueado.
- **Conectar Claude:** con una suscripción (Claude Pro es suficiente), tocás **Iniciar sesión en Claude** y después
  **Revisar ahora**. Bubble vuelve a traducir con tu suscripción, deja de consumir crédito y habilita Basic.

<br>

## 5. Listo para jugar

<img src="instalacion/paso-5.png" alt="La ventana de Bubble lista, y traducciones en el chat del juego" width="100%">

Elegí tu idioma y abrí Roblox. Bubble detecta el chat automáticamente y traduce mientras jugás.

Bubble se muestra en el idioma de tu Windows. Si preferís otro, podés cambiarlo en **Ajustes → Idioma de Bubble**.

- **Abrí Bubble antes que Roblox.** Así Roblox usa el micrófono virtual desde el inicio. Si Roblox ya estaba abierto,
  Bubble indica qué hacer.
- **Desactivá la traducción automática de Roblox** (Esc → Configuración → *Traducción automática del chat*). De lo
  contrario, Bubble lee mensajes ya traducidos y se pierde la jerga original.
- La primera vez te acompaña un tutorial breve. Si lo omitís, está disponible en **Ajustes → Ver el tutorial**.

<br>

## Adaptación a tu PC

Cada vez que se abre, Bubble revisa la memoria, el procesador, la pantalla, los micrófonos y la conexión, y se adapta
para no afectar el rendimiento de Roblox. Si algo le impide funcionar correctamente, lo informa en **Pruebas → Tu
equipo**.

<br>

## Si surge un problema

- **«No se pudo preparar Bubble»:** casi siempre se debe a la conexión. Revisala y volvé a abrir `Iniciar.bat`; la
  instalación continúa desde donde quedó.
- **«Tu Python es de 32 bits»:** abrí `Iniciar.bat` de nuevo; si solo encuentra uno de 32 bits, ofrece instalar el de
  64.
- **«Tu cuenta de Claude es gratuita»:** Bubble necesita Claude Pro o Max para traducir, o Bubble Pro con créditos.
- **No lee el chat e indica que falta un idioma para leer texto:** Configuración de Windows → **Hora e idioma →
  Idioma y región** → agregá **Inglés** (instala el lector de texto que usa Bubble).
- **Los demás no escuchan tu voz traducida:** en Roblox, elegí **CABLE Output** como micrófono (Esc → Configuración →
  *Dispositivo de entrada*), o cerrá y volvé a abrir Roblox con Bubble abierto.
- **Ninguna de estas opciones:** escribí desde Bubble, en **Ajustes → Ayuda → Soporte…** (o el enlace **Soporte** al
  final de la ventana). Describí lo ocurrido y, si podés, agregá una captura: el mensaje le llega directamente al
  creador, junto con los datos de tu PC, lo que permite entender el problema más rápido.

La guía completa, con cada parte de Bubble explicada, está en [GUIA.md](GUIA.md).

<br>

## Versiones nuevas

No hace falta repetir este proceso. Cuando hay una versión nueva, Bubble lo informa y pregunta si querés actualizar
ahora o más tarde. La actualización demora menos de un minuto y conserva toda tu configuración. Nunca pregunta durante
una partida.

Si preferís que no pregunte, activá **Ajustes → Actualizar automáticamente**: la versión nueva se descarga y se instala
cuando no estás jugando.

<br>

## Desinstalar

**Ajustes → Desinstalar Bubble…** (o `Desinstalar.bat`). Elegís qué borrar: la configuración, lo descargado, el acceso
directo, el micrófono virtual y la carpeta. Windows vuelve a usar tu micrófono habitual, como si Bubble nunca hubiera
estado instalado.
