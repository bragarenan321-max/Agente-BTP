# Parte I — Compreendendo o Agente BTP {#parte1}

Esta parte responde às perguntas mais básicas — e mais importantes — sobre o projeto: o que é o Agente BTP, que trabalho ele substitui, qual é o caminho que uma análise percorre do clique ao relatório e quais palavras é preciso conhecer para acompanhar o restante do livro. Nenhum conhecimento de programação é pressuposto. Os capítulos seguintes aprofundam cada ponto apresentado aqui.

## 1. Apresentação geral do projeto {#cap1}

O **Agente BTP** é um programa que roda no computador do usuário e automatiza uma tarefa de consulta repetitiva: para cada ordem de serviço (WO) informada, ele descobre quais materiais estão faltando segundo o *Monitor de alertas* de um sistema corporativo hospedado no SAP BTP, confere essa informação várias vezes e entrega tudo organizado numa planilha Excel. Ele não altera nada no sistema de origem: apenas lê.

### 1.1 O que é, em uma frase e em um parágrafo

Em uma frase: **o Agente BTP lê, confere e organiza os itens faltantes de cada WO no Monitor de alertas, sem que o usuário precise abrir WO por WO.**

Em um parágrafo: o usuário cola uma lista de WOs (ou importa uma planilha com elas) numa tela que o próprio programa abre no navegador. O programa pede à aba do navegador em que o usuário já está logado no BTP que faça, em nome dele, as mesmas consultas de dados que a tela do Monitor faz quando alguém abre a janela "Mensagens" de uma WO. Cada resposta é lida, comparada com leituras repetidas para garantir que nada mudou no meio do caminho, gravada numa base local e, ao final, transformada num relatório Excel com uma linha por item faltante, um resumo por material (inclusive com valores em reais, se uma tabela de preços tiver sido importada) e o rastro de cada leitura feita.

::: conceito
**Agente de software.** Um programa que executa uma tarefa em nome de uma pessoa, tomando pequenas decisões sozinho ao longo do caminho — por exemplo, repetir uma consulta que falhou ou pausar quando a sessão do usuário expira. O nome "agente" aqui não significa inteligência artificial: o Agente BTP segue regras explícitas, escritas no código, e cada decisão que ele toma pode ser rastreada no registro da análise.
:::

### 1.2 O problema concreto que ele resolve

No sistema de origem, a informação sobre o que falta para cada WO existe, mas está "escondida" atrás de vários cliques: é preciso abrir o aplicativo Monitor de alertas, filtrar a lista pela WO, clicar na linha encontrada e abrir uma janela chamada **Mensagens**, onde aparecem o material, o fornecedor, a quantidade faltante e a mensagem de erro de cada item. Para uma WO, isso leva poucos segundos; para milhares de WOs, torna-se inviável. O Capítulo 2 reconstrói esse processo manual em detalhe.

O Agente BTP elimina essa navegação repetida. Em vez de clicar, ele consulta diretamente o mesmo serviço de dados que alimenta a tela — com a mesma sessão autenticada do usuário — e faz isso para muitas WOs em paralelo, conferindo cada resultado.

@@figura antes_depois

### 1.3 Em que ambiente ele opera

O agente vive em três lugares ao mesmo tempo, e entender essa divisão é a chave para entender o resto do livro:

1. **No computador do usuário**, como um programa escrito em Python que abre um pequeno servidor web local (acessível apenas pelo próprio computador, no endereço `127.0.0.1:8767`). É ali que ficam a lógica, a base de dados local e os arquivos gerados.
2. **Na aba do navegador em que o BTP está aberto**, como um pequeno script (`agente_btp.js`) colocado ali por uma extensão do Chrome (ou por um favorito). Esse script é o "braço" do agente dentro da sessão autenticada: é ele quem faz as consultas ao BTP.
3. **No navegador, numa segunda aba**, como a tela de operação (`index.html`), onde o usuário cola as WOs, acompanha o progresso e baixa o relatório.

@@figura contexto

A [[fig:contexto]] mostra essa composição. Repare que o programa local **nunca fala diretamente** com o BTP: toda consulta passa pela aba do usuário. O Capítulo 11 explica por que isso foi feito assim e o que essa decisão garante em termos de segurança.

::: nobtp
O "BTP" do nome refere-se ao **SAP Business Technology Platform**, a plataforma em nuvem da SAP onde está hospedado o Launchpad (portal de aplicativos) que contém o Monitor de alertas. O arquivo `docs/mapeamento-processo.md` registra que o portal roda no ambiente Cloud Foundry da região `br10` e que o login é feito por SSO (SAML) com confirmação no celular (MFA). <span class="confirmado">CONFIRMADO</span> pelos arquivos `agente_btp/config.py` (variável `URL_LAUNCHPAD`) e `extensao/manifest.json` (permissão de acesso ao domínio do Launchpad).
:::

### 1.4 Quem usa os resultados

O usuário direto é o operador que precisa saber, para uma lista de WOs, quais materiais estão faltando e por quê. O histórico Git e os relatórios em `docs/` mostram análises reais de até **13.623 WOs** de uma vez (por exemplo, `docs/relatorio-correcao-raiz.md`, seção 7). A planilha gerada é o produto entregue: ela pode ser filtrada, somada por material e usada para priorizar reposição ou baixa de estoque.

O código não registra quais áreas ou processos consomem a planilha depois que ela sai do agente. <span class="naodet">NÃO DETERMINADO</span> — essa informação dependeria de entrevista com os usuários.

### 1.5 O que é uma execução bem-sucedida

Uma execução — chamada no projeto de **análise** — é considerada bem-sucedida quando:

- todas as WOs da lista foram consultadas (nenhuma ficou "pendente");
- cada WO recebeu um estado claro: **validada**, **divergente**, **incompleta** ou **falha**, sempre com o motivo quando não for validada;
- o arquivo `Análise N - X WOs.xlsx` foi gravado e, logo depois, **reaberto e conferido célula a célula** contra os dados da base (Capítulo 15);
- a análise ficou registrada no histórico, com diagnóstico e registro de eventos consultáveis depois.

Na prática, o sinal visível é o selo **Concluída** na tela e o botão **Baixar relatório auditado (.xlsx)**.

### 1.6 Dentro e fora do escopo

| Dentro do escopo (o agente faz) | Fora do escopo (o agente não faz) |
|---|---|
| Ler a lista de alertas do Monitor filtrada por WO | Fazer login, digitar senha ou responder ao MFA |
| Ler os itens da janela "Mensagens" de cada alerta | Clicar em "Reprocessar" ou "Efetuar Baixa" |
| Repetir leituras para conferir (auditoria) | Alterar qualquer dado no BTP (não há requisição de escrita no código) |
| Retomar uma análise interrompida | Agendar análises sozinho, sem um usuário |
| Gerar e conferir a planilha Excel | Enviar a planilha por e-mail ou para outro sistema |
| Somar quantidades e valores por material (com tabela importada) | Consultar preços no BTP (o preço vem de uma tabela do usuário) |

A restrição de somente leitura é uma das ideias centrais do projeto e aparece já no topo do módulo que faz as consultas:

@@trecho agente_btp/cliente.py 1 6 "docstring do módulo"

::: leitura
As linhas 1 a 6 de `agente_btp/cliente.py` não são código executável: são um comentário de documentação (chamado *docstring* em Python). Elas declaram a regra de projeto — "só existe uma operação de rede: GET" — e explicam sua consequência: como não há no código nenhum caminho para métodos que alteram dados (POST, PUT, PATCH, DELETE), o agente não consegue acionar "Reprocessar" nem "Efetuar Baixa", nem por engano. O Capítulo 28 mostra que essa regra é reforçada em mais dois lugares: no script da aba (que só executa GET) e na ponte (que recusa endereços que não sejam do próprio sistema).
:::

### 1.7 O que o sistema faz hoje e o que ainda não faz

**Faz hoje** (versão do repositório na data desta edição): preparação automática da fonte de dados na primeira execução; conferência da amostra com o usuário; leitura em lote e em paralelo com verificação de equivalência; auditoria por WO com leituras extras em caso de divergência; pausa e retomada automáticas quando a sessão do BTP expira; base local que preserva cada WO concluída; retomada de análises interrompidas; planilha com cinco abas (Dados, Materiais, Análise e duas ocultas); filtro para listar na planilha apenas itens com a mensagem "Estoque indisponível"; tabela de valores importada; histórico com diagnóstico; ajuda guiada na própria tela.

**Ainda não faz**: execução sem usuário (agendada); integração direta com outros sistemas; manutenção da sessão do BTP viva quando o agente está ocioso; validação automática contra uma fonte independente do próprio BTP. O Capítulo 30 discute essas limitações e propõe melhorias, sempre separadas do que já existe.

### 1.8 Exemplo: a mesma tarefa com e sem o agente

::: exemplo
**Sem o agente** (reconstrução a partir de `docs/mapeamento-processo.md`): para cada WO, o operador abre o tile *Monitor de alertas — Itens pendentes* (que no momento do mapeamento listava cerca de 175 mil itens), abre o filtro da coluna WO, pesquisa e marca a WO, confirma, clica na linha filtrada, abre a janela *Mensagens* e copia material, fornecedor, quantidade faltante e mensagem de cada item. Em seguida, volta ao filtro para a próxima WO.

**Com o agente**: o operador cola a lista (por exemplo, 500 WOs) e clica em **Executar análise**. Na primeira vez, confere uma amostra para confirmar que os dados lidos são os mesmos da janela Mensagens. Depois disso, acompanha a barra de progresso e baixa a planilha ao final.

**Sobre ganho de tempo**: o diagnóstico de uma execução real de 13.623 WOs (arquivo `diagnostico_analise_1.json`, exportado pela própria tela do agente e enviado pelo usuário durante o desenvolvimento; não versionado no repositório) registra 2.583 s de processamento — cerca de 43 minutos —, 77.787 consultas ao BTP e 5,27 WOs por segundo, com a leitura em lote desativada naquela execução. Não há medição registrada do tempo manual equivalente, por isso este livro **não** apresenta um percentual de ganho. A conta pode ser feita por quem conhece o tempo manual por WO.
:::

## 2. O problema que motivou a criação {#cap2}

Todo software existe porque alguém tinha um trabalho difícil, lento ou sujeito a erro. Este capítulo reconstrói esse trabalho a partir das evidências disponíveis no repositório — principalmente a especificação original no `README.md`, o mapeamento do processo em `docs/mapeamento-processo.md` e os relatórios técnicos em `docs/`.

### 2.1 Como o processo funciona no sistema de origem

O documento de mapeamento, escrito a partir de capturas de tela reais enviadas pelo usuário, descreve a sequência manual:

| Passo | Tela | Ação do usuário |
|---|---|---|
| 1 | Launchpad (página inicial) | Clica no tile "Monitor de alertas — Itens pendentes" |
| 2 | Lista de itens | Clica no ícone de filtro (funil) da tabela |
| 3 | Diálogo "Filtrar por: WO" | Pesquisa a WO, marca a caixa e clica em OK |
| 4 | Lista filtrada | Clica na linha (mensagem "Clique para mais detalhes") |
| 5 | Diálogo "Mensagens" | Lê os dados da WO — é aqui que está a informação final |

A informação que interessa — o resultado — está no passo 5, na janela "Mensagens". O mesmo documento registra as colunas dessa janela:

| Coluna | Exemplo | Como deve ser tratada |
|---|---|---|
| WO | `88412|126579742` | Texto |
| Material | `000000000022026169` | Texto com 18 dígitos (zeros à esquerda importam) |
| Fornecedor | `0000137628` | Texto com 10 dígitos (zeros à esquerda importam) |
| Quantidade faltante | `2` | Número |
| Mensagem | `Estoque indisponível` | Texto |

::: termo
**WO (Work Order, ordem de serviço).** No contexto deste sistema, é o identificador de uma ordem de serviço no formato `PPPPP|NNNNNNNNN`: cinco dígitos, uma barra vertical e nove dígitos (por exemplo, `02235|230771207`). O prefixo pode começar com zero, por isso a WO é sempre tratada como **texto**, nunca como número — um número perderia o zero inicial. O formato está fixado na expressão regular `PADRAO_WO` de `agente_btp/entrada.py`.
:::

### 2.2 Onde está o esforço

Analisando a tabela do processo manual, o esforço se concentra em três pontos:

1. **Repetição.** Os passos 2 a 5 se repetem para cada WO. Não há na tela do Monitor uma forma de abrir as mensagens de várias WOs de uma vez.
2. **Transcrição.** Os dados da janela Mensagens precisam ser copiados para uma planilha. Códigos de 18 dígitos com zeros à esquerda são especialmente fáceis de copiar errado — e o Excel, se não for orientado, apaga os zeros iniciais ao interpretar o valor como número.
3. **Volume.** O mapeamento registrou cerca de 175 mil itens pendentes no Monitor. As análises reais documentadas tinham de algumas centenas a 13.623 WOs.

### 2.3 Onde os erros podem acontecer

O processo manual é vulnerável a erros de natureza diferente, e vale nomeá-los, porque cada um deles motivou um mecanismo específico no agente:

| Erro possível no processo manual | Mecanismo correspondente no agente | Onde está explicado |
|---|---|---|
| Copiar o material sem os zeros à esquerda | Identificadores sempre lidos como texto (`odata.como_texto`) e gravados em células de texto no Excel | Caps. 14 e 15 |
| Copiar a quantidade errada (quando a tela tem mais de uma) | Recusa em escolher entre dois campos de quantidade ("quantidade ambígua") | Cap. 14 |
| Ler uma WO no meio de uma atualização no BTP | Auditoria: leituras repetidas comparadas campo a campo | Cap. 14 |
| Pular uma WO da lista | Base local com todas as WOs e estado de cada uma | Caps. 14 e 26 |
| Atribuir os itens de uma WO à WO vizinha | Conferência de que cada linha devolvida pertence à WO pedida | Cap. 13 |
| Perder o trabalho se a sessão cair | Pausa automática e retomada sem refazer o que já foi concluído | Cap. 26 |

### 2.4 Por que a automação é útil — e por que não basta "automatizar cliques"

A especificação original no `README.md` previa um agente que "controla um navegador dedicado" e executa uma "navegação automatizada". O histórico Git mostra que o projeto seguiu outro caminho: em vez de clicar na tela, o agente consulta **o serviço de dados** que alimenta a tela, pela própria aba do usuário. O relatório `docs/relatorio-revisao-auditoria.md` registra explicitamente: "O agente **não clica na tela**. Ele lê o mesmo serviço de dados (OData) que alimenta o Monitor de Alertas e a janela 'Mensagens', pela aba já autenticada do usuário (somente GET)."

As vantagens técnicas dessa escolha, sustentadas pelo código, são:

- **Velocidade.** Uma consulta de dados devolve todas as linhas de uma vez, sem esperar a tela desenhar. Além disso, várias WOs podem ser consultadas numa só requisição (leitura em lote, Capítulo 13).
- **Fidelidade.** O agente recebe os valores exatamente como o servidor os envia, sem depender de como a tela os formata.
- **Robustez a mudanças visuais.** Uma alteração no desenho da tela não afeta o agente; só uma alteração no serviço de dados afetaria (Capítulo 32).

<span class="interpretacao">INTERPRETAÇÃO TÉCNICA</span> O histórico não registra por escrito a motivação da troca de "navegador controlado" por "consulta pela aba já aberta". O commit `bea63d9` (13/10/2026) tem a mensagem "conecta à aba do SAP já aberta via favorito (modelo Agente Atlas)", e o `40f7973` "conexão somente pela aba já aberta; remove navegador separado da interface". Uma motivação compatível com as regras de segurança da especificação ("utilizar somente a sessão que já tenha sido autenticada", "não automatizar ou contornar o MFA") é que a aba já logada resolve a autenticação sem que o programa jamais toque em credenciais.

### 2.5 O que continua difícil mesmo com o agente

A automação não elimina todos os problemas, e é importante deixá-los claros desde já:

- **A sessão do BTP expira.** O agente não faz login. Quando a sessão cai, ele pausa e espera o usuário logar de novo (Capítulo 26).
- **O serviço de dados pode mudar.** Se a SAP ou a equipe responsável pelo aplicativo alterar os nomes das tabelas ou dos campos, a fonte gravada deixa de servir. O agente detecta parte dessas situações (por exemplo, HTTP 404) e se prepara de novo, mas não todas (Capítulo 32).
- **A conferência é interna ao BTP.** A auditoria compara leituras do próprio BTP entre si. Ela garante que o agente leu de forma consistente o que o BTP mostrava, mas não verifica se o próprio BTP está certo.
- **A primeira configuração depende do usuário.** A conferência da amostra exige que alguém que conheça a janela Mensagens confirme que os dados lidos são os mesmos (Capítulo 12).

## 3. Visão geral do funcionamento {#cap3}

Antes de mergulhar nos detalhes, vale ver o caminho completo de uma análise numa única figura. Cada bloco da [[fig:visao_geral]] corresponde a código real e é explicado numa subseção a seguir; os capítulos da Parte III aprofundam cada um.

@@figura visao_geral

**Visão geral.** A figura mostra o que acontece desde que o usuário informa as WOs até a entrega do relatório. O caminho principal é vertical; os desvios laterais são a preparação (só quando ainda não há fonte de dados confirmada), a comunicação com o BTP (que passa pela aba do usuário) e o caminho de falha da sessão.

**Leitura passo a passo.** As setas indicam a ordem. O losango "Fonte de dados confirmada?" é uma decisão: se a resposta for "não", o agente primeiro se prepara e pede a conferência da amostra; se for "sim", vai direto ao processamento. A seta dupla entre "Extração + auditorias" e "Aba do BTP" indica ida e volta: uma consulta enviada e uma resposta recebida. A seta tracejada vermelha indica o caminho de exceção.

### 3.1 O usuário informa as WOs

Na tela de Análise, o usuário cola WOs no campo de texto (uma por linha) e/ou importa uma planilha `.xlsx`. A própria tela conta quantas linhas estão no formato certo antes de enviar. O botão **Executar análise** só fica disponível quando a aba do BTP está conectada e há pelo menos uma WO — e, se estiver bloqueado, o motivo aparece logo abaixo dele.

### 3.2 O pedido chega ao servidor local

Ao clicar em Executar, a tela envia uma requisição `POST /api/executar` ao servidor local. A função `executar` de `agente_btp/servidor.py` lê as WOs do texto e da planilha, remove repetições e entrega o trabalho ao orquestrador com `agente.enviar("executar", wos, descrição)`. O servidor responde imediatamente — a análise roda em segundo plano, numa linha de execução própria do orquestrador (Capítulo 10).

### 3.3 A fonte de dados está confirmada?

O agente precisa saber **de onde** ler os dados: qual endereço do serviço devolve a lista de alertas de uma WO e qual devolve os itens da janela Mensagens. Essa informação fica no arquivo `config/api.json`. Na primeira execução ele não existe, e o agente o constrói sozinho (`_garantir_preparo` em `agente_btp/agente.py`); depois, mostra uma amostra real ao usuário e só continua com a confirmação (`_confirmar_fonte`). Esse processo é o tema do Capítulo 12.

### 3.4 O motor distribui o trabalho

Com a fonte confirmada, o orquestrador cria a análise na base local — todas as WOs começam como **pendentes** — e entrega o controle ao **motor** (`agente_btp/motor.py`). O motor divide as WOs em grupos (por padrão, de 20) e coloca vários trabalhadores para processá-los ao mesmo tempo (por padrão, 4). Ele também verifica periodicamente se a sessão do BTP continua válida.

### 3.5 Extração e auditorias

Para cada grupo, o **processador** (`agente_btp/processador.py`) faz a extração inicial — a primeira leitura das WOs — e depois as auditorias: novas leituras completas, comparadas campo a campo com a primeira. Se algo mudou entre as leituras, ele faz até três leituras extras para tentar esclarecer. A decisão final sobre cada item (validado, divergente, incompleto, não concluído) é tomada pelo módulo `agente_btp/auditoria.py` (Capítulo 14).

### 3.6 A comunicação com o BTP

Cada leitura é uma ou mais consultas `GET` ao serviço de dados do Monitor. O cliente (`agente_btp/cliente.py`) monta o endereço e entrega a consulta à **ponte** (`agente_btp/ponte.py`), que a coloca numa fila. O script na aba do BTP busca a consulta nessa fila, executa-a com a sessão do usuário e devolve a resposta. O Capítulo 11 detalha esse circuito.

### 3.7 O caminho de falha da sessão

Se o BTP responder com sinais de sessão expirada (redirecionamento para o login, códigos 401 ou 403, ou uma página HTML de login no lugar dos dados), o agente não marca as WOs como falha: ele **pausa** a análise, devolve à fila as WOs que estavam em andamento e espera o usuário logar de novo. Quando a sessão volta, retoma sozinho (Capítulo 26).

### 3.8 Cada WO é gravada ao terminar

Assim que uma WO termina, seu resultado é gravado numa base de dados local (SQLite, no arquivo `dados/execucoes.sqlite3`) numa transação própria. Isso significa que fechar o programa ou perder a conexão no meio de uma análise **não perde** o trabalho já feito: a análise pode ser retomada, e só as WOs pendentes, em andamento ou com falha são refeitas.

### 3.9 O relatório e o histórico

Quando não há mais WOs na fila, o orquestrador gera o Excel a partir da base (`agente_btp/exportacao.py`), reabre o arquivo gravado para conferi-lo célula a célula e registra a análise no histórico. A tela mostra o selo de conclusão e o botão de download. O Capítulo 15 descreve cada aba da planilha.

::: resumo
1. O usuário informa as WOs e clica em Executar.
2. O servidor local recebe o pedido e o entrega ao orquestrador.
3. Se ainda não há fonte de dados confirmada, o agente se prepara e pede a conferência de uma amostra.
4. O motor distribui as WOs em grupos e trabalhadores paralelos.
5. Cada WO é lida, relida e comparada; divergências recebem leituras extras.
6. Todas as consultas passam pela aba do usuário, somente com GET.
7. Sessão expirada pausa a análise, sem perder nada; ela retoma sozinha.
8. Cada WO concluída é gravada na hora na base local.
9. Ao final, o Excel é gerado, conferido e registrado no histórico.
:::

## 4. Vocabulário fundamental {#cap4}

Este capítulo apresenta, em linguagem simples, os conceitos que aparecem com frequência no restante do livro. Para cada um, há uma explicação geral e a indicação de **como ele aparece no Agente BTP**. Termos que não se aplicam ao projeto foram deixados de fora ou são explicitamente marcados como não utilizados.

### 4.1 Programa, processo e linguagem de programação

Um **programa** é um conjunto de instruções escritas para um computador executar. Essas instruções são escritas numa **linguagem de programação** — um idioma com regras rígidas que o computador consegue interpretar. Quando o programa está em execução, o sistema operacional cria um **processo**: o programa "vivo", ocupando memória e trabalhando.

**No Agente BTP:** o núcleo é escrito em **Python** (pasta `agente_btp/`, arquivos `.py`). A tela e o script da aba são escritos em **JavaScript** (`agente_btp/web/index.html` e `agente_btp/web/agente_btp.js`), a linguagem que os navegadores executam. Quando o usuário abre o `Abrir Agente BTP.bat`, nasce um processo Python — a "janela preta" que deve ficar aberta enquanto o agente é usado.

### 4.2 Código-fonte

É o texto do programa, como escrito por quem o desenvolveu. **No Agente BTP:** todo o código-fonte está no repositório Git, e este livro cita trechos dele sempre com o arquivo e as linhas de origem.

### 4.3 Biblioteca e dependência

Uma **biblioteca** é um conjunto de funções já prontas que um programa pode usar em vez de reescrevê-las. Quando um programa precisa de uma biblioteca para funcionar, ela é uma **dependência** dele.

**No Agente BTP:** o arquivo `requirements.txt` declara apenas duas dependências externas: **Flask** (para criar o servidor web local) e **openpyxl** (para ler e escrever planilhas Excel). Todo o resto usa a biblioteca padrão do Python. O Capítulo 8 detalha cada uma.

### 4.4 Servidor e cliente

Um **servidor** é um programa que fica esperando pedidos e os atende; um **cliente** é quem faz os pedidos. Pense num balcão de atendimento: o atendente (servidor) espera, e quem chega (cliente) pede algo. Os papéis não dependem de máquina: o mesmo programa pode ser servidor de um lado e cliente de outro.

**No Agente BTP** há dois servidores diferentes, e é importante não confundi-los:

- O **servidor local do agente** (Flask, em `127.0.0.1:8767`): atende a tela e o script da aba. Roda no computador do usuário.
- O **servidor do BTP** (o serviço de dados do Monitor, na nuvem da SAP): atende as consultas de dados. O script da aba é o cliente dele.

### 4.5 Endereço local (127.0.0.1) e porta

`127.0.0.1` é um endereço especial que significa "este mesmo computador" (também chamado *localhost* ou *loopback*). Um programa que escuta nesse endereço só pode ser acessado de dentro da própria máquina. A **porta** é um número que identifica qual programa, naquele computador, deve receber o pedido — como o número de um apartamento num prédio.

**No Agente BTP:** o servidor escuta em `127.0.0.1`, porta `8767` (constantes `HOST` e `PORTA` em `agente_btp/__main__.py`). O comentário do código registra que a porta foi escolhida para não colidir com outro agente do mesmo autor (Agente Atlas, porta 8765).

### 4.6 Requisição, resposta e HTTP

**HTTP** é o protocolo — o conjunto de regras de conversa — usado na web. Uma conversa HTTP é sempre um par: o cliente envia uma **requisição** (com um método, um endereço e, às vezes, dados) e o servidor devolve uma **resposta** (com um código de situação, um tipo de conteúdo e um corpo).

Os métodos mais comuns são **GET** (pedir para ler algo) e **POST** (enviar algo para ser processado). Os **códigos de situação** dizem como foi o pedido: `200` significa sucesso; `302`, redirecionamento; `401`, não autenticado; `403`, acesso negado; `404`, endereço não encontrado; `429`, muitos pedidos; `500` a `599`, erro no servidor.

**No Agente BTP:** as consultas ao BTP são **sempre GET**. A tela e o script da aba usam GET e POST para conversar com o servidor local. Os códigos de situação do BTP são interpretados pela função `classificar` de `agente_btp/cliente.py`, que decide se a resposta é dado válido, sessão expirada, falha passageira ou erro (Capítulo 26).

### 4.7 API e endpoint

Uma **API** (*Application Programming Interface*, interface de programação de aplicações) é uma porta de comunicação organizada entre programas: um conjunto de pedidos que um programa aceita, com regras claras sobre o formato de cada um. Um **endpoint** é o endereço específico de uma dessas operações. Se a API fosse um restaurante, o cardápio seria a API e cada prato pedido pelo nome, um endpoint.

**No Agente BTP** há duas APIs:

- A **API interna** do agente: rotas como `/api/estado`, `/api/executar` e `/api/historico`, definidas em `agente_btp/servidor.py`, usadas apenas pela tela e pelo script da aba. O Apêndice A lista todas.
- A **API do serviço de dados do BTP**: endereços OData como `.../Pendencias?$filter=workOrderID eq '...'`. O agente é **cliente** dessa API; não a controla.

### 4.8 OData

**OData** (*Open Data Protocol*) é um padrão para expor dados pela web de forma consultável: cada conjunto de dados (como uma tabela) tem um endereço, e é possível filtrar, ordenar e paginar usando parâmetros na própria URL — por exemplo, `$filter=workOrderID eq '12345|678901234'`. Aplicativos SAP Fiori, como o Monitor de alertas, buscam seus dados assim.

**No Agente BTP:** o arquivo `agente_btp/odata.py` monta os endereços (escapando corretamente caracteres como a barra vertical da WO) e interpreta as respostas nas duas versões do padrão (V2 e V4).

### 4.9 JSON

**JSON** é um formato de texto para representar dados estruturados: listas entre colchetes, objetos com pares "nome": valor entre chaves. Exemplo: `{"value": [{"material": "000000000022026169", "quantidade": "2"}]}`. É o formato em que o serviço OData do BTP devolve os dados, e também o formato de vários arquivos locais do agente (`config/api.json`, `historico/historico.json`).

### 4.10 Arquivo de configuração e variável de ambiente

Um **arquivo de configuração** guarda parâmetros que mudam o comportamento do programa sem alterar o código. Uma **variável de ambiente** é um parâmetro passado pelo sistema operacional ao processo.

**No Agente BTP:** há três arquivos de configuração locais — `config/api.json` (fonte de dados, gerado pelo agente), `config/preferencias.json` (parâmetros da tela de Configurações) e `config/tabela_materiais.json` (tabela de valores importada). Há uma única variável de ambiente reconhecida: `AGENTE_BTP_URL`, que permite substituir o endereço do Launchpad (`agente_btp/config.py`). Nenhum desses arquivos guarda senha ou token.

### 4.11 Banco de dados

Um **banco de dados** é um sistema para guardar informações de forma organizada e consultável. O **SQLite** é um banco de dados que cabe num único arquivo, sem precisar de um servidor separado.

**No Agente BTP:** o arquivo `dados/execucoes.sqlite3` guarda cada análise, cada WO e cada evento, gerenciado por `agente_btp/loja.py` (Capítulo 14).

### 4.12 Autenticação, autorização e sessão

**Autenticação** é provar quem você é (login, senha, MFA). **Autorização** é o que você pode fazer depois de autenticado. Uma **sessão** é o estado de "estar logado" que o servidor mantém por um tempo; o navegador a prova a cada pedido, geralmente com um *cookie* — um pequeno dado guardado pelo navegador e enviado automaticamente.

**No Agente BTP:** autenticação e autorização são inteiramente do BTP. O agente não vê, não lê e não guarda o cookie: as consultas são feitas **de dentro da aba**, e o navegador anexa o cookie sozinho. Quando a sessão expira, o agente percebe pela resposta e pausa.

### 4.13 Log, exceção e tratamento de erros

Um **log** (registro) é uma lista de eventos com horário, gravada pelo programa para permitir investigar o que aconteceu. Uma **exceção** é a forma como um programa sinaliza que algo saiu do esperado; **tratar** a exceção é decidir o que fazer em seguida em vez de simplesmente parar.

**No Agente BTP:** há três registros diferentes — o arquivo `logs/agente_btp.log` (técnico, do processo inteiro), os eventos de cada análise na base SQLite (visíveis em Histórico → Registro de execução) e o diagnóstico de cada análise (problemas classificados com evidência). O projeto define quatro exceções próprias (`SessaoExpirada`, `FalhaComunicacao`, `ErroConsulta`, `TempoEsgotado`) em `agente_btp/modelos.py`, cada uma com um motivo classificado (Capítulo 26).

### 4.14 Execução síncrona, assíncrona e paralela

Uma tarefa **síncrona** é feita do começo ao fim antes de passar à próxima. Uma tarefa **assíncrona** é iniciada e "fica rodando" enquanto o programa faz outras coisas, avisando quando termina. **Paralelismo** é fazer várias tarefas ao mesmo tempo.

**No Agente BTP:** o servidor responde à tela na hora e a análise roda em segundo plano (uma *thread*, ou linha de execução, dedicada). O motor usa várias threads trabalhadoras para processar grupos de WOs em paralelo. No navegador, o script da aba usa funções assíncronas do JavaScript (`async`/`await`) para esperar respostas sem travar a página.

### 4.15 Long-poll (consulta longa)

Normalmente, quem pergunta espera a resposta e, se não houver nada, pergunta de novo mais tarde. No **long-poll**, o servidor segura a pergunta aberta por um tempo — até ter algo para responder ou até o tempo acabar. Isso permite que o servidor "mande" tarefas a quem perguntou, mesmo sem poder iniciar a conversa.

**No Agente BTP:** é assim que o programa local entrega consultas à aba do BTP. A aba pergunta "há tarefa para mim?" e o servidor segura a pergunta por até 15 segundos (`ESPERA_LONG_POLL` em `agente_btp/ponte.py`). O Capítulo 11 explica por que esse desenho é necessário.

### 4.16 Termos do domínio

| Termo | Significado no Agente BTP |
|---|---|
| **Monitor de alertas** | Aplicativo do Launchpad que lista os itens pendentes; fonte das informações |
| **Alerta / linha da lista** | Uma linha da lista do Monitor para uma WO (há uma por *consolidado*) |
| **Consolidado** | Identificador de um agrupamento de itens de uma WO no BTP (campo `consolidID`) |
| **Janela Mensagens** | Diálogo do Monitor com os itens de um alerta: material, fornecedor, quantidade, mensagem |
| **Item** | Uma linha da janela Mensagens: um material faltante |
| **Material SAP** | Código do material, com 18 dígitos e zeros à esquerda |
| **Fornecedor SAP** | Código do fornecedor (ou base), com 10 dígitos e zeros à esquerda |
| **Análise** | Uma execução do agente sobre uma lista de WOs, numerada (nº 1, nº 2...) |
| **Leitura** | Uma consulta completa de uma WO (extração inicial, auditoria ou leitura extra) |
| **Auditoria** | Leitura repetida comparada com a primeira, para confirmar os valores |
| **Fonte de dados** | O par de endereços (lista + itens) gravado em `config/api.json` |
| **Conferência da amostra** | Momento em que o usuário confirma que os dados lidos são os da janela Mensagens |
| **Sonda** | Consulta de teste para saber se a sessão do BTP continua válida |
