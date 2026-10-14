/* Service worker da extensão Agente BTP.
 *
 * Única função: quando a aba do BTP pede, buscar o agente no programa local e
 * executá-lo DENTRO da aba (mundo MAIN), que é onde está a sessão de quem logou.
 *
 * Segurança: o endereço do programa é FIXO aqui (127.0.0.1:8767). Nenhuma página
 * consegue trocá-lo — diferente da extensão experimental do Agente Atlas, em que a
 * página informava o endereço e qualquer servidor local podia se passar pelo motor.
 * Não faz login, não lê cookie, não guarda dado nenhum.
 */
'use strict';

const PROGRAMA = 'http://127.0.0.1:8767';
const SITE_BTP = 'https://claro-prd.launchpad.cfapps.br10.hana.ondemand.com/*';

/* Roda NO CONTEXTO DA PÁGINA do BTP (o mesmo lugar onde o favorito roda). */
function executarNaPagina(codigo) {
  try {
    if (window.__btpAtivo) return 'ja-conectado';
    if (window.__btpSubstituido) return 'outra-aba';     // outra aba do BTP está em uso
    (0, eval)(codigo);
    return 'ok';
  } catch (e) {
    return 'erro: ' + (e && e.message || e);
  }
}

async function injetar(tabId) {
  // A extensão busca o código (ela tem permissão para 127.0.0.1:8767), então a
  // página não precisa de nada para a carga inicial.
  const r = await fetch(PROGRAMA + '/agente_btp.js', { cache: 'no-store' });
  if (!r.ok) throw new Error('programa respondeu HTTP ' + r.status);
  const codigo = await r.text();
  const [res] = await chrome.scripting.executeScript({
    target: { tabId }, world: 'MAIN', func: executarNaPagina, args: [codigo],
  });
  return res && res.result;
}

chrome.runtime.onMessage.addListener((msg, sender, responder) => {
  if (!msg || msg.tipo !== 'conectar' || !sender.tab) {
    responder({ ok: false, motivo: 'mensagem desconhecida' });
    return false;
  }
  injetar(sender.tab.id)
    .then((resultado) => responder({ ok: ['ok', 'ja-conectado', 'outra-aba'].includes(resultado), resultado }))
    .catch((e) => responder({ ok: false, motivo: String(e && e.message || e) }));
  return true;  // resposta assíncrona
});

/* Instalar/atualizar a extensão não alcança abas já abertas: entra nelas agora. */
chrome.runtime.onInstalled.addListener(() => {
  chrome.tabs.query({ url: [SITE_BTP] }, (abas) => {
    void chrome.runtime.lastError;
    for (const aba of abas || []) {
      chrome.scripting.executeScript({ target: { tabId: aba.id }, files: ['no_btp.js'] })
        .catch(() => {});
    }
  });
});
