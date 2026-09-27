from bubble.translate.base import ChatLine, TranslationRequest
from bubble.translate.engine import looks_wrong
from bubble.translate.prompt import OutputFilter, build_user_prompt


def run_filter(chunks: list[str], count: int = 1) -> dict[int, str]:
    f = OutputFilter(count)
    out: dict[int, str] = {}
    for chunk in chunks:
        for index, text in f.feed(chunk):
            out[index] = out.get(index, "") + text
    for index, text in f.finish():
        out[index] = out.get(index, "") + text
    return out


def test_output_filter_single_and_split_tags():
    assert run_filter(["<t1>hola ", "che</t1>"]) == {0: "hola che"}
    assert run_filter(["Sure! <", "t1>gracias", ", bro<", "/t1> hope it helps"]) == {0: "gracias, bro"}
    assert run_filter(["sin marcas"]) == {0: "sin marcas"}  # respaldo con un solo mensaje


def test_output_filter_batch_streams_each_message_in_order():
    chunks = ["<t1>hola</t", "1>\n<t2>qué ", "onda</t2><t3", ">gracias</t3>"]
    assert run_filter(chunks, 3) == {0: "hola", 1: "qué onda", 2: "gracias"}
    # Sin marcas en un lote no se puede saber qué es de quién: no se devuelve nada (se reintenta aparte).
    assert run_filter(["hola qué onda"], 2) == {}
    # Una marca fuera de rango se ignora.
    assert run_filter(["<t9>x</t9><t1>ok</t1>"], 1) == {0: "ok"}


def test_batch_prompt_numbers_messages_and_marks_adapt():
    reqs = [
        TranslationRequest("vlw mano", "es", "incoming", "Pedro", target_region="AR"),
        TranslationRequest("no mames wey", "es", "incoming", "Memo", target_region="AR", mode="adapt",
                           slang_hints=(("wey", "es-MX", "dude"),)),
    ]
    prompt = build_user_prompt(reqs)
    assert '<m1 from="Pedro">vlw mano</m1>' in prompt
    assert '<m2 from="Memo" mode="adapt">no mames wey</m2>' in prompt
    assert "Slang detected in m2: wey (es-MX: dude)." in prompt


def test_looks_wrong_detects_mixed_or_invented_translations():
    context = (ChatLine("cloverx3", "oh kidher hai election"),)
    assert looks_wrong("follow", "síganme =cloverx3: respirás y se te mete un pelo", context)
    assert looks_wrong("follow", "x" * 80, ())
    assert looks_wrong("hola", "   ", ())
    assert not looks_wrong("ratio + L", "ratio + perdiste (te ganaron en likes)", context)
    assert not looks_wrong("cloverx3: hi", "cloverx3: hola", context)
