"""Autodiagnóstico: o que deu errado, onde, por quê e o que fazer.

Cada problema recebe um MOTIVO da taxonomia abaixo, a CATEGORIA de quem o causou e a
EVIDÊNCIA da primeira ocorrência (URL, status, tipo e trecho do corpo, sem cookies/tokens),
mais a ORIGEM no código (módulo, função, linha; traceback quando for exceção do programa).

Categorias:
  FONTE   — o BTP (ou o caminho até ele: sessão, aba, rede) não entregou o dado.
  APP     — o próprio Agente BTP falhou ou um limite configurado nele foi atingido.
  ENTRADA — a WO informada não serve (formato inválido, não existe no Monitor).
"""
from __future__ import annotations

import re
import traceback
from datetime import datetime
from pathlib import Path

# motivo -> (categoria, descrição curta, próxima ação)
TAXONOMIA: dict[str, tuple[str, str, str]] = {
    "sessao_expirada": ("FONTE", "A sessão do BTP expirou (redirecionou ao login ou devolveu a página de login).",
                        "Faça login de novo na aba do BTP. O agente verifica a sessão e retoma sozinho."),
    "aba_desconectada": ("FONTE", "A aba do BTP foi fechada, recarregada ou parou de responder ao agente.",
                         "Abra a aba do BTP já logada e clique no favorito/extensão 'Agente BTP'."),
    "http_401": ("FONTE", "O BTP recusou a consulta por falta de autenticação (HTTP 401).",
                 "Faça login de novo na aba do BTP. O agente retoma sozinho."),
    "http_403": ("FONTE", "O BTP negou acesso (HTTP 403).",
                 "Se ocorreu em todas as WOs: faça login de novo. Se só em algumas: confira a permissão do "
                 "usuário para essas WOs."),
    "http_404": ("FONTE", "O endereço de dados do BTP não existe mais (HTTP 404) — o app pode ter sido atualizado.",
                 "Use 'Refazer fonte' na engrenagem e execute de novo."),
    "http_429": ("FONTE", "O BTP pediu para reduzir o ritmo das consultas (HTTP 429).",
                 "Nada a fazer: o agente reduz o ritmo sozinho. Se persistir, diminua o paralelismo na engrenagem."),
    "http_5xx": ("FONTE", "O servidor do BTP falhou (HTTP 5xx).",
                 "Aguarde alguns minutos e use 'Retomar' (só as WOs que falharam são refeitas)."),
    "falha_rede": ("FONTE", "Falha de rede entre a aba e o BTP.",
                   "Confira a conexão e use 'Retomar'."),
    "aba_sem_resposta": ("FONTE", "A aba do BTP não respondeu a tempo (aba congelada ou em segundo plano).",
                         "Deixe a aba do BTP aberta (sem minimizar) e use 'Retomar'."),
    "timeout_wo": ("FONTE", "Uma consulta de WO excedeu o tempo máximo configurado.",
                   "Use 'Retomar'. Se persistir, aumente o tempo de consulta na engrenagem."),
    "timeout_lote": ("FONTE", "Uma consulta em lote (várias WOs de uma vez) excedeu o tempo máximo.",
                     "Nada a fazer: o agente refaz essas WOs individualmente e reduz o tamanho do lote."),
    "wo_inexistente": ("ENTRADA", "A WO não tem alerta no Monitor de Alertas (confirmado em todas as leituras).",
                       "Confira se o número da WO está correto. Não é erro do agente."),
    "wo_invalida": ("ENTRADA", "O número da WO não está no formato 00000|000000000.",
                    "Corrija o número na lista de entrada."),
    "campo_ausente": ("FONTE", "O BTP não informou um dos campos obrigatórios do item.",
                      "Confira o item na janela Mensagens do BTP."),
    "quantidade_ambigua": ("FONTE", "O registro do BTP tem mais de um campo de quantidade (ex.: quantidade e "
                           "quantidadeDoc) e nenhum é claramente o faltante: o agente não escolhe. Em muitas WOs, "
                           "indica que a tabela usada NÃO é a da janela Mensagens.",
                           "Confira a tabela mostrada na conferência da amostra; se não for UltimoRetornoView, use "
                           "'Refazer a fonte de dados' na engrenagem e execute de novo."),
    "auditoria_incompleta": ("FONTE", "A conferência da WO não pôde ser concluída (veja a observação na planilha).",
                             "Confira a observação da WO na planilha; use 'Retomar' se foi falha passageira."),
    "parse_falhou": ("FONTE", "A resposta do BTP não é um JSON/OData reconhecível.",
                     "Se repetir, baixe este diagnóstico e envie para ajuste."),
    "erro_servico": ("FONTE", "O serviço do BTP respondeu com uma mensagem de erro.",
                     "Veja a evidência abaixo; se repetir, envie este diagnóstico."),
    "consulta_rejeitada": ("APP", "O BTP rejeitou a consulta montada pelo agente (HTTP 4xx).",
                           "Envie este diagnóstico para ajuste (a evidência mostra a URL recusada)."),
    "divergencia_persistente": ("FONTE", "Os valores mudaram entre leituras e o BTP não registrou a alteração.",
                                "Confira manualmente no BTP os itens marcados na planilha."),
    "lote_desativado": ("APP", "A consulta em lote deu resultado diferente da consulta individual; o agente "
                        "passou a consultar WO a WO.", "Nada a fazer: o resultado segue exato, só mais lento."),
    "limite_execucao": ("APP", "O tempo máximo da análise (engrenagem) foi atingido.",
                        "Use 'Retomar' para continuar de onde parou."),
    "interrompida_usuario": ("APP", "A análise foi interrompida pelo usuário.",
                             "Use 'Retomar' para continuar de onde parou."),
    "excecao_app": ("APP", "Erro inesperado no programa.",
                    "Baixe este diagnóstico e envie para correção (contém o traceback)."),
}


def motivo_base(motivo: str) -> str:
    return motivo.split(":", 1)[0]


def categoria(motivo: str) -> str:
    return TAXONOMIA.get(motivo_base(motivo), ("APP",))[0]


def descricao(motivo: str) -> str:
    base = motivo_base(motivo)
    texto = TAXONOMIA.get(base, ("", motivo, ""))[1]
    if ":" in motivo:
        texto += f" Campo: {motivo.split(':', 1)[1]}."
    return texto


def proxima_acao(motivo: str) -> str:
    return TAXONOMIA.get(motivo_base(motivo), ("", "", "Envie este diagnóstico."))[2]


# ------------------------------------------------------------------ evidência sem segredos
_SEGREDOS = [
    (re.compile(r"eyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]{8,}"), "<token removido>"),          # JWT
    (re.compile(r"(?i)(bearer\s+)[\w.~+/=-]+"), r"\1<removido>"),
    (re.compile(r"(?i)((?:access|refresh|id)_token|SAMLResponse|SAMLRequest|RelayState|"
                r"x-csrf-token|session(?:id)?|JSESSIONID|password|senha)([\"']?\s*[:=]\s*[\"']?)[^\"'&\s<>]+"),
     r"\1\2<removido>"),
    (re.compile(r"(?i)(name=[\"'](?:SAMLResponse|SAMLRequest|RelayState)[\"'][^>]*value=[\"'])[^\"']+"),
     r"\1<removido>"),
]
LIMITE_CORPO = 800


def sanitizar(texto: str, limite: int = LIMITE_CORPO) -> str:
    texto = str(texto or "")[: limite * 4]
    for padrao, troca in _SEGREDOS:
        texto = padrao.sub(troca, texto)
    return texto[:limite]


def evidencia(url: str = "", status=None, content_type: str = "", corpo: str = "", wo: str = "", **extra) -> dict:
    ev = {"quando": datetime.now().isoformat(timespec="seconds"), "url": sanitizar(url, 1500),
          "status": status, "content_type": content_type, "corpo": sanitizar(corpo)}
    if wo:
        ev["wo"] = wo
    ev.update({k: v for k, v in extra.items() if v not in (None, "")})
    return ev


# ------------------------------------------------------------------ origem no código
_PACOTE = str(Path(__file__).resolve().parent)


def origem(exc: BaseException | None = None) -> dict:
    """Onde o problema foi detectado: frame mais profundo do pacote + cadeia de chamadas."""
    if exc is not None and exc.__traceback__ is not None:
        quadros = traceback.extract_stack(exc.__traceback__.tb_frame.f_back) + traceback.extract_tb(exc.__traceback__)
    else:
        quadros = traceback.extract_stack()[:-1]
    nossos = [q for q in quadros if q.filename.startswith(_PACOTE)]
    if not nossos:
        return {}
    q = nossos[-1]
    return {"modulo": "agente_btp." + Path(q.filename).stem, "funcao": q.name, "linha": q.lineno,
            "cadeia": [f"{Path(x.filename).stem}.{x.name}:{x.lineno}" for x in nossos[-8:]]}


def rastreio(exc: BaseException) -> str:
    return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-6000:]


def classificar_excecao(exc: BaseException) -> str:
    motivo = getattr(exc, "motivo", None)
    return motivo or "excecao_app"
