"""Auditoria: compara as leituras de uma WO campo a campo e decide o que pode ser afirmado.

Regras (na ordem em que são aplicadas a cada ocorrência):
1. DIVERGENTE  — algum campo mudou entre leituras, ou a ocorrência sumiu/apareceu.
   O valor final só é registrado se houver EVIDÊNCIA: o próprio BTP informa que o registro
   foi alterado (campo de última alteração mudou) e as leituras posteriores à alteração
   concordam entre si (≥ 2). Maioria de votos NÃO é evidência: sem isso, o campo fica vazio
   e a ocorrência vai para revisão manual.
2. INCOMPLETO  — algum dos cinco campos não veio do BTP, ou a quantidade faltante é
   ambígua (há mais de um campo de quantidade e nenhum é claramente o "faltante").
3. NÃO CONCLUÍDO — nem todas as auditorias configuradas foram feitas (tempo, falha, interrupção).
4. VALIDADO    — todas as leituras concluídas concordam e passou nas verificações acima.
"""
from __future__ import annotations

from decimal import Decimal

from .campos import peso_nome
from .modelos import CAMPOS, GRAVIDADE, ROTULO, Auditoria, Leitura, LinhaMensagem
from .odata import instante

RELEITURA = "Releitura por chave"


def _texto(valor) -> str:
    if valor is None or valor == "":
        return "(vazio)"
    if isinstance(valor, Decimal):
        return format(valor.normalize(), "f")
    return str(valor)


def _observacoes(chave: str, leituras: list[Leitura]):
    """(leitura, ocorrência | None=ausente | 'nao_relido') de uma chave em cada leitura concluída."""
    for l in leituras:
        if not l.ok:
            continue
        if l.tipo == RELEITURA:
            yield l, (l.itens[chave] if chave in l.itens else "nao_relido")
        else:
            yield l, l.itens.get(chave)


def _resolver(obs: list, campo: str):
    """Tenta explicar a divergência pelo registro de alteração do próprio BTP.

    Exige: data de alteração REAL (não texto qualquer), datas que não voltam no tempo na ordem
    das leituras, e ≥ 2 leituras feitas DEPOIS da última alteração concordando entre si.
    """
    com_marca = [(l, o, instante(o.marca_tempo)) for l, o in obs
                 if o is not None and o != "nao_relido" and o.marca_tempo]
    if len(com_marca) < 3 or any(t is None for _, _, t in com_marca):
        return None, ""
    tempos = [t for _, _, t in com_marca]
    if any(b < a for a, b in zip(tempos, tempos[1:])) or len(set(tempos)) < 2:
        return None, ""                      # data voltou no tempo ou nunca mudou: não explica
    ultima = tempos[-1]
    inicio = tempos.index(ultima)            # primeira leitura já com a alteração mais recente
    depois = [o.valores[campo] for _, o, _ in com_marca[inicio:]]
    if len(depois) >= 2 and len({_texto(v) for v in depois}) == 1:
        return depois[-1], (f"Resolvida: o BTP registra alteração do item ({com_marca[0][1].marca_tempo} → "
                            f"{com_marca[inicio][1].marca_tempo}); valor atual confirmado em "
                            f"{len(depois)} leituras posteriores à alteração.")
    return None, ""


def consolidar(wo: str, leituras: list[Leitura], previstas: int):
    """Devolve (linhas finais, estado da WO, observação da WO, registros da aba Auditoria)."""
    base = leituras[0]
    concluidas = sum(1 for l in leituras[1:] if l.ok and l.tipo != "Leitura extra")
    registros: list[dict] = []
    obs_wo: list[str] = []
    estado_wo = Auditoria.VALIDADO

    # ---- nível WO: quantidade de ocorrências e de linhas da lista em cada consulta completa
    completas = [l for l in leituras if l.ok and l.tipo != RELEITURA]
    contagens = {l.rodada: len(l.itens) for l in completas}
    alertas = {l.rodada: l.alertas for l in completas if l.alertas is not None}
    if len(set(contagens.values())) > 1:
        estado_wo = Auditoria.DIVERGENTE
        obs_wo.append("Quantidade de ocorrências variou entre leituras: "
                      + ", ".join(f"rodada {r}: {n}" for r, n in contagens.items()))
    if len(set(alertas.values())) > 1:
        estado_wo = Auditoria.DIVERGENTE
        obs_wo.append("Linhas da WO na lista do Monitor variaram: "
                      + ", ".join(f"rodada {r}: {n}" for r, n in alertas.items()))
    for l in leituras:
        registros.append({"wo": wo, "material": "—", "ocorrencia": "(WO)", "rodada": l.rodada, "tipo": l.tipo,
                          "quando": l.quando, "quantidade": None, "fornecedor": "", "mensagem": "",
                          "resultado": (f"Leitura falhou: {l.erro}" if not l.ok else
                                        f"{len(l.itens)} ocorrência(s)" + (f"; {l.alertas} linha(s) na lista"
                                                                          if l.alertas is not None else "")),
                          "observacao": "; ".join(l.avisos)})

    # ---- nível ocorrência
    chaves = list(base.itens) + [k for l in leituras for k in (l.itens or {}) if k not in base.itens]
    chaves = list(dict.fromkeys(chaves))
    linhas: list[LinhaMensagem] = []
    for chave in chaves:
        obs = list(_observacoes(chave, leituras))
        presentes = [(l, o) for l, o in obs if o is not None and o != "nao_relido"]
        if not presentes:
            continue
        ref = presentes[0][1]
        # Auditorias que de fato conferiram ESTA ocorrência ("não relido" não conta).
        feitas = sum(1 for l, o in obs if l.rodada > 0 and l.tipo != "Leitura extra" and o != "nao_relido")
        notas, motivos, divergente = {}, [], False
        ausencias = [l.rodada for l, o in obs if o is None]
        if ausencias or chave not in base.itens:
            divergente = True
            motivos.append(("Não estava na extração inicial; " if chave not in base.itens else "")
                           + (f"ausente na(s) rodada(s) {', '.join(map(str, ausencias))}" if ausencias else
                              f"apareceu na rodada {presentes[0][0].rodada}"))
        finais = {}
        for campo in CAMPOS:
            valores = [o.valores[campo] for _, o in presentes]
            if len({_texto(v) for v in valores}) == 1:
                finais[campo] = valores[0]
                continue
            divergente = True
            vistos = " / ".join(f"r{l.rodada}={_texto(o.valores[campo])}" for l, o in presentes)
            valor, explicacao = _resolver(presentes, campo)
            finais[campo] = valor
            notas[campo] = f"Divergente entre leituras: {vistos}." + (f" {explicacao}" if explicacao else
                                                                      " Não resolvida: revisar no BTP.")
            motivos.append(f"{ROTULO[campo]} divergente ({vistos})"
                           + (" — resolvida pelo registro de alteração" if explicacao else ""))
        incompleto = False
        for campo in CAMPOS:
            if campo not in notas and finais[campo] in (None, ""):
                incompleto = True
                notas[campo] = "Não obtido no BTP (campo vazio ou inexistente no registro)."
                motivos.append(f"{ROTULO[campo]} não obtido")
        fonte_qtd = ref.fonte.get("quantidade_faltante", "")
        if ref.outras_quantidades and fonte_qtd and peso_nome("quantidade_faltante", fonte_qtd) <= 0:
            incompleto = True
            alternativas = ", ".join(f"{k}={_texto(v)}" for k, v in ref.outras_quantidades.items())
            notas.setdefault("quantidade_faltante",
                             f"Ambígua: lido de '{fonte_qtd}', mas o registro também tem {alternativas}.")
            motivos.append(f"Quantidade faltante ambígua ('{fonte_qtd}' x {alternativas})")
        if divergente:
            estado = Auditoria.DIVERGENTE
        elif incompleto:
            estado = Auditoria.INCOMPLETO
        elif feitas < previstas or previstas == 0:
            estado = Auditoria.NAO_CONCLUIDO
            motivos.append(f"{feitas} de {previstas} auditorias conferiram esta ocorrência" if previstas else
                           "Auditoria desativada nas configurações: apenas uma leitura")
        else:
            estado = Auditoria.VALIDADO
        if ref.outras_quantidades and estado is Auditoria.VALIDADO:
            motivos.append("Quantidade lida de '" + fonte_qtd + "'; outros campos de quantidade no registro: "
                           + ", ".join(f"{k}={_texto(v)}" for k, v in ref.outras_quantidades.items()))
        linhas.append(LinhaMensagem(
            consolidado=ref.valores.get("consolidado", ""), wo=finais["wo"] if finais["wo"] else wo,
            material=finais["material"], fornecedor=finais["fornecedor"],
            quantidade_faltante=finais["quantidade_faltante"], mensagem=finais["mensagem"],
            chave=chave, auditoria=estado, observacao="; ".join(motivos), notas=notas,
            mensagem_item=ref.valores.get("mensagem_item", "") or ""))
        if GRAVIDADE[estado] > GRAVIDADE[estado_wo]:
            estado_wo = estado
        for l, o in obs:
            if o == "nao_relido":
                resultado = "Não relido (item sem chave para releitura)"
                o = None
            elif o is None:
                resultado = "Ausente nesta leitura"
            else:
                difs = [f"{ROTULO[c]} ({_texto(ref.valores[c])} → {_texto(o.valores[c])})"
                        for c in CAMPOS if _texto(o.valores[c]) != _texto(ref.valores[c])]
                resultado = ("Referência (1ª leitura)" if o is ref else
                             "Diferente: " + "; ".join(difs) if difs else "Igual à 1ª leitura")
            registros.append({"wo": wo, "material": (o or ref).valores["material"], "ocorrencia": chave,
                              "rodada": l.rodada, "tipo": l.tipo, "quando": l.quando,
                              "quantidade": o.valores["quantidade_faltante"] if o else None,
                              "fornecedor": o.valores["fornecedor"] if o else "",
                              "mensagem": o.valores["mensagem"] if o else "",
                              "resultado": resultado, "situacao": estado.value,
                              "observacao": ("Campo de quantidade: " + fonte_qtd) if o is ref and fonte_qtd else ""})
    if not linhas and estado_wo is Auditoria.VALIDADO and (concluidas < previstas or previstas == 0):
        estado_wo = Auditoria.NAO_CONCLUIDO
    if concluidas < previstas:
        obs_wo.append(f"{concluidas} de {previstas} auditorias concluídas")
    elif previstas == 0:
        obs_wo.append("Auditoria desativada nas configurações")
    for r in registros:
        r.setdefault("situacao", estado_wo.value)
    return linhas, estado_wo, "; ".join(obs_wo), registros
