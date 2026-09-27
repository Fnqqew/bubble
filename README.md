<p align="center">
  <img src="docs/banner.png" alt="Bubble — Hablá con cualquiera en Roblox" width="100%">
</p>

<p align="center">
  <img alt="Versión 2.1" src="https://img.shields.io/badge/versión-2.1-4a90e2?style=flat-square">
  <img alt="Windows 10 y 11" src="https://img.shields.io/badge/Windows-10%20·%2011-2b2f36?style=flat-square">
  <img alt="Con tu suscripción de Claude" src="https://img.shields.io/badge/con%20tu%20suscripción-Claude-d97757?style=flat-square">
</p>

<h3 align="center">El idioma no debería ser una pared.</h3>

<p align="center">
  En un mismo servidor de Roblox juegan chicos de Brasil, de India, de Francia, de México y de Argentina.<br>
  Se cruzan, se piden ayuda, se ríen… y muchas veces no se entienden.<br>
  <b>Bubble existe para que nadie se quede afuera de una conversación.</b>
</p>

<br>

<p align="center">
  <img src="docs/demo.gif" alt="Animación: el chat de Roblox se traduce mensaje por mensaje, la burbuja de un jugador se traduce, escribís en tu idioma y sale en inglés, y lo que te dicen por voz aparece subtitulado" width="100%">
</p>
<p align="center">
  <sub>Ilustración animada. Las traducciones, la barra para escribir y los subtítulos son los de Bubble, dibujados con su mismo código.</sub>
</p>

<br>

## Cómo funciona

<p align="center">
  <img src="docs/como-funciona.png" alt="1. Mira: lee el chat y las burbujas de la pantalla. 2. Entiende: tu Claude traduce con la jerga de cada país. 3. Muestra: la traducción aparece encima del original, en su lugar exacto." width="100%">
</p>

No traduce palabra por palabra: entiende la jerga — *vlw mano, tmj* · *ngl this obby is mid* · *mdr jsp* — y te la
cuenta como la diría alguien de tu barrio.

<br>

## Lo que hace

**Lee el chat.** Cada mensaje en otro idioma se traduce encima de sí mismo. El nombre del jugador queda a la vista y
lo que ya está en tu idioma no se toca.

**Traduce las burbujas.** Los globos sobre la cabeza de los jugadores también, y la traducción los sigue cuando
movés la cámara.

**Escribe por vos.** Apretá **°**, escribí como hablás y apretá **Enter**: sale traducido al chat. Mientras escribís
ves cómo va a quedar. Bubble solo abre el chat, escribe y envía; no toca ninguna otra tecla del juego.

**Te subtitula la voz.** Lo que te dicen por voz aparece abajo, como en una película: quién habla (Voz 1, Voz 2…)
y qué dice, en tu idioma, casi al instante. Escucha solo a Roblox: ni Discord ni la música que tengas de fondo.

**Habla por vos.** Tocás un botón, hablás en tu idioma y, cuando terminás, los demás te escuchan en el suyo, con una
voz femenina o masculina (también en modo directo o escribiendo, con Ctrl+Enter). Como Soundpad, sale por tu
micrófono en Roblox; si estás muteado, te desmutea solo mientras suena. [Cómo activarla](docs/GUIA.md#voz).

<br>

## La app

<p align="center">
  <img src="docs/interfaz.png" alt="La ventana de Bubble: tu idioma y cuatro interruptores (chat, burbujas, voz y tu voz), y los ajustes de apariencia" width="100%">
</p>

Elegís tu idioma y prendés lo que quieras. Después, jugás: Bubble encuentra el chat solo y trabaja en silencio.

**Hacelo tuyo:** tema oscuro o claro, el color, la opacidad y el tamaño de las traducciones en el juego, y dónde y
cómo se ven los subtítulos.

<br>

## Hecho para cualquier juego

- **Encuentra el chat solo**, esté donde esté: arriba, abajo, más grande o más chico.
- **Lee con cualquier fondo:** noche, nieve, cielo, o con el chat transparente.
- **Se adapta a tu PC:** captura la pantalla con la placa de video y regula cuánto trabaja según tu procesador,
  para no quitarle fluidez al juego.
- **Entiende el chat como una conversación:** mensajes repetidos, spam, avisos del juego y mensajes viejos cuando
  subís en el chat.

<br>

## Tu PC, tu cuenta

Bubble corre en tu computadora y traduce con **tu propia suscripción de Claude**, a través de Claude Code. No hay
claves que pegar ni servidores de por medio. La voz se reconoce y se sintetiza en tu PC: Claude solo ve texto.

Nunca toca el programa de Roblox: solo mira la pantalla, como una app de grabación, y escribe como lo harías vos.

<br>

## Empezar

Necesitás Windows 10 u 11, Python 3.12 y [Claude Code](https://claude.com/claude-code) con tu sesión iniciada.

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[voz]"
.\Iniciar.bat
```

Sin `[voz]` también anda: traduce el chat, las burbujas y lo que escribís.

Un tutorial corto te acompaña la primera vez. Para lo demás —configuración, cómo está hecho, cómo probarlo— está
la [guía completa](docs/GUIA.md).

<br>

## En números

Medido con el simulador de pruebas, con chats lentos, rápidos y en ráfagas:

| | |
|---|---|
| Mensajes detectados | **100 %** |
| Traducción lista | **~2 s** desde que aparece el mensaje |
| Parpadeos y traducciones fuera de lugar | **0** |
| Mensajes en tu idioma enviados a traducir | **ninguno** |

Y con el laboratorio de voz (varias personas, siete idiomas, música de fondo):

| | |
|---|---|
| Ves lo que dicen | a los **0,5 s** de que empiezan a hablar |
| La traducción | **~2 s** después de que terminan |
| Quién habla | acierta **9 de cada 10** frases o más |
| Tu voz traducida suena | **~2 s** después de que terminás de hablar |

<br>

<p align="center">
  <img src="src/bubble/assets/bubble.png" alt="" width="44"><br>
  <sub>Hecho con cariño para que el idioma no sea una pared.</sub>
</p>
