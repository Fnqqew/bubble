from bubble.capture.chat_parser import ChatTracker, NameBook, parse_chat
from bubble.capture.ocr import OcrRow, merge_rows
from bubble.translate.base import ChatLine


def rows(*texts: str) -> list[OcrRow]:
    return [OcrRow(t, top=5 + i * 26, height=18, left=10) for i, t in enumerate(texts)]


def test_merge_rows_joins_name_and_message_on_same_line():
    merged = merge_rows([
        OcrRow("gg ez noob", top=16, height=12, left=90),
        OcrRow("xXDragonXx:", top=13, height=14, left=10),
        OcrRow("Pedro_BR: hola", top=39, height=14, left=10),
    ])
    assert [r.text for r in merged] == ["xXDragonXx: gg ez noob", "Pedro_BR: hola"]


def words_row(*parts: tuple[str, float]) -> OcrRow:
    """Una línea del OCR con sus palabras (texto, dónde empieza): cada letra ~9 px, alto 18."""
    from bubble.capture.ocr import OcrWord

    words = tuple(OcrWord(text, left, 100, len(text) * 9, 18) for text, left in parts)
    right = max(w.right for w in words)
    return OcrRow(" ".join(t for t, _ in parts), 100, 18, words[0].left, right - words[0].left, words)


def test_merge_rows_keeps_far_game_text_apart():
    """El OCR juntaba el mensaje con la burbuja de otro jugador a la misma altura: la zona del chat salía enorme y
    el mensaje traía texto que no era suyo."""
    merged = merge_rows([words_row(("Soph:", 5), ("NO", 55), ("spent", 700), ("all", 750), ("day", 782))])
    assert [(r.text, r.left) for r in merged] == [("Soph: NO", 5), ("spent all day", 700)]
    near = merge_rows([words_row(("Soph:", 5), ("no", 55), ("way", 80))])
    assert [r.text for r in near] == ["Soph: no way"]


def test_game_text_beside_the_chat_is_not_part_of_it():
    from bubble.capture.chat_parser import parse_chat_items

    items = parse_chat_items([
        OcrRow("VeloxX: both of you are getting 1k from me", top=10, height=18, left=5, width=480),
        OcrRow("spent all day bragging about how fast", top=12, height=18, left=700, width=300),  # una burbuja
        OcrRow("tomorrow", top=32, height=18, left=5, width=90),  # sigue el mensaje de arriba
        OcrRow("@juanot014", top=60, height=18, left=900, width=110),  # un nombre sobre una cabeza
    ], frame_width=500)
    assert [(item.speaker, item.text) for item in items] == [("VeloxX", "both of you are getting 1k from me tomorrow")]


def test_message_that_lost_its_colon_is_not_a_continuation():
    from bubble.capture.chat_parser import parse_chat_items

    items = parse_chat_items([
        OcrRow("melofruits: could u donate pls i wanna buy a priv", top=10, height=18, left=42, width=470),
        OcrRow("smegJadon40i i wasin a debate", top=32, height=18, left=42, width=260),  # después de la banderita
        OcrRow("SYSTEM: RobloxBestGamerOne has added a comment to", top=54, height=18, left=5, width=490),
        OcrRow("Lovine! (+25)", top=76, height=18, left=5, width=120),  # esta sí sigue al de arriba
    ], frame_width=500)
    assert [item.text for item in items] == ["could u donate pls i wanna buy a priv",
                                             "RobloxBestGamerOne has added a comment to Lovine! (+25)"]


def test_long_message_cut_at_the_bottom_waits_to_be_read_whole():
    from bubble.capture.chat_parser import parse_chat_items

    rows = [OcrRow("Ibarra: entonces", top=10, height=18, left=5, width=150),
            OcrRow("Andres: Once upon a time, there was a beautiful young", top=32, height=18, left=5, width=480)]
    cut = parse_chat_items(rows, frame_width=500, frame_height=52)  # el segundo renglón quedó afuera
    assert cut[-1].uncertain and not cut[0].uncertain
    whole = parse_chat_items(rows + [OcrRow("princess named Snow White.", top=54, height=18, left=5, width=230)],
                             frame_width=500, frame_height=120)
    assert whole[-1].text.endswith("Snow White.") and not whole[-1].uncertain


def test_icon_read_as_part_of_the_name():
    from bubble.capture.chat_parser import parse_chat_items

    items = parse_chat_items([
        OcrRow("1151 melofruits: could u donate pls", top=10, height=18, left=5, width=300),
        OcrRow("313: smegladon40: before u go", top=32, height=18, left=5, width=260),
    ], frame_width=500)
    assert [(item.speaker, item.text) for item in items] == [("melofruits", "could u donate pls"),
                                                            ("smegladon40", "before u go")]


def test_same_message_with_misread_name_is_not_new():
    tracker = ChatTracker(keep_on_start=0)
    tracker.update([ChatLine("smegladon40", "i got hate rallied on there"), ChatLine("reaper", "i gor suspended")])
    new = tracker.update([ChatLine("Silleqlac101140", "i got hate rallied on there"),
                          ChatLine("reaper", "i gor suspended"), ChatLine("park", "I support you")])
    assert new == [ChatLine("park", "I support you")]


def test_broken_read_of_a_recent_message_is_not_new_but_a_repeat_is():
    tracker = ChatTracker(keep_on_start=0)
    tracker.update([ChatLine("Ana", "hola")])
    toxic = ChatLine("smegladon40", "its so toxic on there")
    assert tracker.update([ChatLine("Ana", "hola"), toxic]) == [toxic]
    # Sin fondo, sobre el agua: la misma línea leída rota (y la de arriba no salió) no es un mensaje nuevo.
    assert tracker.update([ChatLine("smegladon40", "its soltoxic101Vt_here")]) == []
    # El mismo jugador repite exactamente lo mismo, debajo: eso sí es nuevo.
    again = ChatLine("smegladon40", "its so toxic on there")
    assert tracker.update([ChatLine("Ana", "hola"), toxic, again]) == [again]


def row(text: str, i: int, width: float = 200) -> OcrRow:
    return OcrRow(text, top=5 + i * 26, height=18, left=10, width=width)


def test_parse_chat_formats_and_continuations():
    lines = parse_chat([
        row("que se cortó arriba", 0),                       # resto de un mensaje fuera de la región
        row("xXDragonXx: gg ez noob, wanna trade", 1, 400),  # ocupa todo el ancho: sigue abajo
        row("my dragon?", 2, 120),
        row("[Pedro_BR]: mano me ajuda", 3, 220),
        row("dddd basura del ocr", 4, 150),                  # el anterior no llenaba el ancho: se descarta
        row("[Team] {To Kira} Kira2011: vamos", 5),
        row("[System] Your friend joined", 6),
        row("Noob Master: hola che", 7),
    ], frame_width=420)
    assert lines == [
        ChatLine("xXDragonXx", "gg ez noob, wanna trade my dragon?"),
        ChatLine("Pedro_BR", "mano me ajuda"),
        ChatLine("Kira2011", "vamos"),
        ChatLine("Noob Master", "hola che"),
    ]


def test_parse_chat_tolerates_ocr_errors_from_real_roblox():
    # Líneas tal como las leyó el OCR de una captura real (banderitas y dos puntos mal leídos).
    real = [
        row("[\"] Pav_bháji'. asterino isse baat kro", 0),   # nombre con "_": se acepta aunque falte ":"
        row("-anohadhi.' bruhhh", 1),                        # nombre desconocido sin ":": se descarta
        row("cloverx3: oh kidher hai-election", 2),
        row("[O] asterion952: by helping them", 3),
    ]
    assert parse_chat(real, frame_width=420) == [
        ChatLine("Pav_bháji", "asterino isse baat kro"),
        ChatLine("cloverx3", "oh kidher hai-election"),
        ChatLine("asterion952", "by helping them"),
    ]
    # Si el nombre ya se vio antes, también se acepta con los dos puntos mal leídos.
    assert parse_chat(real[1:2], is_known_name=lambda n: n == "anohadhi") == [ChatLine("anohadhi", "bruhhh")]


def test_name_book_unifies_ocr_variants():
    book = NameBook()
    for variant in ["cloverx3", "cloverx3", "CCloverx3", "cloverx3"]:
        book.canonical(variant)
    assert book.canonical("clbverx3") == "cloverx3"
    assert book.canonical("clovefk3") == "cloverx3"
    assert book.canonical("asterion952") == "asterion952"  # otro jugador: no se mezcla
    assert book.is_known("claverx3")


def test_tracker_does_not_repeat_truncated_reads():
    tracker = ChatTracker(confirm_frames=2)
    full = [ChatLine("cloverx3", "news nahi dekh rhi")]
    tracker.update(full)
    assert tracker.update(full) == full
    assert tracker.update([ChatLine("clbverx3", "news nahi dekh")]) == []  # misma línea leída cortada


def test_tracker_needs_two_frames_and_does_not_repeat():
    now = [0.0]
    tracker = ChatTracker(clock=lambda: now[0], confirm_frames=2)
    frame = [ChatLine("Bob", "wanna trade?")]
    assert tracker.update(frame) == []  # primera vez: candidato
    assert tracker.update(frame) == frame  # confirmado
    assert tracker.update(frame) == []  # ya visto
    # El OCR lo lee un poco distinto: sigue siendo el mismo mensaje.
    assert tracker.update([ChatLine("B0b", "wanna trade ?")]) == []


def test_tracker_drops_flicker_and_expires_memory():
    now = [0.0]
    tracker = ChatTracker(memory_s=10, clock=lambda: now[0], confirm_frames=2)
    tracker.update([ChatLine("Ann", "glitch txt")])
    assert tracker.update([]) == []  # desapareció: era basura del OCR
    assert tracker.update([ChatLine("Ann", "glitch txt")]) == []  # vuelve a empezar como candidato
    anchor = ChatLine("Bob", "ya conocido")
    tracker.update([anchor])  # sin mensajes conocidos a la vista todo es historial: queda como ancla
    msg = ChatLine("Ann", "hi")
    tracker.update([anchor, msg])
    assert tracker.update([anchor, msg]) == [msg]  # debajo del ancla: es nuevo
    now[0] = 30  # pasó el tiempo de memoria y ya no hay nada conocido a la vista: es historial
    assert tracker.update([msg]) == []
    assert tracker.update([msg]) == []


def L(text: str) -> ChatLine:
    speaker, message = text.split(": ", 1)
    return ChatLine(speaker, message)


def test_repeated_text_in_a_burst_does_not_hide_the_messages_before_it():
    # Caso real de la prueba de ráfagas: Luc repite su mensaje abajo de todo. Antes se tomaba como el mensaje
    # viejo y Mia y Memo (que quedaban arriba de él) se daban por historial.
    tracker = ChatTracker(keep_on_start=0)
    tracker.update([L("Juan: buenas gente"), L("Luana: me ajuda no obby pfv"), L("Luc: mdr jsp comment on fait")])
    assert tracker.update([L("Juan: buenas gente"), L("Luana: me ajuda no obby pfv"), L("Luc: mdr jsp comment on fait"),
                           L("Juan: alguien juega obby?")]) == [L("Juan: alguien juega obby?")]
    burst = [L("Mia: anyone wanna trade my dragon?"), L("Memo: esperen que me conecto"),
             L("Luc: mdr jsp comment on fait")]
    frame = [L("Luana: me ajuda no obby pfv"), L("Luc: mdr jsp comment on fait"), L("Juan: alguien juega obby?"), *burst]
    assert tracker.update(frame) == burst
    assert tracker.update(frame) == []
    # Y una tercera vez, ya con el primero fuera de la vista.
    again = L("Luc: mdr jsp comment on fait")
    assert tracker.update([*frame[2:], again]) == [again]


def test_same_message_twice_in_a_row_counts_twice():
    tracker = ChatTracker(keep_on_start=0)
    tracker.update([L("a: hola")])
    assert tracker.update([L("a: hola"), L("n7r0pyy: Plss donate")]) == [L("n7r0pyy: Plss donate")]
    assert tracker.update([L("a: hola"), L("n7r0pyy: Plss donate"), L("n7r0pyy: Plss donate")]) == [
        L("n7r0pyy: Plss donate")]


def test_message_skipped_by_the_ocr_is_recovered_between_known_ones():
    # El OCR no leyó la línea de Jake en las primeras capturas; cuando aparece ya hay mensajes debajo.
    now = [0.0]
    tracker = ChatTracker(keep_on_start=0, clock=lambda: now[0])
    tracker.update([L("Juan: hola che, todo bien?"), L("cloverx3: news nahi dekh rhi")])
    now[0] = 1
    assert tracker.update([L("Juan: hola che, todo bien?"), L("cloverx3: news nahi dekh rhi"),
                           L("Mia: omg i got a legendary!!")]) == [L("Mia: omg i got a legendary!!")]
    now[0] = 3
    jake = L("Jake: ngl this game is mid")
    assert tracker.update([L("Juan: hola che, todo bien?"), L("cloverx3: news nahi dekh rhi"), jake,
                           L("Mia: omg i got a legendary!!")]) == [jake]
    # Si lo salteado es viejo (lo de abajo llegó hace mucho), ya no se traduce.
    now[0] = 60
    late = L("Luc: tkt frr")
    assert tracker.update([L("Juan: hola che, todo bien?"), L("cloverx3: news nahi dekh rhi"), jake, late,
                           L("Mia: omg i got a legendary!!")]) == []


def test_misread_line_in_its_place_is_not_a_new_message():
    tracker = ChatTracker(keep_on_start=0)
    frame = [L("Jake: ngl this game is mid"), L("Luana: vlw mano, tmj"), L("Mia: i have a shadow dragon ft")]
    tracker.update(frame)
    # La del medio leída muy mal (nombre y texto), en el mismo lugar: es la misma.
    assert tracker.update([frame[0], L("Luena: vIw mamo tnj"), frame[2]]) == []
    # La última leída mal, abajo de todo: también es la misma.
    assert tracker.update([frame[0], frame[1], L("Mia: i hove a shad0w dragn ft")]) == []


def test_same_text_from_another_player_is_new():
    tracker = ChatTracker(keep_on_start=0)
    tracker.update([L("Juan: sos re malo jaja")])
    assert tracker.update([L("Juan: sos re malo jaja"), L("Martina: sos re malo jaja")]) == [
        L("Martina: sos re malo jaja")]


def test_scrolling_back_down_does_not_translate_again():
    tracker = ChatTracker(keep_on_start=0)
    names = ["Ana", "Bruno", "Carla", "Dario", "Elena", "Fede", "Gabi", "Hugo", "Ines", "Joaco", "Kiara", "Lucas"]
    history = [L(f"{name}: mensaje numero {i}") for i, name in enumerate(names)]
    tracker.update(history[:6])
    for end in range(7, 13):  # llegan de a uno
        assert tracker.update(history[end - 6:end]) == [history[end - 1]]
    # Subís hasta arriba y volvés a bajar despacio: nada es nuevo.
    for start in [4, 2, 0, 1, 3, 5, 6]:
        assert tracker.update(history[start:start + 6]) == []
    new = L("Kai: recien llego")
    assert tracker.update([*history[7:], new]) == [new]


def test_doubtful_new_message_needs_a_second_read():
    tracker = ChatTracker(keep_on_start=0)
    tracker.update([L("Juan: hola che")])
    garbage = L("Lue: rn4r.jãP<omment bri fait")
    # Salió solo en la lectura de respaldo: no se traduce todavía.
    assert tracker.update([L("Juan: hola che"), garbage], [False, True]) == []
    # La siguiente lectura lo lee bien (y es parecido): se confirma, con la lectura buena.
    good = L("Luc: mdr jsp comment on fait")
    assert tracker.update([L("Juan: hola che"), good], [False, False]) == [good]
    # Basura que no se repite: nunca se traduce.
    tracker.update([L("Juan: hola che"), good, L("xX: qqq zzz")], [False, False, True])
    assert tracker.update([L("Juan: hola che"), good], [False, False]) == []


def test_tracker_ignores_my_messages():
    tracker = ChatTracker(username="JuanPro", confirm_frames=2)
    tracker.mark_sent("eae, me espera que eu vou")
    for _ in range(2):
        assert tracker.update([
            ChatLine("JuanPro", "hola"),
            ChatLine("Someone", "eae, me espera que eu vou"),
        ]) == []
