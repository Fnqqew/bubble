# Cuánto usa Bubble de tu suscripción de Claude

Bubble traduce con **tu** suscripción de Claude (Pro o Max), a través de Claude Code: no hay API keys ni cobros
aparte. Lo que sí hace es usar una parte de los límites de tu plan, los mismos que usás en claude.ai y en Claude Code.

## Qué se le manda a Claude

Solo lo que hace falta traducir:

- mensajes del chat y de las burbujas que **no** están en tu idioma;
- frases del chat de voz que no están en tu idioma;
- lo que escribís o decís vos para mandar traducido.

No se mandan: lo que ya está en tu idioma (incluido lo corto: "sii", "aja", "Q PEDO"), "gg", "lol", risas, spam,
avisos del juego, mensajes tapados por el filtro de Roblox (####) ni lo repetido (queda guardado). Los mensajes que
llegan juntos van en un mismo pedido.

## Lo que gasta cada traducción (medido)

Medido el 27/9/2026 con `python -m bubble.tools.usage_meter`, con 52 mensajes como los de una partida (chat en
inglés, portugués, alemán, francés e hindi; frases del chat de voz; mensajes tuyos para mandar en inglés):

| Modelo | Por traducción (equivalente en la API) | Tokens por pedido | Tiempo por pedido | ¿Sirve? |
|---|---|---|---|---|
| **Opus** (el que viene puesto) | 0,003 a 0,004 US$ | ~6.000 del caché + ~30 de respuesta | ~2 s | ✅ el más preciso |
| **Sonnet** | 0,003 US$ | ~6.000 del caché + ~25 de respuesta | ~1,5 s | ✅ un poco menos gasto |
| **Haiku** (pensando antes de responder) | 0,006 US$ | respuestas larguísimas | ~24 s | ❌ se va por las ramas |
| **Haiku sin pensar** (la voz y las burbujas) | menos que Sonnet | ~6.000 del caché + ~20 de respuesta | ~0,8 s | ✅ para la voz: 2,5 veces más rápido que Opus |

Desde la versión 2.4, **la voz y las burbujas van por Haiku, sin pensar antes de responder** (medido el 28/9/2026:
tu voz se traduce en ~0,75 s en vez de ~1,9 s con Opus, y los subtítulos en ~1 s en vez de ~1,6 s, casi con la misma
calidad). El chat escrito sigue con Opus, que traduce mejor la jerga. Así además se gasta menos.

Casi todo lo que se manda son las instrucciones de Bubble, que quedan en el **caché** de Claude: se leen de nuevo en
cada pedido a una fracción del costo. La respuesta es corta (la traducción).

"Equivalente en la API" es lo que costaría pagando por uso: con la suscripción no pagás eso, pero sirve para comparar
cuánto pesa cada cosa.

## Por hora de juego

Depende de cuántos mensajes en otro idioma lleguen. Con Opus:

| Cómo está el servidor | Mensajes en otro idioma | Equivalente en la API |
|---|---|---|
| Tranquilo | uno por minuto (~60 por hora) | ~0,20 US$ por hora |
| Normal | uno cada 20 s (~180 por hora) | ~0,60 US$ por hora |
| Muy activo | uno cada 6 s (~600 por hora) | ~2 US$ por hora |

El chat de voz suma un pedido por cada frase que no está en tu idioma; lo que escribís vos, uno por mensaje.

## ¿Cuánto aguanta cada plan?

Anthropic no publica los límites en tokens ni en dólares, así que no se puede decir "X horas" con exactitud. Lo que
sí publica ([cómo funcionan los límites](https://support.claude.com/en/articles/11647753-how-do-usage-and-length-limits-work),
[plan Max](https://support.claude.com/en/articles/11049741-what-is-the-max-plan)):

- El uso de claude.ai, Claude Code y Claude Desktop **comparte el mismo límite** (Bubble cuenta como Claude Code).
- El límite se renueva **cada 5 horas**, y además hay un **límite semanal**.
- **Max 5x** tiene 5 veces el uso por sesión de **Pro**; **Max 20x**, 20 veces.

En la práctica: con Pro, una partida tranquila o normal es un uso chico; un servidor muy activo durante horas, sumado a
lo que uses Claude para otras cosas, puede acercarte al límite de 5 horas. Con Max 5x o 20x hay mucho margen.

**Para ver cuánto llevás:** claude.ai → Configuración → Uso (muestra el porcentaje del límite de 5 horas y del
semanal). Si te acercás al tope, Claude Code te avisa y Bubble lo anota en `%APPDATA%\Bubble\bubble.log`.

## Cómo gastar menos

- **Usar Sonnet:** en `config.toml`, `[claude] model = "sonnet"`. Gasta un poco menos y responde más rápido; Opus
  traduce la jerga un poco mejor.
- **Apagar lo que no uses:** burbujas o subtítulos de voz (interruptores en Inicio).
- Dejar `adapt_slang = false` (viene así): los mensajes en tu idioma con jerga de otro país no se mandan.

## Medilo vos

```powershell
.venv\Scripts\python.exe -m bubble.tools.usage_meter                  # con el modelo que tengas configurado
.venv\Scripts\python.exe -m bubble.tools.usage_meter --modelo sonnet  # o probá otro
```

Son ~50 traducciones (un uso chico de tu suscripción). El resultado queda en `%LOCALAPPDATA%\Bubble\uso_<modelo>.json`.
