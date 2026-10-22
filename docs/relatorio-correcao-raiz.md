# Correção de raiz — preservação, retomada, sessão, desempenho e diagnóstico

Linhas citadas no diagnóstico = versão anterior (commit `72c9acd`).

## 1. Diagnóstico de raiz

### D1 — "perda" do trabalho validado: a sua leitura estava parcialmente errada
**O que os dados mostram.** Na Análise nº 4, `contagem = {"Sessão expirada": 13623}` com
`processadas 13623`. Se alguma WO tivesse sido validada **nessa** análise, a contagem teria outra
chave ("Com itens faltantes" etc.). Logo, **a Análise nº 4 não validou nenhuma WO**: a sessão já
estava morta quando ela começou e falhou na WO 1.

**De onde vieram as centenas de "Validado (5/5)" no registro.** De uma análise anterior.
`agente.py:_msg` (linha 663) acrescenta em `_estado["mensagens"]`, que **nunca era limpo entre análises**, enquanto os contadores eram zerados a cada execução.

Portanto não houve perda de trabalho validado *dentro* de uma análise. Houve três defeitos reais:

1. **Culpa por erro posterior.** `processador.py:processar_lote`, linhas 181–184: ao receber `SessaoExpirada`, marcava **todas as WOs restantes** (`wos[indice-1:]`) como "Sessão expirada". Essas WOs nem tinham sido consultadas.
2. **Falso "conectado".** O estado não percebia que a sessão já estava morta no início (ver D3).
3. **Tudo só em memória.** Os resultados só existiam em memória até o fim do lote (`agente.py:_cmd_executar`, linha 525). Fechar o programa no meio perdia tudo.

### D2 — sem retomada (confirmado)
Não existia registro por WO nem rota para continuar: `_cmd_executar` sempre recomeçava do zero.

### D3 — sessão tratada como infinita (confirmado)
- **A aba só provava que o launchpad existia.** `agente_btp.js:autenticado()` (linhas 58–59) retornava `!!sap.ushell.Container`. Isso diz que o launchpad carregou, **não** que a sessão é válida; depois que a sessão expira, o objeto continua lá.
- **A tela repetia esse sinal.** `ponte.py` (linhas 84 e 127) e `agente.py` (linha 90) mostravam "conectado" com base nesse único sinal.
- **Não havia sonda nem keep-alive.**
- **403 era classificado como erro comum.** `cliente.py:classificar` (linha 43) tratava 403 como `ErroConsulta`.

### D4 — lentidão (confirmado; a maior parte era espera fixa, não BTP)
- **Pausa fixa entre auditorias:** 5 × 0,5 s = 2,5 s por WO (`processador.py` linha 95, padrão `preferencias.py` linha 31).
- **Pausa extra em reconsultas:** mais ≥ 1 s na reconsulta de WO vazia e nas leituras extras (linhas 76 e 121).
- **Tudo em série:** um laço por WO (`processar_lote`, linha 171), e a aba executava **uma** consulta por vez (`agente_btp.js` linha 283).
- **Sem consulta em lote.**

A medição reproduz o observado: **3,39 s/WO**, das quais cerca de 2,5–3 s eram `sleep`.

### D5 — sem autodiagnóstico (confirmado)
- **Causas misturadas:** `classificar` (linhas 39–54) não diferenciava as causas.
- **Exceções sem rastro:** viravam texto solto, sem evidência, origem no código ou traceback.
- **Diagnóstico só da preparação:** `/api/diagnostico` só cobria a preparação.

## 2. O que mudou

| Requisito | Implementação |
|---|---|
| R1 preservação | `loja.py`: SQLite (`dados/execucoes.sqlite3`), chave (run_id, wo), uma transação por WO com payload, `hash_auditorias`, `origem_dado`, tentativas, motivo e horários. `concluir` só grava WO em andamento: nada reescreve WO concluída. Sessão caiu ⇒ só as WOs em andamento voltam para `pendente`. |
| R2 retomada | `POST /api/executar {"run_id","modo":"retomar"}`: refaz só pendente, em andamento e falha (menos WO de número inválido) e é idempotente. A retomada usa a fonte e as preferências **gravadas na análise**. A tela mostra o banner "Retomar análise nº N — X de Y já validadas, Z restantes". A planilha pode ser baixada a qualquer momento (`/api/execucoes/<id>/planilha`) e tem a coluna **Status**. |
| R3 sessão | Ver detalhamento abaixo. |
| R4 desempenho | Ver detalhamento abaixo. |
| R5 diagnóstico | Ver detalhamento abaixo. |
| R6 tela | Progresso, taxa (WO/s), ETA e erros por categoria saem da mesma base que o registro; há teste que cruza o último evento de cada WO com o contador e com a planilha. Pausar/Retomar são separados de Parar. O registro é por análise. |

**R3 — sessão**
- **Sonda:** a mesma consulta confirmada no tráfego real (lista `Pendencias?$filter=workOrderID eq '<WO>'`). Roda antes de começar, a cada 60 s ou a cada 100 WOs.
- **Sessão morta** = redirecionamento, 401, 403 ou HTML/página de login (incluindo HTML com content-type JSON).
- **Pausa automática** (`pausado_sessao`); a análise retoma sozinha quando a aba se anuncia de novo (ou a cada 30 s) e a sonda passa.
- **Erro de sessão numa WO com a sonda passando** ⇒ falha só daquela WO (ex.: 403 só nela), sem pausar a análise inteira.
- **Conexão honesta:** `desconectado` | `aba_conectada` (sessão não verificada) | `conectado` (batimento < 25 s **e** sessão confirmada há ≤ 120 s) | `sessao_expirada`.

**R4 — desempenho**
1. **Lote:** `$filter` com `or` de até 20 WOs (Pendencias) e de condições `(consolidID … and workOrderID …)` (UltimoRetornoView), repartido por WO pelas **mesmas** regras da leitura individual. Linha que não casa ⇒ o grupo é refeito WO a WO.
2. **Paralelismo:** 4 por padrão, máximo 8. Na aba, os trabalhadores usam uma conexão cada (até 6).
3. **Política de auditoria:** 2 conferências; havendo divergência, mais 3 leituras **individuais** (5 no total). Divergência nunca é aprovada.
4. **Ritmo adaptativo** (429/5xx/Retry-After) no lugar das pausas fixas.
5. **Cache por análise:** modelos de URL interpretados, disponibilidade da releitura por chave e do lote. **Respostas do BTP nunca são reaproveitadas:** cada auditoria é uma consulta nova.
6. **Equivalência no dado real:** os 3 primeiros grupos de cada análise são relidos WO a WO; depois, 1 WO com itens + 1 vazia por grupo. Qualquer diferença ⇒ lote desativado na análise (registrado no diagnóstico).

**R5 — diagnóstico**
- **Rota:** `GET /api/diagnostico?run_id=` (JSON exportável) e seção "O que deu errado" na tela.
- **Taxonomia** FONTE/APP/ENTRADA: sessao_expirada, http_401/403/404/429/5xx, timeout_wo, timeout_lote, wo_inexistente, wo_invalida, campo_ausente:<campo>, parse_falhou, divergencia_persistente, excecao_app e outros.
- **Evidência da 1ª ocorrência:** URL, status, content-type, corpo sanitizado (tokens/SAML removidos), WO e horário, mais a origem no código (módulo/função/linha) e o traceback.
- **Linha do tempo** e **próxima ação**.

## 3. Testes (141 passando) e validação
- `testes/test_execucao.py` (28 testes):
  - **R1:** 50 validadas e queda na WO 51 ⇒ as 50 continuam validadas (mesmo hash, 1 tentativa); a análise retoma sozinha. A queda também foi testada com lote + paralelismo nos 4 modos (302, 401, 403, HTML de login).
  - **R2:** retomada refaz só as 3 que falharam; 2ª retomada = só a sonda, nada muda; testes da API e da planilha parcial.
  - **R3:** detecção de 401, redirecionamento, HTML e 403; 403 isolado não pausa; sonda inicial; conexão honesta; 429.
  - **R4 — equivalência campo a campo em 500 WOs contra a versão anterior** (extraída do git): status, auditoria, cada item (WO, material, fornecedor, quantidade, mensagem, consolidado, chave) e as 6 colunas da aba Dados idênticos; consultas ao BTP mais de 5× menores.
  - **R4 — outros casos:** lote paralelo × individual serial com releitura por chave; servidor que ignora o filtro em lote ⇒ lote desativado e resultado exato; divergência escalona para 5 leituras.
  - **R5:** exceção vira motivo com traceback; sanitização.
  - **R6:** registro × contadores × planilha.
- **Navegador (Chromium):**
  - `rodar_aba.py`: 6 cenários. Passou em 9 execuções seguidas; uma única falha do cenário F na 1ª execução não se repetiu nem foi explicada.
  - `rodar_extensao.py` e `rodar_combinacoes.py`: 1 registro, sem laço de reconexão.
  - **Novo `rodar_sessao.py`:** 500 WOs; 302 para o SSO no meio; pausou com 40 validadas, 460 pendentes e 0 falhas; depois do login, retomou sozinho e concluiu 500/500, sem gravar WO em duplicidade.

## 4. Medição antes/depois (`testes/medir_desempenho.py`, BTP simulado: 60 ms por consulta + 0,5 ms/registro)
| Cenário | s/WO | consultas/WO | projeção 13.623 WOs |
|---|---|---|---|
| Antes (72c9acd): 5 auditorias, pausas fixas, 1 por vez | 3,389 | 12,70 | **769 min** (o limite de 240 min interrompia) |
| Depois sem lote (se o lote for desativado) | 0,102 | 6,41 | 23 min |
| Depois, padrão (lote 20, 4 em paralelo) | 0,015 | 0,65 | 3 min |

A latência foi calibrada pelos 3,0–3,5 s/WO observados. **A projeção supõe que o BTP atende 4 consultas
simultâneas com a mesma latência e aceita `or` no `$filter`; só a execução real confirma.** O próprio
agente registra no fim da análise: "Desempenho: N consultas em T s (X WO/s)".

## 5. Limitações honestas
- **Keep-alive:** o padrão Fiori `sessionKeepAlive` foi visto no tráfego, mas a URL não foi capturada; não inventei. O keep-alive é a própria sonda (consulta real pelo approuter, que renova a sessão), só durante a análise, inclusive pausada pelo usuário (a cada 4 min). Com o agente ocioso, nada mantém a sessão viva.
- **Login:** o agente não faz login nem contorna o MFA: na queda, ele **pausa e espera**.
- **Lote no BTP real:** depende de o BTP aceitar `or`. Se recusar ou divergir, o agente volta sozinho para WO a WO (cerca de 23 min projetados).
- **Preferências antigas:** quem tinha 5 auditorias / 0,5 s gravados (padrão antigo) é migrado para 2 (+3 em divergência) / 0 s, com aviso no registro. Valores escolhidos explicitamente são mantidos.

## 6. Rodada 2 — teste de escala com 13.623 WOs (o tamanho real)
Os testes anteriores iam até 1.000 WOs. Rodei uma análise completa de 13.623 WOs no BTP falso
(sem latência, para medir só o custo do agente) e encontrei três problemas que só aparecem nesse
tamanho — todos atingiriam a análise real **depois** das horas de extração:

| Problema | Causa | Antes | Depois |
|---|---|---|---|
| Geração do Excel travava | `ws.max_row` chamado a cada linha (percorre a planilha inteira): custo quadrático | 3.000 WOs > 60 s e crescendo; 13.623 ≈ horas | 13.623 WOs: ~35 s |
| Conferência do Excel pesada | arquivo aberto inteiro na memória | 38,6 s e ~840 MB | 15,3 s e 22 MB (modo somente leitura, ainda detecta fórmula) |
| Pico de memória ~1,5 GB | planilha montada inteira na memória + resultados completos | ~1 GB só no Excel | escrita contínua (write-only) + resultados "leves" |
| historico.json inchado | lista de todas as WOs de cada análise no resumo (até 300 análises) | ~2 MB por análise de 13 mil WOs, lido a cada abertura | resumo leve; lista de WOs num arquivo por análise |

**Equivalência:** a planilha da escrita contínua foi comparada célula a célula com a da versão anterior
(valor, tipo, formato, fonte, preenchimento, alinhamento, borda, larguras, painel congelado, filtro, aba
oculta) — **0 diferenças**. Teste de regressão novo garante que a geração não volta a ser quadrática.
