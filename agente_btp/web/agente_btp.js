/*
 * Agente BTP — AGENTE DA ABA
 *
 * Roda dentro da aba do SAP (Fiori Launchpad) JÁ AUTENTICADA pelo usuário.
 * Carregado pelo favorito "Agente BTP". Ele:
 *   1. pergunta ao Agente BTP local (127.0.0.1) se há consulta a fazer;
 *   2. faz o GET com a sessão da própria aba (same-origin) e devolve a resposta;
 *   3. enquanto o agente não está preparado, observa as consultas da tela (XHR/fetch).
 *
 * SOMENTE LEITURA: só executa GET, só para endereços do próprio sistema.
 * Não lê, copia nem envia cookies. Não faz login. Se a sessão cair, o agente
 * local percebe pela resposta e para.
 */
(() => {
  'use strict';
  const principal = window;
  const SERVIDOR = principal.__BTP_SERVIDOR || 'http://127.0.0.1:8767';
  if (principal.__agenteBTP && principal.__agenteBTP.parar) principal.__agenteBTP.parar();

  const ID = Math.random().toString(36).slice(2) + Date.now().toString(36);
  const VERSAO = principal.__BTP_VERSAO || '';
  const LOOPBACK = { targetAddressSpace: 'loopback' }; // Chrome: acesso à rede local
  const IGNORAR = /user-api|userinfo|currentuser|\/resources\/|theming/i;
  const TIPOS = /json|xml|multipart\/mixed/i;
  const SENSIVEL = /^(cookie|authorization|x-csrf-token|set-cookie)\s*:.*$/gim;
  const LIMITE = 300000;
  const estado = { ativo: true, capturando: false, buffer: [] };

  // fetch original, guardado na 1ª injeção (antes de qualquer gancho).
  principal.__btpFetchOriginal = principal.__btpFetchOriginal || principal.fetch;
  const fetchOriginal = (...a) => principal.__btpFetchOriginal.apply(principal, a);

  // ---------------------------------------------------------------- aviso na tela
  let selo = document.getElementById('agente-btp-selo');
  if (!selo) {
    selo = document.createElement('div');
    selo.id = 'agente-btp-selo';
    selo.style.cssText = 'position:fixed;right:12px;bottom:12px;z-index:2147483647;padding:6px 12px;' +
      'border-radius:6px;font:13px Segoe UI,Arial,sans-serif;color:#fff;background:#2e7d32;' +
      'box-shadow:0 2px 8px rgba(0,0,0,.4);pointer-events:none';
    document.body.appendChild(selo);
  }
  const aviso = (texto, cor) => { selo.textContent = 'Agente BTP: ' + texto; selo.style.background = cor || '#2e7d32'; };

  // ---------------------------------------------------------------- servidor local
  async function servidor(caminho, corpo) {
    const init = corpo === undefined
      ? Object.assign({ cache: 'no-store' }, LOOPBACK)
      : Object.assign({ method: 'POST', headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(corpo) }, LOOPBACK);
    const r = await fetchOriginal(SERVIDOR + caminho, init);
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return r.json();
  }

  function autenticado() {
    try { return !!(principal.sap && principal.sap.ushell && principal.sap.ushell.Container); }
    catch (e) { return false; }
  }

  // ---------------------------------------------------------------- descoberta
  const limpar = (t) => String(t || '').replace(SENSIVEL, (l) => l.split(':')[0] + ': <removido>');
  principal.__btpRegistrar = (metodo, url, status, tipo, corpoReq, corpoResp) => {
    if (!estado.capturando) return;
    try {
      const u = new URL(url, location.href);
      if (u.host !== location.host || IGNORAR.test(u.href) || !TIPOS.test(tipo || '')) return;
      estado.buffer.push({
        quando: new Date().toISOString(), metodo: metodo, url: u.href, status: status,
        content_type: tipo || '', corpo_requisicao: limpar(typeof corpoReq === 'string' ? corpoReq : ''),
        corpo_resposta: limpar(String(corpoResp || '').slice(0, LIMITE)),
      });
    } catch (e) { /* a captura nunca atrapalha a tela */ }
  };

  // Ganchos instalados UMA vez por janela; chamam o registrador atual (reinjeção segura).
  function instalarGanchos(w) {
    try {
      if (!w || w.__btpGanchos) return;
      const reg = (...a) => principal.__btpRegistrar && principal.__btpRegistrar(...a);
      const X = w.XMLHttpRequest.prototype;
      const abrir = X.open, enviar = X.send;
      X.open = function (m, u) { this.__btp = { m: String(m).toUpperCase(), u: String(u) }; return abrir.apply(this, arguments); };
      X.send = function (b) {
        const info = this.__btp;
        if (info) {
          info.b = b;
          this.addEventListener('loadend', () => {
            let resp = '';
            try { resp = (this.responseType === '' || this.responseType === 'text') ? this.responseText : JSON.stringify(this.response); } catch (e) {}
            reg(info.m, info.u, this.status, this.getResponseHeader('content-type'), info.b, resp);
          });
        }
        return enviar.apply(this, arguments);
      };
      const f = w.fetch;
      w.fetch = function (entrada, init) {
        const p = f.apply(this, arguments);
        if (!principal.__btpCapturando) return p;
        try {
          const metodo = ((init && init.method) || (entrada && entrada.method) || 'GET').toUpperCase();
          const url = typeof entrada === 'string' ? entrada : (entrada && entrada.url) || String(entrada);
          const corpo = init && typeof init.body === 'string' ? init.body : '';
          p.then((r) => r.clone().text().then((t) => reg(metodo, url, r.status, r.headers.get('content-type'), corpo, t)))
           .catch(() => {});
        } catch (e) {}
        return p;
      };
      w.__btpGanchos = true;
    } catch (e) { /* frame de outra origem: ignorado */ }
  }
  instalarGanchos(principal);
  const ganchosFrames = setInterval(() => {
    for (let i = 0; i < principal.frames.length; i++) instalarGanchos(principal.frames[i]);
  }, 2000);

  const descarga = setInterval(async () => {
    if (!estado.buffer.length) return;
    const lote = estado.buffer.splice(0);
    try { await servidor('/api/agente/captura', { agente: ID, itens: lote }); }
    catch (e) { estado.buffer.unshift(...lote); }
  }, 1000);

  // ---------------------------------------------------------------- fontes de dados
  // Quais serviços OData os apps abertos na aba usam (somente leitura da configuração).
  function fontesDeDados() {
    const out = [];
    const janelas = [principal];
    for (let i = 0; i < principal.frames.length; i++) janelas.push(principal.frames[i]);
    janelas.forEach((w) => {
      try {
        if (!w.sap || !w.sap.ui || !w.sap.ui.core || !w.sap.ui.core.Component) return;
        w.sap.ui.core.Component.registry.forEach((c) => {
          try {
            const app = c.getManifestEntry('sap.app') || {};
            const modelos = (c.getManifestEntry('sap.ui5') || {}).models || {};
            Object.keys(modelos).forEach((nome) => {
              const m = c.getModel(nome || undefined);
              if (m && m.sServiceUrl) {
                const original = String(m.sServiceUrl);
                // Endereço relativo (ex.: "odata/v4/x/") é resolvido a partir da pasta do app.
                let url = original;
                try {
                  const pasta = w.sap.ui.require.toUrl(c.getMetadata().getComponentName().replace(/\./g, '/')) + '/';
                  url = new URL(original, new URL(pasta, w.location.href)).pathname;
                } catch (e) {
                  try { url = new URL(original, w.location.href).pathname; } catch (e2) {}
                }
                out.push({ app: app.id || '', titulo: String(app.title || ''), url: url, url_original: original,
                           tipo: m.getMetadata ? m.getMetadata().getName() : '' });
              }
            });
          } catch (e) {}
        });
      } catch (e) { /* frame de outra origem */ }
    });
    // Registro de rede do próprio navegador: consultas feitas ANTES de o agente ser conectado.
    janelas.forEach((w) => {
      try {
        w.performance.getEntriesByType('resource').forEach((e) => {
          if (/xmlhttprequest|fetch/.test(e.initiatorType) && /odata|\$batch|\$metadata/i.test(e.name)) {
            const u = new URL(e.name, location.href);
            if (u.host === location.host) out.push({ recurso: u.pathname + u.search });
          }
        });
      } catch (e) {}
    });
    return out;
  }

  // ---------------------------------------------------------------- consultas (só GET)
  async function executar(t) {
    if (t.tipo === 'fontes') {
      return { id: t.id, status: 200, ct: 'application/json', body: JSON.stringify(fontesDeDados()), redir: false };
    }
    if (typeof t.url !== 'string' || !/^\/(?!\/)/.test(t.url)) {
      return { id: t.id, status: -1, ct: '', body: 'URL recusada', redir: false };
    }
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 60000);
    try {
      const r = await fetchOriginal(t.url, {
        method: 'GET', credentials: 'same-origin', redirect: 'manual', signal: ctrl.signal,
        headers: { 'Accept': t.accept || 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
      });
      if (r.type === 'opaqueredirect') return { id: t.id, status: 0, ct: '', body: '', redir: true };
      return { id: t.id, status: r.status, ct: r.headers.get('content-type') || '', body: await r.text(), redir: false };
    } catch (e) {
      return { id: t.id, status: -1, ct: '', body: String(e), redir: false };
    } finally { clearTimeout(timer); }
  }

  function parar() {
    estado.ativo = false;
    principal.__btpAtivo = false;
    principal.__btpCapturando = false;
    clearInterval(ganchosFrames);
    clearInterval(descarga);
  }
  principal.__agenteBTP = { parar: parar, id: ID };
  principal.__btpAtivo = true;
  principal.__btpSubstituido = false;

  // Fechou/recarregou a aba: avisa o programa na hora (as tarefas pendentes são salvas).
  const despedida = () => {
    if (!estado.ativo) return;
    try {
      fetchOriginal(SERVIDOR + '/api/agente/tchau', Object.assign({
        method: 'POST', keepalive: true, headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ agente: ID }) }, LOOPBACK));
    } catch (e) {}
  };
  principal.addEventListener('pagehide', despedida);

  async function laco() {
    let falhas = 0;
    try { await servidor('/api/agente/ola', { agente: ID, pagina: location.pathname + location.hash }); }
    catch (e) {
      aviso('não encontrei o programa local. Abra o Agente BTP e clique no favorito de novo.', '#b0191e');
      parar();
      return;
    }
    aviso('conectado');
    while (estado.ativo) {
      try {
        const r = await servidor('/api/agente/tarefa?agente=' + ID + '&autenticado=' + (autenticado() ? 1 : 0) +
                                 '&capturando=' + (estado.capturando ? 1 : 0) + '&versao=' + VERSAO +
                                 '&pagina=' + encodeURIComponent(location.pathname + location.hash));
        falhas = 0;
        if (r.atualizar) {                       // o programa local foi atualizado: recarrega este script
          parar();
          const novo = await fetchOriginal(SERVIDOR + '/agente_btp.js', Object.assign({ cache: 'no-store' }, LOOPBACK));
          (0, eval)(await novo.text());
          return;
        }
        if (r.substituido) {
          aviso('desconectado (outra aba do BTP está conectada).', '#666');
          parar();
          principal.__btpSubstituido = true;   // a extensão não reconecta esta aba sozinha
          return;
        }
        estado.capturando = principal.__btpCapturando = !!r.capturar;
        aviso('conectado');
        if (r.tarefa) await servidor('/api/agente/resposta', await executar(r.tarefa));
      } catch (e) {
        falhas++;
        if (falhas >= 3) aviso('sem contato com o programa local. Ele está aberto?', '#b0191e');
        await new Promise((ok) => setTimeout(ok, Math.min(5000, 1000 * falhas)));
      }
    }
  }
  aviso('conectando…', '#555');
  laco();
})();
