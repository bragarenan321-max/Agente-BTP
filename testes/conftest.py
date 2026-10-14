import pytest


@pytest.fixture(autouse=True)
def sem_pausas(monkeypatch):
    """Pausas de auditoria/retentativa não interessam aos testes (o tempo é testado à parte)."""
    monkeypatch.setattr("agente_btp.processador._dormir", lambda s: None)
