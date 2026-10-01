# Cuánto usa Bubble de tu suscripción de Claude

Bubble traduce con **tu** suscripción de Claude (Pro o Max), a través de Claude Code: no requiere claves de API ni
cobros adicionales. Lo que sí hace es consumir una parte de los límites de tu plan, los mismos que se aplican en
claude.ai y en Claude Code.

## Qué se envía a Claude

Solo lo que hace falta traducir:

- los mensajes del chat y de las burbujas que **no** están en tu idioma;
- las frases del chat de voz que no están en tu idioma;
- lo que escribís o decís para enviar traducido.

No se envía: lo que ya está en tu idioma (incluidos los mensajes cortos, como "sii", "aja" o "Q PEDO"), "gg", "lol",
risas, spam, avisos del juego, mensajes ocultos por el filtro de Roblox (####) ni mensajes repetidos (quedan
guardados). Los mensajes que llegan juntos se agrupan en un mismo pedido.

## Consumo de cada traducción

Medición realizada con `python -m bubble.tools.usage_meter`, con 52 mensajes representativos de una partida (chat en
inglés, portugués, alemán, francés e hindi; frases del chat de voz; mensajes propios para enviar en inglés):

| Modelo | Por traducción (equivalente en la API) | Tokens por pedido | Tiempo por pedido | ¿Es adecuado? |
|---|---|---|---|---|
| **Opus** (predeterminado) | 0,003 a 0,004 US$ | ~6.000 de caché + ~30 de respuesta | ~2 s | ✅ el más preciso |
| **Sonnet** | 0,003 US$ | ~6.000 de caché + ~25 de respuesta | ~1,5 s | ✅ algo más económico |
| **Haiku** (con razonamiento previo) | 0,006 US$ | respuestas muy extensas | ~24 s | ❌ se desvía del pedido |
| **Haiku sin razonamiento** (voz y burbujas) | menos que Sonnet | ~6.000 de caché + ~20 de respuesta | ~0,8 s | ✅ para la voz: 2,5 veces más rápido que Opus |

**La voz y las burbujas usan Haiku**, sin razonamiento previo: tu voz se traduce en menos de un segundo (con Opus
demoraba casi dos) con una calidad muy similar. El chat escrito sigue con Opus, que interpreta mejor la jerga.

Casi todo lo que se envía son las instrucciones de Bubble, que quedan en el **caché** de Claude: se vuelven a leer en
cada pedido a una fracción del costo. La respuesta es breve (la traducción).

"Equivalente en la API" es lo que costaría pagando por uso. Con la suscripción no se paga ese monto, pero sirve para
comparar el peso de cada operación.

## Por hora de juego

Depende de cuántos mensajes en otro idioma lleguen. Con Opus:

| Actividad del servidor | Mensajes en otro idioma | Equivalente en la API |
|---|---|---|
| Tranquila | uno por minuto (~60 por hora) | ~0,20 US$ por hora |
| Normal | uno cada 20 s (~180 por hora) | ~0,60 US$ por hora |
| Muy alta | uno cada 6 s (~600 por hora) | ~2 US$ por hora |

El chat de voz suma un pedido por cada frase que no está en tu idioma, y lo que escribís, uno por mensaje.

**Bubble en tu idioma.** Si tu idioma no está incluido, la primera vez Bubble traduce su propia ventana con tu cuenta
de Claude (unos 850 textos, en tandas, con Sonnet). Ocurre una sola vez y el resultado queda guardado.

## Alcance de cada plan

Anthropic no publica los límites en tokens ni en dólares, por lo que no es posible indicar una cantidad exacta de horas.
Lo que sí informa ([cómo funcionan los límites](https://support.claude.com/en/articles/11647753-how-do-usage-and-length-limits-work),
[plan Max](https://support.claude.com/en/articles/11049741-what-is-the-max-plan)):

- El uso de claude.ai, Claude Code y Claude Desktop **comparte el mismo límite** (Bubble cuenta como Claude Code).
- El límite se renueva **cada 5 horas**, y además existe un **límite semanal**.
- **Max 5x** ofrece 5 veces el uso por sesión de **Pro**; **Max 20x**, 20 veces.

En la práctica, con Pro una partida tranquila o normal representa un consumo bajo; un servidor muy activo durante horas,
sumado a otros usos de Claude, puede acercarte al límite de 5 horas. Con Max 5x o 20x hay un margen amplio.

**Para consultar el consumo:** claude.ai → Configuración → Uso (muestra el porcentaje del límite de 5 horas y del
semanal). Si te acercás al límite, Claude Code lo avisa y Bubble lo registra en `%APPDATA%\Bubble\bubble.log`.

## Velocidad a cambio de más consumo

Por defecto, Bubble prioriza la velocidad de lo que dicen y escriben los demás, aunque eso use más de tu suscripción:

- **Traducción en vivo de la voz:** mientras alguien habla, lo dicho hasta el momento se traduce cada pocas palabras
  (varios pedidos por frase, con el modelo rápido). La traducción aparece mientras la persona habla, y no unos
  segundos después de que termina.
- **Traducción rápida del chat:** cada mensaje se traduce dos veces: primero con el modelo rápido y después con el
  principal, que corrige la primera si hace falta.
- **Traducción definitiva en dos canales a la vez:** la de cada frase de voz se pide en paralelo a dos sesiones del
  modelo rápido, y se usa la que responde primero.

Las tres se pueden desactivar: **Voz → Traducir mientras hablan** y **Ajustes → Chat de Roblox → Traducción rápida
del chat** (o `live_translation = false` en `[voice]` y `quick_chat = false` en `[translation]`).

## Cómo reducir el consumo

- **Desactivar la traducción en vivo y la traducción rápida del chat** (ver la sección anterior).
- **Usar Sonnet:** en `config.toml`, `[claude] model = "sonnet"`. Consume algo menos y responde más rápido; Opus
  interpreta la jerga un poco mejor.
- **Desactivar lo que no uses:** las burbujas o los subtítulos de voz (interruptores en Inicio).
- Mantener `adapt_slang = false` (valor predeterminado): los mensajes en tu idioma con jerga de otro país no se envían.

## Medición propia

```powershell
.venv\Scripts\python.exe -m bubble.tools.usage_meter                  # con el modelo configurado
.venv\Scripts\python.exe -m bubble.tools.usage_meter --modelo sonnet  # o con otro modelo
```

Son unas 50 traducciones (un consumo bajo de la suscripción). El resultado se guarda en
`%LOCALAPPDATA%\Bubble\uso_<modelo>.json`.
