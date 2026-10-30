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

## 9. Rodada 5 — layout fluido, refinamento visual, diagnóstico no Histórico, tema e logo

### Diagnóstico do layout (medido no Chromium antes de mudar)
Causa: `max-width: 1320px` no conteúdo e no cabeçalho + coluna de preparação fixa (330–400 px) + indicadores
em 6 colunas fixas. Uso da largura: 1920 px = 66%; zoom 67% (2150 px CSS) = 59%; zoom 50% (2880 px) = 44%.

### O que mudou
- **Largura total, sem largura máxima:** cabeçalho de 60 px de ponta a ponta; margens fluidas (`clamp`); área de
  trabalho em grade com proporções em `fr` — preparação ~35% | monitoramento ~65% (dominante) | apoio abaixo;
  a partir de 2000 px CSS (monitores largos ou zoom 67%/50%) vira 3 colunas. Medido depois: **96–97% da largura em
  todas as janelas** (1280×720 a 2880 px), sem rolagem horizontal; celular/tablet empilham em ordem operacional.
- **Título da área de trabalho** "Monitor de alertas" + resumo real do agente (conexão, tabela do BTP e se está
  confirmada, conferência, análise atual).
- **Indicadores** maiores (valor fluido 28–40 px), numa linha quando cabe, 3 ou 2 por linha quando não (container query).
- **Estado vazio de verdade:** sem análise, o painel mostra orientação curta; se existe análise anterior, os
  indicadores mostram os **números reais da última análise**, rotulados como tal (nunca inventados).
- **Execuções recentes** (dados do histórico real) com acesso ao relatório e ao detalhe.
- **Diagnóstico agora no Histórico (pedido do usuário):** cada análise mostra o diagnóstico (o que deu errado, próxima
  ação, evidência), o registro de execução e o .json; a análise em andamento já aparece no Histórico e atualiza ao vivo.
  A tela principal fica só com a operação (chips de problemas + "Ver diagnóstico desta análise").
- **Raio-X da fonte** foi para Configurações › Fonte de dados (junto de "Refazer a fonte de dados").
- **Mensagem na planilha: "As duas"** — na conferência da amostra e em Configurações: `item — Alerta: alerta`
  (a mensagem composta também é conferida nas auditorias; teste novo).
- **Tema claro / escuro / automático** em Configurações › Aparência (vale para o navegador, aplicado sem piscar);
  todas as cores viraram tokens; declarar `color-scheme` evita que o "modo escuro forçado" do Chrome inverta a tela.
- **Logo própria** (quadro de captura + sinal de conferência, vermelho #E3261E): cabeçalho, ícone da aba (favicon),
  ícones da extensão (16/32/48/128 px) e `docs/logo-agente-btp.svg`.

### Testes executados
- `pytest testes`: **152 passando** (novos: mensagem "as duas", análise no Histórico desde o início, registro por análise).
- `rodar_tela.py`: **71 verificações** (layout, estados vazio/execução/pausa/interrompida/concluída, ajuda guiada
  inclusive com painel oculto, diagnóstico no Histórico, tema escuro persistente, "As duas", Raio-X em Configurações,
  logo, 390/768 px).
- Layout em 1280×720, 1440×900, 1920×1080, 1800/2150/2880 px (zoom 80/67/50%), 1024, 820 e 390 px: sem overflow.
- `rodar_aba.py`, `rodar_sessao.py`, `rodar_extensao.py` (com os ícones novos): passaram.

## 10. Rodada 6 — identidade sem azul e nova logo

- **Problema relatado:** o azul-marinho (cabeçalho, títulos, barra de progresso, links) brigava com o vermelho da logo.
- **Correção:** a identidade passou a ser neutra (grafite), e o vermelho é a única cor de marca.
  - Tema claro: cabeçalho branco com borda.
  - Tema escuro: cabeçalho grafite quase preto, com tokens próprios do cabeçalho (`--cab-*`).
  - Títulos, barra, links, balões de ajuda e notificações em grafite.
  - Os cinzas perderam o leve tom azulado.
  - Verde, âmbar e vermelho seguem só como cores de estado (o significado dos indicadores não mudou).
- **Logo:** marca-símbolo sem moldura. É um "Λ" maciço com um ponto: lê-se como o "A" de Agente e como sinal de alerta.
  - Funciona em fundo claro e escuro, e de 16 px a 128 px.
  - Aplicada no cabeçalho, no favicon, em `docs/logo-agente-btp.svg` e nos ícones da extensão.
- **Validação:**
  - pytest 152 ok;
  - `rodar_tela` 71/71 (o teste do tema escuro agora confere o novo fundo `rgb(15, 16, 18)`);
  - `rodar_aba`, `rodar_sessao` e `rodar_extensao` ok.

## 11. Rodada 7 — linhas repetidas na planilha e células vazias (análise real de 13.623 WOs)

**Medido na planilha real:** 17.617 linhas para 13.623 WOs.
- 1.884 WOs têm vários materiais faltando. Uma linha por material está correto e não é repetição; foi isso que o "Remover duplicadas" do Excel, aplicado só na coluna WO, contou como 3.994 "duplicadas".
- **Repetição de verdade:** 157 WOs com 218 linhas 100% idênticas.

**Causa raiz:** a linha da WO aparece mais de uma vez em `Pendencias` com o mesmo consolidado.
- Cada linha disparava a mesma consulta em `UltimoRetornoView` e recebia os mesmos registros.
- A identificação do item incluía o ID da linha da lista, por isso cada cópia virava um item "novo".
- O BTP falso já simulava esse caso (`lista_repetida`), e o teste de equivalência aceitava a cópia.

**Correções:**
- **Leitura** (`cliente._sem_consulta_repetida`): linhas da lista que levam à mesma consulta de itens são lidas uma vez. A linha que representa o grupo é escolhida pelo conteúdo, não pela ordem, para a identificação ficar estável entre auditorias. Se a mensagem do alerta vai para a planilha e difere entre as linhas, nada é juntado.
- **Planilha** (`exportacao.itens_unicos`): o mesmo item com dados idênticos em tudo que vai para a planilha vira uma linha, com a Observação "O BTP traz este item N vezes nesta WO...". Isso cobre também alertas diferentes (consolidados diferentes) com o mesmo item. Nada é somado: a quantidade não dobra no resumo de materiais.
  - Itens do mesmo material com dados diferentes continuam em linhas separadas, porque são registros distintos do BTP. Na análise real há 3 casos assim (ex.: 05659|231320584, material 22056366: "Estor.EstqForn..." e "Estoque indisponível").
- **Células vazias:** nenhuma célula da aba Dados fica em branco; sem informação = "-".
- **Histórico:** "Baixar relatório" gera a planilha de novo a partir da base (`/api/execucoes/<id>/relatorio`), sem consultar o BTP, e substitui o arquivo antigo. Sem a análise na base, entrega o arquivo gravado na época.

**Validação:**
- pytest 153 ok, incluindo os testes novos: item igual em alertas diferentes, relatório regerado sem consultar o BTP, e equivalência que exige a remoção das cópias e nada mais;
- `rodar_tela` 71/71; `rodar_sessao`, `rodar_aba` e `rodar_extensao` ok.

## 12. Rodada 8 — Tipo Material, aba Materiais e tabela de valores

**Fonte do tipo e do valor:** tabela do usuário ("TABELA VALORES 07-2026.xlsx": Material, Texto breve material, Unitário). Ela é importada em Configurações → Tabela de valores e gravada em `config/tabela_materiais.json` (`agente_btp/materiais.py`).
- Tipo Material = "Texto breve material", exatamente como está.
- O material é comparado pelo número: `000000000022056365` (BTP) = `22056365` (tabela).
- Se a tabela tem o mesmo material com tipo ou valor diferentes, a importação é recusada e diz quais materiais.
- O unitário é lido sem resíduo de ponto flutuante (0.13 fica 0.13).

**Planilha:**
- **Dados:** nova coluna "Tipo Material" ao lado de Material SAP. Material fora da tabela = "-".
- **Materiais (nova):** um material SAP por linha, juntando todas as WOs. Colunas: Quantidade total | Valor total (R$) = quantidade total × unitário | Valor unitário (R$) | Observação.
  - Ordem: maior valor primeiro, com a linha Total no fim.
  - Item não conferido fica fora da soma e é avisado.
  - Material fora da tabela ou sem preço sai com "-" e aviso.
- **Tabela de valores (oculta):** a tabela importada inteira, com o material em 18 dígitos como no BTP, para PROCV direto com a aba Dados.
- A conferência depois de gravar passou a cobrir as abas Dados, Materiais e Tabela de valores, célula a célula.

**Validação com os arquivos reais:** análise de 13.623 WOs (sem as 218 cópias) + tabela de 1.305 materiais.
- 270 materiais: 263 com preço, 7 fora da tabela (22069613 com 2.144 unidades, 22067265 com 62 e outros 5).
- Total R$ 183.667,48, igual à conferência independente (soma de quantidade × unitário).
- pytest 163 ok (10 novos em `test_materiais.py`); `rodar_tela` 71/71; `rodar_sessao`, `rodar_aba` e `rodar_extensao` ok; importação pela tela testada no navegador com a tabela real.

## 13. Rodada 9 — refinamento de UX/UI: menos explicação fixa, ajuda guiada detalhada, cor da marca

**Explicações fixas removidas:**
- Configurações: os parágrafos sob os 16 parâmetros, as descrições dos grupos, a introdução da página, os textos de Aparência, Mensagem, Raio-X e Tabela de valores, e o cartão que só tinha o título.
- Análise: a linha "Cada dado é conferido…", a instrução do estado vazio e o texto padrão da barra.
- Histórico: a frase "Clique numa análise…".
- Esses textos agora ficam no "?" de cada item; o valor padrão de cada parâmetro aparece no "?".
- Continuam visíveis: motivo do Executar bloqueado, avisos de conexão, retomada e versão, conferência da amostra, erros e confirmações.

**Ajuda:** pedido do usuário: no modelo de balão, como antes, mais detalhada.
- Botão Ajuda ou F1: 18 passos pelas três páginas (Análise, Histórico, Configurações). O tour troca de página sozinho e volta à página de origem ao sair.
- Só lê: nenhuma requisição de escrita durante o tour (testado).
- O "?" de cada item tem o link "Ver na Ajuda guiada", que abre o tour no passo do assunto.
- O "?" dos indicadores e dos parâmetros só aparece ao passar o mouse ou focar; em tela de toque fica sempre visível.

**Visual:**
- Sem "balões" atrás de tudo: Configurações em seções planas (título com marcador vermelho e linha fina); superfícies sem sombra.
- Os 6 indicadores numa superfície única com divisórias, com o ícone num círculo tingido pela cor do estado.
- Cor da marca (pedido do usuário, sem voltar ao azul): faixa vermelha no topo, links vinho e barra de progresso vermelha (verde ao concluir sem ressalvas, âmbar com ressalvas).
- Também na cor da marca: selo Executando e número da análise em tom da marca, botão ativo e linha selecionada do Histórico com acento vermelho. Tema escuro com os tons equivalentes.
- Coluna Detalhe do Histórico limitada a 2 linhas (texto completo no tooltip).

**Microinterações:**
- Entrada de página (0,18 s).
- Realce animado da aba ativa.
- Clique dos botões (escala 0,98).
- ✓ que surge ao escolher a planilha ou a tabela.
- Número do indicador que assenta ao mudar.
- Barra com transição suave.
- Seções recolhíveis com expansão suave (::details-content).
- Notificação e dica entrando com fade.
- Retirado: o ponto pulsando sem parar no selo.
- Tudo desligado com "reduzir movimento" do sistema.

**Validação:**
- pytest 163 ok.
- `rodar_tela` 123/123, incluindo:
  - os 18 passos (página certa, destaque sobre o elemento, balão dentro da tela, inclusive em 390 e 768 px);
  - F1;
  - "Ver na Ajuda guiada";
  - "?" escondido até o hover;
  - nenhum parágrafo explicativo em Configurações;
  - 16 "?" de parâmetros com o padrão.
- `rodar_sessao`, `rodar_aba` e `rodar_extensao` ok.
- Sem rolagem horizontal e com 100% da largura usada em 1280×720, 1920×1080, 2160×1350 (zoom 67%) e 2880×1800 (zoom 50%), nas três páginas.

## 14. Rodada 10 — reestruturação com menu lateral persistente

**Estrutura:** a navegação horizontal saiu. O app é uma grade: menu lateral (236 px) + área de conteúdo, que ocupa todo o resto.
- **Menu:** marca no topo; grupos Operação (Análise, Histórico), Sistema (Configurações) e Ajuda (ajuda guiada, F1). No rodapé, a conexão com o BTP e "Recolher menu" (trilho de 68 px; a escolha é lembrada no navegador).
- **Item ativo:** fundo suave da marca + barra lateral vermelha.
- **Tablet (701–1099 px):** trilho de ícones, com o rótulo no title.
- **Celular (≤700 px):** barra superior com ícone + rótulo.
- **Ligações preservadas:** os ids (`abaAnalise`, `abaHistorico`, `abaConfig`, `bAjuda`, `pilulaConexao`), e o estado da tela é mantido ao trocar de página (texto digitado preservado, testado).
- **Cabeçalho de página compacto:** título + ações à direita. Histórico: "Limpar histórico". Configurações: Restaurar/Salvar, num cabeçalho fixo ao rolar.

**Configurações:** composição deliberada.
- Aparência e Tabela de valores à esquerda, Fonte de dados à direita; abaixo, Auditoria | Tempo e Execução | Desempenho em 2 colunas (1 coluna abaixo de 1000 px).
- Cada seção é um título fora + um painel com linhas (rótulo à esquerda, controle à direita); sem cartões dentro de cartões e sem os marcadores vermelhos dos títulos.
- Fonte de dados numa unidade: Tabela do BTP (com a etiqueta confirmada / a confirmar), Mensagem na planilha, Raio-X recolhível e "Refazer a fonte de dados" no rodapé do painel.
- Tabela de valores com upload compacto (sem a caixa pontilhada).
- Valores, limites e regras dos parâmetros não mudaram.

**Componentes padronizados (tokens `--ctl-h`, `--ctl-num`, `--ctl-sel`):**
- Altura única de 36 px para campos, seletores e botões pequenos.
- Número com a unidade acoplada (s/min), todos com a mesma largura total.
- Seletor próprio com seta, interruptor no lugar da caixa de seleção (continua sendo um checkbox) e foco visível em todos.
- Conjunto único de ícones de traço (sprite SVG) no menu, nos indicadores e nos botões de enviar/baixar.

**Validação:**
- pytest 163 ok.
- `rodar_tela` 136/136. Novos:
  - menu lateral persistente e grupos;
  - fundo do item ativo;
  - recolher/expandir lembrado ao recarregar;
  - texto preservado ao trocar de página;
  - Aparência ao lado de Fonte;
  - parâmetros em 2 colunas;
  - números com a mesma largura;
  - Salvar visível ao rolar;
  - trilho em 768 px e barra superior em 390 px.
- `rodar_sessao`, `rodar_aba` e `rodar_extensao` ok.
- Sem rolagem horizontal em 1280×720, 1440×900, 1920×1080, 2160×1350 (zoom 67%) e 2880×1800 (zoom 50%).
- **Limitação percebida na revisão:** os testes conferem posição e largura, não a aparência. Um conflito de nome de classe (`.painel`) passou nos testes e só apareceu nos prints; foi corrigido.

## 15. Rodada 11 — planilha só com "Estoque indisponível"; outras mensagens na Observação

**Regra do usuário:** na planilha só entram itens com a mensagem "Estoque indisponível". Os demais itens da WO continuam sendo lidos e auditados, mas vão para a Observação.

**Implementação:**
- **Preferência `mensagem_planilha`** (Configurações › Execução › "Mensagem dos itens na planilha"), padrão "Estoque indisponível"; vazio = todos os itens.
- **Comparação:** com a mensagem do item (janela Mensagens), ignorando maiúsculas, acentos, espaços e pontuação final. É igualdade exata: "Estoque insuficiente" não entra.
- **Modos "alerta" e "as duas":** o filtro continua olhando o texto do item, que agora é guardado em `LinhaMensagem.mensagem_item` (base e auditoria).
- **Análises antigas, sem o campo:** no modo item, usa a própria mensagem; no modo "as duas", o trecho antes de " — Alerta: ". No modo "alerta" o texto do item não foi guardado, e o filtro compara a mensagem gravada.
- **Aba Dados:**
  - Os outros itens aparecem na Observação da 1ª linha da WO: "Outros itens no BTP, fora da planilha (mensagem diferente de "Estoque indisponível"): <material> (qtd N): <mensagem>; …".
  - WO só com outras mensagens: uma linha "-", com "Nenhum item com "Estoque indisponível". …".
  - A nota é informativa (sem amarelo); o amarelo continua só para "conferir no BTP".
- **Materiais e Análise:** só com os itens da planilha. Análise ganha "Itens com outra mensagem (só na Observação)".
- **Verificação depois de gravar:** aplica a mesma regra.
- **Histórico:** o relatório gerado de novo usa a regra (análises antigas recebem o padrão).

**Efeito nos dados reais (análise de 13.623 WOs, sem as cópias):**
- 15.219 itens: 13.873 com "Estoque indisponível" ficam na planilha; 1.346 vão para a Observação. Exemplos: "Os dados de centro do material … estão bloqueados…", "Estoque insuficiente", "Estor.EstqForn…".
- WOs com itens: 11.163 só com Estoque indisponível, 37 mistas e 243 só com outras mensagens (estas viram uma linha "-").

**Validação:**
- pytest 164 ok (teste novo `test_planilha_so_com_estoque_indisponivel_e_outros_na_observacao`; os testes de estrutura, zeros e fórmulas rodam com o filtro vazio, que é o propósito deles).
- `rodar_tela` 136/136; `rodar_sessao`, `rodar_aba` e `rodar_extensao` ok.

## 16. Rodada 12 — regra "Estoque indisponível": Status e mensagens que mudam entre leituras

**Problemas encontrados na regra da rodada 11 (revisão do código):**
1. **Status contaminado por itens fora da planilha.** O Status da WO (planilha, tela e histórico) vinha de *todos* os itens. Uma WO com o item "Estoque indisponível" conferido e um item de outra mensagem com quantidade instável aparecia como "Divergente", com a Observação da linha "-": duas colunas contraditórias.
2. **Item de estoque podia sumir da planilha.** `mensagem_item` vinha só da 1ª leitura. Se a 1ª leitura trazia "Estoque insuficiente" e as seguintes "Estoque indisponível", o item ia para a Observação como "outra mensagem". No modo "mensagem do alerta", a variação da mensagem do item nem era detectada como divergência.

**Correção:**
- `auditoria.consolidar` guarda em `LinhaMensagem.mensagens_lidas` a mensagem do item de **todas** as leituras (gravada na base). Se ela variar, o item fica **Divergente** com a nota "Divergente entre leituras (mensagem do item): …", inclusive no modo alerta.
- `exportacao.entra_na_planilha`: o item entra se **qualquer** leitura trouxe a mensagem do filtro, ou se o BTP não informou o campo de mensagem (incerto: entra sinalizado). Item sem mensagem própria, de forma consistente, continua fora (como antes).
- `loja.visao_da_planilha` + `estado_da_wo(r, filtro)` / `motivo_da_wo(r, filtro)`: o estado gravado (contadores da tela, histórico, retomada) e a coluna Status consideram só os itens da planilha. O motor passa `prefs.mensagem_planilha`; sem filtro, nada muda.
- Aba Análise: "WOs com itens "Estoque indisponível" (na planilha)" e "WOs só com itens de outra mensagem"; na situação por WO, "Sem itens "Estoque indisponível" (só outras mensagens, na Observação)".
- Observação: valores de divergência com ponto dentro da mensagem não são mais cortados (`_valores_da_nota`).

**Testes:** 3 novos (`test_status_da_planilha_ignora_divergencia_em_item_de_outra_mensagem`, `test_item_cuja_mensagem_muda_para_estoque_indisponivel_fica_na_planilha`, `test_mensagem_do_item_decide_pela_leitura_e_pela_certeza`); 167 passando; ponta a ponta: tela 136/136, sessão OK, aba e extensão OK.

**Efeito nos dados reais:** na análise de 13.623 WOs, todas estavam "Validada" e nenhum item teve mensagem diferente entre leituras: a planilha daquela análise não muda. A correção cobre o que acontece quando o BTP muda durante uma análise.

**Limitação:** análises gravadas antes desta versão não têm `mensagens_lidas`; o relatório regerado usa a mensagem gravada (1ª leitura), como antes. Os contadores já gravados dessas análises também não são recalculados.
