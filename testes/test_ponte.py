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


def test_arquivo_do_favorito_esta_atualizado():
    """O arquivo ao lado do .bat precisa ser idêntico ao favorito gerado pelo programa."""
    from agente_btp.config import RAIZ
    from agente_btp.servidor import codigo_carregador

    texto = (RAIZ / "FAVORITO - Agente BTP.txt").read_text(encoding="utf-8")
    assert "javascript:" + codigo_carregador("http://127.0.0.1:8767") in texto


def test_aba_com_script_antigo_recebe_atualizar():
    p = PonteAba()
    p.versao_agente = "nova"
    p.registrar_agente("a1", "/site")
    assert p.proxima_tarefa("a1", True, "/site", espera=0, versao="velha")["atualizar"] is True
    assert "atualizar" not in p.proxima_tarefa("a1", True, "/site", espera=0, versao="nova")


def test_tarefa_entregue_a_aba_que_sumiu_volta_para_a_fila():
    """Aba recarregada no meio de uma consulta: a consulta não se perde."""
    p = PonteAba()
    p.registrar_agente("velha", "/site")
    resultado = {}
    threading.Thread(target=lambda: resultado.setdefault("r", p.get("/srv/x")), daemon=True).start()
    time.sleep(0.1)
    assert "tarefa" in p.proxima_tarefa("velha", True, "/site", espera=1)   # a aba velha pega... e morre
    p.registrar_agente("nova", "/site")                                    # aba nova conecta
    t = p.proxima_tarefa("nova", True, "/site", espera=1)["tarefa"]
    p.entregar_resposta({"id": t["id"], "status": 200, "ct": "application/json", "body": "{}"})
    time.sleep(0.2)
    assert resultado["r"].status == 200


def test_despedida_desconecta_na_hora():
    p = PonteAba()
    p.registrar_agente("a1", "/site")
    p.proxima_tarefa("a1", True, "/site", espera=0)
    assert p.autenticado()
    p.despedir("a1")
    assert not p.conectada
    with pytest.raises(SessaoExpirada):
        p.get("/srv/x")


def test_copia_do_agente_na_extensao_e_identica():
    """A extensão injeta a própria cópia do agente: ela tem de ser igual à do programa."""
    from agente_btp.config import RAIZ
    original = (RAIZ / "agente_btp" / "web" / "agente_btp.js").read_text(encoding="utf-8")
    assert (RAIZ / "extensao" / "agente_btp.js").read_text(encoding="utf-8") == original


def test_versao_diferente_nao_entra_em_laco():
    """Aba com outra versão: pede recarga UMA vez; se voltar igual, aceita (sem laço)."""
    p = PonteAba()
    p.versao_agente = "nova"
    p.registrar_agente("a1", "/site")
    assert p.proxima_tarefa("a1", True, "/site", espera=0, versao="velha").get("atualizar")
    p.registrar_agente("a2", "/site")                      # recarregou, mas veio a mesma versão
    r = p.proxima_tarefa("a2", True, "/site", espera=0, versao="velha")
    assert "atualizar" not in r and "substituido" not in r


def test_aba_ocupada_com_consulta_lenta_nao_e_dada_como_desconectada(monkeypatch):
    """Caso real (27/10): consultas lentas deixaram a aba 26 s sem falar com o programa; ela estava
    trabalhando, não desconectada. Só conta como desconectada depois do prazo da própria consulta."""
    import threading
    import time
    from agente_btp import ponte as ponte_mod
    monkeypatch.setattr(ponte_mod, "TOLERANCIA_SILENCIO", 0.2)
    p = ponte_mod.PonteAba()
    p.registrar_agente("aba", "/site")
    resposta = {}
    t = threading.Thread(target=lambda: resposta.update(r=p.get("/srv/Lenta", timeout=2)))
    t.start()
    tarefa = p.proxima_tarefa("aba", True, "/site", espera=1)["tarefa"]
    time.sleep(0.6)                                # aba calada, executando a consulta
    assert p.conectada
    p.entregar_resposta({"id": tarefa["id"], "status": 200, "ct": "application/json", "body": "{}"})
    t.join(5)
    assert resposta["r"].status == 200
    time.sleep(0.3)
    assert not p.conectada                         # sem consulta em curso e calada: desconectada
