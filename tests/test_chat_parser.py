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
