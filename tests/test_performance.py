from bubble.performance import Hardware, Pacer


def test_pacer_keeps_cpu_use_within_its_share():
    pacer = Pacer(share=0.25, min_s=0.1, max_s=1.0)
    assert pacer.sleep == 0.1  # sin mediciones: lo más rápido permitido
    for _ in range(40):
        pacer.record(0.06)  # cada lectura cuesta 60 ms
    # 60 ms de trabajo + espera: el trabajo es como mucho el 25% del tiempo.
    assert abs(pacer.sleep - 0.18) < 0.01
    assert pacer.cost / (pacer.cost + pacer.sleep) <= 0.25 + 1e-6
    for _ in range(40):
        pacer.record(0.005)  # PC rápida (o captura por GPU): lee seguido, hasta el mínimo
    assert pacer.sleep == 0.1
    for _ in range(40):
        pacer.record(2.0)  # PC muy lenta: nunca espera más que el máximo
    assert pacer.sleep == 1.0


def test_hardware_tiers():
    assert Hardware("x", 16).tier == "alta"
    assert Hardware("x", 8).tier == "media"
    assert Hardware("x", 4).tier == "baja"
    assert Hardware("x", 16, forced_tier="baja").tier == "baja"
    # En una PC modesta cada tarea usa menos CPU y las lecturas se espacian más.
    fast, slow = Hardware("x", 16).pacer("bubbles", 0.12, 1.0), Hardware("x", 4).pacer("bubbles", 0.12, 1.0)
    assert slow.share < fast.share and slow.min_s > fast.min_s
