"""Navegador dedicado (Playwright) — sessão autenticada, transporte GET e Modo Descoberta.

Princípios:
- O login e o MFA são sempre feitos pelo usuário, na janela aberta aqui.
- Nenhuma senha, cookie ou token é lido, copiado ou gravado pelo agente: as consultas
  são feitas DE DENTRO da página (fetch same-origin), com a sessão que o próprio
  navegador já mantém.
- Todos os métodos devem ser chamados pela mesma thread (exigência do Playwright sync).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from .cliente import Resposta
from .modelos import FalhaComunicacao

log = logging.getLogger("agente_btp")

URL_LAUNCHPAD = "https://claro-prd.launchpad.cfapps.br10.hana.ondemand.com/site#Shell-home"
HOST_LAUNCHPAD = urlparse(URL_LAUNCHPAD).hostname

_JS_GET = """
async (url) => {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 60000);
  try {
    const r = await fetch(url, {
      method: 'GET', credentials: 'same-origin', redirect: 'manual', signal: ctrl.signal,
      headers: { 'Accept': 'application/json', 'X-Requested-With': 'XMLHttpRequest' }
    });
    if (r.type === 'opaqueredirect') return { status: 0, ct: '', body: '', redir: true };
    return { status: r.status, ct: r.headers.get('content-type') || '', body: await r.text(), redir: false };
  } catch (e) {
    return { status: -1, ct: '', body: String(e), redir: false };
  } finally { clearTimeout(timer); }
}
"""

_JS_AUTENTICADO = "() => !!(window.sap && sap.ushell && sap.ushell.Container)"

_JS_FONTES_DADOS = """
() => {
  const out = [];
  try {
    sap.ui.core.Component.registry.forEach(c => {
      const app = c.getManifestEntry('sap.app') || {};
      Object.entries(app.dataSources || {}).forEach(([nome, ds]) => out.push({
        app: app.id, fonte: nome, uri: ds.uri, tipo: ds.type,
        versao: (ds.settings || {}).odataVersion || null
      }));
    });
  } catch (e) {}
  return out;
}
"""

# Respostas que interessam à descoberta: dados (JSON/XML/batch). Endpoints de
# identificação do usuário são descartados para não gravar dados pessoais.
_TIPOS_DADOS = ("json", "xml", "multipart/mixed")
_IGNORAR_URL = re.compile(r"user-api|userinfo|currentuser|/resources/|theming", re.I)
_LINHA_SENSIVEL = re.compile(r"^(cookie|authorization|x-csrf-token|set-cookie)\s*:.*$", re.I | re.M)
_LIMITE_CORPO = 300_000


class Navegador:
    def __init__(self, pasta_perfil: Path):
        self.pasta_perfil = Path(pasta_perfil)
        self._pw = None
        self.contexto = None
        self._captura: list[dict] | None = None

    # ---------- ciclo de vida ----------
    @property
    def aberto(self) -> bool:
        if not self.contexto:
            return False
        try:
            _ = self.contexto.pages
            return True
        except Exception:  # noqa: BLE001
            return False

    def abrir(self) -> None:
        if self.aberto:
            self._pagina().bring_to_front()
            return
        from playwright.sync_api import sync_playwright

        self.pasta_perfil.mkdir(parents=True, exist_ok=True)
        self._pw = self._pw or sync_playwright().start()
        ultimo_erro = None
        # Prefere o Edge/Chrome já instalados (dispensa download do Chromium).
        for canal in ("msedge", "chrome", None):
            try:
                self.contexto = self._pw.chromium.launch_persistent_context(
                    str(self.pasta_perfil), channel=canal, headless=False, no_viewport=True)
                log.info("Navegador aberto (%s).", canal or "chromium")
                break
            except Exception as exc:  # noqa: BLE001
                ultimo_erro = exc
        else:
            raise RuntimeError(f"Não foi possível abrir o navegador: {ultimo_erro}")
        self.contexto.on("response", self._ao_receber_resposta)
        pagina = self.contexto.pages[0] if self.contexto.pages else self.contexto.new_page()
        pagina.goto(URL_LAUNCHPAD, wait_until="domcontentloaded")

    def fechar(self) -> None:
        try:
            if self.contexto:
                self.contexto.close()
        finally:
            self.contexto = None
            if self._pw:
                self._pw.stop()
                self._pw = None

    # ---------- sessão ----------
    def _pagina(self):
        """Uma aba no domínio do launchpad (o fetch precisa ser same-origin)."""
        if not self.aberto:
            raise FalhaComunicacao("Navegador fechado. Clique em 'Conectar ao sistema'.")
        for pagina in self.contexto.pages:
            if urlparse(pagina.url).hostname == HOST_LAUNCHPAD:
                return pagina
        pagina = self.contexto.pages[0] if self.contexto.pages else self.contexto.new_page()
        pagina.goto(URL_LAUNCHPAD, wait_until="domcontentloaded")
        return pagina

    def autenticado(self) -> bool:
        if not self.aberto:
            return False
        for pagina in self.contexto.pages:
            if urlparse(pagina.url).hostname != HOST_LAUNCHPAD:
                continue
            try:
                if pagina.evaluate(_JS_AUTENTICADO):
                    return True
            except Exception:  # noqa: BLE001 — página navegando
                continue
        return False

    # ---------- transporte (somente GET) ----------
    def get(self, url: str) -> Resposta:
        try:
            r = self._pagina().evaluate(_JS_GET, url)
        except FalhaComunicacao:
            raise
        except Exception as exc:  # noqa: BLE001
            raise FalhaComunicacao(f"Falha ao consultar pelo navegador: {exc}") from exc
        if r["status"] == -1:
            raise FalhaComunicacao(f"Falha de rede: {r['body'][:200]}")
        return Resposta(status=r["status"], content_type=r["ct"], corpo=r["body"],
                        redirecionado=r["redir"])

    # ---------- Modo Descoberta ----------
    @property
    def capturando(self) -> bool:
        return self._captura is not None

    def iniciar_captura(self) -> None:
        self._captura = []

    def salvar_captura(self, pasta: Path) -> Path:
        if self._captura is None:
            raise RuntimeError("Nenhuma captura em andamento.")
        fontes = []
        for pagina in self.contexto.pages if self.aberto else []:
            try:
                fontes.extend(pagina.evaluate(_JS_FONTES_DADOS))
            except Exception:  # noqa: BLE001
                pass
        pasta.mkdir(parents=True, exist_ok=True)
        destino = pasta / f"captura_{datetime.now():%Y%m%d_%H%M%S}.json"
        conteudo = {"gerado_em": datetime.now().isoformat(timespec="seconds"),
                    "fontes_de_dados": fontes, "requisicoes": self._captura}
        destino.write_text(json.dumps(conteudo, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("Captura salva: %s (%d requisições)", destino.name, len(self._captura))
        self._captura = None
        return destino

    def _ao_receber_resposta(self, resposta) -> None:
        if self._captura is None:
            return
        try:
            req = resposta.request
            if req.resource_type not in ("xhr", "fetch"):
                return
            if urlparse(resposta.url).hostname != HOST_LAUNCHPAD or _IGNORAR_URL.search(resposta.url):
                return
            tipo = resposta.headers.get("content-type", "")
            if not any(t in tipo for t in _TIPOS_DADOS):
                return
            try:
                corpo = resposta.text()
            except Exception:  # noqa: BLE001
                corpo = "<corpo indisponível>"
            self._captura.append({
                "quando": datetime.now().isoformat(timespec="seconds"),
                "metodo": req.method,
                "url": resposta.url,
                "status": resposta.status,
                "content_type": tipo,
                "corpo_requisicao": _LINHA_SENSIVEL.sub(r"\1: <removido>", req.post_data or ""),
                "corpo_resposta": _LINHA_SENSIVEL.sub(r"\1: <removido>", corpo[:_LIMITE_CORPO]),
            })
        except Exception:  # noqa: BLE001 — a captura nunca pode atrapalhar o usuário
            log.debug("Resposta ignorada na captura", exc_info=True)
