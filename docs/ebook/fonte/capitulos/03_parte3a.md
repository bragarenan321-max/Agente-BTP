# Parte III — Como o agente funciona internamente {#parte3}

Esta é a parte central do livro. Ela acompanha o programa por dentro, na ordem em que as coisas acontecem: a inicialização, a comunicação entre o programa local, a aba do navegador e o BTP, a preparação da fonte de dados, a extração, a interpretação e a auditoria dos dados, a geração do relatório e, por fim, o papel de cada função importante e as relações entre os módulos. Cada mecanismo é explicado em três níveis: a ideia intuitiva, o funcionamento real e o código que o implementa.

## 10. O que acontece quando o agente é iniciado {#cap10}

### 10.1 A ideia

Iniciar o agente é como abrir uma loja: antes de atender o primeiro cliente, alguém precisa acender as luzes, conferir o caixa, organizar o estoque e destrancar a porta. No programa, "acender as luzes" é preparar o registro de eventos; "conferir o caixa" é abrir a base de dados; "organizar o estoque" é carregar as configurações; e "destrancar a porta" é começar a escutar pedidos no endereço local.

### 10.2 A sequência real

@@figura inicializacao

**Leitura passo a passo.** O fluxograma começa no duplo clique no `.bat` (Capítulo 9) e termina com o servidor escutando em `127.0.0.1:8767` e o navegador abrindo a tela. A decisão ".venv existe?" só cria o ambiente na primeira vez. A seta tracejada indica uma verificação feita durante a criação do orquestrador: se houver uma configuração de fonte gravada por versão anterior do programa, ela é descartada.

### 10.3 O ponto de entrada: `__main__.py`

Em Python, um pacote pode ser executado como programa com `python -m nome_do_pacote`; nesse caso, o arquivo `__main__.py` do pacote é executado. É o que a linha 15 do `.bat` faz.

@@codigo agente_btp/__main__.py main

::: leitura
- **Linha 48** — `desligar_selecao_da_janela()`: no Windows, um clique na janela preta coloca o console em "modo de seleção"; enquanto isso, qualquer escrita nela **congela** o programa. O comentário de `configurar_log` registra o caso real: "a análise parava no meio, ex.: 391 de 499 WOs". A função desliga esse modo só para esta janela (commit `72c9acd`).
- **Linha 49** — `configurar_log()`: todo o registro técnico vai para o arquivo `logs/agente_btp.log`, **nunca** para a janela — pelo mesmo motivo.
- **Linha 50** — `criar_app(Agente())`: primeiro cria o **orquestrador** (`Agente`), depois o **aplicativo web** (`criar_app`), que recebe o orquestrador para poder repassar a ele os pedidos.
- **Linha 55** — agenda a abertura do navegador na tela do agente um segundo depois, dando tempo ao servidor de começar a escutar.
- **Linhas 56–58** — silenciam a mensagem de boas-vindas do Flask (de novo, nada é escrito na janela) e iniciam o servidor com `threaded=True`, para atender a tela e a aba ao mesmo tempo.
:::

@@codigo agente_btp/__main__.py desligar_selecao_da_janela

A função acima usa `ctypes` (acesso direto às funções do sistema operacional) para ler o modo atual do console e retirar o bit `0x0040` (QuickEdit), acrescentando `0x0080` (flags estendidas, exigido para a mudança valer). Em qualquer outro sistema operacional (`os.name != "nt"`) ela não faz nada; se a chamada falhar, a exceção é ignorada de propósito — sem console, não há o que desligar.

### 10.4 A criação do orquestrador

O orquestrador é a classe `Agente`, em `agente_btp/agente.py`. Seu construtor prepara tudo o que uma análise vai precisar:

@@codigo agente_btp/agente.py Agente.__init__

::: leitura
- **Linhas 54–67** — estruturas internas: a **fila de comandos** (`queue.Queue`), travas para acesso concorrente (`threading.Lock`), eventos de sinalização (`threading.Event`) e listas usadas na preparação da fonte.
- **Linhas 68–69** — cria a **ponte** com a aba (`PonteAba`) e registra `_ao_observar` como a função a ser chamada quando a aba relatar consultas observadas na tela (usado na preparação, Capítulo 12).
- **Linha 70** — abre a **base de dados** (`Loja(ARQUIVO_LOJA)`, arquivo `dados/execucoes.sqlite3`). Se o arquivo não existir, é criado com as tabelas necessárias.
- **Linhas 75–91** — o dicionário `_estado`: tudo o que a tela mostra (fase, progresso, aviso, mensagens...). A tela lê esse estado a cada segundo.
- **Linhas 92–93** — se `config/api.json` foi gravado por uma versão anterior do programa, ele é apagado e a fonte é refeita na próxima análise (função `descartar_se_antiga` de `config.py`, que compara `VERSAO_CONFIG`).
- **Linha 94** — inicia a **linha de execução de comandos** (`_laco`), que vai rodar durante toda a vida do programa.
:::

### 10.5 A fila de comandos

O orquestrador executa um comando por vez, numa linha de execução própria. Isso é importante: o servidor web nunca executa uma análise diretamente; ele apenas **coloca um pedido na fila** e responde à tela imediatamente.

@@codigo agente_btp/agente.py Agente._laco

::: conceito
**Thread (linha de execução).** Um programa pode fazer várias coisas "ao mesmo tempo" dividindo o trabalho em linhas de execução. Aqui, uma thread atende a tela (as do Flask), outra executa comandos (`_laco`) e, durante uma análise, várias trabalham em paralelo (as do motor). Como elas compartilham dados, o código usa **travas** (`Lock`) para que duas threads não alterem o mesmo dado ao mesmo tempo.
:::

O laço é simples: espera um comando na fila (`self._fila.get()` bloqueia até chegar algo), marca o agente como ocupado, chama o método correspondente (`_cmd_executar`, `_cmd_retomar`, `_cmd_refazer`, `_cmd_preparar`, `_cmd_raiox`, `_cmd_sondar`) e, aconteça o que acontecer, marca o agente como livre. Uma exceção inesperada é registrada no log com o *traceback* completo (`log.exception`) e aparece como mensagem na tela — o laço nunca morre por causa dela.

O método que coloca comandos na fila é `enviar`. Um detalhe de projeto: para os comandos que iniciam análises, ele já marca o agente como ocupado **no momento do pedido**, e não só quando a fila o pegar — assim um segundo clique rápido em "Executar" não inicia outra análise.

@@codigo agente_btp/agente.py Agente.enviar

### 10.6 A criação do aplicativo web

@@codigo agente_btp/servidor.py criar_app 47-58

::: leitura
- **Linha 49** — lê o script da aba (`web/agente_btp.js`).
- **Linha 50** — `sincronizar_extensao`: copia esse script para dentro da pasta `extensao/` se a cópia estiver diferente. A extensão leva sua própria cópia do script (o Chrome não permite que ela execute código baixado), e esta linha garante que a cópia corresponda ao programa aberto.
- **Linhas 52–55** — calculam a **versão** do programa: uma impressão digital (SHA-1) de todos os arquivos `.py` do pacote e de todos os arquivos da pasta `web/`. Qualquer alteração em qualquer arquivo gera outra versão. A ponte guarda esse valor para detectar abas com script desatualizado.
- **Linha 57** — limita o tamanho dos pedidos a 20 MB (importação de planilhas).
:::

### 10.7 Configurações ausentes ou inválidas

| Situação | Comportamento | Onde |
|---|---|---|
| `config/api.json` não existe | Normal na primeira vez; a preparação o cria na primeira análise | `agente._garantir_preparo` |
| `config/api.json` de versão anterior ou ilegível | Apagado na inicialização; refeito na próxima análise | `config.descartar_se_antiga` |
| `config/preferencias.json` não existe ou ilegível | Valores padrão (por exemplo, 2 auditorias, 4 em paralelo) | `preferencias.carregar` |
| Preferência fora da faixa permitida | Ajustada ao limite e registrada como problema | `preferencias.validar` |
| Preferências da versão 1 com valores padrão antigos | Migradas para a política atual, com aviso no registro | `preferencias.migrar` |
| `config/tabela_materiais.json` não existe | Planilha sem Tipo Material e sem valores ("-") | `materiais.carregar` |
| Porta 8767 ocupada | O `.bat` encerra o processo que a ocupa antes de iniciar | `Abrir Agente BTP.bat`, linhas 6–7 |

### 10.8 Como o programa indica que está pronto

Três sinais, em ordem: a mensagem na janela preta ("Agente BTP aberto em http://127.0.0.1:8767..."), a abertura automática da tela no navegador e, na tela, o estado da conexão no rodapé do menu lateral — que começa em "Aguardando o BTP" e passa a "Aba conectada" quando a aba se anuncia, e a "BTP conectado · sessão válida" quando uma consulta confirma a sessão.

## 11. A relação entre o agente, o servidor e os sistemas externos {#cap11}

Este é o capítulo mais importante para entender **como o Agente BTP conversa com o BTP** — e por que ele conversa desse jeito.

### 11.1 A ideia: um mensageiro dentro da sessão

Imagine um escritório com uma sala de acesso restrito, onde só entra quem tem crachá. O agente não tem crachá — e não deve ter: ele não guarda senhas. Mas o usuário tem, e já está lá dentro (a aba do BTP, logada). A solução do projeto é deixar dentro da sala um **mensageiro** (o script `agente_btp.js`) que pergunta periodicamente ao agente, do lado de fora, "tem algo para eu buscar?". Quando há, o mensageiro busca com o crachá do usuário e entrega a resposta. O agente nunca entra na sala e nunca toca no crachá.

Tecnicamente, o "crachá" é o cookie de sessão que o navegador anexa automaticamente a toda consulta feita **de dentro da página do BTP** (uma consulta *same-origin*, ou "da mesma origem"). O script faz as consultas de dentro da página; por isso o cookie vai junto, sem que nenhum código do agente o leia.

### 11.2 Quem é servidor e quem é cliente

| Componente | Onde roda | Papel | Fala com |
|---|---|---|---|
| Tela (`index.html`) | Navegador do usuário | Cliente do servidor local | Servidor local (HTTP, JSON) |
| Servidor local (Flask) | Computador do usuário, `127.0.0.1:8767` | Servidor para a tela e para a aba | Tela; script da aba |
| Script da aba (`agente_btp.js`) | Dentro da aba do BTP | Cliente do servidor local **e** cliente do BTP | Servidor local; serviço OData do BTP |
| Serviço OData do Monitor | Nuvem SAP BTP | Servidor de dados | Script da aba |

Repare na consequência: o **único** componente que conversa com o BTP é o script da aba. O programa local (Python) não abre conexão nenhuma para a internet durante uma análise — todas as consultas saem do navegador.

@@figura comunicacao

### 11.3 Por que a aba pergunta e o programa não manda

Um navegador não aceita conexões de entrada: um programa externo não consegue "mandar" uma ordem para uma aba. Quem pode iniciar a conversa é a própria aba. Por isso a comunicação é invertida: a aba faz pedidos ao programa local, e o programa usa a técnica de **long-poll** (Capítulo 4) — segura o pedido aberto até ter uma tarefa para entregar, ou até 15 segundos.

### 11.4 A sequência de uma consulta

@@figura seq_consulta

**Leitura passo a passo.**

1. Cada **trabalhador** do script da aba envia `POST /api/agente/ciclo` ao programa local.
2. O servidor chama `PonteAba.proxima_tarefa`, que espera até haver tarefa na fila (ou até 15 s).
3. Enquanto isso, do lado Python, o cliente (`ClienteBTP`) precisa de um dado e chama `PonteAba.get(url)`, que coloca a tarefa na fila e fica esperando a resposta.
4. A ponte entrega a tarefa ao pedido que estava esperando: `{id, url, timeout}`.
5. O servidor responde ao ciclo da aba com essa tarefa.
6. O script faz `fetch` (o GET) ao serviço OData, com a sessão da aba.
7. O BTP responde (por exemplo, `200` com o JSON).
8. O script envia o **próximo** ciclo levando a resposta da tarefa anterior.
9. O servidor entrega a resposta à ponte (`entregar_resposta`), que acorda quem estava esperando.
10. O cliente recebe um objeto `Resposta` com situação, tipo de conteúdo e corpo.
11. O cliente classifica a resposta e extrai os registros (Capítulos 13 e 14).

Um detalhe elegante: cada ciclo **entrega a resposta anterior e pede a próxima tarefa** no mesmo pedido. Assim, cada trabalhador usa uma única conexão por vez com o programa local — importante porque o navegador limita a seis as conexões simultâneas a um mesmo servidor (`LIMITE_TRABALHADORES_ABA = 6` em `ponte.py`).

### 11.5 O lado Python: a ponte

A fila e a espera ficam em `agente_btp/ponte.py`. Primeiro, o lado que a aba chama:

@@codigo agente_btp/ponte.py PonteAba.proxima_tarefa

::: leitura
- **Linhas 85–90** — controle de versão: se a aba estiver com um script de outra versão (e não for o da extensão, marcado como `"ext"`), a ponte pede **uma** recarga. Se a mesma versão voltar, aceita — o comentário diz explicitamente: "nunca entra em laço".
- **Linhas 91–92** — se outra aba se conectou depois, esta é avisada de que foi substituída.
- **Linhas 93–95** — registra o "batimento" da aba (`_visto_em`): é assim que o programa sabe que a aba está viva.
- **Linhas 99–102** — a espera do long-poll: enquanto a fila estiver vazia e o tempo não acabar, a thread dorme em fatias de no máximo 1 segundo (`self._cond.wait`), sempre atualizando o batimento.
- **Linhas 104–108** — se há tarefa, ela sai da fila, é registrada como "entregue" e ganha um prazo (`_em_curso`) — o tempo máximo da consulta mais 10 segundos.
:::

Agora o lado que o cliente Python chama para fazer uma consulta:

@@codigo agente_btp/ponte.py PonteAba.get

A linha 163 é uma proteção de segurança: o endereço precisa começar com `/` e não com `//` — ou seja, precisa ser um caminho **do próprio site** em que a aba está. Um endereço absoluto para outro site é recusado antes de chegar à aba. As linhas 171–176 traduzem a falha de rede devolvida pela aba (situação `-1`) em exceções classificadas: tempo esgotado (`timeout_wo`) ou falha de rede (`falha_rede`).

@@codigo agente_btp/ponte.py PonteAba._executar

::: leitura
A espera pela resposta é feita em fatias de meio segundo (linha 203). A cada fatia, a ponte verifica se a aba **continua conectada**; se não, desiste na hora com `SessaoExpirada("aba_desconectada")`, em vez de esperar os 90 segundos inteiros. O bloco `finally` (linhas 211–217) garante que a tarefa seja removida de todas as estruturas aconteça o que acontecer — inclusive se a espera terminar com exceção.
:::

### 11.6 O que significa "aba conectada"

@@codigo agente_btp/ponte.py PonteAba.conectada

A aba é considerada conectada se falou com o programa há menos de 25 segundos (`TOLERANCIA_SILENCIO`) **ou** se está executando uma consulta ainda dentro do prazo. A segunda condição nasceu de um caso real registrado no comentário: em 27/10, quatro consultas lentas em paralelo deixaram a aba 26 segundos sem falar com o programa, e ela foi considerada desconectada — a análise pausou sem motivo. O relatório `docs/relatorio-correcao-raiz.md`, seção 7, documenta esse episódio.

### 11.7 O lado da aba: o script `agente_btp.js`

O script tem duas funções centrais. A primeira executa a consulta:

@@trecho agente_btp/web/agente_btp.js 183 205 "função executar"

::: leitura
- **Linhas 187–189** — recusa qualquer endereço que não comece com uma única barra (a mesma regra da ponte, verificada de novo do lado da aba).
- **Linhas 190–192** — cria um `AbortController` e um temporizador: se a consulta passar do tempo máximo recebido (`t.timeout`, padrão 60 s), ela é abortada e marcada com `TEMPO_ESGOTADO`.
- **Linhas 195–198** — o `fetch` em si: método **GET**, credenciais `same-origin` (o cookie da sessão vai junto, e só para o próprio site), `redirect: 'manual'` (um redirecionamento para o login não é seguido: vira sinal de sessão expirada), `cache: 'no-store'` (cada leitura vem do BTP, nunca de uma cópia guardada pelo navegador — essencial para que as auditorias sejam leituras de verdade).
- **Linha 199** — um redirecionamento é devolvido como `redir: true`, que o Python interpreta como sessão expirada.
- **Linhas 200–201** — a resposta normal: situação, tipo de conteúdo, corpo como texto e o cabeçalho `Retry-After` (pedido do servidor para esperar antes de tentar de novo).
:::

A segunda é o **trabalhador**, o laço que conversa com o programa local:

@@trecho agente_btp/web/agente_btp.js 231 280 "função trabalhador"

O trabalhador faz o ciclo descrito na [[fig:seq_consulta]] e trata três casos especiais: pedido de atualização (no máximo uma recarga por minuto, para nunca entrar em laço), substituição por outra aba (para e mostra o aviso "desconectado (outra aba do BTP está conectada)") e falhas de contato com o programa (depois de três falhas seguidas, mostra "sem contato com o programa local. Ele está aberto?" e tenta de novo com espera crescente, até 5 segundos).

### 11.8 Quantos trabalhadores e quem decide

Quando o script começa, ele se anuncia com `POST /api/agente/ola`. O servidor responde dizendo quantos trabalhadores abrir:

@@trecho agente_btp/servidor.py 91 99 "rota /api/agente/ola"

O número é o **paralelismo** configurado (padrão 4), limitado a 6 pelo limite de conexões do navegador. A linha 95 também avisa o orquestrador de que uma aba se conectou (`aba_conectada`), o que dispara a preparação (se ainda não houver fonte) ou uma sonda de sessão.

### 11.9 Proteções na comunicação

@@trecho agente_btp/servidor.py 66 81 "CORS e verificação de origem"

::: conceito
**CORS e origem.** Por segurança, um navegador só permite que uma página de um site (origem) leia respostas de outro site se esse outro site autorizar explicitamente. A aba do BTP é de um site; o programa local (`127.0.0.1:8767`) é outro. As linhas 66–75 autorizam **apenas** a origem do Launchpad do BTP (`ORIGEM_SAP`), e apenas nas rotas do canal da aba. As linhas 77–81 vão além: toda rota do canal exige que o cabeçalho `Origin` do pedido seja exatamente a origem do BTP — e o navegador sempre preenche esse cabeçalho sozinho, sem que uma página possa falsificá-lo. Assim, outra página aberta no navegador não consegue se passar pela aba do BTP. Os cabeçalhos `Access-Control-Allow-Private-Network` e `Access-Control-Allow-Local-Network` atendem à política do Chrome que exige autorização para um site público acessar um endereço local.
:::

### 11.10 Falha do agente ou falha do sistema externo?

| Sintoma | Quem falhou | Como o agente sabe | O que faz |
|---|---|---|---|
| Aba não fala com o programa há mais de 25 s (sem consulta em curso) | Aba (fechada, recarregada, congelada) | `PonteAba.conectada` | Pausa a análise por sessão; espera a aba voltar |
| Resposta 302/401/403 ou HTML de login | Sessão do BTP | `cliente.classificar` | Sonda; se confirmar, pausa; se não, falha só da WO |
| Resposta 429 ou 5xx | Servidor do BTP sobrecarregado ou com erro | `classificar` + `Ritmo` | Repete com espera crescente; ajusta o ritmo |
| Resposta 404 | Endereço do serviço mudou | `classificar` | Erro da consulta; se for geral, a fonte é refeita |
| JSON inválido | Serviço devolveu algo inesperado | `odata.extrair_pagina` | Erro da consulta (`parse_falhou`) com evidência |
| Exceção Python não prevista | O próprio agente | `except Exception` no motor | Falha do grupo com traceback (`excecao_app`); a análise continua |

### 11.11 O estado "honesto" da conexão

A tela não mostra "conectado" só porque a aba deu sinal de vida. A função `estado_conexao` distingue quatro situações:

@@codigo agente_btp/agente.py Agente.estado_conexao

Só é "conectado" (ponto verde) quando há batimento recente da aba **e** uma consulta ao BTP deu certo há no máximo 120 segundos (`VALIDADE_SESSAO_SEG`). O relatório de correção (seção 1, D3) explica a origem dessa regra: antes, a aba considerava "autenticado" se o objeto `sap.ushell.Container` existisse na página — mas ele continua existindo depois que a sessão expira.

A verificação ativa da sessão é a **sonda**: uma consulta real, a mesma usada para buscar a lista de uma WO.

@@codigo agente_btp/agente.py Agente.sondar

::: nobtp
A sonda não inventa endereço nenhum: usa o mesmo modelo de URL da lista (`api.url_lista`) com uma WO real da análise. Um detalhe importante está nas linhas 214–217: se o BTP responder com um erro de consulta (por exemplo, 400), isso significa que **o BTP respondeu** — não é uma página de login —, e portanto a sessão está viva. Só redirecionamento, 401, 403 ou HTML de login contam como sessão caída.
:::

## 12. Como o agente acessa a fonte de informações {#cap12}

O capítulo anterior mostrou **como** as consultas chegam ao BTP. Este mostra **quais** consultas o agente faz e como ele descobre os endereços certos — o que o projeto chama de **preparação da fonte de dados** —, além de como o script chega à aba.

### 12.1 Como o script chega à aba

@@figura conexao_aba

A extensão tem dois arquivos ativos. O `no_btp.js` roda em toda página do domínio do Launchpad e pede ao service worker que conecte; o `fundo.js` faz a injeção:

@@trecho extensao/fundo.js 26 36 "função injetar"

A chave está na opção `world: 'MAIN'`: o script é colocado no mesmo ambiente de JavaScript da página do BTP — onde a sessão do usuário vale —, e não no ambiente isolado em que extensões normalmente rodam. Antes de injetar, a função verifica se a página já tem um agente ativo (`window.__btpAtivo`) ou se foi substituída por outra aba, para não injetar duas vezes. O endereço do programa (`http://127.0.0.1:8767`) é fixo dentro da extensão: nenhuma página consegue alterá-lo.

### 12.2 O que precisa ser descoberto

Para ler uma WO, o agente precisa de duas consultas:

1. **A lista**: qual endereço devolve as linhas do Monitor para uma WO (por exemplo, `Pendencias?$filter=workOrderID eq '{wo}'`).
2. **Os itens**: como, a partir de uma linha da lista, chegar aos itens da janela Mensagens (por exemplo, `UltimoRetornoView?$filter=consolidID eq '{linha[consolidID]}' and workOrderID eq '{linha[workOrderID]}' and status eq 'E'`).

Além disso, precisa saber o **nome técnico** de cada campo (material, fornecedor, quantidade faltante, mensagem). Tudo isso fica gravado em `config/api.json`, representado em Python pela classe `ApiConfig`:

@@codigo agente_btp/config.py ApiConfig

Os marcadores `{wo}` e `{linha[Campo]}` nas URLs são **modelos**: a cada consulta, são substituídos pela WO e pelos valores da linha da lista, com a codificação correta (função `odata.preencher_modelo`, Capítulo 13).

### 12.3 A ordem de tentativa

@@figura preparacao

A preparação tenta caminhos em ordem e para no primeiro que servir. O teste é sempre feito com as **cinco primeiras WOs válidas da própria lista do usuário**:

| Ordem | Caminho | Módulo | Como funciona |
|---|---|---|---|
| 0º | **Receita confirmada** | `autoconfig.receita_monitor` | Se o serviço da aba tiver os conjuntos `Pendencias` e `UltimoRetornoView` com os campos esperados, usa o caminho confirmado no tráfego real da tela |
| 1º | **Exploração pelos dados** | `exploracao.py` | Consulta cada conjunto de dados que tem campo de WO e escolhe o que devolve registros parecidos com a janela Mensagens |
| 2º | **Dicionário de dados** | `autoconfig.candidatos` | Lê o `$metadata` (a descrição formal do serviço) e monta combinações plausíveis de lista + itens |
| 3º | **Sondagem na linha** | `sondagem.py` | A partir de uma linha real da lista, procura os itens num link de navegação, dentro da própria linha ou num campo JSON |
| 4º | **Observação da tela** | `analisador.py` | Pede ao usuário que abra a janela Mensagens no BTP e aprende com as consultas que a tela faz (até 5 minutos) |

::: termo
**`$metadata` (dicionário de dados).** Todo serviço OData publica, no endereço `<serviço>/$metadata`, um documento XML que descreve seus conjuntos de dados, campos, tipos e chaves. O agente descobre quais serviços a página usa lendo a configuração do SAPUI5 já carregada (`fontesDeDados` em `agente_btp.js`) e, então, lê o `$metadata` de cada um.
:::

### 12.4 A receita confirmada

@@codigo agente_btp/autoconfig.py receita_monitor

::: leitura
- **Linhas 456–460** — a receita só se aplica se o serviço tiver os dois conjuntos e os campos `consolidID` e `workOrderID` (e `status` no conjunto de itens).
- **Linhas 462–465** — o mapeamento confirmado dos campos: `material`, `fornecedorID`, `quantidade`, `mensagem`. O comentário registra uma armadilha real: `quantidadeDoc` é outra coisa e **não** entra.
- **Linhas 473–477** — os modelos de URL. O filtro `status eq 'E'` é essencial: sem ele, vêm também linhas "Ok" que não aparecem na janela Mensagens (comentário na linha 449: "Sem o status 'E' vêm linhas 'Ok'").
- **Linha 481** — `campos_confirmados` marca que os nomes vieram da resposta real; com isso, a auditoria não considera a quantidade "ambígua" por existirem outros campos numéricos no registro.
:::

<span class="confirmado">CONFIRMADO</span> O comentário nas linhas 447–449 registra a origem da receita: "Confirmado pelas consultas da tela do BTP (DevTools, 15/10/2026): a janela 'Mensagens' do Monitor de Alertas lê UltimoRetornoView filtrando por consolidado + WO + status 'E' (erro)".

### 12.5 Como um caminho é validado

Nenhum caminho é aceito só porque "parece certo". A função `_validar` testa o caminho com as WOs da amostra e dá uma nota:

@@codigo agente_btp/agente.py Agente._validar

| Nota | Significado | Consequência |
|---|---|---|
| 2 | Trouxe os itens da própria WO (com material ou mensagem) | Caminho aceito e gravado |
| 1 | Nenhuma WO da amostra está na lista | Não há como julgar; guardado como "fraco" |
| 0 | Não serve (itens de outra WO, nenhum item em 2 WOs com alerta, erro) | Recusado |

As linhas 439–442 são uma proteção contra o pior erro possível — atribuir os itens de uma WO a outra: se os registros devolvidos contiverem um valor no formato de WO diferente da WO pedida, o caminho é recusado na hora.

### 12.6 A preparação em código

@@codigo agente_btp/agente.py Agente._preparar_com_wos 468-485

O trecho acima é o início da preparação: o "0º" passo, a receita. As linhas seguintes da mesma função (até a 562) seguem a ordem da tabela da seção 12.3. A função que coordena a preparação e a espera pela observação da tela é `_garantir_preparo`:

@@codigo agente_btp/agente.py Agente._garantir_preparo

Se nenhum caminho automático servir, a tela passa a mostrar, com contagem regressiva: "Ação necessária (uma única vez): na aba do BTP, filtre uma WO no Monitor de alertas e abra a janela 'Mensagens'". Enquanto isso, o script da aba observa as consultas da tela (ganchos instalados em `XMLHttpRequest` e `fetch`, função `instalarGanchos` de `agente_btp.js`), retirando antes cabeçalhos sensíveis como `cookie`, `authorization` e `x-csrf-token` (expressão `SENSIVEL`, linha 26 do script). A cada 20 segundos, a preparação tenta de novo — o usuário pode ter acabado de abrir o Monitor.

### 12.7 A conferência da amostra

Mesmo um caminho validado pode ser "parecido mas errado" — foi o que aconteceu duas vezes no BTP real (Capítulo 5). Por isso, antes do primeiro lote com uma fonte nova, o agente mostra ao usuário uma amostra real e só continua com a confirmação.

@@figura conferencia_amostra

@@codigo agente_btp/agente.py Agente._confirmar_fonte 272-302

::: leitura
- **Linha 278** — no máximo 6 fontes diferentes por execução.
- **Linhas 284–285** — se a fonte gravada não for a receita confirmada e a receita servir para estas WOs, ela substitui a fonte antes da amostra.
- **Linhas 286–290** — lê a amostra, muda a fase para `confirmando` (a tela mostra o cartão "Confira a amostra antes de continuar") e espera a resposta por até 30 minutos.
- **Linhas 296–302** — "Sim": a fonte é gravada como confirmada, junto com a escolha de qual mensagem vai para a planilha (do item, do alerta ou as duas).
:::

Quando a resposta é "não", a fonte é **recusada para sempre**: sua assinatura vai para `config/fontes_recusadas.json` (função `_rejeitar`) e ela nunca mais é testada.

### 12.8 O Raio-X da fonte

Para quando os dados não batem com o BTP, a tela de Configurações oferece o **Raio-X da fonte**: o usuário informa uma WO cuja janela Mensagens ele conhece, e o agente consulta tudo o que o serviço tem para ela, ao mesmo tempo em que observa as consultas da tela enquanto o usuário abre a janela. O resultado é um arquivo JSON em `saidas/` (módulo `raiox.py`). Ele não altera a fonte; serve de evidência para ajuste.

## 13. Como o agente encontra e extrai as informações {#cap13}

### 13.1 A ideia

Extrair os dados de uma WO é repetir, por consulta de dados, o que o usuário faria na tela: filtrar a lista pela WO, pegar cada linha encontrada e abrir os itens dela. A diferença é que, em vez de ler uma tela, o agente lê registros estruturados — e confere que cada registro pertence de fato à WO pedida.

### 13.2 De onde vêm as WOs

As WOs vêm do campo de texto, de uma planilha ou dos dois. A leitura está em `agente_btp/entrada.py`:

@@codigo agente_btp/entrada.py normalizar_wo

@@codigo agente_btp/entrada.py ler_wos_planilha

::: leitura
A planilha é lida com tolerância a variações de formato, em três tentativas (linhas 33–35 do comentário): uma coluna com cabeçalho exatamente "WO"; senão, um cabeçalho que contenha a palavra WO ("Nº WO", "WO Claro"); senão, qualquer célula no formato `5 dígitos|9 dígitos`. O cabeçalho é procurado nas 10 primeiras linhas de cada aba. WOs repetidas contam uma vez (`_sem_duplicadas`), mantendo a ordem. A leitura é feita em modo somente leitura (`read_only=True, data_only=True`): valores, nunca fórmulas.
:::

WOs fora do formato não são descartadas em silêncio: elas entram na análise e saem no relatório como "Número de WO inválido" (função `_invalida` de `processador.py`).

### 13.3 Montando o endereço da consulta

@@codigo agente_btp/odata.py preencher_modelo

A WO contém uma barra vertical (`|`), que não pode aparecer crua numa URL, e valores de dados podem conter aspas, que em OData precisam ser duplicadas. `codificar_valor` resolve as duas coisas: primeiro troca `'` por `''` (regra do OData), depois codifica todo caractere especial (`|` vira `%7C`). A WO `02235|230771207`, por exemplo, entra no filtro como `'02235%7C230771207'`.

### 13.4 A extração de uma WO, passo a passo

@@figura extracao

O método que lê uma WO é `ClienteBTP.ler_wo`, que busca a lista e entrega as linhas a `_montar`:

@@codigo agente_btp/cliente.py ClienteBTP._montar

::: leitura
- **Linhas 292–297** — **proteção contra itens de outra WO**: cada linha da lista tem seu campo de WO conferido; se for de outra WO (o servidor ignorou o filtro), a linha é descartada e um aviso é registrado.
- **Linha 299** — `_sem_consulta_repetida`: linhas da lista que levariam à **mesma** consulta de itens (por exemplo, duas linhas da WO com o mesmo consolidado) são lidas uma vez só. Essa regra corrigiu, em 27/10, 157 WOs que apareciam com linhas duplicadas na planilha real (`docs/relatorio-correcao-raiz.md`, seção 11).
- **Linhas 307–311** — para cada item, `ocorrencia()` monta os valores; um item cuja WO não seja a pedida também é descartado.
- **Linhas 312–320** — sem chave técnica do BTP, o item é identificado pelo **conteúdo** (sem quantidade, mensagem ou data de alteração), para que uma mudança de quantidade apareça como divergência, e não como "item novo".
- **Linhas 321–323** — o mesmo item chegando duas vezes conta uma vez (`duplicadas`).
:::

### 13.5 De um registro do BTP a um item

@@codigo agente_btp/cliente.py ClienteBTP.ocorrencia

Esta função transforma um registro bruto (um dicionário com os nomes técnicos do BTP) num conjunto de **valores** com nomes do agente (`material`, `fornecedor`, `quantidade_faltante`, `mensagem`...). Três detalhes merecem atenção:

1. **Identificadores como texto** (`odata.como_texto`) e **quantidade como número exato** (`odata.como_quantidade`), explicados no Capítulo 14.
2. **A mensagem que vai para a planilha** depende da escolha feita na conferência (`mensagem_origem`): do item, do alerta ou as duas, no formato `"<item> — Alerta: <alerta>"`. O texto do item é sempre guardado à parte em `mensagem_item`, porque é por ele que o filtro "Estoque indisponível" funciona (Capítulo 15).
3. **A chave do item**: se o serviço declara chaves (por exemplo, `consolidID` e `zeile`), elas identificam o item; senão, um resumo do conteúdo (SHA-1) que exclui os campos que podem mudar.

### 13.6 A leitura em lote

Ler uma WO por vez custa pelo menos duas consultas por WO (lista + itens), multiplicadas pelas auditorias. Para volumes de milhares de WOs, o agente consulta **várias WOs numa só requisição**, unindo os filtros com `or`:

@@figura lote

@@codigo agente_btp/cliente.py ClienteBTP.ler_lote

A resposta do lote precisa ser **repartida** por WO. Isso é feito pelas mesmas regras da leitura individual (o mesmo `_montar`). Se aparecer uma linha que não pertence a nenhuma WO pedida, a exceção `LoteInconsistente` é levantada e o grupo é refeito WO a WO.

Mas repartir corretamente não basta para confiar no lote: e se o servidor tratar o filtro com `or` de um jeito diferente? Por isso o processador **compara** a leitura em lote com leituras individuais da mesma WO:

@@codigo agente_btp/processador.py processar_grupo 258-294

::: leitura
- **Linhas 264–270** — quais WOs são conferidas: **todas** nos três primeiros grupos da análise (`verificar == "todas"`); depois, uma amostra de duas por grupo — uma com itens e uma sem —, em rodízio.
- **Linhas 273–276** — cada WO conferida é relida individualmente; se a "assinatura" (itens, valores, número de alertas) diferir da leitura em lote, `LoteDivergente` é levantada.
- **Linhas 277–283** — o lote é **desativado para a análise inteira**, o fato vai para o diagnóstico (`lote_desativado`) e o grupo é refeito WO a WO. O resultado continua exato; só fica mais lento.
- **Linhas 284–294** — outros problemas (URL longa demais, tempo esgotado no lote) reduzem o tamanho do lote pela metade nas próximas vezes; com tamanho 1, o lote é desativado.
:::

::: exemplo
Na execução real de 13.623 WOs descrita no diagnóstico enviado pelo usuário (Capítulo 1), o evento "Consulta em lote desativada nesta análise (leitura em lote diferente da leitura individual)" aparece um segundo depois do início. A análise seguiu WO a WO e terminou com 13.623 WOs validadas, a 5,27 WOs por segundo. Esse é exatamente o comportamento projetado: diante de qualquer diferença, o agente escolhe a exatidão em vez da velocidade.
:::

### 13.7 A extração inicial e as auditorias de uma WO

O fluxo completo de uma WO está em `processar_wo`:

@@codigo agente_btp/processador.py processar_wo

::: leitura
- **Linhas 143–146** — cada WO tem um **prazo** próprio (`tempo_max_wo_seg`, padrão 180 s), que limita a soma de todas as suas consultas.
- **Linhas 151–157** — **etapa A**, a extração inicial. Se a primeira leitura não encontrar nada e a preferência `wo_sem_resultado` for "reconsultar" (padrão), lê de novo antes de concluir que a WO não está no Monitor.
- **Linhas 166–180** — **etapa B**, as auditorias. Cada rodada é uma leitura nova; se a preferência "auditorias independentes" estiver ligada e o serviço permitir, as rodadas pares releem cada item pelo seu endereço próprio (chave), um caminho diferente do da lista. Uma falha numa auditoria não derruba a WO: vira uma leitura com erro, e a auditoria fica "não concluída".
- **Linha 181** — **etapa C** (`_concluir`): comparação, leituras extras em caso de divergência e situação operacional da WO.
:::

A função `_com_retentativa` (usada em todas as leituras) repete uma consulta que falhou por rede ou tempo até `repeticoes_falha` vezes (padrão 2), com espera de 1, 2 e no máximo 5 segundos — sem ultrapassar o prazo da WO.

### 13.8 Como o agente sabe que a extração produziu algo utilizável

| Verificação | Onde | O que acontece se falhar |
|---|---|---|
| A resposta é JSON/OData válido | `odata.extrair_pagina` | `ErroConsulta` (`parse_falhou`) |
| A resposta não é página de login | `cliente.classificar` | `SessaoExpirada` |
| Cada linha da lista é da WO pedida | `ClienteBTP._montar` | Linha descartada, aviso registrado |
| Cada item é da WO pedida | `ClienteBTP._montar` | Item descartado, aviso registrado |
| A leitura em lote equivale à individual | `processar_grupo` | Lote desativado; grupo refeito WO a WO |
| As leituras repetidas concordam | `auditoria.consolidar` | Estado divergente (Capítulo 14) |
| Os quatro campos vieram preenchidos | `auditoria.consolidar` | Estado incompleto |
| Mais de 50 páginas de resultado | `ClienteBTP._get` | Consulta interrompida com erro |
