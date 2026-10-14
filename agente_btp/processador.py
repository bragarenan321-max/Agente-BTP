"""Processa uma lista de WOs: extração inicial + auditorias configuráveis, WO a WO.

Cada auditoria é uma NOVA consulta ao BTP (nunca a mesma resposta guardada em memória).
Com "auditorias independentes", as rodadas pares releem cada item pelo seu endereço próprio
(chave), um caminho diferente da consulta da lista — se o serviço permitir; senão, isso é
registrado como limitação e todas as rodadas usam nova consulta pelo mesmo caminho.
"""
from __future__ import annotations

import logging
import time
from typing import Callable

from .auditoria import consolidar
from .cliente import ClienteBTP, ReleituraIndisponivel
from .entrada import wo_valida
from .modelos import (
    Auditoria,
    ErroConsulta,
    FalhaComunicacao,
    Leitura,
    ResultadoWO,
    SessaoExpirada,
    Status,
    TempoEsgotado,
)
from .preferencias import Preferencias

log = logging.getLogger("agente_btp")
_dormir = time.sleep   # substituível nos testes


def _com_retentativa(funcao, tentativas_extras: int, prazo: float | None):
    for tentativa in range(tentativas_extras + 1):
        try:
            return funcao()
        except FalhaComunicacao:
            if tentativa == tentativas_extras:
                raise
            pausa = min(2 ** tentativa, 5)
            if prazo is not None and time.monotonic() + pausa >= prazo:
                raise
            _dormir(pausa)


def _pausa(segundos: float, prazo: float | None) -> None:
    if segundos <= 0:
        return
    if prazo is not None:
        segundos = min(segundos, max(0.0, prazo - time.monotonic()))
    _dormir(segundos)


def processar_wo(cliente: ClienteBTP, wo: str, prefs: Preferencias | None = None,
                 ao_etapa: Callable[[str], None] | None = None,
                 deve_parar: Callable[[], bool] | None = None) -> ResultadoWO:
    """Uma WO, do início ao fim. SessaoExpirada é propagada para interromper o lote."""
    prefs = prefs or Preferencias()
    etapa = ao_etapa or (lambda texto: None)
    if not wo_valida(wo):
        return ResultadoWO(wo, Status.WO_INVALIDA, auditoria=Auditoria.NAO_APLICAVEL,
                           observacao="Formato esperado: 5 dígitos|9 dígitos (ex.: 02235|230771207)")
    prazo = time.monotonic() + prefs.tempo_max_wo_seg
    cliente.prazo = prazo
    previstas = prefs.auditorias_por_wo
    extras = prefs.repeticoes_falha

    def ler(rodada, tipo):
        return _com_retentativa(lambda: cliente.ler_wo(wo, rodada, tipo), extras, prazo)

    # ---- Etapa A: extração inicial
    etapa(f"{wo}: extração inicial")
    try:
        base = ler(0, "Extração inicial")
        if not base.itens and not base.alertas and prefs.wo_sem_resultado == "reconsultar":
            _pausa(max(prefs.intervalo_auditorias_seg, 1.0), prazo)
            base = ler(0, "Extração inicial (reconsulta: 1ª leitura sem resultado)")
    except TempoEsgotado:
        return ResultadoWO(wo, Status.TEMPO_ESGOTADO, auditoria=Auditoria.NAO_CONCLUIDO,
                           observacao=f"Tempo máximo da WO ({prefs.tempo_max_wo_seg:g} s) atingido na extração.",
                           rodadas_previstas=previstas)
    finally:
        cliente.prazo = prazo
    leituras = [base]

    # ---- Etapa B: auditorias
    por_chave = prefs.auditorias_independentes and cliente.pode_reler_por_chave and bool(base.itens)
    limitacao = ""
    if prefs.auditorias_independentes and base.itens and not cliente.pode_reler_por_chave:
        limitacao = ("Releitura por chave indisponível neste serviço: as auditorias foram novas consultas "
                     "pelo mesmo caminho da extração.")
    for rodada in range(1, previstas + 1):
        if deve_parar and deve_parar():
            break
        _pausa(prefs.intervalo_auditorias_seg, prazo)
        tipo = "Releitura por chave" if por_chave and rodada % 2 == 0 else "Nova consulta"
        etapa(f"{wo}: auditoria {rodada} de {previstas} ({tipo.lower()})")
        try:
            if tipo == "Nova consulta":
                leituras.append(ler(rodada, tipo))
            else:
                try:
                    leituras.append(_com_retentativa(lambda r=rodada: cliente.reler(wo, base, r), extras, prazo))
                except ReleituraIndisponivel as exc:
                    por_chave = False
                    limitacao = f"{exc} As auditorias seguintes foram novas consultas pelo mesmo caminho."
                    leituras.append(ler(rodada, "Nova consulta"))
        except TempoEsgotado:
            leituras.append(Leitura(rodada, tipo, erro=f"tempo máximo da WO ({prefs.tempo_max_wo_seg:g} s) atingido"))
            break
        except (FalhaComunicacao, ErroConsulta) as exc:
            leituras.append(Leitura(rodada, tipo, erro=str(exc)))

    # ---- Etapa C: divergência -> leituras extras (nunca aprovam sozinhas)
    linhas, estado, obs_aud, registros = consolidar(wo, leituras, previstas)
    if estado is Auditoria.DIVERGENTE and prefs.divergencia == "reconsultar":
        for i in range(prefs.consultas_extras_divergencia):
            if deve_parar and deve_parar():
                break
            _pausa(max(prefs.intervalo_auditorias_seg, 1.0), prazo)
            etapa(f"{wo}: divergência — leitura extra {i + 1} de {prefs.consultas_extras_divergencia}")
            try:
                extra = ler(previstas + 1 + i, "Leitura extra")
            except TempoEsgotado:
                break
            except (FalhaComunicacao, ErroConsulta) as exc:
                extra = Leitura(previstas + 1 + i, "Leitura extra", erro=str(exc))
            leituras.append(extra)
        linhas, estado, obs_aud, registros = consolidar(wo, leituras, previstas)
    if limitacao:
        obs_aud = "; ".join(x for x in (obs_aud, limitacao) if x)

    # ---- situação OPERACIONAL (separada da auditoria)
    avisos = list(dict.fromkeys(a for l in leituras for a in l.avisos))
    if not base.alertas and not base.itens:
        status, obs = Status.NAO_ENCONTRADA, "WO sem alerta no Monitor de Alertas"
    elif not linhas:
        status, obs = Status.SEM_REGISTROS, f"{base.alertas} alerta(s) sem itens na janela Mensagens"
    else:
        status, obs = Status.PROCESSADA, "OK"
    if avisos:
        obs = "; ".join([obs] + avisos)
    return ResultadoWO(wo, status, linhas=linhas, observacao=obs, auditoria=estado,
                       observacao_auditoria=obs_aud, leituras=leituras, rodadas_previstas=previstas,
                       registros_auditoria=registros)


def processar_lote(
    cliente: ClienteBTP,
    wos: list[str],
    ao_progredir: Callable[[int, int, ResultadoWO], None] | None = None,
    deve_parar: Callable[[], bool] | None = None,
    prefs: Preferencias | None = None,
    ao_etapa: Callable[[str], None] | None = None,
) -> list[ResultadoWO]:
    prefs = prefs or Preferencias()
    fim_lote = time.monotonic() + prefs.tempo_max_lote_min * 60
    resultados: list[ResultadoWO] = []
    total = len(wos)

    def nao_processadas(restantes, motivo):
        return [ResultadoWO(w, Status.NAO_PROCESSADA, auditoria=Auditoria.NAO_CONCLUIDO, observacao=motivo)
                for w in restantes]

    def parar_wo():
        return bool(deve_parar and deve_parar()) or time.monotonic() >= fim_lote

    for indice, wo in enumerate(wos, start=1):
        if deve_parar and deve_parar():
            resultados.extend(nao_processadas(wos[indice - 1:], "Execução interrompida pelo usuário"))
            break
        if time.monotonic() >= fim_lote:
            resultados.extend(nao_processadas(wos[indice - 1:],
                                              f"Tempo máximo do lote ({prefs.tempo_max_lote_min:g} min) atingido"))
            break
        try:
            resultado = processar_wo(cliente, wo, prefs, ao_etapa, parar_wo)
        except SessaoExpirada as exc:
            # Sem sessão, as demais WOs falhariam igual: registra e interrompe.
            resultados.extend(ResultadoWO(w, Status.SESSAO_EXPIRADA, auditoria=Auditoria.NAO_CONCLUIDO,
                                          observacao=str(exc)) for w in wos[indice - 1:])
            log.warning("Sessão expirada na WO %s; lote interrompido.", wo)
            break
        except FalhaComunicacao as exc:
            resultado = ResultadoWO(wo, Status.FALHA_COMUNICACAO, auditoria=Auditoria.NAO_CONCLUIDO,
                                    observacao=str(exc))
        except ErroConsulta as exc:
            resultado = ResultadoWO(wo, Status.ERRO, auditoria=Auditoria.NAO_CONCLUIDO, observacao=str(exc))
        except Exception as exc:  # noqa: BLE001 — uma WO nunca derruba o lote
            log.exception("Erro inesperado na WO %s", wo)
            resultado = ResultadoWO(wo, Status.ERRO, auditoria=Auditoria.NAO_CONCLUIDO,
                                    observacao=f"Erro inesperado: {exc}")

        resultados.append(resultado)
        log.info("WO %s | %s | %d ocorrência(s) | auditoria %s", wo, resultado.status.value,
                 resultado.registros, resultado.auditoria.value)
        if ao_progredir:
            ao_progredir(indice, total, resultado)
        if prefs.em_erro == "interromper" and resultado.status in (
                Status.ERRO, Status.FALHA_COMUNICACAO, Status.TEMPO_ESGOTADO):
            resultados.extend(nao_processadas(wos[indice:], f"Lote interrompido após erro na WO {wo} "
                                                            "(configuração: interromper diante de erros)"))
            break
    return resultados
