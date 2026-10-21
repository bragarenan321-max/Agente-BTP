import pytest


@pytest.fixture(autouse=True)
def sem_pausas(monkeypatch):
    """Pausas de auditoria/retentativa não interessam aos testes (o tempo é testado à parte)."""
    monkeypatch.setattr("agente_btp.processador._dormir", lambda s: None)


@pytest.fixture(autouse=True)
def loja_temporaria(tmp_path, monkeypatch):
    """A base de execuções de cada teste fica na pasta temporária (nunca em dados/)."""
    monkeypatch.setattr("agente_btp.agente.ARQUIVO_LOJA", tmp_path / "execucoes.sqlite3")
