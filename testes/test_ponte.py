import threading
import time

import pytest

from agente_btp import ponte as ponte_mod
from agente_btp.modelos import FalhaComunicacao, SessaoExpirada
from agente_btp.ponte import PonteAba


def aba_falsa(ponte, agente="a1", resposta=None, parar=None):
    """Simula o agente_btp.js: faz long-poll e responde as tarefas."""
    while not parar.is_set():
        r = ponte.proxima_tarefa(agente, True, "/site", espera=0.2)
        if "tarefa" in r:
            t = r["tarefa"]
            ponte.entregar_resposta(dict(resposta or {"status": 200, "ct": "application/json",
                                                      "body": '{"d":{"results":[]}}'}, id=t["id"]))


def test_get_pela_aba():
    p, parar = PonteAba(), threading.Event()
    p.registrar_agente("a1", "/site")
    threading.Thread(target=aba_falsa, args=(p,), kwargs={"parar": parar}, daemon=True).start()
    time.sleep(0.1)
    try:
        assert p.autenticado()
        r = p.get("/srv/AlertaSet")
        assert r.status == 200 and "results" in r.corpo
    finally:
        parar.set()


def test_recusa_url_de_outro_site():
    p = PonteAba()
    p.registrar_agente("a1", "/site")
    with pytest.raises(FalhaComunicacao):
        p.get("https://outro.site/x")
    with pytest.raises(FalhaComunicacao):
        p.get("//outro.site/x")


def test_sem_aba_conectada_e_sessao_expirada():
    with pytest.raises(SessaoExpirada):
        PonteAba().get("/srv/x")


def test_aba_que_some_vira_sessao_expirada(monkeypatch):
    monkeypatch.setattr(ponte_mod, "TIMEOUT_TAREFA", 0.3)
    monkeypatch.setattr(ponte_mod, "TOLERANCIA_SILENCIO", 0.1)
    p = PonteAba()
    p.registrar_agente("a1", "/site")
    with pytest.raises(SessaoExpirada):
        p.get("/srv/x")


def test_nova_aba_substitui_a_antiga():
    p = PonteAba()
    p.registrar_agente("velha", "/site")
    p.registrar_agente("nova", "/site")
    assert p.proxima_tarefa("velha", True, "/site", espera=0) == {"substituido": True}


def test_inicio_da_descoberta_acorda_a_aba():
    p = PonteAba()
    p.registrar_agente("a1", "/site")
    resultado = {}

    def poll():
        inicio = time.monotonic()
        resultado["r"] = p.proxima_tarefa("a1", True, "/site", capturando_na_aba=False, espera=5)
        resultado["t"] = time.monotonic() - inicio

    th = threading.Thread(target=poll)
    th.start()
    time.sleep(0.2)
    p.iniciar_captura()
    th.join()
    assert resultado["r"]["capturar"] is True and resultado["t"] < 2
    p.adicionar_captura([{"url": "u"}])
    assert p.capturando
