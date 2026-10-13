"""Processa uma lista de WOs e devolve um ResultadoWO por WO."""
from __future__ import annotations

import logging
import time
from typing import Callable

from .cliente import ClienteBTP
from .entrada import wo_valida
from .modelos import (
    ErroConsulta,
    FalhaComunicacao,
    ResultadoWO,
    SessaoExpirada,
    Status,
)

log = logging.getLogger("agente_btp")

TENTATIVAS_REDE = 3


def processar_wo(cliente: ClienteBTP, wo: str) -> ResultadoWO:
    """Uma WO, do início ao fim. SessaoExpirada é propagada para interromper o lote."""
    if not wo_valida(wo):
        return ResultadoWO(wo, Status.WO_INVALIDA,
                           observacao="Formato esperado: 5 dígitos|9 dígitos (ex.: 02235|230771207)")

    alertas = _com_retentativa(lambda: cliente.buscar_alertas(wo))
    if not alertas:
        return ResultadoWO(wo, Status.NAO_ENCONTRADA,
                           observacao="WO sem pendência no Monitor de alertas")

    linhas = []
    for alerta in alertas:
        mensagens = _com_retentativa(lambda a=alerta: cliente.buscar_mensagens(a))
        linhas.extend(cliente.converter(wo, alerta, m) for m in mensagens)

    if not linhas:
        return ResultadoWO(wo, Status.SEM_REGISTROS,
                           observacao=f"{len(alertas)} alerta(s) sem mensagens de detalhe")
    return ResultadoWO(wo, Status.PROCESSADA, linhas=linhas, observacao="OK")


def processar_lote(
    cliente: ClienteBTP,
    wos: list[str],
    ao_progredir: Callable[[int, int, ResultadoWO], None] | None = None,
    deve_parar: Callable[[], bool] | None = None,
) -> list[ResultadoWO]:
    resultados: list[ResultadoWO] = []
    total = len(wos)
    for indice, wo in enumerate(wos, start=1):
        if deve_parar and deve_parar():
            resultados.extend(ResultadoWO(w, Status.ERRO, observacao="Execução interrompida pelo usuário")
                              for w in wos[indice - 1:])
            break
        try:
            resultado = processar_wo(cliente, wo)
        except SessaoExpirada as exc:
            # Sem sessão, as demais WOs falhariam igual: registra e interrompe.
            resultados.extend(ResultadoWO(w, Status.SESSAO_EXPIRADA, observacao=str(exc))
                              for w in wos[indice - 1:])
            log.warning("Sessão expirada na WO %s; lote interrompido.", wo)
            break
        except FalhaComunicacao as exc:
            resultado = ResultadoWO(wo, Status.FALHA_COMUNICACAO, observacao=str(exc))
        except ErroConsulta as exc:
            resultado = ResultadoWO(wo, Status.ERRO, observacao=str(exc))
        except Exception as exc:  # noqa: BLE001 — uma WO nunca derruba o lote
            log.exception("Erro inesperado na WO %s", wo)
            resultado = ResultadoWO(wo, Status.ERRO, observacao=f"Erro inesperado: {exc}")

        resultados.append(resultado)
        log.info("WO %s | %s | %d registro(s)", wo, resultado.status.value, resultado.registros)
        if ao_progredir:
            ao_progredir(indice, total, resultado)
    return resultados


def _com_retentativa(funcao):
    for tentativa in range(1, TENTATIVAS_REDE + 1):
        try:
            return funcao()
        except FalhaComunicacao:
            if tentativa == TENTATIVAS_REDE:
                raise
            time.sleep(2 ** tentativa)
