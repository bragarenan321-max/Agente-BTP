# Parte II — A construção do projeto {#parte2}

Esta parte conta como o Agente BTP chegou à forma atual e descreve sua estrutura: a história reconstruída a partir do histórico Git, a arquitetura (quais peças existem e como se ligam), a organização das pastas e arquivos, as tecnologias usadas e o que é preciso para preparar um computador para executá-lo.

## 5. Como o projeto foi iniciado {#cap5}

O histórico Git do repositório tinha **39 commits**, de 01/10/2026 a 29/10/2026, quando este livro começou a ser escrito (depois vieram os commits do próprio livro e a correção de 30/10). Um *commit* é um "ponto de salvamento" do projeto: um conjunto de mudanças registrado com data, autor e uma mensagem descrevendo o que mudou. Lendo os commits em ordem, é possível reconstruir a evolução do projeto com segurança — e, quando a mensagem do commit não explica a motivação, este capítulo diz isso explicitamente.

@@figura linha_tempo

### 5.1 Antes do código: a especificação (01/10)

Os dois primeiros commits criam o repositório (`27a26fe`, "Initial commit") e um `README.md` com a especificação inicial (`53a5f3d`). O texto dessa especificação continua no fim do `README.md` atual, sob o título "Especificação original", e mostra o que se imaginava no começo:

- uma interface web local, "semelhante ao conceito utilizado no Agente Atlas";
- login e MFA sempre feitos pelo usuário, com regras explícitas de segurança: "não armazenar senha", "não automatizar ou contornar o MFA", "utilizar somente a sessão que já tenha sido autenticada";
- uma arquitetura que permitisse "controlar um navegador dedicado à tarefa";
- vários tipos de análise selecionáveis numa lista ("Análise 01"...);
- a observação de que "as informações específicas que serão coletadas ainda serão definidas durante o levantamento do processo manual".

<span class="confirmado">CONFIRMADO</span> Na data da especificação, o que seria extraído ainda não estava definido.

### 5.2 O mapeamento do processo e a primeira versão (13/10)

Em 13/10 o projeto dá um salto. O commit `0563fc6` acrescenta `docs/mapeamento-processo.md`, que descreve o processo manual a partir de capturas de tela reais, define as colunas da janela "Mensagens" e as regras de somente leitura ("o agente **nunca** aciona Reprocessar, Efetuar Baixa ou qualquer requisição que altere dados"). Logo em seguida, o commit `514543b` ("implementa Agente BTP (Monitor de alertas -> Excel)") cria a primeira versão funcional: 20 arquivos e cerca de 1.350 linhas, incluindo `agente.py`, `cliente.py`, `config.py`, `entrada.py`, `exportacao.py`, `modelos.py` e um módulo chamado `navegador.py`.

Esse `navegador.py` usava a biblioteca **Playwright** para abrir um navegador dedicado, onde o usuário fazia o login. Seu cabeçalho já trazia o princípio que sobreviveria a todas as versões: "as consultas são feitas DE DENTRO da página (fetch same-origin), com a sessão que o próprio navegador já mantém".

### 5.3 A virada: conectar à aba já aberta (13/10–14/10)

No mesmo dia, três commits mudam a forma de conexão:

- `7d18a36` — "configuração automática da API a partir da descoberta": o agente passa a aprender os endereços de dados observando as consultas da tela (nasce `analisador.py`).
- `bea63d9` — "conecta à aba do SAP já aberta via favorito (modelo Agente Atlas)": nascem `ponte.py`, `servidor.py`, `web/agente_btp.js` e a tela `web/index.html`. Em vez de abrir um navegador próprio, o agente passa a usar a **aba em que o usuário já está logado**, com um script carregado por um favorito.
- `40f7973` — "conexão somente pela aba já aberta; remove navegador separado da interface".

Em 14/10, o commit `f532242` remove definitivamente `navegador.py` e a dependência Playwright do `requirements.txt` e introduz a **preparação automática** pelo dicionário de dados do serviço (`autoconfig.py`). No mesmo dia nasce a **extensão do Chrome** (`954999f`), que injeta o script na aba sozinha, sem o usuário precisar clicar no favorito.

<span class="interpretacao">INTERPRETAÇÃO TÉCNICA</span> A troca do navegador dedicado pela aba já aberta elimina a necessidade de o usuário fazer login numa janela à parte e reaproveita a sessão que ele já usa no dia a dia. As mensagens dos commits não registram a motivação com essas palavras.

### 5.4 Fidelidade: auditoria e a fonte certa (14/10–15/10)

Os commits seguintes tratam de **fidelidade** — garantir que o que vai para a planilha é o que a janela Mensagens mostra:

- `dcff192` (14/10) — "auditoria por WO, correção da quantidade faltante e relatório em 6 abas": nascem `auditoria.py` e `campos.py`. O relatório `docs/relatorio-revisao-auditoria.md` explica a correção: quando um registro tinha dois campos de quantidade, o agente escolhia o de nome mais curto (a quantidade total) em vez da faltante.
- `6c7acdf` (15/10) — "9 defeitos da revisão independente da auditoria".
- `df81dca` (15/10) — "fonte errada no BTP real; conferência da amostra; planilha direta": nasce a **conferência da amostra** (o usuário confirma uma amostra antes do lote) e `exploracao.py`.
- `6f25ebc` (15/10) — "lê a janela Mensagens pelo caminho real do BTP (UltimoRetornoView, status E)": fixa no código a "receita" confirmada no tráfego real da tela: lista em `Pendencias`, itens em `UltimoRetornoView` filtrados por consolidado, WO e status `'E'`.

### 5.5 Robustez para volume real (21/10–22/10)

O commit `1201fd0` (21/10) é o maior do histórico (26 arquivos, cerca de 3.400 linhas acrescentadas): "preserva cada WO, retoma análise, sessão verificada, lote paralelo e autodiagnóstico". Nascem `loja.py` (base SQLite), `motor.py` (paralelismo e sessão) e `diagnostico.py`. O relatório `docs/relatorio-correcao-raiz.md`, seção 1, documenta os defeitos que motivaram essa reescrita, com números de linha da versão anterior — por exemplo, a queda de sessão marcava todas as WOs restantes como falha, e tudo ficava apenas em memória até o fim do lote.

O commit `8f69d6f` (22/10) resolve problemas que só apareciam com **13.623 WOs**: a geração do Excel tinha custo quadrático (chamava uma operação que percorre a planilha inteira a cada linha) e o pico de memória chegava a cerca de 1,5 GB. A seção 6 do mesmo relatório registra a medição antes e depois (pico de 1.487 MB para 387 MB).

### 5.6 Primeira execução real e ajustes de uso (27/10–29/10)

Em 27/10, a primeira análise real de 13.623 WOs revelou que todas as WOs ficavam "incompletas". A seção 7 do relatório de correção mostra a causa — a fonte aprendida observando a tela era outra tabela (`RetornoDaBaixa`), com dois campos de quantidade — e a correção (`25ee8e5`): a receita confirmada passa a ter prioridade e configurações antigas são descartadas (`VERSAO_CONFIG = 6` em `agente_btp/config.py`).

Os commits seguintes tratam de uso operacional: retomada em qualquer interrupção (`457e87a`), redesenho da interface com ajuda guiada (`35f0af6`, `4c4973c`, `cc02174`, `080d8de`, `2460ba6`), itens repetidos e células vazias com "-" (`b99c093`), tipo de material e tabela de valores (`b44a9e7`) e, por fim, a regra de listar na planilha apenas os itens "Estoque indisponível" (`1f16b17`, 29/10). Em 30/10, depois dos commits deste livro, essa regra foi revisada (`0a39d6c`): o Status passou a considerar só os itens da planilha, e a mensagem do item passou a ser lida em todas as leituras (Cap. 15.5).

### 5.7 O que o histórico não permite saber

- <span class="naodet">NÃO DETERMINADO</span> Quanto tempo o processo manual levava por WO e quantas pessoas o executavam.
- <span class="naodet">NÃO DETERMINADO</span> Quem consome a planilha depois de gerada e para quê.
- <span class="naodet">NÃO DETERMINADO</span> A motivação formal para escolher Python e Flask; o histórico mostra a escolha desde o primeiro commit de código, sem justificativa escrita.
- Como confirmar: entrevistas com o usuário responsável e, se houver, registros do processo anterior à automação.

## 6. A arquitetura do Agente BTP {#cap6}

### 6.1 O que é arquitetura de software

A **arquitetura** de um programa é a forma como ele é dividido em partes e como essas partes conversam. Assim como a planta de uma casa mostra cômodos, portas e encanamentos antes de mostrar os móveis, a arquitetura mostra os componentes e suas ligações antes do código de cada um. Ela responde perguntas como: quem recebe o pedido do usuário? Quem fala com o sistema externo? Onde os dados são guardados? O que acontece se uma peça falhar?

### 6.2 A arquitetura em uma figura

@@figura arquitetura

**Visão geral.** A [[fig:arquitetura]] mostra todos os módulos que participam de uma análise. A coluna da esquerda contém os elementos que vivem no navegador (a tela e o script da aba) e o serviço externo do BTP; as demais colunas contêm os módulos Python do programa local.

**Leitura passo a passo.** De cima para baixo, o pedido nasce na tela, passa pelo servidor, chega ao orquestrador (`agente.py`), desce para o motor e o processador e termina nas consultas feitas pelo cliente, entregues à ponte e executadas pela aba. À direita ficam os serviços auxiliares: preparação da fonte, diagnóstico, auditoria, base de dados, exportação e histórico. As setas indicam quem chama quem.

### 6.3 Os componentes, um a um

| Componente | Onde está | Por que existe | Recebe | Entrega | Se falhar |
|---|---|---|---|---|---|
| Tela | `agente_btp/web/index.html` | Interface de operação | Ações do usuário; estado a cada 1 s | Pedidos HTTP ao servidor | Nada é executado; o programa continua rodando |
| Servidor | `agente_btp/servidor.py` | Porta de entrada HTTP local | Pedidos da tela e da aba | Respostas JSON; comandos ao orquestrador | Nada funciona: é o único ponto de entrada |
| Orquestrador | `agente_btp/agente.py` | Coordena preparação, análise, planilha e histórico | Comandos (executar, retomar, refazer...) | Estado para a tela; análise ao motor | A análise não começa; comandos ficam na fila |
| Preparação | `autoconfig.py`, `exploracao.py`, `sondagem.py`, `analisador.py`, `raiox.py` | Descobrir de onde vêm os dados | WOs de amostra; respostas do BTP | Configuração candidata | Sem fonte, a análise não começa |
| Motor | `agente_btp/motor.py` | Paralelismo, sessão, pausa e retomada | WOs pendentes da base | Grupos aos trabalhadores; resultados à base | A análise para; WOs em andamento voltam a pendentes ao retomar |
| Processador | `agente_btp/processador.py` | Extração + auditorias por WO | Grupo de WOs | Resultado por WO | Erros viram falha da WO (com motivo) |
| Auditoria | `agente_btp/auditoria.py` | Comparar leituras e decidir o estado | Leituras de uma WO | Linhas finais + estado + registros | Sem ela, nada pode ser validado |
| Cliente | `agente_btp/cliente.py` + `odata.py` | Montar consultas e interpretar respostas | Configuração; WOs | Leituras; exceções classificadas | Nenhuma leitura é possível |
| Ponte | `agente_btp/ponte.py` | Fila de consultas para a aba | Consultas do cliente; respostas da aba | Respostas ao cliente | Consultas não saem; sessão é dada como desconectada |
| Script da aba | `agente_btp/web/agente_btp.js` | Executar o GET com a sessão do usuário | Tarefas da ponte | Respostas do BTP | Aba "desconectada"; análise pausa |
| Base (loja) | `agente_btp/loja.py` | Guardar cada WO, eventos e problemas | Resultados por WO | Contadores, retomada, dados da planilha | Sem gravação, nada é preservado |
| Exportação | `agente_btp/exportacao.py` | Gerar e conferir o Excel | Resultados da base; tabela de valores | Arquivo `.xlsx` | A análise conclui, mas sem planilha (erro registrado) |
| Histórico | `agente_btp/historico.py` | Listar análises passadas | Resumo de cada análise | `historico/historico.json` | Análises não aparecem na lista (a base continua) |
| Diagnóstico | `agente_btp/diagnostico.py` | Classificar problemas e registrar evidência | Exceções e eventos | Motivo, categoria, próxima ação | Problemas ficam sem explicação |
| Preferências e configuração | `preferencias.py`, `config.py`, `materiais.py` | Parâmetros e dados de referência | Edições na tela; importação de planilha | Valores validados | Valores padrão são usados |
| Extensão | `extensao/` | Injetar o script na aba sem favorito | Abertura de páginas do BTP | Script na aba | Usa-se o favorito como alternativa |

### 6.4 Camadas

Outra forma de ver a arquitetura é em **camadas**: cada camada usa apenas as que estão abaixo dela. Isso ajuda a prever o impacto de uma mudança — alterar uma camada de baixo pode afetar todas as de cima; alterar uma de cima não afeta as de baixo.

@@figura camadas

::: nobtp
<span class="interpretacao">INTERPRETAÇÃO TÉCNICA</span> A separação em camadas não está declarada como regra no projeto, mas é visível nas importações: `modelos.py` e `diagnostico.py` não importam nenhum outro módulo do agente; `cliente.py` não importa `processador.py`; `processador.py` não importa `motor.py`. A exceção é `exportacao.py`, que importa `loja.estado_da_wo` dentro de uma função para usar a mesma regra de estado da tela (`estado_rotulo`).
:::

### 6.5 Decisões de arquitetura observadas

| Decisão | Evidência | Consequência |
|---|---|---|
| Consultar o serviço de dados em vez de clicar na tela | `cliente.py`; `docs/relatorio-revisao-auditoria.md` | Rapidez e fidelidade; depende dos nomes do serviço |
| Usar a aba já logada como transporte | `ponte.py`, `agente_btp.js` | O programa nunca toca em credenciais |
| Uma única linha de execução para comandos | `Agente._laco` em `agente.py` | Uma análise por vez; sem disputa entre comandos |
| Base local transacional por WO | `loja.py` | Retomada sem perda após qualquer interrupção |
| Planilha gerada sempre a partir da base | `Agente._planilha` | O Histórico pode regenerar o relatório sem consultar o BTP |
| Somente GET em três camadas | `cliente.py`, `ponte.get`, `agente_btp.js:executar` | Não há caminho de código para alterar dados |

## 7. Estrutura de pastas e arquivos {#cap7}

A árvore abaixo foi gerada automaticamente a partir dos arquivos versionados no Git (comando `git ls-files`), excluindo a pasta desta documentação. Pastas que o programa cria durante o uso — `dados/`, `saidas/`, `logs/`, `historico/` e os arquivos locais de `config/` — não aparecem porque, de propósito, não são versionadas (veja o `.gitignore`).

@@arvore

### 7.1 Raiz do repositório

| Arquivo | Finalidade |
|---|---|
| `Abrir Agente BTP.bat` | Ponto de partida no Windows: prepara o ambiente e inicia o programa (Capítulo 9) |
| `FAVORITO - Agente BTP.txt` | Instruções para criar o favorito que conecta a aba, quando a extensão não puder ser usada |
| `README.md` | Instruções de uso, arquitetura resumida e a especificação original |
| `requirements.txt` | Dependências Python: `flask>=3.0` e `openpyxl>=3.1` |
| `.gitignore` | Lista do que não deve ir para o Git (dados locais, saídas, logs, configuração gerada) |

::: atencao
O `README.md` está parcialmente desatualizado em relação ao código. Por exemplo, ele diz que cada WO é "conferida N vezes (padrão 5)", mas o padrão atual em `agente_btp/preferencias.py` é `auditorias_por_wo = 2`, com até 3 leituras extras em caso de divergência; também descreve um relatório com abas que mudaram (hoje: Dados, Materiais, Análise, Rastreio e Tabela de valores). Quando houver diferença, vale o código — e este livro descreve o código.
:::

### 7.2 Pasta `agente_btp/` — o programa

| Arquivo | Linhas | Papel principal | Capítulo |
|---|---|---|---|
| `__main__.py` | 62 | Ponto de entrada (`python -m agente_btp`) | 10 |
| `agente.py` | 1.117 | Orquestrador: comandos, preparação, conferência, execução, planilha, histórico | 10, 12, 16 |
| `servidor.py` | 404 | Servidor Flask: tela, API interna, canal com a aba | 11 |
| `ponte.py` | 254 | Fila de tarefas entre o programa e a aba | 11 |
| `cliente.py` | 741 | Consultas OData, leitura em lote, releitura, classificação de respostas | 11, 13 |
| `odata.py` | 123 | Montagem de URLs e leitura de respostas OData | 13, 14 |
| `motor.py` | 335 | Paralelismo, sondas de sessão, pausa e retomada | 16, 26 |
| `processador.py` | 421 | Extração inicial, auditorias e leituras extras | 13, 16 |
| `auditoria.py` | 188 | Comparação de leituras e estado final | 14 |
| `loja.py` | 449 | Base SQLite de análises, WOs, eventos e problemas | 14, 26 |
| `exportacao.py` | 536 | Geração e conferência do Excel | 15 |
| `materiais.py` | 168 | Tabela de valores (Tipo Material e preço unitário) | 15 |
| `historico.py` | 108 | Histórico de análises em JSON | 15 |
| `diagnostico.py` | 149 | Taxonomia de problemas e evidências | 26 |
| `preferencias.py` | 147 | Parâmetros da tela Configurações | 22 |
| `config.py` | 116 | Fonte de dados (`config/api.json`) | 12, 22 |
| `modelos.py` | 158 | Estruturas de dados e exceções compartilhadas | 14, 16 |
| `entrada.py` | 70 | Leitura e validação das WOs | 13 |
| `campos.py` | 87 | Reconhecimento de campos pelo nome e pelo valor | 12 |
| `autoconfig.py` | 482 | Preparação pelo dicionário de dados e receita confirmada | 12 |
| `exploracao.py` | 205 | Preparação consultando os dados | 12 |
| `sondagem.py` | 71 | Preparação pela linha do alerta | 12 |
| `analisador.py` | 261 | Preparação observando a tela | 12 |
| `raiox.py` | 127 | Raio-X de uma WO (diagnóstico da fonte) | 12, 27 |
| `web/index.html` | 1.714 | Tela de operação (HTML, CSS e JavaScript num só arquivo) | 23 |
| `web/agente_btp.js` | 306 | Script executado dentro da aba do BTP | 11, 12 |

### 7.3 Pasta `extensao/` — a extensão do Chrome

| Arquivo | Papel |
|---|---|
| `manifest.json` | Declara a extensão (Manifest V3), as permissões (`scripting`, `tabs`) e o único site em que ela atua |
| `no_btp.js` | Roda nas páginas do BTP e pede ao service worker para carregar o agente |
| `fundo.js` | Service worker: injeta `agente_btp.js` na aba, no "mundo" da página |
| `agente_btp.js` | Cópia do script da aba; o servidor a mantém igual a `agente_btp/web/agente_btp.js` ao iniciar |
| `icones/` | Ícones da extensão |

### 7.4 Pasta `testes/` — verificação

| Arquivo | Papel |
|---|---|
| `test_*.py` (8 arquivos) | Testes automáticos executados pelo pytest (Capítulo 24) |
| `servico_real_falso.py` | Serviço falso que imita o Monitor real (nomes de campos confirmados) |
| `btp_lote_falso.py` | Serviço falso para lotes grandes, com `$filter` avaliado de verdade |
| `conftest.py` | Configuração comum dos testes |
| `medir_desempenho.py` | Medição de desempenho antes e depois |
| `e2e/*.py` | Testes de ponta a ponta com navegador real (Chromium) e um BTP falso |

### 7.5 Pasta `docs/` — documentação técnica anterior

| Arquivo | Conteúdo |
|---|---|
| `mapeamento-processo.md` | Processo manual, colunas da janela Mensagens, estados por WO |
| `relatorio-revisao-auditoria.md` | Revisão da extração, auditoria e relatório (14–15/10) |
| `relatorio-correcao-raiz.md` | Diagnóstico de raiz, correções e medições das rodadas de 21/10 a 29/10 |
| `logo-agente-btp.svg` | Logotipo |

### 7.6 Pastas criadas durante o uso (não versionadas)

| Pasta / arquivo | Criado por | Conteúdo |
|---|---|---|
| `.venv/` | `Abrir Agente BTP.bat` | Ambiente Python isolado com Flask e openpyxl |
| `config/api.json` | `config.salvar` | Fonte de dados confirmada |
| `config/preferencias.json` | `preferencias.salvar` | Parâmetros da tela Configurações |
| `config/tabela_materiais.json` | `materiais.salvar` | Tabela de valores importada |
| `config/fontes_recusadas.json` | `agente._rejeitar` | Fontes recusadas na conferência (nunca mais usadas) |
| `dados/execucoes.sqlite3` | `Loja` | Base de análises, WOs, eventos e problemas |
| `saidas/` | `exportacao`, `raiox` | Planilhas `Análise N - X WOs.xlsx` e arquivos de Raio-X |
| `historico/` | `historico.registrar` | `historico.json`, `contador.txt` e `analises/<id>.json` |
| `logs/agente_btp.log` | `__main__.configurar_log` | Registro técnico do processo |

### 7.7 Arquivos essenciais por finalidade

| Finalidade | Arquivos |
|---|---|
| Inicialização | `Abrir Agente BTP.bat`, `agente_btp/__main__.py` |
| Configuração | `config.py`, `preferencias.py`, `materiais.py` |
| Execução | `agente.py`, `motor.py`, `processador.py` |
| Comunicação | `servidor.py`, `ponte.py`, `web/agente_btp.js`, `extensao/` |
| Extração | `cliente.py`, `odata.py`, `entrada.py` |
| Processamento | `auditoria.py`, `campos.py` |
| Resultados | `exportacao.py`, `historico.py`, `loja.py` |
| Diagnóstico | `diagnostico.py`, `raiox.py` |
| Testes | `testes/` |
| Implantação | `Abrir Agente BTP.bat`, `requirements.txt`, `extensao/manifest.json` |

::: atencao
`config/api.exemplo.json` é um modelo ilustrativo antigo: seu comentário interno menciona um botão "Salvar descoberta" que não existe mais. O arquivo real `config/api.json` é gerado pelo agente na preparação. O exemplo serve apenas para mostrar o formato geral.
:::

## 8. Tecnologias, linguagens e ferramentas utilizadas {#cap8}

Esta lista contém apenas o que aparece de fato no repositório. Para cada item, a classificação diz o que ele é: linguagem, biblioteca, serviço externo, protocolo, plataforma ou ferramenta.

### 8.1 Python — linguagem de programação

**O que é.** Uma linguagem de programação de uso geral, conhecida pela legibilidade.

**Como é usada.** Todo o núcleo do agente (pasta `agente_btp/`) e todos os testes. O código usa recursos modernos da linguagem, como *dataclasses* (classes de dados declarativas, em `modelos.py`, `config.py`, `preferencias.py`) e anotações de tipo (`str | None`).

**Indispensável?** Sim; reescrever o núcleo em outra linguagem seria refazer o projeto.

**Cuidados.** O projeto não declara uma versão mínima. <span class="naodet">NÃO DETERMINADO</span> Os testes desta edição foram executados com Python 3.13; o `.bat` usa o lançador `py -3` (qualquer Python 3 instalado). Como verificar: `python --version` no terminal do ambiente.

### 8.2 Flask — biblioteca (framework web)

**O que é.** Uma biblioteca Python para criar servidores web: define rotas (endereços) e as funções que respondem a cada uma.

**Como é usada.** Em `agente_btp/servidor.py`, a função `criar_app` cria o aplicativo e declara as rotas com decoradores como `@app.get("/api/estado")`. Em `__main__.py`, `app.run(host="127.0.0.1", port=8767, threaded=True)` inicia o servidor de desenvolvimento embutido do Flask (Werkzeug), atendendo vários pedidos ao mesmo tempo com threads.

**Indispensável?** Substituível por outro framework, mas toda a API interna teria de ser reescrita.

**Cuidados.** O servidor embutido é o de desenvolvimento do Flask. Para um programa que atende apenas o próprio computador (`127.0.0.1`), isso é aceitável; ele não deve ser exposto na rede.

### 8.3 openpyxl — biblioteca

**O que é.** Biblioteca Python para ler e escrever arquivos Excel (`.xlsx`).

**Como é usada.** Para **ler** a planilha de WOs importada (`entrada.ler_wos_planilha`) e a tabela de valores (`materiais.ler_planilha`), sempre em modo somente leitura; e para **escrever** o relatório (`exportacao.gerar_planilha`) em modo de escrita contínua (*write-only*), que grava linha a linha sem montar a planilha inteira na memória.

**Cuidados.** O modo write-only exige que larguras de coluna e painéis congelados sejam definidos antes da primeira linha (`exportacao._preparar`).

### 8.4 SQLite — banco de dados embutido (biblioteca padrão)

**O que é.** Um banco de dados que cabe num arquivo, acessado pelo módulo `sqlite3` da biblioteca padrão do Python (não precisa instalar nada).

**Como é usada.** Em `agente_btp/loja.py`, com o modo WAL (que permite ler enquanto se escreve) e transações por WO.

### 8.5 JavaScript — linguagem de programação (no navegador)

**Como é usada.** Na tela (`index.html`, sem bibliotecas externas: JavaScript puro) e no script da aba (`agente_btp.js`), que usa `fetch` com `AbortController` (para impor tempo máximo às consultas) e funções assíncronas.

### 8.6 Extensão do Chrome (Manifest V3) — plataforma

**O que é.** Um pequeno pacote que adiciona comportamento ao Chrome. "Manifest V3" é a versão atual do formato de extensões.

**Como é usada.** `extensao/manifest.json` restringe a atuação ao domínio do Launchpad. O service worker (`fundo.js`) usa `chrome.scripting.executeScript` com `world: 'MAIN'` para colocar o agente no mesmo "mundo" de JavaScript da página — onde a sessão do usuário é válida.

**Substituível?** Sim: o favorito (`javascript:` que busca o script no programa local) faz o mesmo, com um clique manual a cada carregamento da página.

### 8.7 OData, HTTP e JSON — protocolos e formatos

**OData** é o padrão usado pelo serviço do Monitor; **HTTP** é o protocolo das conversas entre tela, programa, aba e BTP; **JSON** é o formato dos dados. O agente entende OData V2 e V4 (`odata.extrair_pagina`).

### 8.8 SAP BTP e SAP Fiori — serviço externo

O Monitor de alertas é um aplicativo **SAP Fiori** (padrão de interface da SAP, construído com a biblioteca SAPUI5) publicado no Launchpad do **SAP BTP**. O agente não depende de nenhuma biblioteca SAP; ele apenas lê a configuração do UI5 já carregada na página para descobrir quais serviços de dados o aplicativo usa (`fontesDeDados` em `agente_btp.js`).

### 8.9 Windows e o arquivo `.bat` — plataforma de execução

O ponto de partida é um script de lote do Windows. O `__main__.py` também tem código específico do Windows: desliga o "modo de edição rápida" (QuickEdit) do console, porque um clique na janela preta congelava o programa (Capítulo 10). <span class="interpretacao">INTERPRETAÇÃO TÉCNICA</span> O núcleo Python é multiplataforma (os testes rodam em Linux), mas a experiência de uso foi desenhada para Windows.

### 8.10 Ferramentas de desenvolvimento e teste

| Ferramenta | Tipo | Uso |
|---|---|---|
| Git | Controle de versão | Histórico de 39 commits |
| pytest | Biblioteca de testes | 167 testes automáticos (Capítulo 24) |
| Playwright + Chromium | Automação de navegador | Somente nos testes de ponta a ponta (`testes/e2e/`) — não é dependência do programa |
| http.server | Biblioteca padrão | Servidores falsos do BTP nos testes (`testes/e2e/sap_falso.py`) |

## 9. Dependências e preparação do ambiente {#cap9}

### 9.1 Pré-requisitos

| Item | Por quê | Como verificar |
|---|---|---|
| Windows | O `.bat` e o ajuste do console são do Windows | — |
| Python 3 instalado | O programa é Python | `py -3 --version` ou `python --version` |
| Google Chrome (ou Chromium) | Extensão e aba do BTP | `chrome://version` |
| Acesso ao Launchpad do BTP com usuário que vê o Monitor de alertas | Fonte dos dados | Abrir o Monitor manualmente |
| Acesso à internet para instalar Flask e openpyxl (só na 1ª vez) | `pip install` | — |

Nenhuma credencial é configurada no agente. Login e MFA são feitos pelo usuário no próprio Chrome, como de costume.

### 9.2 O que o `Abrir Agente BTP.bat` faz, linha a linha

@@trecho "Abrir Agente BTP.bat" 1 16 "script de inicialização"

| Linhas | O que fazem | Resultado esperado | Como reconhecer um problema |
|---|---|---|---|
| 1–4 | Desligam o eco de comandos, ajustam a página de código para UTF-8, dão título à janela e entram na pasta do próprio `.bat` (`%~dp0`) | Janela "Agente BTP" aberta | — |
| 6–7 | Procuram um processo escutando a porta 8767 e o encerram | Uma versão antiga aberta é fechada | Se outro programa usar a porta, ele também é encerrado (ver Capítulo 28) |
| 9–12 | Se não existir `.venv`, criam um ambiente Python isolado com `py -3 -m venv .venv` (ou `python -m venv .venv`) | Mensagem "Preparando ambiente pela primeira vez..." | Erro de "py não reconhecido" e "python não reconhecido" = Python não instalado |
| 13 | Ativam o ambiente isolado | Comandos `python` e `pip` passam a ser os do `.venv` | — |
| 14 | Instalam ou atualizam Flask e openpyxl silenciosamente | Nenhuma saída (`-q`) | Mensagens de erro de rede ou proxy |
| 15 | Iniciam o programa | Mensagem "Agente BTP aberto em http://127.0.0.1:8767" e o navegador abre a tela | Mensagem de erro Python na janela |
| 16 | `pause`: mantém a janela aberta se o programa terminar | — | A janela mostra o erro que encerrou o programa |

::: termo
**Ambiente virtual (`.venv`).** Uma pasta com uma cópia isolada do Python e das bibliotecas instaladas só para este projeto. Assim, instalar Flask para o agente não interfere em outros programas Python do computador. Apagar a pasta `.venv` é seguro: o `.bat` a recria na próxima abertura.
:::

### 9.3 Instalação da extensão do Chrome (uma vez)

Os passos abaixo são os mesmos exibidos na própria tela do agente quando a aba do BTP não está conectada (cartão "Conecte a aba do BTP"):

1. No Chrome, abrir `chrome://extensions`.
2. Ligar o **Modo do desenvolvedor** (canto superior direito).
3. Clicar em **Carregar sem compactação** e escolher a pasta `extensao` do Agente BTP. A tela do agente tem um botão que abre essa pasta no Explorador (rota `POST /api/extensao/abrir`).
4. Recarregar a aba do BTP (F5). Se o Chrome perguntar sobre **acesso à rede local**, clicar em **Permitir** — o script da aba precisa falar com `127.0.0.1`.

Quando tudo está certo, aparece no canto inferior direito da aba do BTP um selo verde **"Agente BTP: conectado"**.

### 9.4 Alternativa sem extensão: o favorito

Se a empresa bloquear extensões, a tela oferece um **favorito**: um endereço do tipo `javascript:` que, clicado na aba do BTP, busca o script no programa local e o executa. O código desse carregador é gerado pela função `codigo_carregador` de `agente_btp/servidor.py`. A diferença prática é que o favorito precisa ser clicado de novo sempre que a página do BTP for recarregada.

### 9.5 Verificações antes da primeira execução

1. A janela preta mostra "Agente BTP aberto em http://127.0.0.1:8767".
2. A tela do agente abre no navegador e mostra, no rodapé do menu lateral, o estado da conexão.
3. Na aba do BTP, o selo diz "Agente BTP: conectado".
4. Na tela do agente, o estado passa a **"BTP conectado · sessão válida"** (ponto verde). Se ficar em "Aba conectada · sessão a verificar" (amarelo), a sessão ainda não foi confirmada por uma consulta bem-sucedida — veja o Capítulo 11.

### 9.6 Implantação e atualização

@@figura implantacao

A distribuição observada ao longo do desenvolvimento é um arquivo `Agente-BTP.zip`, gerado com `git archive` (que inclui apenas arquivos versionados — portanto, sem dados locais). Para **atualizar** sem perder a base de análises, o pacote deve ser extraído **por cima** da pasta existente: as pastas `dados/`, `config/`, `historico/` e `saidas/` não fazem parte do pacote e ficam intactas.

::: atencao
Ao atualizar, o programa recalcula sua "versão" (uma impressão digital de todos os arquivos `.py` e da pasta `web/`) e, se a aba estiver com um script de outra versão, pede a ela que se recarregue uma única vez. Se a extensão instalada for de **outra pasta** (outra cópia do programa), a aba mostra um aviso vermelho pedindo para abrir o `.bat` da mesma pasta da extensão. Esse comportamento está em `agente_btp.js` (função `trabalhador`) e foi criado para evitar laços de recarga (commit `4ea72e2`).
:::
