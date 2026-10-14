"""Exploração pelos DADOS: descobre de onde vêm os itens da janela "Mensagens" perguntando
direto ao serviço, em vez de adivinhar combinações pelo dicionário.

Raciocínio:
1. O dicionário ($metadata) lista todos os conjuntos de dados do serviço e seus campos.
2. Todo conjunto que tem um campo de WO é consultado filtrando pela WO informada
   (poucos registros, $top). Quem guarda os itens da janela "Mensagens" devolve registros
   com material, fornecedor, quantidade e mensagem — reconhecidos pelo nome E pelo valor
   (material com 18 dígitos, fornecedor com 10, quantidade numérica, mensagem em texto).
3. Conjuntos sem campo de WO são consultados pelos identificadores que aparecem nos
   registros já achados (ex.: consolidID, ID do alerta).
4. Os achados são ordenados pela semelhança com a janela "Mensagens" e pela cobertura
   (quantas WOs da amostra trazem itens). O agente ainda valida cada um antes de gravar.

Somente leitura: só GET em conjuntos de dados; nomes que lembram ação são ignorados.
"""
from __future__ import annotations

import re
from typing import Callable

from .analisador import mapear_campos
from .autoconfig import TIPOS_NUMERO, mapear_por_nomes
from .campos import ESSENCIAIS, partes, tipo_do_campo
from .modelos import ErroConsulta, FalhaComunicacao, SessaoExpirada
from .odata import extrair_pagina, preencher_modelo

TOP = 20
LIMITE_CONSULTAS = 220
_ACAO = re.compile(r"^(limpa|finaliza|reprocess|efetua|executa|atualiza|cria|deleta|apaga|envia|grava|"
                   r"aprova|estorna|cancela|libera|bloqueia|reset)", re.I)
_DICA_NOME = re.compile(r"erro|mensag|msg|pendenc|falta|item", re.I)
_IDENT = re.compile(r"(^id$|id$|_id$|guid|uuid|^key$|chave|consol|^num|numero|^nr|ordem|order|alerta|"
                    r"pendenc|lote|^doc|documento)", re.I)


def _literal(campo: str, tipo: str, v4: bool, valor=None) -> str | None:
    """Literal OData do valor (ou marcador {linha[campo]})."""
    texto = "{linha[" + campo + "]}" if valor is None else str(valor).replace("'", "''")
    if tipo == "Edm.String" or not tipo:
        return f"'{texto}'"
    if tipo == "Edm.Guid":
        return texto if v4 else f"guid'{texto}'"
    if tipo in TIPOS_NUMERO:
        return texto
    return None   # datas e afins não servem de ligação


def _campos_wo(props: dict[str, str]) -> list[str]:
    campos = [c for c, t in props.items() if tipo_do_campo(c) == "wo" and t in ("Edm.String", "")]
    # workOrderID antes de wo_texto: identificador primeiro
    return sorted(campos, key=lambda c: (0 if {"id", "order", "num", "numero"} & set(partes(c)) else 1, len(c)))[:2]


def _nota(registros: list[dict], wo: str) -> tuple[int, dict]:
    """Semelhança com a janela "Mensagens": (pontos, campos mapeados)."""
    melhor = (0, {})
    for r in registros[:3]:
        campos = mapear_campos(r, wo)
        pontos = sum(1 for c in ESSENCIAIS if c in campos)
        if "material" in campos and "mensagem" in campos:
            pontos += 2
        if "falt" in campos.get("quantidade_faltante", "").lower():   # coluna "Quantidade faltante"
            pontos += 1
        melhor = max(melhor, (pontos, campos), key=lambda x: x[0])
    return melhor


class Explorador:
    def __init__(self, get: Callable, modelos: list[tuple[str, dict]], amostra: list[str],
                 ao_avancar: Callable[[int], None] | None = None):
        self.get, self.modelos, self.amostra = get, modelos, amostra
        self.ao_avancar = ao_avancar or (lambda n: None)
        self.consultas = 0
        self.relato: list[dict] = []

    def _ler(self, url: str) -> list[dict] | None:
        if self.consultas >= LIMITE_CONSULTAS:
            return None
        self.consultas += 1
        self.ao_avancar(self.consultas)
        try:
            r = self.get(url)
        except FalhaComunicacao:
            return None
        html = "html" in (r.content_type or "").lower()
        if r.redirecionado or r.status in (0, 401) or (r.status == 200 and html):
            raise SessaoExpirada("Sessão expirada durante a exploração.")
        if r.status != 200 or html:
            return None
        try:
            return [x for x in extrair_pagina(r.corpo)[0] if isinstance(x, dict)]
        except ErroConsulta:
            return None

    def explorar(self) -> list[tuple[dict, list[str]]]:
        por_wo, achados = [], []      # conjuntos consultados pela WO / candidatos
        for wo in self.amostra[:3]:   # a 1ª WO pode não estar mais pendente: tenta a próxima
            self._pela_wo(wo, por_wo, achados)
            if por_wo:
                break
        else:
            return []
        self._ligados(wo, por_wo, achados)
        achados.sort(key=lambda a: (-a[0], not a[1], len(a[2])))
        return [(dados, [f"Itens da janela 'Mensagens' achados nos dados de '{nome}' ({pontos} pontos)."])
                for pontos, _, nome, dados in achados]

    def _pela_wo(self, wo, por_wo, achados) -> None:
        for servico, modelo in self.modelos:
            for nome, info in modelo["conjuntos"].items():
                if _ACAO.match(nome):
                    continue
                for campo in _campos_wo(info["props"]):
                    modelo_url = f"{servico}{nome}?$filter={campo} eq '{{wo}}'"
                    regs = self._ler(preencher_modelo(modelo_url + f"&$top={TOP}", wo=wo))
                    if regs is None:
                        continue
                    pontos, campos = _nota(regs, wo)
                    self.relato.append({"conjunto": nome, "filtro": campo, "registros": len(regs),
                                        "pontos": pontos, "campos": sorted(campos)})
                    if regs:
                        por_wo.append((servico, modelo, nome, campo, modelo_url, regs))
                        if pontos >= 3 and ("material" in campos or "mensagem" in campos):
                            achados.append((pontos, _DICA_NOME.search(nome) is not None, nome, {
                                "url_lista": modelo_url, "url_mensagens": None, "campo_mensagens_expandidas": ".",
                                "campo_consolidado_lista": campos.get("consolidado", ""), "campos": campos}))
                    break   # um campo de WO que respondeu basta para este conjunto
    def _ligados(self, wo, por_wo, achados) -> None:
        """Conjuntos sem WO: ligados pelos identificadores dos registros já achados."""
        for servico, modelo, lista, campo_wo, modelo_lista, regs in por_wo[:6]:
            linha = regs[0]
            for nome, info in modelo["conjuntos"].items():
                if nome == lista or _ACAO.match(nome) or _campos_wo(info["props"]):
                    continue
                if not any(tipo_do_campo(c) in ESSENCIAIS for c in info["props"]):
                    continue
                for campo, tipo in info["props"].items():
                    # mesmo nome nos dois (consolidID) ou chave estrangeira do CAP (pendencia_ID -> ID)
                    origem = campo if campo in linha else campo.rsplit("_", 1)[-1] if "_" in campo else None
                    valor = linha.get(origem) if origem else None
                    if valor in (None, "") or isinstance(valor, (dict, list)) or not _IDENT.search(campo):
                        continue
                    literal = _literal(campo, tipo, modelo["v4"], valor)
                    if not literal:
                        continue
                    regs_m = self._ler(f"{servico}{nome}?$filter={campo}%20eq%20{_codificar(literal)}&$top={TOP}")
                    if not regs_m:
                        continue
                    pontos, campos = _nota(regs_m, wo)
                    self.relato.append({"conjunto": nome, "ligado_a": f"{lista}.{campo}", "registros": len(regs_m),
                                        "pontos": pontos, "campos": sorted(campos)})
                    if pontos >= 3 and ("material" in campos or "mensagem" in campos):
                        achados.append((pontos, _DICA_NOME.search(nome) is not None, nome, {
                            "url_lista": modelo_lista, "url_mensagens":
                                f"{servico}{nome}?$filter={campo} eq {_literal(origem, tipo, modelo['v4'])}",
                            "campo_mensagens_expandidas": None,
                            "campo_consolidado_lista": mapear_por_nomes({c: "Edm.String" for c in linha}).get(
                                "consolidado", ""), "campos": campos}))


def _codificar(literal: str) -> str:
    from urllib.parse import quote
    return quote(literal, safe="'()")
