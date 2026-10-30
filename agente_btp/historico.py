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
CONTADOR = RAIZ / "historico" / "contador.txt"
LIMITE = 300  # análises guardadas (as mais antigas saem)
_trava = threading.Lock()


def _ler(arquivo: Path) -> list[dict]:
    try:
        dados = json.loads(arquivo.read_text(encoding="utf-8"))
        return dados if isinstance(dados, list) else []
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _pasta_detalhes(arquivo: Path) -> Path:
    return arquivo.parent / "analises"


def _detalhe(arquivo: Path, id_: str) -> Path:
    seguro = "".join(c for c in str(id_) if c.isalnum() or c in "-_")
    return _pasta_detalhes(arquivo) / f"{seguro}.json"


def _gravar(caminho: Path, dados) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    tmp = caminho.with_suffix(".tmp")
    tmp.write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(caminho)


def registrar(analise: dict, arquivo: Path | None = None) -> None:
    """O resumo vai para historico.json; a lista de WOs (pode ter dezenas de milhares) vai para
    um arquivo próprio da análise — o histórico continua leve para listar."""
    arquivo = arquivo or ARQUIVO
    analise = dict(analise)
    wos = analise.pop("wos", None)
    with _trava:
        if wos is not None:
            _gravar(_detalhe(arquivo, analise.get("id")), wos)
        dados = [a for a in _ler(arquivo) if a.get("id") != analise.get("id")]
        dados.insert(0, analise)
        for antiga in dados[LIMITE:]:
            _detalhe(arquivo, antiga.get("id")).unlink(missing_ok=True)
        _gravar(arquivo, dados[:LIMITE])


def listar(arquivo: Path | None = None) -> list[dict]:
    """Resumo de cada análise (sem a lista de WOs, que pode ser grande)."""
    arquivo = arquivo or ARQUIVO
    with _trava:
        return [{k: v for k, v in a.items() if k != "wos"} for a in _ler(arquivo)]


def obter(id_: str, arquivo: Path | None = None) -> dict | None:
    arquivo = arquivo or ARQUIVO
    with _trava:
        item = next((a for a in _ler(arquivo) if a.get("id") == id_), None)
        if item is None or "wos" in item:          # análises antigas guardavam as WOs no resumo
            return item
        try:
            wos = json.loads(_detalhe(arquivo, id_).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return item                            # registrada sem lista de WOs
        except json.JSONDecodeError:
            wos = []
        return dict(item, wos=wos)


def apagar(ids: list[str] | None = None, arquivo: Path | None = None) -> list[dict]:
    """Remove as análises indicadas (todas, se ids for None). Devolve as removidas."""
    arquivo = arquivo or ARQUIVO
    with _trava:
        dados = _ler(arquivo)
        removidas = [a for a in dados if ids is None or a.get("id") in ids]
        restantes = [a for a in dados if a not in removidas]
        if removidas:
            _gravar(arquivo, restantes)
            for a in removidas:
                _detalhe(arquivo, a.get("id")).unlink(missing_ok=True)
        return removidas


def proximo_numero(arquivo: Path | None = None, contador: Path | None = None) -> int:
    """Nº sequencial da análise. Nunca se repete, mesmo depois de apagar o histórico."""
    arquivo, contador = arquivo or ARQUIVO, contador or CONTADOR
    with _trava:
        try:
            ultimo = int(contador.read_text(encoding="utf-8").strip() or 0)
        except (FileNotFoundError, ValueError):
            ultimo = 0
        ultimo = max([ultimo] + [int(a.get("numero") or 0) for a in _ler(arquivo)])
        contador.parent.mkdir(parents=True, exist_ok=True)
        contador.write_text(str(ultimo + 1), encoding="utf-8")
        return ultimo + 1
