"""Histórico das análises (arquivo local historico/historico.json).

Cada análise guarda: quando, quanto durou, de onde vieram as WOs, contagem por
resultado, a planilha gerada e o resultado de cada WO. A gravação é atômica
(arquivo temporário + substituição) para nunca corromper o histórico.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from .config import RAIZ

ARQUIVO = RAIZ / "historico" / "historico.json"
LIMITE = 300  # análises guardadas (as mais antigas saem)
_trava = threading.Lock()


def _ler(arquivo: Path) -> list[dict]:
    try:
        dados = json.loads(arquivo.read_text(encoding="utf-8"))
        return dados if isinstance(dados, list) else []
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def registrar(analise: dict, arquivo: Path = ARQUIVO) -> None:
    with _trava:
        dados = [a for a in _ler(arquivo) if a.get("id") != analise.get("id")]
        dados.insert(0, analise)
        arquivo.parent.mkdir(parents=True, exist_ok=True)
        tmp = arquivo.with_suffix(".tmp")
        tmp.write_text(json.dumps(dados[:LIMITE], ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(arquivo)


def listar(arquivo: Path = ARQUIVO) -> list[dict]:
    """Resumo de cada análise (sem a lista de WOs, que pode ser grande)."""
    with _trava:
        return [{k: v for k, v in a.items() if k != "wos"} for a in _ler(arquivo)]


def obter(id_: str, arquivo: Path = ARQUIVO) -> dict | None:
    with _trava:
        return next((a for a in _ler(arquivo) if a.get("id") == id_), None)


def apagar(ids: list[str] | None = None, arquivo: Path = ARQUIVO) -> list[dict]:
    """Remove as análises indicadas (todas, se ids for None). Devolve as removidas."""
    with _trava:
        dados = _ler(arquivo)
        removidas = [a for a in dados if ids is None or a.get("id") in ids]
        restantes = [a for a in dados if a not in removidas]
        if removidas:
            arquivo.parent.mkdir(parents=True, exist_ok=True)
            tmp = arquivo.with_suffix(".tmp")
            tmp.write_text(json.dumps(restantes, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(arquivo)
        return removidas
