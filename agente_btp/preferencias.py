"""Configurações do usuário (engrenagem da tela), salvas em config/preferencias.json.

Só parâmetros operacionais: nenhuma senha, cookie ou token é guardado aqui.
Todo valor recebido é validado e limitado à faixa permitida antes de ser usado ou salvo.
"""
from __future__ import annotations

import json
import math
import threading
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from .config import RAIZ

ARQUIVO = RAIZ / "config" / "preferencias.json"
_trava = threading.Lock()


@dataclass
class Preferencias:
    # --- Auditoria (política: 2 conferências; divergência => +3 leituras individuais = 5 no total)
    auditorias_por_wo: int = 2                 # rodadas de conferência após a extração inicial
    auditorias_independentes: bool = True      # alterna nova consulta e releitura item a item (por chave)
    repeticoes_falha: int = 2                  # tentativas adicionais de uma consulta que falhou/excedeu o tempo
    divergencia: str = "reconsultar"           # "reconsultar" | "revisar" (nunca aprova divergência)
    consultas_extras_divergencia: int = 3      # leituras extras (individuais) para esclarecer uma divergência
    # --- Tempo
    tempo_consulta_wo_seg: float = 30.0          # filtro da WO no Monitor (consulta da lista)
    tempo_consulta_detalhes_seg: float = 30.0    # detalhes da janela "Mensagens" (itens)
    intervalo_auditorias_seg: float = 0.0      # pausa entre rodadas (0 = sem pausa fixa; o ritmo é adaptativo)
    tempo_max_wo_seg: float = 180.0              # tudo de uma WO (extração + auditorias)
    tempo_max_lote_min: float = 240.0            # a análise inteira
    # --- Execução
    intervalo_consultas_seg: float = 0.0       # pausa entre consultas ao BTP (aliviar o sistema)
    wo_sem_resultado: str = "reconsultar"      # "registrar" | "reconsultar" uma vez antes de concluir
    em_erro: str = "continuar"                 # "continuar" | "interromper" o lote
    exportar_incompletos: bool = True          # incluir no Excel (sinalizadas) ocorrências não validadas
    # --- Desempenho
    concorrencia: int = 4                      # consultas simultâneas (grupos de WOs em paralelo), máx. 8
    tamanho_lote: int = 20                     # WOs por consulta em lote (1 = uma WO por consulta)
    versao: int = 2                            # versão das preferências (migração de padrões antigos)


# Faixas aceitas (mín, máx) e opções.
_FAIXAS = {
    "auditorias_por_wo": (0, 20), "repeticoes_falha": (0, 10), "consultas_extras_divergencia": (0, 10),
    "tempo_consulta_wo_seg": (5, 600), "tempo_consulta_detalhes_seg": (5, 600),
    "intervalo_auditorias_seg": (0, 60), "tempo_max_wo_seg": (10, 3600), "tempo_max_lote_min": (1, 1440),
    "intervalo_consultas_seg": (0, 30), "concorrencia": (1, 8), "tamanho_lote": (1, 50), "versao": (1, 99),
}
# Padrões da versão 1 (5 auditorias fixas, pausa de 0,5 s) que a versão 2 troca pela política de
# escalonamento. Só valores IGUAIS ao padrão antigo são migrados (escolha explícita é mantida).
_MIGRACAO_V2 = {"auditorias_por_wo": (5, 2), "consultas_extras_divergencia": (2, 3),
                "intervalo_auditorias_seg": (0.5, 0.0)}
_OPCOES = {"divergencia": ("reconsultar", "revisar"), "wo_sem_resultado": ("registrar", "reconsultar"),
           "em_erro": ("continuar", "interromper")}


def validar(dados: dict) -> tuple[Preferencias, list[str]]:
    """Monta as preferências a partir de um dicionário qualquer. Devolve (prefs, problemas)."""
    base, problemas = asdict(Preferencias()), []
    for f in fields(Preferencias):
        if f.name not in (dados or {}):
            continue
        bruto, padrao = dados[f.name], base[f.name]
        try:
            if isinstance(padrao, bool):
                valor = bruto if isinstance(bruto, bool) else str(bruto).lower() in ("1", "true", "sim", "on")
            elif isinstance(padrao, int):
                valor = int(float(bruto))
            elif isinstance(padrao, float):
                valor = float(bruto)
            else:
                valor = str(bruto)
        except (TypeError, ValueError):
            problemas.append(f"{f.name}: valor inválido ({bruto!r}); mantido {padrao}")
            continue
        if isinstance(valor, float) and not math.isfinite(valor):
            problemas.append(f"{f.name}: valor inválido ({bruto!r}); mantido {padrao}")
            continue
        if f.name in _FAIXAS:
            minimo, maximo = _FAIXAS[f.name]
            if not minimo <= valor <= maximo:
                problemas.append(f"{f.name}: {valor} fora da faixa {minimo}–{maximo}; ajustado")
                valor = min(max(valor, minimo), maximo)
        if f.name in _OPCOES and valor not in _OPCOES[f.name]:
            problemas.append(f"{f.name}: opção inválida ({valor}); mantido {padrao}")
            continue
        base[f.name] = valor
    return Preferencias(**base), problemas


def migrar(dados: dict) -> tuple[dict, list[str]]:
    """Preferências gravadas pela versão 1 -> versão 2. Devolve (dados, o que mudou)."""
    if not isinstance(dados, dict) or int(dados.get("versao", 1) or 1) >= 2:
        return dados, []
    dados, mudou = dict(dados), []
    for campo, (antigo, novo) in _MIGRACAO_V2.items():
        if campo in dados and dados[campo] == antigo:
            dados[campo] = novo
            mudou.append(f"{campo}: {antigo} → {novo}")
    dados["versao"] = 2
    return dados, mudou


ultima_migracao: list[str] = []


def carregar(arquivo: Path | None = None) -> Preferencias:
    arquivo = arquivo or ARQUIVO
    with _trava:
        try:
            dados = json.loads(arquivo.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return Preferencias()
        dados, mudou = migrar(dados)
        try:
            prefs = validar(dados)[0]
        except AttributeError:
            return Preferencias()
        if mudou:
            ultima_migracao[:] = mudou
            tmp = arquivo.with_suffix(".tmp")
            tmp.write_text(json.dumps(asdict(prefs), ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(arquivo)
        return prefs


def salvar(dados: dict, arquivo: Path | None = None) -> tuple[Preferencias, list[str]]:
    arquivo = arquivo or ARQUIVO
    prefs, problemas = validar(dados)
    with _trava:
        arquivo.parent.mkdir(parents=True, exist_ok=True)
        tmp = arquivo.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(prefs), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(arquivo)
    return prefs, problemas


def como_dict(prefs: Preferencias) -> dict:
    return asdict(prefs)
