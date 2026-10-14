"""Ponte com a aba do SAP JÁ ABERTA no navegador do usuário (modelo do Agente Atlas).

O script agente_btp.js é injetado na aba logada (pelo favorito "Agente BTP"). Ele:
  1. pergunta ao servidor local (127.0.0.1) se há consulta a fazer (long-poll);
  2. faz o GET com a sessão da própria aba (same-origin) e devolve a resposta;
  3. enquanto o agente ainda não está preparado, observa as consultas que a tela do
     Monitor faz, para o agente aprender sozinho de onde vêm os dados.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path

from .cliente import Resposta
from .modelos import FalhaComunicacao, SessaoExpirada

ESPERA_LONG_POLL = 15.0      # quanto o pedido do agente fica aguardando uma tarefa
TOLERANCIA_SILENCIO = 25.0   # sem contato há mais que isso = aba desconectada
TIMEOUT_TAREFA = 90.0
LIMITE_CAPTURA = 400


class PonteAba:
    def __init__(self):
        self._cond = threading.Condition()
        self._fila: deque[dict] = deque()
        self._pendentes: dict[str, dict] = {}
        self._entregues: dict[str, dict] = {}   # tarefas já com a aba, aguardando resposta
        self.agente_id: str | None = None
        self._visto_em = 0.0
        self._aba_autenticada = False
        self.pagina = ""
        self._captura: list[dict] | None = None
        # Chamado com as consultas observadas, para o agente aprender sozinho.
        self.observador = None
        # Versão do agente_btp.js servido agora; abas com versão diferente se recarregam.
        self.versao_agente: str | None = None
        self._versoes_avisadas: set[str] = set()
        self.observadas = 0

    # ------------------------------------------------ lado da aba (threads HTTP)
    def registrar_agente(self, agente: str, pagina: str) -> None:
        with self._cond:
            self.agente_id = agente
            self.pagina = pagina
            self._visto_em = time.monotonic()
            self._devolver_entregues()

    def despedir(self, agente: str) -> None:
        """A aba avisou que vai fechar/recarregar: desconecta na hora e salva as tarefas."""
        with self._cond:
            if agente == self.agente_id:
                self.agente_id = None
                self._aba_autenticada = False
                self._devolver_entregues()

    def _devolver_entregues(self) -> None:
        # Tarefas entregues a uma aba que sumiu (fechada, recarregada, favorito clicado de
        # novo) voltam para o início da fila: a próxima aba conectada as executa.
        for tarefa in reversed(list(self._entregues.values())):
            if tarefa["id"] in self._pendentes and tarefa not in self._fila:
                self._fila.appendleft(tarefa)
        self._entregues.clear()
        self._cond.notify_all()

    def proxima_tarefa(self, agente: str, autenticado: bool, pagina: str,
                       capturando_na_aba: bool = False, espera: float = ESPERA_LONG_POLL,
                       versao: str | None = None) -> dict:
        with self._cond:
            if (self.versao_agente and versao not in (self.versao_agente, "ext")
                    and versao not in self._versoes_avisadas):
                # Script de outra versão na aba: pede UMA recarga. Se a mesma versão voltar
                # (a recarga não a mudou), aceita e segue — nunca entra em laço.
                self._versoes_avisadas.add(versao)
                return {"atualizar": True, "substituido": True}
            if agente != self.agente_id:
                return {"substituido": True}
            self._visto_em = time.monotonic()
            self._aba_autenticada = autenticado
            self.pagina = pagina
            limite = time.monotonic() + espera
            # Responde na hora se há tarefa OU se a aba precisa saber que a observação
            # começou/terminou — senão ela perderia as primeiras consultas da tela.
            while (not self._fila and time.monotonic() < limite and agente == self.agente_id
                   and (self._captura is not None) == capturando_na_aba):
                self._cond.wait(timeout=min(1.0, limite - time.monotonic()))
                self._visto_em = time.monotonic()
            resposta = {"capturar": self._captura is not None}
            if self._fila and agente == self.agente_id:
                tarefa = self._fila.popleft()
                self._entregues[tarefa["id"]] = tarefa
                resposta["tarefa"] = tarefa
            return resposta

    def entregar_resposta(self, dados: dict) -> None:
        with self._cond:
            self._visto_em = time.monotonic()
            self._entregues.pop(dados.get("id"), None)
            pendente = self._pendentes.get(dados.get("id"))
            if pendente:
                pendente["resposta"] = dados
                pendente["evento"].set()

    def adicionar_captura(self, itens: list[dict]) -> None:
        with self._cond:
            self._visto_em = time.monotonic()
            if self._captura is None:
                return
            novos = [i for i in itens if isinstance(i, dict)]
            self.observadas += len(novos)
            self._captura.extend(novos)
            del self._captura[:-LIMITE_CAPTURA]   # memória limitada: só as mais recentes
            copia = list(self._captura)
        if self.observador:
            self.observador(copia)  # fora da trava: a análise pode demorar

    # ------------------------------------------------ lado do agente (worker)
    @property
    def conectada(self) -> bool:
        return bool(self.agente_id) and time.monotonic() - self._visto_em < TOLERANCIA_SILENCIO

    def autenticado(self) -> bool:
        return self.conectada and self._aba_autenticada

    def get(self, url: str, accept: str | None = None, timeout: float | None = None) -> Resposta:
        """GET pela aba. `timeout` (s) = tempo máximo desta consulta (a aba aborta ao estourar)."""
        if not url.startswith("/") or url.startswith("//"):
            raise FalhaComunicacao(f"URL recusada (precisa ser do próprio sistema): {url[:80]}")
        tarefa = {"url": url}
        if accept:
            tarefa["accept"] = accept
        if timeout:
            tarefa["timeout"] = int(timeout * 1000)
        r = self._executar(tarefa, (timeout + 10) if timeout else TIMEOUT_TAREFA)
        if r.get("status") == -1:
            if str(r.get("body")).startswith("TEMPO_ESGOTADO"):
                raise FalhaComunicacao(f"Tempo máximo da consulta ({timeout or 60:g} s) excedido.")
            raise FalhaComunicacao(f"Falha de rede na aba: {str(r.get('body'))[:200]}")
        return Resposta(status=int(r.get("status", 0)), content_type=r.get("ct") or "",
                        corpo=r.get("body") or "", redirecionado=bool(r.get("redir")))

    def fontes(self) -> list[dict]:
        """Serviços OData usados pelos apps abertos na aba (lidos da configuração do UI5)."""
        r = self._executar({"tipo": "fontes"})
        try:
            dados = json.loads(r.get("body") or "[]")
        except json.JSONDecodeError:
            return []
        return [d for d in dados if isinstance(d, dict)]

    def _executar(self, tarefa: dict, espera_max: float | None = None) -> dict:
        if not self.conectada:
            raise SessaoExpirada("A aba do BTP não está conectada. Clique no favorito 'Agente BTP' na aba.")
        tarefa = dict(tarefa, id=uuid.uuid4().hex)
        pendente = {"evento": threading.Event(), "resposta": None}
        with self._cond:
            self._pendentes[tarefa["id"]] = pendente
            self._fila.append(tarefa)
            self._cond.notify_all()
        try:
            limite = time.monotonic() + (espera_max or TIMEOUT_TAREFA)
            # Espera em fatias curtas: se a aba sumir, percebe logo (não espera os 90 s).
            while not pendente["evento"].wait(0.5):
                if not self.conectada:
                    raise SessaoExpirada("A aba do BTP foi fechada ou recarregada. "
                                         "Clique no favorito 'Agente BTP' na aba do BTP.")
                if time.monotonic() > limite:
                    raise FalhaComunicacao("A aba do BTP não respondeu a tempo.")
        finally:
            with self._cond:
                self._pendentes.pop(tarefa["id"], None)
                self._entregues.pop(tarefa["id"], None)
                if tarefa in self._fila:
                    self._fila.remove(tarefa)
        return pendente["resposta"]

    def aguardar(self, ms: int) -> None:
        time.sleep(ms / 1000)

    @property
    def capturando(self) -> bool:
        return self._captura is not None

    def iniciar_captura(self) -> None:
        with self._cond:
            if self._captura is not None:
                return
            self._captura = []
            self._cond.notify_all()

    def consultas_observadas(self) -> list[dict]:
        with self._cond:
            return list(self._captura or [])

    def parar_captura(self) -> None:
        with self._cond:
            self._captura = None
            self._cond.notify_all()

    def salvar_captura(self, pasta: Path) -> Path:
        with self._cond:
            if self._captura is None:
                raise RuntimeError("Nenhuma captura em andamento.")
            itens, self._captura = self._captura, None
            self._cond.notify_all()
        pasta.mkdir(parents=True, exist_ok=True)
        destino = pasta / f"captura_{datetime.now():%Y%m%d_%H%M%S}.json"
        conteudo = {"gerado_em": datetime.now().isoformat(timespec="seconds"),
                    "origem": "aba", "fontes_de_dados": [], "requisicoes": itens}
        destino.write_text(json.dumps(conteudo, ensure_ascii=False, indent=2), encoding="utf-8")
        return destino
