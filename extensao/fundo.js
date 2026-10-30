/* Service worker da extensão Agente BTP.
 *
 * Injeta o agente (arquivo agente_btp.js, DENTRO da extensão) na aba do BTP, no mundo
 * MAIN — onde está a sessão de quem logou. Não usa eval e não busca código em lugar
 * nenhum: injetar arquivo da própria extensão não é bloqueado pela política da página.
 * Quem conversa com o programa local é o próprio agente, de dentro da página (como no
 * favorito). O endereço do programa é fixo (127.0.0.1:8767); nenhuma página o altera.
 * Não faz login, não lê cookie, não guarda dado nenhum.
 */
'use strict';

const PROGRAMA = 'http://127.0.0.1:8767';
const SITE_BTP = 'https://claro-prd.launchpad.cfapps.br10.hana.ondemand.com/*';

function situacaoNaPagina() {
  if (window.__btpAtivo) return 'ja-conectado';
  if (window.__btpSubstituido) return 'outra-aba';
  return 'livre';
}

function prepararNaPagina(programa) {
  window.__BTP_SERVIDOR = programa;
  window.__BTP_VERSAO = 'ext';          // a versão é a do arquivo da extensão
  window.__BTP_VIA_EXTENSAO = true;
}

async function injetar(tabId, forcar) {
  const alvo = { tabId };
  if (!forcar) {
    const [s] = await chrome.scripting.executeScript({ target: alvo, world: 'MAIN', func: situacaoNaPagina });
    if (s && s.result !== 'livre') return s.result;
  }
  await chrome.scripting.executeScript({ target: alvo, world: 'MAIN', func: prepararNaPagina, args: [PROGRAMA] });
  await chrome.scripting.executeScript({ target: alvo, world: 'MAIN', files: ['agente_btp.js'] });
  return 'ok';
}

chrome.runtime.onMessage.addListener((msg, sender, responder) => {
  if (!msg || msg.tipo !== 'conectar' || !sender.tab) {
    responder({ ok: false, motivo: 'mensagem desconhecida' });
    return false;
  }
  injetar(sender.tab.id, !!msg.forcar)
    .then((resultado) => responder({ ok: true, resultado }))
    .catch((e) => responder({ ok: false, motivo: String(e && e.message || e) }));
  return true;  // resposta assíncrona
});

/* Instalar/atualizar a extensão não alcança abas já abertas: entra nelas agora. */
chrome.runtime.onInstalled.addListener(() => {
  chrome.tabs.query({ url: [SITE_BTP] }, (abas) => {
    void chrome.runtime.lastError;
    for (const aba of abas || []) {
      chrome.scripting.executeScript({ target: { tabId: aba.id }, files: ['no_btp.js'] }).catch(() => {});
    }
  });
});
