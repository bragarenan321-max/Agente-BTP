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
