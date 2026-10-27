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

**Medição completa, 13.623 WOs, mesmo processo (BTP falso sem latência):**

| Etapa | Antes desta rodada | Depois |
|---|---|---|
| Motor (custo do próprio agente) | 48,8 s | 55,2 s |
| Leitura da base para o Excel | 5,1 s | 3,0 s |
| Geração do Excel | travava (quadrático); 34,7 s após a 1ª correção | 35,3 s |
| Conferência do Excel | 38,6 s | 12,3 s |
| Pico de RAM do processo | 1.487 MB | **387 MB** |
| Tela (`/api/estado`) / diagnóstico | 49 ms / 47 ms | 55 ms / 45 ms |

Base: 24 MB; Excel: 10,9 MB (≈37 mil linhas na aba Dados). Nenhuma diferença na conferência.

## 7. Rodada 3 — primeira execução real (27/10, 13.623 WOs): análise do diagnóstico
Evidência: `diagnostico_analise_1.json` (2.900 WOs feitas, todas "incompletas", 2,88 WO/s, 9,1 consultas/WO).

| Achado | Causa (com evidência) | Correção |
|---|---|---|
| **Todas incompletas** | As URLs mostram `RetornoDaBaixa`, não `Pendencias → UltimoRetornoView (status E)` (caminho confirmado no DevTools). A configuração foi aprendida **observando a tela** antes de o dicionário ser lido (`_ao_observar`). Essa tabela tem dois campos de quantidade (`quantidade`, `quantidadeDoc`); sem a fonte confirmada o agente se recusa a escolher ⇒ "Ambígua" ⇒ incompleta. | A observação só decide depois do dicionário lido e **nunca** quando o caminho confirmado existe; fonte não confirmada é trocada por ele antes da amostra; a amostra mostra "Tabela do BTP usada"; `VERSAO_CONFIG = 6` descarta a fonte gravada; análise feita com fonte descartada não é retomada (409, banner some). |
| **Lento (9,1 consultas/WO)** | `RetornoDaBaixa` é histórico (`contador=12` na chave): muitos registros por WO, e a 2ª conferência relia item a item pelo endereço. | Fonte certa + **releitura por chave em lote** (filtro pelas chaves dos itens, unido por `or`), com volta automática ao item a item. Simulação: 0,85 consulta/WO (antes 6,84), resultado idêntico. |
| **Diagnóstico calado** sobre as 2.900 incompletas | `motivo_da_wo` não mapeava "Ambígua". | Novos motivos `quantidade_ambigua` (com a ação "Refazer a fonte") e `auditoria_incompleta` — nenhuma WO não validada fica sem explicação. |
| **Pausa às 10:19 por "aba desconectada"** | 26,2 s de silêncio > 25 s de tolerância, porque os 4 trabalhadores estavam todos esperando consultas lentas. A aba estava viva. | Consulta em execução dentro do prazo mantém a aba conectada. |
| Pausa às 10:22 | A aba recarregou ("Failed to fetch" + despedida) e depois veio o Parar: comportamento correto. | — |
| Sondas sem rótulo no JSON | chave `quando` sobrescrita | renomeada para `momento`. |

## 8. Rodada 4 — retomada em qualquer interrupção + redesign da interface

### Retomada
Já cobria: Parar, queda de sessão, aba fechada, limite de tempo. Faltavam dois casos, agora cobertos:
- **Análise feita com fonte descartada** (a nº 1, com RetornoDaBaixa) tinha sumido do banner. Agora aparece como
  **"Refazer as N WOs com a fonte certa"**: nova análise com a mesma lista, sem reimportar; a antiga vira "substituída".
- **Programa fechado / PC desligado no meio** (WOs em andamento): teste novo abre um programa novo sobre a mesma base,
  o banner oferece a retomada e as WOs já gravadas não são refeitas.

### Redesign (arquivo `agente_btp/web/index.html`; rota nova só de leitura `POST /api/entrada/planilha`)
- **Tokens de design** em `:root` (marca, marinho, neutros, semânticas, tipografia, espaçamento, raio, sombra).
- **Cabeçalho compacto** azul-marinho: marca, abas com estado ativo, conexão honesta, **Ajuda**, Configurações (ícone).
- **Nível 1 (operação):** grade com "Nova análise" (campo, contador com linhas fora do formato, importação com nome do
  arquivo e **WOs reconhecidas lidas na hora**, botão com o motivo de bloqueio escrito) e painel de **Execução sempre
  visível** (selo de estado derivado da lógica real, barra, WO/s e ETA só com taxa medida, WO atual, Pausar/Retomar/Parar
  com confirmação no Parar, indicadores com ícone + texto + cor).
- **Nível 2 (sob demanda):** tutorial guiado de 6 passos e "?" contextuais só em WO, conferência e indicadores, com
  textos conferidos com o código (`loja.estado_da_wo`, `auditoria`, `entrada`).
- **Nível 3 (técnico):** "Diagnóstico da execução" recolhível: O que deu errado (rótulos legíveis, problemas superados
  marcados "resolvido"), Registro de execução, Raio-X da fonte, diagnóstico .json.
- **Histórico:** coluna "Resultado" (validadas/divergências/incompletas/falhas), situação, arquivos; detalhe limitado a
  2.000 linhas na tela (13 mil WOs não travam o navegador; o relatório tem tudo).
- **Tutorial (componente independente, passos num único array `PASSOS_TUTORIAL`):** destaque sem mexer na geometria
  (camada com recorte), balão com posicionamento inteligente e acompanhamento contínuo (rolagem/redimensionamento),
  rolagem automática até o alvo, abre a seção recolhida e restaura, leva à aba de análise e volta à aba anterior,
  teclado (← → Esc, Tab preso no balão), anúncio para leitor de tela, foco devolvido ao botão Ajuda, nenhuma chamada à API.

### Lógica (achado durante o redesign, corrigido à parte)
Após retomar e concluir, "O que deu errado" ainda sugeria "Use Retomar" por causa da parada antiga. Problemas de
sessão/interrupção superados agora ficam "resolvidos" e não ditam a próxima ação (teste novo).

### Testes efetivamente executados
- 150 testes de unidade/integração (`pytest testes`).
- `testes/e2e/rodar_tela.py` (Chromium, novo): **60 verificações** — entrada, planilha real, execução, pausa, retomada,
  parada com confirmação, banner de retomada, conclusão, indicadores = base, downloads .xlsx/.json, diagnóstico sob
  demanda, histórico, configurações, tutorial completo (6 passos, teclado, foco, redimensionar, rolar, seção oculta,
  abrir/fechar repetido, **0 requisições de escrita durante a ajuda**), dica contextual, 390/768 px sem rolagem horizontal.
- `rodar_aba.py` (6 cenários), `rodar_sessao.py` (queda e retomada, 500 WOs), `rodar_extensao.py`: passaram.
- Não testável aqui: o BTP real e a extensão no Chrome corporativo do usuário.
