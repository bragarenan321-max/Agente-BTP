/* Script da extensão na aba do BTP.
 * Pede ao service worker para conectar o Agente BTP. Se o programa ainda não
 * estiver aberto, tenta de novo a cada 10 s — abrir o programa depois basta,
 * sem recarregar a página. Não lê a página, não lê cookie, não consulta nada.
 */
'use strict';
(() => {
  if (window.__agenteBtpExtensao) return;      // já rodando nesta aba
  window.__agenteBtpExtensao = true;
  let avisado = false;

  function tentar() {
    chrome.runtime.sendMessage({ tipo: 'conectar' }, (r) => {
      if (chrome.runtime.lastError) { setTimeout(tentar, 10000); return; }
      if (r && r.ok) { remover(); avisado = false; return; }
      if (!avisado) { avisar('Agente BTP: abra o programa (Abrir Agente BTP.bat). A conexão é automática.'); avisado = true; }
      setTimeout(tentar, 10000);
    });
  }

  function avisar(texto) {
    remover();
    const cx = document.createElement('div');
    cx.id = 'agente-btp-extensao-aviso';
    cx.style.cssText = 'position:fixed;right:12px;bottom:12px;z-index:2147483647;padding:6px 12px;' +
      'border-radius:6px;font:13px Segoe UI,Arial,sans-serif;color:#fff;background:#6b6b6b;' +
      'box-shadow:0 2px 8px rgba(0,0,0,.35);pointer-events:none';
    cx.textContent = texto;     // sem innerHTML
    (document.body || document.documentElement).appendChild(cx);
  }
  function remover() {
    const a = document.getElementById('agente-btp-extensao-aviso');
    if (a) a.remove();
  }

  tentar();
  // Se a aba ficar sem agente (programa reiniciado, etc.), reconecta sozinha.
  setInterval(() => {
    chrome.runtime.sendMessage({ tipo: 'conectar' }, () => { void chrome.runtime.lastError; });
  }, 60000);
})();
