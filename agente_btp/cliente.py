"""Cliente de dados SOMENTE LEITURA.

Só existe uma operação de rede: GET. Não há caminho no código para POST, PUT,
PATCH, DELETE ou $batch — assim o agente não consegue acionar "Reprocessar" nem
"Efetuar Baixa", nem por engano.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urljoin, urlsplit

from . import odata
from .config import ApiConfig
from .campos import campos_de_quantidade
from .modelos import (ErroConsulta, FalhaComunicacao, Leitura, LinhaMensagem, Ocorrencia,
                      SessaoExpirada, TempoEsgotado)


@dataclass
class Resposta:
    status: int
    content_type: str
    corpo: str
    redirecionado: bool = False


# Transporte: recebe uma URL e devolve a Resposta de um GET feito com a sessão
# autenticada. Em produção é a aba do SAP (ponte.py); nos testes, um falso.
Transporte = Callable[[str], Resposta]

LIMITE_PAGINAS = 50


def classificar(resposta: Resposta) -> str:
    """Valida a resposta HTTP e devolve o corpo, ou levanta a exceção adequada."""
    if resposta.redirecionado or resposta.status in (0, 401):
        raise SessaoExpirada("Sessão expirada ou não autenticada.")
    if resposta.status == 403:
        raise ErroConsulta("Acesso negado (HTTP 403).")
    if resposta.status >= 500:
        raise FalhaComunicacao(f"Servidor indisponível (HTTP {resposta.status}).")
    if resposta.status == 404:
        raise ErroConsulta("Endereço de dados não encontrado (HTTP 404).")
    if resposta.status >= 400:
        raise ErroConsulta(f"Consulta rejeitada (HTTP {resposta.status}).")
    if "html" in resposta.content_type.lower():
        # O approuter devolve a página de login em HTML quando a sessão cai.
        raise SessaoExpirada("Recebida página HTML no lugar de dados (provável login).")
    return resposta.corpo


class ClienteBTP:
    """Leitura das WOs no BTP. `tempos` = tempo máximo (s) por tipo de consulta; `prazo` =
    instante (time.monotonic) em que acaba o tempo da WO atual; `intervalo` = pausa entre consultas."""

    def __init__(self, config: ApiConfig, transporte: Transporte, tempos: dict | None = None,
                 intervalo: float = 0.0):
        self.config = config
        self._transporte = transporte
        self.tempos = tempos or {}
        self.intervalo = intervalo
        self.prazo: float | None = None
        self._ultima = 0.0
        m = re.search(r"\$filter=([\w/]+)\s+eq\s+'\{wo\}'", config.url_lista)
        self.campo_wo_lista = m.group(1) if m else None

    # ------------------------------------------------ transporte com limites de tempo
    def _chamar(self, url: str, tipo: str) -> Resposta:
        if self.intervalo:
            espera = self._ultima + self.intervalo - time.monotonic()
            if espera > 0:
                time.sleep(espera)
        limite = self.tempos.get(tipo)
        if self.prazo is not None:
            restante = self.prazo - time.monotonic()
            if restante < 0.2:   # não dá tempo para uma consulta: encerra em vez de abortar no meio
                raise TempoEsgotado("Tempo máximo da WO atingido.")
            limite = min(limite, restante) if limite else restante
        if limite:
            limite = max(limite, 1.0)   # a aba nunca recebe 0 (lá, 0 viraria o padrão de 60 s)
        try:
            return self._transporte(url, timeout=limite) if (limite and self.tempos) else self._transporte(url)
        finally:
            self._ultima = time.monotonic()

    def _get(self, url: str, tipo: str = "detalhes") -> list[dict]:
        """GET com paginação do servidor (__next / @odata.nextLink)."""
        registros: list[dict] = []
        for _ in range(LIMITE_PAGINAS):
            pagina, proximo = odata.extrair_pagina(classificar(self._chamar(url, tipo)))
            registros.extend(pagina)
            if not proximo:
                return registros
            url = urljoin(url, proximo)
        raise ErroConsulta(f"Mais de {LIMITE_PAGINAS} páginas de resultado; consulta interrompida.")

    def buscar_alertas(self, wo: str) -> list[dict]:
        return self._get(odata.preencher_modelo(self.config.url_lista, wo=wo), "lista")

    def buscar_mensagens(self, alerta: dict) -> list[dict]:
        if self.config.campo_mensagens_link:
            uri = link_navegacao(alerta, self.config.campo_mensagens_link)
            if not uri:
                return []
            return self._get(relativizar(uri, self.config.url_lista))
        if self.config.campo_mensagens_json:
            return registros_json(odata.obter_campo(alerta, self.config.campo_mensagens_json))
        if self.config.campo_mensagens_expandidas == ".":
            return [alerta]  # a própria linha já é a mensagem
        if self.config.campo_mensagens_expandidas:
            bruto = odata.obter_campo(alerta, self.config.campo_mensagens_expandidas)
            if isinstance(bruto, dict) and "results" in bruto:
                bruto = bruto["results"]
            return list(bruto or [])
        return self._get(odata.preencher_modelo(self.config.url_mensagens, linha=alerta))

    # ------------------------------------------------ leitura completa de uma WO
    def ler_wo(self, wo: str, rodada: int = 0, tipo: str = "Extração inicial") -> Leitura:
        """Lê todas as ocorrências da WO, do jeito que o BTP as entrega, sem somar nem agrupar.

        - Linha da lista de OUTRA WO (filtro ignorado pelo servidor) é descartada e avisada.
        - O mesmo item que chega duas vezes (ex.: dois alertas da WO apontando para os
          mesmos itens) é contado UMA vez; a repetição fica registrada em `duplicadas`.
        """
        leitura = Leitura(rodada, tipo)
        alertas = self.buscar_alertas(wo)
        validos = []
        for a in alertas:
            lido = odata.como_texto(odata.obter_campo(a, self.campo_wo_lista)) if self.campo_wo_lista else ""
            if lido and lido != wo:
                leitura.avisos.append(f"Linha da lista de outra WO ({lido}) descartada: o filtro não foi aplicado.")
                continue
            validos.append(a)
        leitura.alertas = len(validos)
        itens: dict = {}
        sem_chave = 0
        mesma_consulta = self.config.campo_mensagens_expandidas == "."   # cada linha já é um item
        vistos_no_alerta: dict[str, int] = {}
        for a in validos:
            if not mesma_consulta:
                vistos_no_alerta = {}
            for m in self.buscar_mensagens(a):
                o = self.ocorrencia(wo, a, m)
                if o.valores["wo"] != wo:
                    leitura.avisos.append(f"Item de outra WO ({o.valores['wo']}) descartado.")
                    continue
                if o.chave.startswith("conteudo:"):
                    # Sem chave do BTP: itens iguais DENTRO do mesmo alerta são itens distintos
                    # (numerados pela ordem); o mesmo item vindo de outra linha idêntica da lista
                    # recebe o mesmo número e aí sim é repetição.
                    sem_chave += 1
                    n = vistos_no_alerta.get(o.chave, 0) + 1
                    vistos_no_alerta[o.chave] = n
                    if n > 1:
                        o.chave = f"{o.chave}#{n}"
                if o.chave in itens:
                    leitura.duplicadas += 1
                    continue
                itens[o.chave] = o
        if sem_chave:
            leitura.avisos.append(f"{sem_chave} item(ns) sem chave no BTP: identificados pelo conteúdo "
                                  "(sem quantidade, mensagem ou data de alteração).")
        leitura.itens = itens
        if leitura.duplicadas:
            leitura.avisos.append(f"{leitura.duplicadas} item(ns) repetido(s) no retorno do BTP contado(s) uma vez.")
        return leitura

    @property
    def pode_reler_por_chave(self) -> bool:
        return bool(self.config.url_item and self.config.chaves_item)

    def reler(self, wo: str, base: Leitura, rodada: int) -> Leitura:
        """Auditoria independente: busca CADA item pelo seu endereço próprio (chave), por outro
        caminho que não a consulta da lista. Item que não existe mais fica como ausente (None)."""
        leitura = Leitura(rodada, "Releitura por chave", itens={})
        encontrados = nao_encontrados = recusados = 0
        for chave, o in base.itens.items():
            if not all(o.bruto.get(k) not in (None, "") for k in self.config.chaves_item):
                leitura.avisos.append(f"Item {chave}: chave não disponível para releitura.")
                continue   # fica fora da leitura: "não relido" (diferente de "ausente" = None)
            try:
                registros = self._get(odata.preencher_modelo(self.config.url_item, linha=o.bruto))
            except ErroConsulta as exc:
                if "404" not in str(exc):
                    # Endereço recusado (ex.: 400 por formato da chave): não prova nada sobre o
                    # item; fica "não relido", nunca "ausente".
                    recusados += 1
                    leitura.avisos.append(f"Item {chave}: releitura recusada pelo serviço ({exc}).")
                    continue
                registros = []
            leitura.itens[chave] = self.ocorrencia(wo, o.alerta, registros[0]) if registros else None
            encontrados += bool(registros)
            nao_encontrados += not registros
        if not encontrados:
            # Nenhum item pôde ser lido pelo endereço próprio: é o serviço que não oferece esse
            # caminho (não prova que os itens sumiram). Quem chama troca por nova consulta.
            raise ReleituraIndisponivel("O serviço não permite ler os itens pela chave "
                                        f"({nao_encontrados + recusados} tentativa(s) sem resposta"
                                        + ("; itens sem chave" if not (nao_encontrados or recusados) else "")
                                        + ").")
        return leitura

    def ocorrencia(self, wo: str, alerta: dict, mensagem: dict) -> Ocorrencia:
        campos = self.config.campos

        def campo(nome):
            return odata.obter_campo(mensagem, campos[nome]) if campos.get(nome) else None

        consolidado = campo("consolidado")
        if consolidado in (None, ""):
            consolidado = odata.obter_campo(alerta, self.config.campo_consolidado_lista)
        bruto = {k: v for k, v in mensagem.items()
                 if not k.startswith(("__", "@")) and not isinstance(v, (dict, list))}
        valores = {
            "wo": odata.como_texto(campo("wo")) if campos.get("wo") else wo,
            "material": odata.como_texto(campo("material")),
            "fornecedor": odata.como_texto(campo("fornecedor")),
            "quantidade_faltante": odata.como_quantidade(campo("quantidade_faltante")),
            "mensagem": odata.como_texto(campo("mensagem")),
            "consolidado": odata.como_texto(consolidado),
            "mensagem_alerta": odata.como_texto(odata.obter_campo(alerta, self.config.campo_mensagem_alerta))
                               if self.config.campo_mensagem_alerta else "",
        }
        valores["mensagem_item"] = valores["mensagem"]
        if self.config.mensagem_origem == "alerta" and valores["mensagem_alerta"]:
            valores["mensagem"] = valores["mensagem_alerta"]
        elif not valores["mensagem"] and valores["mensagem_alerta"]:
            valores["mensagem"] = valores["mensagem_alerta"]   # o item não tem texto próprio
        fonte = {c: campos[c] for c in ("wo", "material", "fornecedor", "quantidade_faltante", "mensagem")
                 if campos.get(c)}
        quantidades = campos_de_quantidade(bruto)
        outras = {k: v for k, v in quantidades.items() if k != campos.get("quantidade_faltante")}
        # Evidência de alteração: só campo com nome de alteração E valor que é DATA de fato
        # ("alteradoPor", "motivoAlteracao"... não servem).
        tempo = next((str(v) for k, v in bruto.items()
                      if _MARCA_TEMPO.search(k) and odata.instante(v) is not None), None)
        chaves = self.config.chaves_item or []
        if chaves and all(bruto.get(k) not in (None, "") for k in chaves):
            chave = "|".join(f"{k}={bruto[k]}" for k in chaves)
        else:
            # Sem chave declarada: identifica pelo que NÃO muda quando o BTP atualiza o item —
            # sem quantidades, sem mensagem, sem campos de alteração — mais o alerta de origem.
            # Assim uma mudança de quantidade aparece como DIVERGÊNCIA, não como item novo.
            variaveis = set(quantidades) | {campos.get("mensagem")} | {
                k for k in bruto if _MARCA_TEMPO.search(k)}
            identidade = {k: v for k, v in bruto.items() if k not in variaveis}
            origem = {k: v for k, v in alerta.items() if not k.startswith(("__", "@"))
                      and not isinstance(v, (dict, list)) and k not in variaveis
                      and k not in campos_de_quantidade(alerta)} if alerta is not mensagem else {}
            chave = "conteudo:" + hashlib.sha1(json.dumps([origem, identidade], sort_keys=True, default=str)
                                                .encode()).hexdigest()[:12]
        return Ocorrencia(chave, valores, bruto, alerta, fonte, outras, tempo)

    def converter(self, wo: str, alerta: dict, mensagem: dict) -> LinhaMensagem:
        v = self.ocorrencia(wo, alerta, mensagem).valores
        return LinhaMensagem(consolidado=v["consolidado"], wo=v["wo"], material=v["material"],
                             fornecedor=v["fornecedor"], quantidade_faltante=v["quantidade_faltante"],
                             mensagem=v["mensagem"])


class ReleituraIndisponivel(ErroConsulta):
    """O BTP não atende a leitura de item pela chave (nenhum item encontrado por esse caminho)."""


# Campo de "última alteração" do registro (evidência para explicar divergências).
_MARCA_TEMPO = re.compile(r"(modif|chang|alterad|atualiz|updated|lastchange)", re.I)


def link_navegacao(registro: dict, campo: str) -> str | None:
    """URI da navegação que o próprio OData informa na linha (V2: {"__deferred": {"uri": ...}})."""
    valor = registro.get(campo)
    if isinstance(valor, dict):
        valor = (valor.get("__deferred") or {}).get("uri")
    if not valor:
        valor = registro.get(campo + "@odata.navigationLink")   # V4
    return valor if isinstance(valor, str) and valor else None


def relativizar(uri: str, url_lista: str) -> str:
    """Converte a URI informada pelo servidor em caminho da própria aba.

    Atrás do approuter, o Gateway pode informar o endereço interno do servidor; por isso
    a parte a partir do conjunto de dados é recolocada sobre o caminho do serviço já usado.
    """
    caminho_lista = url_lista.split("?", 1)[0]
    raiz, _, conjunto = caminho_lista.rpartition("/")
    partes = urlsplit(uri)
    caminho = partes.path if (partes.scheme or uri.startswith("/")) else raiz + "/" + uri
    if partes.query:
        caminho += "?" + partes.query
    if conjunto:
        i = caminho.rfind("/" + conjunto + "(")
        if i >= 0:
            return raiz + caminho[i:]
    return caminho


def registros_json(valor) -> list[dict]:
    """Mensagens guardadas como texto JSON num campo da linha (lista, {results:[]} ou {value:[]})."""
    if isinstance(valor, str):
        texto = valor.strip()
        if not texto.startswith(("[", "{")):
            return []
        try:
            valor = json.loads(texto)
        except json.JSONDecodeError:
            return []
    if isinstance(valor, dict):
        valor = valor.get("results", valor.get("value", valor.get("d", [valor])))
        if isinstance(valor, dict):
            valor = valor.get("results", [valor])
    return [v for v in valor if isinstance(v, dict)] if isinstance(valor, list) else []
