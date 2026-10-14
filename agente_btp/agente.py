"""Orquestrador do Agente BTP.

Conexão: a aba do SAP JÁ ABERTA e logada no navegador do usuário (favorito "Agente BTP").
Preparação (automática, por trás, só na primeira vez):
  1. pelo dicionário de dados do SAP ($metadata) dos apps abertos na aba;
  2. observando em silêncio as consultas que a tela do Monitor faz (prioridade, por ser real).
Uma thread dedicada executa os comandos; o servidor web só lê o estado.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from datetime import datetime
from pathlib import Path

from . import config as cfg
from . import historico
from .analisador import AnaliseFalhou, analisar
from .autoconfig import MetadataSemCorrespondencia, configurar_pela_aba
from .cliente import ClienteBTP
from .exportacao import gerar_planilha, nome_padrao
from .modelos import Status
from .ponte import PonteAba
from .processador import processar_lote

log = logging.getLogger("agente_btp")

RAIZ = cfg.RAIZ
PASTA_SAIDAS = RAIZ / "saidas"
ESPERA_APRENDIZADO_SEG = 600


class Agente:
    def __init__(self):
        self._fila: queue.Queue = queue.Queue()
        self._trava = threading.Lock()
        self._trava_config = threading.Lock()
        self._parar = threading.Event()
        self._diag: dict = {}
        self.ponte = PonteAba()
        self.ponte.observador = self._ao_observar
        self._estado = {
            "ocupado": False,
            "fase": "ocioso",            # ocioso | preparando | processando | concluido
            "progresso": {"atual": 0, "total": 0},
            "contagem": {},
            "inicio": None,
            "ultimo_arquivo": None,
            "aviso": "",
            "mensagens": [],
        }
        threading.Thread(target=self._laco, name="agente-btp", daemon=True).start()

    # ------------------------------------------------ API pública (servidor web)
    def estado(self) -> dict:
        with self._trava:
            copia = dict(self._estado)
            copia["mensagens"] = list(self._estado["mensagens"][-200:])
            copia["contagem"] = dict(self._estado["contagem"])
        copia["pronto"] = cfg.ARQUIVO_CONFIG.exists()
        copia["observadas"] = self.ponte.observadas
        copia["conexao"] = ("conectado" if self.ponte.autenticado() else
                            "sem_login" if self.ponte.conectada else "desconectado")
        copia["decorrido_seg"] = round(time.monotonic() - copia.pop("inicio"), 1) if copia["inicio"] else 0
        return copia

    def enviar(self, comando: str, *args) -> None:
        self._fila.put((comando, args))

    def aba_conectada(self) -> None:
        self._msg("Aba do SAP conectada.")
        if not cfg.ARQUIVO_CONFIG.exists():
            self.ponte.iniciar_captura()   # aprende em silêncio enquanto o usuário usa o Monitor
            self.enviar("preparar")

    def parar(self) -> None:
        self._parar.set()
        self._msg("Interrupção solicitada; a WO atual será concluída.")

    # ------------------------------------------------ execução
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

    def _cmd_preparar(self) -> None:
        if cfg.ARQUIVO_CONFIG.exists() or not self.ponte.conectada:
            return
        diag = {"quando": datetime.now().isoformat(timespec="seconds")}
        try:
            dados, relatorio = configurar_pela_aba(self.ponte, diag)
        except (MetadataSemCorrespondencia, Exception) as exc:  # noqa: BLE001
            diag["erro"] = str(exc)[:500]
            self._diag["dicionario"] = diag
            log.info("Preparação pelo dicionário de dados não concluída: %s", exc)
            return
        self._diag["dicionario"] = diag
        self._gravar_config(dados, "dicionario", relatorio)

    def _ao_observar(self, consultas: list[dict]) -> None:
        """Chamado a cada lote de consultas observadas na aba (thread HTTP)."""
        if not self._trava_config.acquire(blocking=False):
            return
        try:
            if cfg.ARQUIVO_CONFIG.exists() and self._origem_config() == "observacao":
                self.ponte.parar_captura()
                return
            try:
                dados, relatorio = analisar({"requisicoes": consultas})
            except AnaliseFalhou:
                return
            self._gravar_config(dados, "observacao", relatorio)
            self.ponte.parar_captura()
        finally:
            self._trava_config.release()

    def _gravar_config(self, dados: dict, origem: str, relatorio: list[str]) -> None:
        cfg.salvar(dict(dados, origem=origem))
        for linha in relatorio:
            log.info("Configuração (%s): %s", origem, linha)
        self._msg("Conexão com o Monitor de alertas preparada.")

    @staticmethod
    def _origem_config() -> str:
        try:
            import json
            return json.loads(cfg.ARQUIVO_CONFIG.read_text(encoding="utf-8")).get("origem", "")
        except Exception:  # noqa: BLE001
            return ""

    def diagnostico(self) -> dict:
        """Tudo o que ajuda a entender por que a preparação não concluiu (sem senhas/cookies)."""
        consultas = [dict(c, corpo_resposta=str(c.get("corpo_resposta", ""))[:3000],
                          corpo_requisicao=str(c.get("corpo_requisicao", ""))[:3000])
                     for c in self.ponte.consultas_observadas()[-60:]]
        e = self.estado()
        return {"gerado_em": datetime.now().isoformat(timespec="seconds"),
                "conexao": e["conexao"], "pronto": e["pronto"], "versao_agente": self.ponte.versao_agente,
                "pagina_da_aba": self.ponte.pagina, "consultas_observadas_total": self.ponte.observadas,
                "preparacao": self._diag, "consultas_observadas": consultas,
                "registro": e["mensagens"][-200:]}

    def _cmd_executar(self, wos: list[str], descricao: str = "") -> None:
        inicio_relogio = datetime.now()
        self._parar.clear()
        self._definir(fase="preparando", progresso={"atual": 0, "total": len(wos)}, contagem={},
                      ultimo_arquivo=None, aviso="", inicio=time.monotonic())
        if not self.ponte.autenticado():
            self._definir(fase="ocioso", aviso="Conecte a aba do SAP: clique no favorito 'Agente BTP' na aba já logada.")
            return
        if not self._garantir_preparo():
            self._definir(fase="ocioso")
            self._registrar_historico(inicio_relogio, descricao, wos, [], None,
                                      "Interrompida" if self._parar.is_set() else "Não preparada")
            return

        self._definir(fase="processando", inicio=time.monotonic())
        self._msg(f"Processando {len(wos)} WO(s).")
        cliente = ClienteBTP(cfg.carregar(), self.ponte.get)

        def ao_progredir(atual, total, resultado):
            with self._trava:
                cont = self._estado["contagem"]
                cont[resultado.status.value] = cont.get(resultado.status.value, 0) + 1
                self._estado["progresso"] = {"atual": atual, "total": total}
            self._msg(f"[{atual}/{total}] {resultado.wo} — {resultado.status.value}"
                      + (f" — {resultado.registros} linha(s)" if resultado.registros else ""))

        resultados = processar_lote(cliente, wos, ao_progredir, self._parar.is_set)
        contagem: dict[str, int] = {}
        for r in resultados:
            contagem[r.status.value] = contagem.get(r.status.value, 0) + 1
        arquivo = gerar_planilha(resultados, nome_padrao(PASTA_SAIDAS))

        aviso = ""
        if any(r.status is Status.SESSAO_EXPIRADA for r in resultados):
            aviso = ("A sessão do SAP caiu no meio do processamento. Faça login de novo na aba, "
                     "clique no favorito 'Agente BTP' e execute novamente.")
        elif any("HTTP 404" in r.observacao for r in resultados):
            # O SAP foi atualizado e o endereço mudou: o agente se prepara de novo sozinho.
            cfg.ARQUIVO_CONFIG.unlink(missing_ok=True)
            self.ponte.iniciar_captura()
            self.enviar("preparar")
            aviso = "O SAP foi atualizado. O agente já está se ajustando; execute de novo em instantes."
        elif self._origem_config() == "dicionario" and resultados and all(
                r.status in (Status.ERRO, Status.NAO_ENCONTRADA) for r in resultados):
            # A preparação automática pelo dicionário não serviu: descarta e aprende observando.
            cfg.ARQUIVO_CONFIG.unlink(missing_ok=True)
            self.ponte.iniciar_captura()
            aviso = ("Não consegui ler os dados desta vez. Na aba do SAP, abra a janela 'Mensagens' "
                     "de qualquer WO uma vez e execute de novo.")
        self._definir(fase="concluido", contagem=contagem, ultimo_arquivo=arquivo.name, aviso=aviso,
                      progresso={"atual": len(resultados), "total": len(wos)})
        situacao = ("Sessão expirada" if any(r.status is Status.SESSAO_EXPIRADA for r in resultados) else
                    "Interrompida" if self._parar.is_set() else "Concluída")
        self._registrar_historico(inicio_relogio, descricao, wos, resultados, arquivo.name, situacao)
        self._msg(f"Concluído. Planilha: {arquivo.name}")

    def _registrar_historico(self, inicio, descricao, wos, resultados, arquivo, situacao) -> None:
        fim = datetime.now()
        contagem: dict[str, int] = {}
        for r in resultados:
            contagem[r.status.value] = contagem.get(r.status.value, 0) + 1
        try:
            historico.registrar({
                "id": inicio.strftime("%Y%m%d%H%M%S%f"),
                "inicio": inicio.isoformat(timespec="seconds"),
                "fim": fim.isoformat(timespec="seconds"),
                "duracao_seg": round((fim - inicio).total_seconds()),
                "origem": descricao or "WOs coladas",
                "total": len(wos),
                "linhas_excel": sum(r.registros for r in resultados),
                "situacao": situacao,
                "contagem": contagem,
                "arquivo": arquivo,
                "wos": [{"wo": r.wo, "status": r.status.value, "registros": r.registros,
                         "observacao": r.observacao} for r in resultados],
            })
        except Exception:  # noqa: BLE001 — histórico nunca derruba a análise
            log.exception("Falha ao gravar o histórico")

    def _garantir_preparo(self) -> bool:
        """Na primeira vez, prepara a conexão com o Monitor sem exigir nada técnico do usuário."""
        if cfg.ARQUIVO_CONFIG.exists():
            return True
        self._msg("Preparando a conexão com o Monitor de alertas (só na primeira vez)…")
        self.ponte.iniciar_captura()
        self._cmd_preparar()
        if cfg.ARQUIVO_CONFIG.exists():
            return True
        self._definir(aviso="Só desta vez: na aba do SAP, abra o Monitor de alertas e clique em qualquer "
                            "WO para abrir a janela 'Mensagens'. O agente continua sozinho.")
        limite = time.monotonic() + ESPERA_APRENDIZADO_SEG
        while time.monotonic() < limite and not self._parar.is_set():
            if cfg.ARQUIVO_CONFIG.exists():
                self._definir(aviso="")
                return True
            if int(limite - time.monotonic()) % 15 == 0:
                self._cmd_preparar()   # o usuário pode ter acabado de abrir o Monitor
            time.sleep(1)
        self._definir(aviso="Não foi possível preparar a conexão. Abra a janela 'Mensagens' de uma WO "
                            "na aba do SAP e tente de novo.")
        return False

    # ------------------------------------------------ auxiliares
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
