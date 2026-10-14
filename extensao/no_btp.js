/* Script da extensão na aba do BTP.
 * Pede ao service worker para carregar o Agente BTP nesta aba. O próprio agente mostra
 * o status no canto da aba ("conectado" / "aguardando o programa"). Não lê a página,
 * não lê cookie, não consulta nada.
 */
'use strict';
(() => {
  if (window.__agenteBtpExtensao) return;      // já rodando nesta aba
  window.__agenteBtpExtensao = true;

  function conectar(forcar) {
    chrome.runtime.sendMessage({ tipo: 'conectar', forcar: !!forcar }, (r) => {
      if (chrome.runtime.lastError) {
        avisar('Agente BTP: a extensão não respondeu (' + chrome.runtime.lastError.message +
               '). Recarregue a extensão em chrome://extensions.');
        return;
      }
      if (r && !r.ok) avisar('Agente BTP: não consegui carregar o agente nesta aba (' + (r.motivo || '?') + ').');
      else remover();
    });
  }

  // O agente pede para ser recarregado quando o programa foi atualizado.
  window.addEventListener('message', (ev) => {
    if (ev.source === window && ev.data && ev.data.agenteBtp === 'reinjetar') conectar(true);
  });

  function avisar(texto) {
    remover();
    const cx = document.createElement('div');
    cx.id = 'agente-btp-extensao-aviso';
    cx.style.cssText = 'position:fixed;right:12px;bottom:12px;z-index:2147483647;padding:6px 12px;' +
      'border-radius:6px;font:13px Segoe UI,Arial,sans-serif;color:#fff;background:#b0191e;max-width:420px;' +
      'box-shadow:0 2px 8px rgba(0,0,0,.35);pointer-events:none';
    cx.textContent = texto;     // sem innerHTML
    (document.body || document.documentElement).appendChild(cx);
  }
  function remover() {
    const a = document.getElementById('agente-btp-extensao-aviso');
    if (a) a.remove();
  }

  conectar(false);
})();
