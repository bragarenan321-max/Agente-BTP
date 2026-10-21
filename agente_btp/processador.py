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
from .cliente import ClienteBTP, LoteInconsistente, ReleituraIndisponivel
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


LIMITACAO_SEM_CHAVE = ("Releitura por chave indisponível neste serviço: as auditorias foram novas consultas "
                       "pelo mesmo caminho da extração.")


def _releitura_possivel(cliente: ClienteBTP) -> bool:
    return cliente.pode_reler_por_chave and not cliente.cache.releitura_indisponivel


def _ler_auditoria(cliente, wo, base, rodada, tipo, extras, prazo, estado) -> Leitura:
    """Uma rodada de auditoria de UMA WO (nova consulta ou releitura por chave)."""
    if tipo == "Nova consulta":
        return _com_retentativa(lambda: cliente.ler_wo(wo, rodada, tipo), extras, prazo)
    try:
        leitura = _com_retentativa(lambda: cliente.reler(wo, base, rodada), extras, prazo)
        cliente.cache.registrar_releitura(True)
        return leitura
    except ReleituraIndisponivel as exc:
        cliente.cache.registrar_releitura(False)
        estado["por_chave"] = False
        estado["limitacao"] = f"{exc} As auditorias seguintes foram novas consultas pelo mesmo caminho."
        return _com_retentativa(lambda: cliente.ler_wo(wo, rodada, "Nova consulta"), extras, prazo)


def _estado_auditoria(cliente, prefs, base) -> dict:
    estado = {"por_chave": prefs.auditorias_independentes and _releitura_possivel(cliente) and bool(base.itens),
              "limitacao": ""}
    if prefs.auditorias_independentes and base.itens and not _releitura_possivel(cliente):
        estado["limitacao"] = LIMITACAO_SEM_CHAVE
    return estado


def _tipo_rodada(estado, rodada) -> str:
    return "Releitura por chave" if estado["por_chave"] and rodada % 2 == 0 else "Nova consulta"


def _concluir(cliente, wo, leituras, prefs, prazo, estado, etapa, deve_parar) -> ResultadoWO:
    """Etapa C (divergência -> leituras extras, que nunca aprovam sozinhas) + situação operacional."""
    previstas = prefs.auditorias_por_wo
    extras = prefs.repeticoes_falha
    linhas, est, obs_aud, registros = consolidar(wo, leituras, previstas)
    if est is Auditoria.DIVERGENTE and prefs.divergencia == "reconsultar":
        # Escalonamento: as leituras extras são INDIVIDUAIS (caminho diferente da leitura em lote).
        for i in range(prefs.consultas_extras_divergencia):
            if deve_parar and deve_parar():
                break
            _pausa(prefs.intervalo_auditorias_seg, prazo)
            etapa(f"{wo}: divergência — leitura extra {i + 1} de {prefs.consultas_extras_divergencia}")
            try:
                extra = _com_retentativa(lambda r=previstas + 1 + i: cliente.ler_wo(wo, r, "Leitura extra"),
                                         extras, prazo)
            except TempoEsgotado:
                break
            except (FalhaComunicacao, ErroConsulta) as exc:
                extra = Leitura(previstas + 1 + i, "Leitura extra", erro=str(exc))
            leituras.append(extra)
        linhas, est, obs_aud, registros = consolidar(wo, leituras, previstas)
    if estado["limitacao"]:
        obs_aud = "; ".join(x for x in (obs_aud, estado["limitacao"]) if x)

    # ---- situação OPERACIONAL (separada da auditoria)
    avisos = list(dict.fromkeys(a for l in leituras for a in l.avisos))
    # Decide por TODAS as leituras, não só pela primeira (que pode ter vindo vazia).
    max_alertas = max((l.alertas or 0 for l in leituras if l.ok), default=0)
    if linhas:
        status, obs = Status.PROCESSADA, "OK"
    elif max_alertas:
        status, obs = Status.SEM_REGISTROS, f"{max_alertas} alerta(s) sem itens na janela Mensagens"
    else:
        status, obs = Status.NAO_ENCONTRADA, "WO sem alerta no Monitor de Alertas"
    if avisos:
        obs = "; ".join([obs] + avisos)
    return ResultadoWO(wo, status, linhas=linhas, observacao=obs, auditoria=est,
                       observacao_auditoria=obs_aud, leituras=leituras, rodadas_previstas=previstas,
                       registros_auditoria=registros)


def _invalida(wo) -> ResultadoWO:
    return ResultadoWO(wo, Status.WO_INVALIDA, auditoria=Auditoria.NAO_APLICAVEL,
                       observacao="Formato esperado: 5 dígitos|9 dígitos (ex.: 02235|230771207)")


def processar_wo(cliente: ClienteBTP, wo: str, prefs: Preferencias | None = None,
                 ao_etapa: Callable[[str], None] | None = None,
                 deve_parar: Callable[[], bool] | None = None) -> ResultadoWO:
    """Uma WO, do início ao fim, por consultas individuais. SessaoExpirada é propagada."""
    prefs = prefs or Preferencias()
    etapa = ao_etapa or (lambda texto: None)
    if not wo_valida(wo):
        return _invalida(wo)
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
            _pausa(prefs.intervalo_auditorias_seg, prazo)
            base = ler(0, "Extração inicial (reconsulta: 1ª leitura sem resultado)")
    except TempoEsgotado:
        return ResultadoWO(wo, Status.TEMPO_ESGOTADO, auditoria=Auditoria.NAO_CONCLUIDO,
                           observacao=f"Tempo máximo da WO ({prefs.tempo_max_wo_seg:g} s) atingido na extração.",
                           rodadas_previstas=previstas)
    finally:
        cliente.prazo = prazo
    leituras = [base]

    # ---- Etapa B: auditorias
    estado = _estado_auditoria(cliente, prefs, base)
    for rodada in range(1, previstas + 1):
        if deve_parar and deve_parar():
            break
        _pausa(prefs.intervalo_auditorias_seg, prazo)
        tipo = _tipo_rodada(estado, rodada)
        etapa(f"{wo}: auditoria {rodada} de {previstas} ({tipo.lower()})")
        try:
            leituras.append(_ler_auditoria(cliente, wo, base, rodada, tipo, extras, prazo, estado))
        except TempoEsgotado:
            leituras.append(Leitura(rodada, tipo, erro=f"tempo máximo da WO ({prefs.tempo_max_wo_seg:g} s) atingido"))
            break
        except (FalhaComunicacao, ErroConsulta) as exc:
            leituras.append(Leitura(rodada, tipo, erro=str(exc)))
    r = _concluir(cliente, wo, leituras, prefs, prazo, estado, etapa, deve_parar)
    r.origem = "consulta individual"
    return r


def processar_wo_seguro(cliente: ClienteBTP, wo: str, prefs: Preferencias | None = None,
                        ao_etapa: Callable[[str], None] | None = None,
                        deve_parar: Callable[[], bool] | None = None) -> ResultadoWO:
    """processar_wo, mas falha de consulta vira resultado da PRÓPRIA WO (com motivo classificado).
    SessaoExpirada continua propagando: não é problema da WO, é da sessão."""
    try:
        return processar_wo(cliente, wo, prefs, ao_etapa, deve_parar)
    except FalhaComunicacao as exc:
        return ResultadoWO(wo, Status.FALHA_COMUNICACAO, auditoria=Auditoria.NAO_CONCLUIDO, observacao=str(exc),
                           motivo=exc.motivo, origem="consulta individual")
    except TempoEsgotado as exc:
        return ResultadoWO(wo, Status.TEMPO_ESGOTADO, auditoria=Auditoria.NAO_CONCLUIDO, observacao=str(exc),
                           motivo=exc.motivo, origem="consulta individual")
    except ErroConsulta as exc:
        return ResultadoWO(wo, Status.ERRO, auditoria=Auditoria.NAO_CONCLUIDO, observacao=str(exc),
                           motivo=exc.motivo, origem="consulta individual")


def assinatura_leitura(leitura: Leitura) -> list:
    """O que uma leitura afirma sobre a WO, na ordem: (chave, valores) de cada item + nº de alertas."""
    return [leitura.alertas, leitura.duplicadas,
            [(k, sorted((c, str(v)) for c, v in o.valores.items())) for k, o in (leitura.itens or {}).items()]]


class LoteDivergente(Exception):
    """A leitura em lote não bateu com a leitura individual da mesma WO."""


def processar_grupo(cliente: ClienteBTP, wos: list[str], prefs: Preferencias | None = None,
                    ao_etapa: Callable[[str], None] | None = None,
                    deve_parar: Callable[[], bool] | None = None,
                    verificar: str = "amostra",
                    ao_lote_desativado: Callable[[str, dict], None] | None = None,
                    ao_concluir: Callable[[ResultadoWO], None] | None = None) -> list[ResultadoWO]:
    """Várias WOs com consultas EM LOTE (cada rodada = poucas consultas para o grupo inteiro).

    Mesmas regras de processar_wo, rodada a rodada. Proteções:
      - `verificar`: WOs do grupo relidas INDIVIDUALMENTE na extração — "todas" (primeiros grupos
        da análise) ou "amostra" (uma com itens e uma vazia, em rodízio); se a leitura em lote não for
        idêntica, o lote é desativado para a análise inteira e o grupo é refeito WO a WO;
      - qualquer resposta que não possa ser repartida por WO com segurança => grupo refeito WO a WO;
      - divergência => leituras extras individuais (outro caminho), como em processar_wo.
    SessaoExpirada é propagada (o chamador devolve para "pendente" as WOs ainda não concluídas).
    `ao_concluir` é chamado com cada WO assim que ela termina (para gravar na hora).
    """
    prefs = prefs or Preferencias()
    etapa = ao_etapa or (lambda texto: None)
    avisar = ao_concluir or (lambda r: None)
    saida: dict[str, ResultadoWO] = {}
    for w in wos:
        if not wo_valida(w) and w not in saida:
            saida[w] = _invalida(w)
            avisar(saida[w])
    validas = list(dict.fromkeys(w for w in wos if wo_valida(w)))

    def individual(lista):
        for w in lista:
            saida[w] = processar_wo_seguro(cliente, w, prefs, ao_etapa, deve_parar)
            avisar(saida[w])

    if len(validas) <= 1 or not cliente.pode_ler_em_lote:
        individual(validas)
        return [saida[w] for w in wos]
    previstas = prefs.auditorias_por_wo
    extras = prefs.repeticoes_falha
    prazo = time.monotonic() + prefs.tempo_max_wo_seg
    cliente.prazo = prazo

    def lote(lista, rodada, tipo):
        return _com_retentativa(lambda: cliente.ler_lote(lista, rodada, tipo), extras, prazo)

    try:
        etapa(f"{len(validas)} WOs: extração inicial (consulta em lote)")
        base = lote(validas, 0, "Extração inicial")
        vazias = [w for w in validas if not base[w].itens and not base[w].alertas]
        if vazias and prefs.wo_sem_resultado == "reconsultar":
            _pausa(prefs.intervalo_auditorias_seg, prazo)
            base.update(lote(vazias, 0, "Extração inicial (reconsulta: 1ª leitura sem resultado)"))
        if verificar == "todas":
            alvos = list(validas)
        elif verificar:
            com_itens = [w for w in validas if base[w].itens]
            sem_itens = [w for w in validas if not base[w].itens]
            vez = cliente.cache.consultas
            alvos = [lista[vez % len(lista)] for lista in (com_itens, sem_itens) if lista]
        else:
            alvos = []
        for alvo in alvos:
            conferida = _com_retentativa(lambda a=alvo: cliente.ler_wo(a, 0, base[a].tipo), extras, prazo)
            if assinatura_leitura(conferida) != assinatura_leitura(base[alvo]):
                raise LoteDivergente(alvo)
    except LoteDivergente as exc:
        detalhe = {"wo": str(exc)}
        if cliente.cache.desativar_lote("leitura em lote diferente da individual") and ao_lote_desativado:
            ao_lote_desativado("leitura em lote diferente da leitura individual", detalhe)
        cliente.prazo = None
        individual(validas)
        return [saida[w] for w in wos]
    except (LoteInconsistente, ErroConsulta, FalhaComunicacao, TempoEsgotado) as exc:
        # Lote recusado (URL longa, filtro, tempo...): menor nas próximas vezes; este grupo, WO a WO.
        if isinstance(exc, ErroConsulta) or getattr(exc, "motivo", "") == "timeout_lote":
            cliente.cache.reduzir_lote(len(validas))
            if not cliente.cache.lote_ativo and ao_lote_desativado:
                ao_lote_desativado(str(exc), getattr(exc, "evidencia", {}))
        elif isinstance(exc, LoteInconsistente) and cliente.cache.desativar_lote(str(exc)) and ao_lote_desativado:
            ao_lote_desativado(str(exc), {})
        cliente.prazo = None
        individual(validas)
        return [saida[w] for w in wos]

    leituras = {w: [base[w]] for w in validas}
    estados = {w: _estado_auditoria(cliente, prefs, base[w]) for w in validas}
    ativas = list(validas)
    for rodada in range(1, previstas + 1):
        if deve_parar and deve_parar():
            break
        _pausa(prefs.intervalo_auditorias_seg, prazo)
        etapa(f"{len(validas)} WOs: auditoria {rodada} de {previstas}")
        novas = [w for w in ativas if _tipo_rodada(estados[w], rodada) == "Nova consulta"]
        if novas:
            try:
                lidas = lote(novas, rodada, "Nova consulta") if len(novas) > 1 else \
                    {novas[0]: _com_retentativa(lambda: cliente.ler_wo(novas[0], rodada, "Nova consulta"),
                                                extras, prazo)}
                for w in novas:
                    leituras[w].append(lidas[w])
            except TempoEsgotado:
                for w in novas:
                    leituras[w].append(Leitura(rodada, "Nova consulta",
                                               erro=f"tempo máximo da WO ({prefs.tempo_max_wo_seg:g} s) atingido"))
                ativas = [w for w in ativas if w not in novas]
            except (LoteInconsistente, FalhaComunicacao, ErroConsulta):
                # O lote desta rodada falhou: cada WO faz a sua leitura individual (nada se perde).
                for w in novas:
                    try:
                        leituras[w].append(_ler_auditoria(cliente, w, base[w], rodada, "Nova consulta", extras,
                                                          prazo, estados[w]))
                    except TempoEsgotado:
                        leituras[w].append(Leitura(rodada, "Nova consulta",
                                                   erro=f"tempo máximo da WO ({prefs.tempo_max_wo_seg:g} s) atingido"))
                        ativas.remove(w)
                    except (FalhaComunicacao, ErroConsulta) as exc:
                        leituras[w].append(Leitura(rodada, "Nova consulta", erro=str(exc)))
        for w in [w for w in ativas if w not in novas]:
            try:
                leituras[w].append(_ler_auditoria(cliente, w, base[w], rodada, "Releitura por chave", extras,
                                                  prazo, estados[w]))
            except TempoEsgotado:
                leituras[w].append(Leitura(rodada, "Releitura por chave",
                                           erro=f"tempo máximo da WO ({prefs.tempo_max_wo_seg:g} s) atingido"))
                ativas.remove(w)
            except (FalhaComunicacao, ErroConsulta) as exc:
                leituras[w].append(Leitura(rodada, "Releitura por chave", erro=str(exc)))
    for w in validas:
        # Leituras extras de divergência: prazo próprio por WO (como no processamento individual).
        prazo_wo = time.monotonic() + prefs.tempo_max_wo_seg
        cliente.prazo = prazo_wo
        saida[w] = _concluir(cliente, w, leituras[w], prefs, prazo_wo, estados[w], etapa, deve_parar)
        saida[w].origem = f"consulta em lote ({len(validas)} WOs)"
        avisar(saida[w])
    return [saida[w] for w in wos]


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
            # Só a WO em que a sessão caiu leva esse motivo; as seguintes NÃO foram consultadas
            # (ficam "não processadas", nunca como falha por um erro que aconteceu depois delas).
            resultados.append(ResultadoWO(wo, Status.SESSAO_EXPIRADA, auditoria=Auditoria.NAO_CONCLUIDO,
                                          observacao=str(exc)))
            resultados.extend(nao_processadas(wos[indice:], f"Não consultada: a sessão do BTP expirou na WO {wo}"))
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
