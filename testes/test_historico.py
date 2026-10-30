from agente_btp import historico


def test_registrar_listar_obter_apagar(tmp_path):
    arq = tmp_path / "h.json"
    for i in range(3):
        historico.registrar({"id": str(i), "total": i, "wos": [{"wo": "x"}]}, arq)
    lista = historico.listar(arq)
    assert [a["id"] for a in lista] == ["2", "1", "0"]          # mais recente primeiro
    assert "wos" not in lista[0]                                 # resumo leve
    assert historico.obter("1", arq)["wos"] == [{"wo": "x"}]
    assert [a["id"] for a in historico.apagar(["1"], arq)] == ["1"]
    assert [a["id"] for a in historico.listar(arq)] == ["2", "0"]
    assert len(historico.apagar(None, arq)) == 2
    assert historico.listar(arq) == []


def test_arquivo_corrompido_nao_quebra(tmp_path):
    arq = tmp_path / "h.json"
    arq.write_text("{lixo", encoding="utf-8")
    assert historico.listar(arq) == []
    historico.registrar({"id": "a"}, arq)
    assert historico.obter("a", arq) == {"id": "a"}


def test_lista_de_wos_fica_fora_do_resumo(tmp_path):
    """Análise de 13 mil WOs não pode inchar o historico.json (lido a cada listagem)."""
    arq = tmp_path / "h.json"
    wos = [{"wo": f"{i:05d}|{i:09d}", "status": "Com itens faltantes"} for i in range(13623)]
    historico.registrar({"id": "grande", "total": 13623, "wos": wos}, arq)
    assert arq.stat().st_size < 2000
    assert historico.obter("grande", arq)["wos"] == wos
    historico.apagar(["grande"], arq)
    assert not list((tmp_path / "analises").glob("*.json"))


def test_historico_antigo_com_wos_no_resumo_continua_legivel(tmp_path):
    import json
    arq = tmp_path / "h.json"
    arq.write_text(json.dumps([{"id": "v", "wos": [{"wo": "1"}]}]), encoding="utf-8")
    assert historico.obter("v", arq)["wos"] == [{"wo": "1"}]
    assert "wos" not in historico.listar(arq)[0]
