"""Orquestrador do Agente BTP.

Conexão: a aba do SAP JÁ ABERTA e logada no navegador do usuário (favorito "Agente BTP").
Preparação (automática, por trás, só na primeira vez):
  1. pelo dicionário de dados do SAP ($metadata) dos apps abertos na aba;
  2. observando em silêncio as consultas que a tela do Monitor faz (prioridade, por ser real).
Uma thread dedicada executa os comandos; o servidor web só lê o estado.
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
from . import historico
from .analisador import AnaliseFalhou, analisar
from .autoconfig import candidatos_pela_aba, leitura_por_chave
from .cliente import ClienteBTP
from .exploracao import Explorador
from . import preferencias
from .exportacao import gerar_planilha, nome_analise, verificar_planilha
from .entrada import wo_valida
from .modelos import STATUS_FALHA, Auditoria, ErroConsulta, FalhaComunicacao, SessaoExpirada, Status
from .ponte import PonteAba
from .processador import processar_lote
from .sondagem import sondar

log = logging.getLogger("agente_btp")

RAIZ = cfg.RAIZ
PASTA_SAIDAS = RAIZ / "saidas"
ESPERA_APRENDIZADO_SEG = 300
LIMITE_CANDIDATOS = 80
LIMIAR_SEM_REGISTROS = 0.9   # acima disso, a ligação com as mensagens é considerada errada


class Agente:
    def __init__(self):
        self._fila: queue.Queue = queue.Queue()
        self._trava = threading.Lock()
        self._trava_config = threading.Lock()
        self._parar = threading.Event()
        self._diag: dict = {}
        self._candidatos: list = []
        self._testados: set[str] = set()
        self._achados: dict = {}
        self._sondadas: set[str] = set()
        self._modelos: list = []
        self._explorados: set[str] = set()
        self.ponte = PonteAba()
        self.ponte.observador = self._ao_observar
        self._estado = {
            "ocupado": False,
            "fase": "ocioso",            # ocioso | preparando | processando | concluido
            "progresso": {"atual": 0, "total": 0},
            "contagem": {},
            "inicio": None,
            "ultimo_arquivo": None,
            "ultimo_numero": None,
            "etapa": "",
            "indicadores": {},
            "aviso": "",
            "mensagens": [],
        }
        if cfg.descartar_se_antiga():
            self._msg("Configuração de versão anterior descartada; será refeita na próxima análise.")
        threading.Thread(target=self._laco, name="agente-btp", daemon=True).start()

    # ------------------------------------------------ API pública (servidor web)
    def estado(self) -> dict:
        with self._trava:
            copia = dict(self._estado)
            copia["mensagens"] = list(self._estado["mensagens"][-200:])
            copia["contagem"] = dict(self._estado["contagem"])
            copia["indicadores"] = dict(self._estado["indicadores"])
        copia["pronto"] = cfg.ARQUIVO_CONFIG.exists()
        copia["observadas"] = self.ponte.observadas
        copia["conexao"] = ("conectado" if self.ponte.autenticado() else
                            "sem_login" if self.ponte.conectada else "desconectado")
        copia["decorrido_seg"] = round(time.monotonic() - copia.pop("inicio"), 1) if copia["inicio"] else 0
        return copia

    def enviar(self, comando: str, *args) -> None:
        self._fila.put((comando, args))

    def aba_conectada(self) -> None:
        agora = time.monotonic()
        if agora - getattr(self, "_ultimo_aviso_conexao", -60) > 30:   # sem repetir no log
            self._msg("Aba do BTP conectada.")
            self._ultimo_aviso_conexao = agora
        self._candidatos = []   # nova aba/página: o que foi lido antes pode não valer mais
        self._modelos = []
        self._testados.clear()
        self._sondadas.clear()
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
        """Ao conectar: já busca as configurações candidatas (o teste é feito com as WOs)."""
        if cfg.ARQUIVO_CONFIG.exists() or not self.ponte.conectada:
            return
        self._buscar_candidatos()

    def _buscar_candidatos(self) -> list:
        anterior = self._diag.get("dicionario", {})
        diag = {"quando": datetime.now().isoformat(timespec="seconds"),
                "testes": anterior.get("testes", []), "exploracao": anterior.get("exploracao", [])}
        modelos: list = []
        try:
            self._candidatos = candidatos_pela_aba(self.ponte, diag, modelos)
            diag["candidatos"] = len(self._candidatos)
        except Exception as exc:  # noqa: BLE001
            self._candidatos = []
            diag["erro"] = str(exc)[:500]
            log.info("Dicionário de dados: %s", exc)
        self._modelos = modelos
        self._diag["dicionario"] = diag
        return self._candidatos

    def _validar(self, dados: dict, amostra: list[str], get, achados: dict) -> int:
        """2 = trouxe as mensagens da WO; 1 = nenhuma WO da amostra está na lista; 0 = não serve.

        Achar a WO na lista NÃO basta: se a lista traz o alerta e as mensagens vêm vazias,
        a ligação com a janela "Mensagens" está errada e o caminho é recusado.
        """
        try:
            cliente = ClienteBTP(cfg.a_partir_de_dict(dados), get)
        except ValueError:
            return 0
        com_alerta, falhas = 0, 0
        for wo in amostra:
            try:
                alertas = cliente.buscar_alertas(wo)
                if not alertas:
                    continue
                com_alerta += 1
                achados.setdefault(dados["url_lista"], (wo, alertas[0], dados))
                mensagens = cliente.buscar_mensagens(alertas[0])
                if mensagens:
                    linha = cliente.converter(wo, alertas[0], mensagens[0])
                    outras_wos = {str(v).strip() for v in mensagens[0].values()
                                  if isinstance(v, str) and wo_valida(v.strip())} - {wo}
                    if linha.wo != wo or outras_wos:   # mensagens de outra WO: ligação errada
                        return 0
                    return 2 if (linha.material or linha.mensagem) else 0
            except ErroConsulta:
                return 0
            except FalhaComunicacao:
                falhas += 1          # sem resposta não há como julgar: nunca conta como "lista vazia"
                continue
            if com_alerta >= 2:                 # 2 WOs com alerta e nenhuma mensagem: caminho errado
                return 0
        return 0 if (com_alerta or falhas) else 1

    @staticmethod
    def _transporte_com_cache(get):
        """Durante a preparação, a mesma consulta (ex.: a lista de uma WO) é feita uma vez só."""
        memoria: dict = {}

        def ler(url, accept=None):
            chave = (url, accept)
            if chave not in memoria:
                resposta = get(url, accept) if accept else get(url)
                if resposta.status != 200:
                    return resposta
                memoria[chave] = resposta
            return memoria[chave]
        return ler

    def _preparar_com_wos(self, wos: list[str]) -> bool:
        """Testa os caminhos possíveis com as WOs do usuário; grava só o que trouxer as mensagens."""
        candidatos = self._candidatos or self._buscar_candidatos()
        amostra = [w for w in wos if wo_valida(w)][:5]
        get = self._transporte_com_cache(self.ponte.get)
        testes = self._diag.setdefault("dicionario", {}).setdefault("testes", [])
        # 1º) Pergunta direto aos dados: qual conjunto devolve os itens da janela "Mensagens"?
        novos = [m for m in self._modelos if m[0] not in self._explorados]
        if novos and amostra and not self._parar.is_set():
            self._explorados.update(m[0] for m in novos)
            explorador = Explorador(get, novos, amostra, lambda n: self._definir(
                aviso=f"Identificando a origem dos itens da janela 'Mensagens' ({n} consultas)…"))
            try:
                achados = explorador.explorar()
            finally:
                self._diag["dicionario"].setdefault("exploracao", []).extend(explorador.relato)
                self._definir(aviso="")
            for dados, relatorio in achados[:15]:
                if self._parar.is_set():
                    break
                nota = self._validar(dados, amostra, get, {})
                testes.append({"lista": dados["url_lista"], "nota": nota, "exploracao": relatorio[0],
                               "mensagens": dados.get("url_mensagens") or "mesmo conjunto"})
                if nota == 2:
                    self._gravar_config(dados, "exploracao", relatorio)
                    return True
        fraca = None
        for dados, relatorio in candidatos[:LIMITE_CANDIDATOS]:
            chave = json.dumps(dados, sort_keys=True)
            if chave in self._testados:
                continue
            if self._parar.is_set() or cfg.ARQUIVO_CONFIG.exists():
                return cfg.ARQUIVO_CONFIG.exists()
            self._testados.add(chave)
            nota = self._validar(dados, amostra, get, self._achados)
            testes.append({"lista": dados["url_lista"], "nota": nota,
                           "mensagens": dados.get("url_mensagens") or dados.get("campo_mensagens_link")
                           or dados.get("campo_mensagens_expandidas")})
            if nota == 2:
                self._gravar_config(dados, "dicionario", relatorio)
                return True
            if nota == 1 and fraca is None:
                fraca = (dados, relatorio)
        # Nenhum caminho do dicionário trouxe as mensagens: procura nos dados da própria linha.
        for base in self._diag.get("dicionario", {}).get("listas", [])[:10]:
            if base["url_lista"] in self._achados or self._parar.is_set():
                continue
            cliente = ClienteBTP(cfg.ApiConfig(base["url_lista"], base["campo_consolidado_lista"], None, ".", {}), get)
            for wo in amostra[:3]:
                try:
                    alertas = cliente.buscar_alertas(wo)
                except (ErroConsulta, FalhaComunicacao):
                    break
                if alertas:
                    self._achados[base["url_lista"]] = (wo, alertas[0], dict(base, campos={}))
                    break
        for url_lista, (wo, alerta, base) in list(self._achados.items()):
            if url_lista in self._sondadas:
                continue
            self._sondadas.add(url_lista)

            def ler(url):
                return ClienteBTP(cfg.ApiConfig(url_lista, "", None, ".", {}), get)._get(url)

            for parcial, relatorio in sondar(alerta, wo, url_lista, ler):
                dados = dict(base, url_mensagens=None, campo_mensagens_expandidas=None,
                              campo_mensagens_link=None, campo_mensagens_json=None)
                dados.update(parcial)
                nota = self._validar(dados, amostra, get, {})
                testes.append({"lista": url_lista, "sondagem": relatorio[0], "nota": nota})
                if nota == 2:
                    self._gravar_config(dados, "sondagem", relatorio)
                    return True
        if fraca and not self._achados:
            # Nenhuma WO da amostra está no Monitor: não há como conferir as mensagens agora.
            # Fica com a melhor sem erro; a conferência pós-lote a descarta se não servir.
            self._gravar_config(fraca[0], "dicionario", fraca[1])
            return True
        return False

    def _ao_observar(self, consultas: list[dict]) -> None:
        """Chamado a cada lote de consultas observadas na aba (thread HTTP)."""
        if not self._trava_config.acquire(blocking=False):
            return
        try:
            if cfg.ARQUIVO_CONFIG.exists():
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
        if not dados.get("url_item"):
            dados = dict(dados, **leitura_por_chave(dados, self._modelos))
        cfg.salvar(dict(dados, origem=origem))
        if not dados.get("url_item"):
            relatorio = list(relatorio) + ["Releitura por chave indisponível: o serviço não declara chave do item."]
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
                      ultimo_arquivo=None, ultimo_numero=None, aviso="", inicio=time.monotonic(),
                      etapa="", indicadores=_indicadores([], len(wos)))
        if not self.ponte.autenticado():
            self._definir(fase="ocioso", aviso="Conecte a aba do BTP: clique no favorito 'Agente BTP' na aba já logada.")
            return
        if not self._garantir_preparo(wos):
            if self._estado["fase"] != "falhou":
                self._definir(fase="ocioso")
            self._registrar_historico(inicio_relogio, descricao, wos, [], None,
                                      "Interrompida" if self._parar.is_set() else "Não preparada")
            return

        prefs = preferencias.carregar()
        api = cfg.carregar()
        self._definir(fase="processando", inicio=time.monotonic(), indicadores=_indicadores([], len(wos)))
        self._msg(f"Processando {len(wos)} WO(s) · {prefs.auditorias_por_wo} auditoria(s) por WO.")
        if prefs.auditorias_independentes and not (api.url_item and api.chaves_item):
            self._msg("Releitura por chave indisponível neste serviço: as auditorias serão novas consultas.")
        cliente = ClienteBTP(api, self.ponte.get, intervalo=prefs.intervalo_consultas_seg,
                             tempos={"lista": prefs.tempo_consulta_wo_seg,
                                     "detalhes": prefs.tempo_consulta_detalhes_seg})
        concluidos: list = []

        def ao_progredir(atual, total, resultado):
            concluidos.append(resultado)
            with self._trava:
                cont = self._estado["contagem"]
                cont[resultado.status.value] = cont.get(resultado.status.value, 0) + 1
                self._estado["progresso"] = {"atual": atual, "total": total}
                self._estado["indicadores"] = _indicadores(concluidos, total)
                self._estado["etapa"] = ""
            self._msg(f"[{atual}/{total}] {resultado.wo} — {resultado.status.value}"
                      + (f" — {resultado.registros} ocorrência(s)" if resultado.registros else "")
                      + f" — auditoria: {resultado.auditoria.value}"
                      + (f" ({resultado.rodadas_concluidas}/{resultado.rodadas_previstas})"
                         if resultado.rodadas_previstas else ""))

        resultados = processar_lote(cliente, wos, ao_progredir, self._parar.is_set, prefs,
                                    lambda texto: self._definir(etapa=texto))
        contagem: dict[str, int] = {}
        for r in resultados:
            contagem[r.status.value] = contagem.get(r.status.value, 0) + 1
        numero = historico.proximo_numero()
        arquivo, erro_planilha = None, ""
        try:
            arquivo = gerar_planilha(resultados, nome_analise(PASTA_SAIDAS, numero, len(wos)), numero=numero,
                                     emitida_em=inicio_relogio, origem=descricao, prefs=prefs,
                                     fonte_campos=api.campos, releitura_por_chave=bool(api.url_item and api.chaves_item))
            problemas = verificar_planilha(arquivo, resultados, prefs.exportar_incompletos)
            if problemas:
                erro_planilha = ("A conferência do Excel gravado encontrou diferenças: "
                                 + "; ".join(problemas[:5]))
                log.error(erro_planilha)
        except Exception as exc:  # noqa: BLE001 — a análise feita não se perde
            log.exception("Falha ao gerar o Excel")
            erro_planilha = f"Erro ao gerar ou gravar o Excel: {exc}"
        validos = [r for r in resultados if r.status not in (Status.WO_INVALIDA, Status.SESSAO_EXPIRADA)]
        sem_registros = sum(r.status is Status.SEM_REGISTROS for r in validos)

        aviso = erro_planilha
        if aviso:
            pass
        elif any(r.status is Status.SESSAO_EXPIRADA for r in resultados):
            aviso = ("A sessão do BTP caiu no meio do processamento. Faça login de novo na aba, "
                     "clique no favorito 'Agente BTP' e execute novamente.")
        elif any("HTTP 404" in r.observacao for r in resultados):
            # O SAP foi atualizado e o endereço mudou: o agente se prepara de novo sozinho.
            cfg.ARQUIVO_CONFIG.unlink(missing_ok=True)
            self.ponte.iniciar_captura()
            self.enviar("preparar")
            aviso = "O BTP foi atualizado. O agente já está se ajustando; execute de novo em instantes."
        elif len(validos) >= 3 and sem_registros >= LIMIAR_SEM_REGISTROS * len(validos):
            # A lista encontrou as WOs, mas quase nenhuma trouxe mensagens: a ligação com a
            # janela "Mensagens" não é a certa. Descarta e aprende na próxima execução.
            cfg.ARQUIVO_CONFIG.unlink(missing_ok=True)
            self._candidatos = []
            self.ponte.iniciar_captura()
            aviso = (f"{sem_registros} de {len(validos)} WOs vieram sem os itens da janela 'Mensagens'. "
                     "A configuração foi refeita: execute novamente; se for solicitado, abra a janela "
                     "'Mensagens' de uma WO na aba do BTP.")
        elif self._origem_config() == "dicionario" and resultados and all(
                r.status in (Status.ERRO, Status.NAO_ENCONTRADA) for r in resultados):
            # A preparação automática pelo dicionário não serviu: descarta e aprende observando.
            cfg.ARQUIVO_CONFIG.unlink(missing_ok=True)
            self.ponte.iniciar_captura()
            aviso = ("Não consegui ler os dados desta vez. Na aba do BTP, abra a janela 'Mensagens' "
                     "de qualquer WO uma vez e execute de novo.")
        nome = arquivo.name if arquivo else None
        self._definir(fase="concluido", contagem=contagem, ultimo_arquivo=nome, aviso=aviso,
                      ultimo_numero=numero, etapa="", indicadores=_indicadores(resultados, len(wos)),
                      progresso={"atual": len(resultados), "total": len(wos)})
        situacao = ("Erro na planilha" if erro_planilha else
                    "Sessão expirada" if any(r.status is Status.SESSAO_EXPIRADA for r in resultados) else
                    "Interrompida" if self._parar.is_set() else "Concluída")
        self._registrar_historico(inicio_relogio, descricao, wos, resultados, nome, situacao, numero)
        self._msg(f"Concluído. Planilha: {nome}" if nome else "Concluído, mas o Excel não foi gerado.")

    def _registrar_historico(self, inicio, descricao, wos, resultados, arquivo, situacao, numero=None) -> None:
        fim = datetime.now()
        contagem: dict[str, int] = {}
        for r in resultados:
            contagem[r.status.value] = contagem.get(r.status.value, 0) + 1
        try:
            historico.registrar({
                "id": inicio.strftime("%Y%m%d%H%M%S%f"),
                "numero": numero,
                "inicio": inicio.isoformat(timespec="seconds"),
                "fim": fim.isoformat(timespec="seconds"),
                "duracao_seg": round((fim - inicio).total_seconds()),
                "origem": descricao or "WOs coladas",
                "total": len(wos),
                "linhas_excel": sum(r.registros for r in resultados),
                "situacao": situacao,
                "contagem": contagem,
                "indicadores": _indicadores(resultados, len(wos)),
                "arquivo": arquivo,
                "wos": [{"wo": r.wo, "status": r.status.value, "registros": r.registros,
                         "auditoria": r.auditoria.value,
                         "auditorias": f"{r.rodadas_concluidas}/{r.rodadas_previstas}" if r.rodadas_previstas else "",
                         "observacao": "; ".join(x for x in (r.observacao if r.observacao != "OK" else "",
                                                             r.observacao_auditoria) if x)}
                        for r in resultados],
            })
        except Exception:  # noqa: BLE001 — histórico nunca derruba a análise
            log.exception("Falha ao gravar o histórico")

    def _garantir_preparo(self, wos: list[str]) -> bool:
        """Na primeira vez, prepara a conexão com o Monitor sem exigir nada técnico do usuário."""
        if cfg.ARQUIVO_CONFIG.exists():
            return True
        self._msg("Preparando a conexão com o Monitor de alertas (só na primeira vez)…")
        self._testados, self._achados, self._sondadas = set(), {}, set()
        self._explorados = set()
        self.ponte.iniciar_captura()
        try:
            if self._preparar_com_wos(wos):
                return True
        except SessaoExpirada:
            self._definir(aviso="A sessão do BTP caiu. Faça login na aba e clique no favorito 'Agente BTP'.")
            return False
        limite = time.monotonic() + ESPERA_APRENDIZADO_SEG
        proxima_tentativa = time.monotonic() + 20
        while not self._parar.is_set():
            restante = int(limite - time.monotonic())
            if cfg.ARQUIVO_CONFIG.exists():
                self._definir(aviso="")
                return True
            if restante <= 0:
                break
            self._definir(aviso=f"Ação necessária (uma única vez): na aba do BTP, filtre uma WO no Monitor de "
                                f"alertas e abra a janela 'Mensagens'. A análise prossegue automaticamente. "
                                f"({restante} s)")
            if time.monotonic() >= proxima_tentativa:   # o usuário pode ter acabado de abrir o Monitor
                proxima_tentativa = time.monotonic() + 20
                self._candidatos = []
                try:
                    if self._preparar_com_wos(wos):
                        self._definir(aviso="")
                        return True
                except SessaoExpirada:
                    break
            time.sleep(1)
        self._definir(aviso="Não foi possível identificar a origem das mensagens. Baixe o diagnóstico "
                            "(abaixo) e envie o arquivo para ajuste.")
        self._definir(fase="falhou")
        return False

    # ------------------------------------------------ auxiliares
    def _definir(self, **valores) -> None:
        with self._trava:
            self._estado.update(valores)

    def _msg(self, texto: str) -> None:
        log.info(texto)
        with self._trava:
            self._estado["mensagens"].append(f"{datetime.now():%H:%M:%S}  {texto}")


def _indicadores(resultados: list, total: int) -> dict:
    """Painel da tela: só conta uma WO como validada DEPOIS de todas as auditorias dela."""
    validos = [r for r in resultados if r.status is not Status.WO_INVALIDA]
    return {
        "processadas": len(resultados), "pendentes": max(total - len(resultados), 0),
        "validadas": sum(r.auditoria is Auditoria.VALIDADO for r in validos),
        "divergentes": sum(r.auditoria is Auditoria.DIVERGENTE for r in validos),
        "incompletas": sum(r.auditoria in (Auditoria.INCOMPLETO, Auditoria.NAO_CONCLUIDO)
                           and r.status not in STATUS_FALHA for r in validos),
        "falhas": sum(r.status in STATUS_FALHA for r in resultados),
        "com_ocorrencias": sum(r.status is Status.PROCESSADA for r in resultados),
        "ocorrencias": sum(r.registros for r in resultados),
    }


def caminho_saida(nome: str) -> Path | None:
    """Resolve um arquivo de saída sem permitir sair da pasta (../)."""
    caminho = (PASTA_SAIDAS / nome).resolve()
    if caminho.parent != PASTA_SAIDAS.resolve() or not caminho.is_file():
        return None
    return caminho
