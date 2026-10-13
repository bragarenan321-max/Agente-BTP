"""Orquestrador: uma thread dedicada executa todos os comandos (exigência do Playwright).

Dois canais de conexão com o SAP, com a mesma interface:
  - "aba" (padrão): a aba JÁ ABERTA e logada no navegador do usuário, via favorito;
  - "navegador": um navegador dedicado aberto pelo agente (alternativa).
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
from datetime import datetime
from pathlib import Path

from . import config as cfg
from .analisador import AnaliseFalhou, analisar
from .cliente import ClienteBTP
from .exportacao import gerar_planilha, nome_padrao
from .navegador import Navegador
from .ponte import PonteAba
from .processador import processar_lote, processar_wo

log = logging.getLogger("agente_btp")

RAIZ = cfg.RAIZ
PASTA_SAIDAS = RAIZ / "saidas"
PASTA_DESCOBERTA = RAIZ / "descoberta"
PASTA_PERFIL = RAIZ / ".perfil_navegador"
ESPERA_LOGIN_SEG = 600
LIMITE_DESCOBERTA_SEG = 1800


class Agente:
    def __init__(self):
        self._fila: queue.Queue = queue.Queue()
        self._trava = threading.Lock()
        self._parar = threading.Event()
        self._salvar_descoberta = threading.Event()
        self.navegador = Navegador(PASTA_PERFIL)
        self.ponte = PonteAba()
        self._modo = "aba"
        self._estado = {
            "conexao": "desconectado",  # desconectado | aguardando_login | conectado
            "ocupado": False,
            "descoberta_ativa": False,
            "progresso": {"atual": 0, "total": 0},
            "ultimo_arquivo": None,
            "mensagens": [],
        }
        threading.Thread(target=self._laco, name="agente-btp", daemon=True).start()

    # ---------- API pública (chamada pelo servidor web) ----------
    def estado(self) -> dict:
        with self._trava:
            copia = dict(self._estado)
            copia["mensagens"] = list(self._estado["mensagens"][-200:])
        copia["api_configurada"] = cfg.ARQUIVO_CONFIG.exists()
        copia["modo"] = self._modo
        if self._modo == "aba":
            copia["conexao"] = ("conectado" if self.ponte.autenticado() else
                                "aguardando_login" if self.ponte.conectada else "desconectado")
        return copia

    @property
    def conexao(self):
        return self.ponte if self._modo == "aba" else self.navegador

    def enviar(self, comando: str, *args) -> None:
        self._fila.put((comando, args))

    def aba_conectada(self) -> None:
        if self._modo == "aba":
            self._msg("Aba do SAP conectada pelo favorito.")

    def parar(self) -> None:
        self._parar.set()
        self._msg("Interrupção solicitada; a WO atual será concluída.")

    # ---------- execução ----------
    def _laco(self) -> None:
        while True:
            comando, args = self._fila.get()
            self._definir(ocupado=True)
            try:
                getattr(self, f"_cmd_{comando}")(*args)
            except Exception as exc:  # noqa: BLE001
                log.exception("Falha no comando %s", comando)
                self._msg(f"ERRO: {exc}")
            finally:
                self._definir(ocupado=False)

    def _cmd_usar_aba(self) -> None:
        self._modo = "aba"
        self._msg("Modo: aba já aberta. Na aba do SAP logada, clique no favorito 'Agente BTP'.")

    def _cmd_conectar(self) -> None:
        """Alternativa: abre um navegador dedicado (quando o favorito não puder ser usado)."""
        self._modo = "navegador"
        self.navegador.abrir()
        if self.navegador.autenticado():
            self._conectado()
            return
        self._definir(conexao="aguardando_login")
        self._msg("Navegador aberto. Faça o login e aprove o MFA no celular.")
        limite = time.monotonic() + ESPERA_LOGIN_SEG
        while time.monotonic() < limite:
            self.navegador.aguardar(2000)
            if self.navegador.autenticado():
                self._conectado()
                return
        self._definir(conexao="desconectado")
        self._msg("Tempo de espera do login esgotado. Clique em 'Conectar' novamente.")

    def _cmd_descoberta_iniciar(self) -> None:
        if not self._exigir_sessao():
            return
        self._salvar_descoberta.clear()
        self.conexao.iniciar_captura()
        self._definir(descoberta_ativa=True)
        self._msg("Descoberta iniciada. Na aba do SAP, faça o processo completo com UMA WO: "
                  "abrir Monitor de alertas, filtrar a WO, clicar na linha e abrir 'Mensagens'. "
                  "Depois clique em 'Salvar descoberta'.")
        # Mantém o navegador "bombeando" eventos até o usuário pedir para salvar.
        limite = time.monotonic() + LIMITE_DESCOBERTA_SEG
        while not self._salvar_descoberta.is_set():
            if time.monotonic() > limite:
                self._msg("Tempo da descoberta esgotado; salvando o que foi capturado.")
                break
            self.conexao.aguardar(300)
        self.conexao.aguardar(2000)  # respostas que ainda estejam chegando
        destino = self.conexao.salvar_captura(PASTA_DESCOBERTA)
        self._definir(descoberta_ativa=False)
        self._msg(f"Descoberta salva em: {destino}")
        self._configurar_por_descoberta(destino)

    def salvar_descoberta(self) -> None:
        self._salvar_descoberta.set()

    def _configurar_por_descoberta(self, arquivo: Path) -> None:
        captura = json.loads(arquivo.read_text(encoding="utf-8"))
        try:
            dados, relatorio = analisar(captura)
        except AnaliseFalhou as exc:
            self._msg(f"Configuração automática não concluída: {exc}")
            return
        for linha in relatorio:
            self._msg(linha)
        if cfg.ARQUIVO_CONFIG.exists():
            backup = cfg.ARQUIVO_CONFIG.with_suffix(f".{datetime.now():%Y%m%d_%H%M%S}.bak")
            cfg.ARQUIVO_CONFIG.replace(backup)
            self._msg(f"Configuração anterior guardada em {backup.name}")
        cfg.salvar(dados)
        self._msg("API configurada automaticamente. Validando com a mesma WO...")

        wo = next((l.split(": ", 1)[1] for l in relatorio if l.startswith("WO usada")), None)
        try:
            resultado = processar_wo(ClienteBTP(cfg.carregar(), self.conexao.get), wo)
        except Exception as exc:  # noqa: BLE001
            self._msg(f"Validação falhou: {exc}")
            return
        self._msg(f"Validação: WO {wo} -> {resultado.status.value}, {resultado.registros} linha(s).")
        for linha in resultado.linhas[:10]:
            self._msg("   " + " | ".join("" if v is None else str(v) for v in linha.como_lista()))
        self._msg("Confira se as linhas acima batem com a janela 'Mensagens'. Se sim, o agente está pronto.")

    def _cmd_executar(self, wos: list[str]) -> None:
        if not self._exigir_sessao():
            return
        try:
            api = cfg.carregar()
        except (FileNotFoundError, ValueError) as exc:
            self._msg(f"ERRO: {exc}")
            return
        self._parar.clear()
        self._definir(progresso={"atual": 0, "total": len(wos)}, ultimo_arquivo=None)
        self._msg(f"Iniciando processamento de {len(wos)} WO(s).")
        cliente = ClienteBTP(api, self.conexao.get)

        def ao_progredir(atual, total, resultado):
            self._definir(progresso={"atual": atual, "total": total})
            self._msg(f"[{atual}/{total}] WO {resultado.wo} — {resultado.status.value} — "
                      f"{resultado.registros} registro(s)"
                      + (f" — {resultado.observacao}" if resultado.observacao not in ("", "OK") else ""))

        resultados = processar_lote(cliente, wos, ao_progredir, self._parar.is_set)
        expiradas = [r for r in resultados if r.status.name == "SESSAO_EXPIRADA"]
        if expiradas:
            self._msg(f"ATENÇÃO: a sessão do SAP caiu; {len(expiradas)} WO(s) não processada(s). "
                      "Faça login de novo na aba, clique no favorito 'Agente BTP' e execute novamente.")
        arquivo = gerar_planilha(resultados, nome_padrao(PASTA_SAIDAS))
        self._definir(ultimo_arquivo=arquivo.name)
        self._msg(f"Planilha gerada: {arquivo.name}")

    # ---------- auxiliares ----------
    def _exigir_sessao(self) -> bool:
        if self.conexao.autenticado():
            self._definir(conexao="conectado")
            return True
        self._definir(conexao="desconectado")
        if self._modo == "aba":
            self._msg("A aba do SAP não está conectada. Abra o SAP já logado e clique no favorito 'Agente BTP'.")
        else:
            self._msg("Sessão não autenticada. Clique em 'Abrir navegador separado' e faça o login.")
        return False

    def _conectado(self) -> None:
        self._definir(conexao="conectado")
        self._msg("Sessão autenticada. Agente conectado ao sistema.")

    def _definir(self, **valores) -> None:
        with self._trava:
            self._estado.update(valores)

    def _msg(self, texto: str) -> None:
        log.info(texto)
        with self._trava:
            self._estado["mensagens"].append(f"{datetime.now():%H:%M:%S}  {texto}")


def caminho_saida(nome: str) -> Path | None:
    """Resolve um arquivo de saída sem permitir sair da pasta (../)."""
    caminho = (PASTA_SAIDAS / nome).resolve()
    if caminho.parent != PASTA_SAIDAS.resolve() or not caminho.is_file():
        return None
    return caminho
