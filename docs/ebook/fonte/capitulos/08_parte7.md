# Parte VII — Visão integrada do sistema {#parte7}

As partes anteriores examinaram o agente peça por peça. Esta última parte junta tudo: acompanha uma análise inteira num único exemplo, mostra o que acontece quando algo muda e termina com o mapa completo do conhecimento do projeto.

## 31. A execução completa explicada em um único exemplo {#cap31}

::: atencao
**Todos os dados deste capítulo são fictícios.** As WOs `11111|111111111`, `22222|222222222` e `33333|333333333`, o material `000000000012345678` e o fornecedor `0000099999` não existem no BTP. Os nomes de funções, arquivos e mensagens são os reais.
:::

@@figura seq_execucao

### 31.1 Estado inicial

- O programa está aberto (`Abrir Agente BTP.bat`), servindo a tela em `127.0.0.1:8767` (Cap. 10).
- A aba do BTP está logada e conectada pela extensão: quatro trabalhadores do `agente_btp.js` estão em ciclo com o servidor (Cap. 11).
- A fonte já foi preparada e confirmada em análises anteriores: `config/api.json` com `confirmada: true` e `versao: 6` (Cap. 12).
- Preferências padrão: 2 auditorias, 4 consultas em paralelo, lote de 20, filtro "Estoque indisponível" (Cap. 22).
- A tabela de valores está importada e contém o material `000000000012345678` com Tipo Material "CABO DROP" e valor unitário R$ 2,50 (fictício).

### 31.2 O evento inicial

O usuário cola as três WOs na tela e clica em **Executar análise**. A tela envia `POST /api/executar` com a lista.

### 31.3 Primeiro componente acionado

A rota `executar` (`servidor.py`) valida que não há análise em curso (senão, 409), normaliza as WOs (`entrada.ler_wos_texto`) e chama `agente.enviar("executar", ...)`. A thread do `Agente` (`_laco`) recebe o comando e executa `_cmd_executar` (Cap. 16.1–16.2).

### 31.4 Operações de preparação

1. `_garantir_preparo` verifica que a fonte existe e está confirmada — nada a fazer.
2. A análise é criada na base: `Loja.criar` grava as 3 WOs como `pendente`, com as preferências e a fonte **congeladas** na análise.
3. O histórico recebe a entrada "Em andamento" com o próximo número de análise.
4. `_rodar` cria o `Motor` e chama `executar`.

### 31.5 Comunicações

1. **Sonda inicial** (Cap. 26.5): o motor pede, pela ponte, uma consulta leve. A `PonteAba` coloca a tarefa na fila; um trabalhador da aba a recebe no próximo ciclo e executa um `fetch` GET na própria origem do BTP, com os cookies da sessão enviados pelo navegador. A resposta (200, JSON) volta pelo `POST /api/agente/ciclo` e é entregue a quem pediu.
2. **Leitura em lote** (Cap. 13): como há mais de uma WO válida, o processador monta uma consulta com filtro OR para as três WOs (`ClienteBTP.ler_lote`), seguida da consulta dos itens. Por ser um dos 3 primeiros grupos da análise, cada WO também é lida individualmente para verificar a equivalência.
3. **Auditorias**: mais duas leituras de cada WO, alternando nova consulta e releitura por chave (`auditorias_independentes`).

### 31.6 Dados obtidos (fictícios)

| WO | O que o BTP devolve |
|---|---|
| `11111|111111111` | Um item: material `000000000012345678`, fornecedor `0000099999`, quantidade `"2e+0"`, mensagem "Estoque indisponível" |
| `22222|222222222` | Nenhuma linha de alerta |
| `33333|333333333` | Um item com mensagem "Estoque insuficiente" e quantidade `"1e+0"` |

### 31.7 Transformações

- `odata.extrair_pagina` lê o JSON; `ClienteBTP.ocorrencia` mapeia os nomes de campo gravados na fonte.
- `como_texto` mantém `000000000012345678` como texto; `como_quantidade("2e+0")` devolve `2` (Cap. 14.3).

### 31.8 Validações

- WO 11111: as três leituras trazem os mesmos valores → item **Validado** (Cap. 14.5).
- WO 22222: as três leituras confirmam a ausência → situação "Não consta no Monitor", auditoria **Validado**, motivo `wo_inexistente` registrado no diagnóstico (categoria ENTRADA).
- WO 33333: item validado, mas a mensagem do item não é a do filtro.

### 31.9 Decisões e gravação

Cada WO é gravada assim que termina, numa transação (`Loja.concluir`): estado `validada` nas três. A tela, que consulta `GET /api/estado` a cada segundo, mostra 3/3.

### 31.10 Resultado gerado

`_planilha` gera `Análise N - 3 WOs.xlsx`:

| WO | Material SAP | Tipo Material | Fornecedor SAP | Qtd. faltante | Mensagem | Observação | Status |
|---|---|---|---|---|---|---|---|
| 11111\|111111111 | 000000000012345678 | CABO DROP | 0000099999 | 2 | Estoque indisponível | - | Validada |
| 22222\|222222222 | - | - | - | - | - | Nenhum alerta encontrado no Monitor para esta WO | Validada |
| 33333\|333333333 | - | - | - | - | - | Nenhum item com "Estoque indisponível". Outros itens no BTP, fora da planilha (...): ... (qtd 1): Estoque insuficiente | Validada |

Na aba **Materiais**: uma linha para o material, quantidade total 2, valor total R$ 5,00, valor unitário R$ 2,50.

### 31.11 Verificações finais

`verificar_planilha` reabre o arquivo e compara cada célula das abas Dados, Materiais e Tabela de valores com o esperado (Cap. 15.9). Sem diferenças, a análise é marcada como concluída no histórico, e a tela mostra o selo **Concluída** e o botão **Baixar relatório auditado**.

### 31.12 E se uma etapa falhasse?

| Etapa | Falha | Consequência (capítulo) |
|---|---|---|
| 31.3 | Outra análise em curso | 409; nada é criado (27.6) |
| 31.5 | Sessão expira durante a leitura | Pausa; WOs em andamento voltam a pendente; retomada automática após o login (26.5) |
| 31.5 | Lote diferente da leitura individual | Lote desativado nesta análise; segue WO a WO (13) |
| 31.5 | BTP responde 503 | Até 2 repetições com espera crescente; persistindo, falha da WO com `http_5xx` (26.3) |
| 31.8 | Quantidade muda entre leituras | +3 leituras extras; sem explicação do BTP, item **Divergente**, valor vazio e nota "revisar no BTP" (14.7) |
| 31.10 | Excel aberto no Excel com o mesmo nome | Erro registrado; análise preservada; regerar pelo Histórico (27.5) |

## 32. O que acontece quando algo muda {#cap32}

@@figura ciclo_mudanca

O diagrama resume o roteiro seguro já detalhado nos Capítulos 20 e 21. As seções a seguir aplicam esse roteiro a mudanças plausíveis na arquitetura real.

### 32.1 A estrutura da fonte de dados muda (o BTP é atualizado)

- **Sinais:** `http_404` em massa; amostra diferente da janela Mensagens; muitas WOs sem itens.
- **O que o agente já faz:** 404 em massa e ≥ 90% sem itens disparam a re-preparação.
- **Examinar:** `autoconfig.receita_monitor`, `exploracao.py`, `analisador.py`; os registros do diagnóstico.
- **Testar:** `test_preparo.py`; uma análise curta no BTP real com conferência atenta da amostra.
- **Efeitos colaterais:** se a nova receita for gravada no código, aumentar `VERSAO_CONFIG` para descartar as fontes antigas de todos os usuários.

### 32.2 Um campo relevante muda de nome ou de significado

- **Exemplo:** a quantidade passa a vir em `quantidadePendente`.
- **Examinar:** `campos.py` (pesos dos nomes de campo, `peso_nome`), `auditoria.consolidar` (regra de quantidade ambígua, linhas 134–140), `config/api.json` (campo `quantidade_faltante`).
- **Risco:** se o nome novo não indicar "faltante", os itens ficam **Incompletos** por ambiguidade — o comportamento seguro, mas que bloqueia a validação até o ajuste.
- **Testar:** `test_quantidade_ambigua_nao_e_validada` e os testes de mapeamento de campos.

### 32.3 A forma de autenticação muda

- **Exemplo:** o BTP passa a usar outro provedor de login, com outra página.
- **Examinar:** os padrões `_LOGIN` em `cliente.py` (linhas 46–47) e `classificar`.
- **Risco:** se a nova página de login vier como JSON ou sem as marcas conhecidas, a queda de sessão poderia ser lida como erro de consulta e virar falha de WO. A proteção do motor (sonda após erro de sessão) não ajudaria nesse caso, porque o erro nem seria classificado como sessão. <span class="interpretacao">INTERPRETAÇÃO TÉCNICA</span>
- **Testar:** `test_redirecionamento_e_sessao_expirada`, `test_r3_deteccao_de_sessao_morta` e `rodar_sessao.py`, com o novo formato reproduzido no SAP falso.

### 32.4 Uma dependência falha

- **flask:** sem ele, o programa não abre (erro na janela preta). **openpyxl:** sem ele, não lê planilhas de entrada nem gera o relatório.
- **Examinar:** `pip list`; a mensagem da janela; `requirements.txt`.
- **Correção:** apagar `.venv` e reabrir o `.bat`; se a versão nova for incompatível, fixar a anterior em `requirements.txt` (Cap. 28.3).

### 32.5 A estrutura do resultado final muda

Ver o exemplo completo do Capítulo 21.2. Pontos de atenção: `COLUNAS_DADOS`, listas posicionais em `gerar_planilha`, `verificar_planilha` (acompanha automaticamente se as funções de linha forem as mesmas), testes de estrutura e fórmulas dos usuários.

### 32.6 Uma nova regra operacional

- **Exemplo:** "WOs com mais de 10 itens indisponíveis devem ser destacadas".
- **Onde:** é uma regra de **apresentação**, não de auditoria: pertence a `exportacao.py` (por exemplo, a Observação ou a aba Análise), nunca a `auditoria.py`, que deve continuar decidindo apenas a fidelidade da leitura.
- **Testar:** novo teste em `test_auditoria.py` no padrão dos testes de planilha; conferir que `verificar_planilha` continua sem diferenças.

## 33. Mapa final do conhecimento do projeto {#cap33}

@@figura mapa_final

| Tema | Em uma frase | Onde aprofundar |
|---|---|---|
| Problema operacional | Conferir, WO a WO, os itens com "Estoque indisponível" na janela Mensagens do Monitor de Alertas do BTP e consolidá-los numa planilha | Caps. 1–2 |
| Arquitetura | Programa local em Python (Flask) + script na aba do BTP + extensão do Chrome; o BTP só é acessado pela aba logada | Caps. 6, 11 |
| Componentes principais | `servidor`, `agente`, `motor`, `processador`, `cliente`, `ponte`, `auditoria`, `loja`, `exportacao` | Caps. 16–17 |
| Fluxo dos dados | JSON do BTP → `Ocorrencia` → `Leitura` → `LinhaMensagem` → base SQLite → Excel | Caps. 13–15 |
| Comunicação externa | GET na própria origem do BTP feito pela aba, com fila *long-poll* entre a aba e o programa | Cap. 11 |
| Extração | Receita confirmada (`Pendencias` → `UltimoRetornoView`), em lote com verificação de equivalência ou individual | Caps. 12–13 |
| Processamento | Extração + 2 auditorias; divergência escalona para até 5 leituras e nunca é aprovada sem evidência do BTP | Cap. 14 |
| Resultados | Excel com Dados, Materiais, Análise, Rastreio e Tabela de valores, gravado em escrita contínua | Cap. 15 |
| Validação | Auditoria por item, transação por WO, conferência célula a célula do arquivo | Caps. 14, 25 |
| Execução | `.bat` → tela → conectar aba → WOs → amostra → acompanhar → baixar | Cap. 23 |
| Diagnóstico | Taxonomia de 24 motivos em três categorias, com evidência sem segredos e próxima ação | Caps. 26–27 |
| Manutenção | Testes (164 casos + roteiros de navegador), relatório de rodadas, Git, `VERSAO_CONFIG` | Caps. 24, 29 |

::: resumo
O Agente BTP não "raspa" a tela: ele pede ao próprio navegador logado que faça consultas somente leitura ao serviço de dados por trás do Monitor de Alertas. Lê cada WO mais de uma vez, só afirma o que se repetiu, grava cada resultado assim que termina e entrega uma planilha que ele mesmo confere depois de gravar. Quando algo dá errado, ele diz de quem é o problema e o que fazer.
:::

# Apêndices {#apendices}

## Apêndice A — Rotas do programa local {#apA}

Rotas definidas em `agente_btp/servidor.py` (função `criar_app`). As rotas `/api/agente/*` são usadas apenas pela aba do BTP e exigem a origem do *launchpad*; as demais são usadas pela tela.

| Método | Rota | Função |
|---|---|---|
| GET | `/` | A tela (`index.html`) com a versão do programa |
| GET | `/agente_btp.js` | O script injetado na aba |
| POST | `/api/agente/ola` | A aba se anuncia; recebe o número de trabalhadores |
| POST | `/api/agente/ciclo` | Ciclo *long-poll*: entrega uma resposta e recebe a próxima tarefa |
| GET | `/api/agente/tarefa` | Busca da próxima tarefa (modo alternativo ao ciclo) |
| POST | `/api/agente/tchau` | A aba se despede (fechamento ou recarga) |
| POST | `/api/agente/resposta` | Entrega de resposta (modo alternativo ao ciclo) |
| POST | `/api/agente/captura` | Consultas observadas durante o aprendizado pela tela |
| GET | `/api/favorito` | Código do favorito "Agente BTP" |
| POST | `/api/extensao/abrir` | Abre a pasta da extensão no Explorer (Windows) |
| GET | `/api/estado` | Estado completo para a tela (atualizado a cada segundo) |
| POST | `/api/executar` | Inicia análise (ou refaz uma, com `modo: refazer`) |
| POST | `/api/entrada/planilha` | Lê uma planilha de WOs enviada |
| GET | `/api/execucoes` | As 20 análises mais recentes da base (usadas para oferecer a retomada) |
| GET | `/api/execucoes/<run_id>/registro` | Eventos de uma análise |
| GET | `/api/execucoes/<run_id>/planilha` | Planilha parcial |
| GET | `/api/execucoes/<run_id>/relatorio` | Relatório regerado a partir da base |
| POST | `/api/fonte/mensagem` | Define qual mensagem vai para a planilha: `item`, `alerta` ou `ambas` |
| POST | `/api/fonte` | Resposta à conferência da amostra: `sim`, `outra`, `mostrar` |
| POST | `/api/fonte/refazer` | Descarta a fonte e prepara de novo |
| POST | `/api/raiox`, `/api/raiox/pronto` | Raio-X de uma WO (investigação da fonte) |
| POST | `/api/pausar`, `/api/retomar`, `/api/parar` | Controle da análise em curso |
| GET/POST/DELETE | `/api/materiais/tabela` | Consultar, importar ou remover a tabela de valores |
| GET/POST | `/api/configuracoes` | Ler e salvar as preferências |
| POST | `/api/configuracoes/padrao` | Restaurar os padrões |
| GET/DELETE | `/api/historico`, `/api/historico/<id>` | Listar, obter e apagar análises do histórico |
| GET | `/api/diagnostico` | Diagnóstico da análise atual ou de uma análise do histórico |
| GET | `/api/download/<nome>` | Download de um arquivo da pasta `saidas/` |

## Apêndice B — Configurações {#apB}

A referência completa das preferências, com rótulos, padrões e faixas, está no Capítulo 22.3; a da fonte de dados, no Capítulo 22.2. Os padrões podem ser restaurados pela tela (Configurações → restaurar padrões, rota `POST /api/configuracoes/padrao`).

## Apêndice C — Taxonomia de motivos do diagnóstico {#apC}

Tabela gerada a partir do dicionário `TAXONOMIA` de `agente_btp/diagnostico.py` (linhas 20–73), sem edição.

| Motivo | Categoria | Descrição | Próxima ação |
|---|---|---|---|
| `sessao_expirada` | FONTE | A sessão do BTP expirou (redirecionou ao login ou devolveu a página de login). | Faça login de novo na aba do BTP. O agente verifica a sessão e retoma sozinho. |
| `aba_desconectada` | FONTE | A aba do BTP foi fechada, recarregada ou parou de responder ao agente. | Abra a aba do BTP já logada e clique no favorito/extensão 'Agente BTP'. |
| `http_401` | FONTE | O BTP recusou a consulta por falta de autenticação (HTTP 401). | Faça login de novo na aba do BTP. O agente retoma sozinho. |
| `http_403` | FONTE | O BTP negou acesso (HTTP 403). | Se ocorreu em todas as WOs: faça login de novo. Se só em algumas: confira a permissão do usuário para essas WOs. |
| `http_404` | FONTE | O endereço de dados do BTP não existe mais (HTTP 404) — o app pode ter sido atualizado. | Use 'Refazer fonte' na engrenagem e execute de novo. |
| `http_429` | FONTE | O BTP pediu para reduzir o ritmo das consultas (HTTP 429). | Nada a fazer: o agente reduz o ritmo sozinho. Se persistir, diminua o paralelismo na engrenagem. |
| `http_5xx` | FONTE | O servidor do BTP falhou (HTTP 5xx). | Aguarde alguns minutos e use 'Retomar' (só as WOs que falharam são refeitas). |
| `falha_rede` | FONTE | Falha de rede entre a aba e o BTP. | Confira a conexão e use 'Retomar'. |
| `aba_sem_resposta` | FONTE | A aba do BTP não respondeu a tempo (aba congelada ou em segundo plano). | Deixe a aba do BTP aberta (sem minimizar) e use 'Retomar'. |
| `timeout_wo` | FONTE | Uma consulta de WO excedeu o tempo máximo configurado. | Use 'Retomar'. Se persistir, aumente o tempo de consulta na engrenagem. |
| `timeout_lote` | FONTE | Uma consulta em lote (várias WOs de uma vez) excedeu o tempo máximo. | Nada a fazer: o agente refaz essas WOs individualmente e reduz o tamanho do lote. |
| `wo_inexistente` | ENTRADA | A WO não tem alerta no Monitor de Alertas (confirmado em todas as leituras). | Confira se o número da WO está correto. Não é erro do agente. |
| `wo_invalida` | ENTRADA | O número da WO não está no formato 00000/000000000. | Corrija o número na lista de entrada. |
| `campo_ausente` | FONTE | O BTP não informou um dos campos obrigatórios do item. | Confira o item na janela Mensagens do BTP. |
| `quantidade_ambigua` | FONTE | O registro do BTP tem mais de um campo de quantidade (ex.: quantidade e quantidadeDoc) e nenhum é claramente o faltante: o agente não escolhe. Em muitas WOs, indica que a tabela usada NÃO é a da janela Mensagens. | Confira a tabela mostrada na conferência da amostra; se não for UltimoRetornoView, use 'Refazer a fonte de dados' na engrenagem e execute de novo. |
| `auditoria_incompleta` | FONTE | A conferência da WO não pôde ser concluída (veja a observação na planilha). | Confira a observação da WO na planilha; use 'Retomar' se foi falha passageira. |
| `parse_falhou` | FONTE | A resposta do BTP não é um JSON/OData reconhecível. | Se repetir, baixe este diagnóstico e envie para ajuste. |
| `erro_servico` | FONTE | O serviço do BTP respondeu com uma mensagem de erro. | Veja a evidência abaixo; se repetir, envie este diagnóstico. |
| `consulta_rejeitada` | APP | O BTP rejeitou a consulta montada pelo agente (HTTP 4xx). | Envie este diagnóstico para ajuste (a evidência mostra a URL recusada). |
| `divergencia_persistente` | FONTE | Os valores mudaram entre leituras e o BTP não registrou a alteração. | Confira manualmente no BTP os itens marcados na planilha. |
| `lote_desativado` | APP | A consulta em lote deu resultado diferente da consulta individual; o agente passou a consultar WO a WO. | Nada a fazer: o resultado segue exato, só mais lento. |
| `limite_execucao` | APP | O tempo máximo da análise (engrenagem) foi atingido. | Use 'Retomar' para continuar de onde parou. |
| `interrompida_usuario` | APP | A análise foi interrompida pelo usuário. | Use 'Retomar' para continuar de onde parou. |
| `excecao_app` | APP | Erro inesperado no programa. | Baixe este diagnóstico e envie para correção (contém o traceback). |

## Apêndice D — Fontes e bibliografia {#apD}

### D.1 Fontes primárias (do projeto)

| Fonte | Uso neste livro |
|---|---|
| Código-fonte em `agente_btp/` (25 módulos Python), `agente_btp/web/` e `extensao/` | Fonte principal de todas as descrições de funcionamento; cada trecho citado foi extraído automaticamente do arquivo, com as linhas indicadas |
| `testes/` e `testes/e2e/` | Comportamentos garantidos; resultados da execução de 29/10/2026 (Cap. 24.3) |
| Histórico Git do repositório | Cronologia (Cap. 5) |
| `docs/relatorio-correcao-raiz.md` | Medições de desempenho e memória, números das análises reais citadas |
| `README.md`, `config/api.exemplo.json` | Documentação anterior (parcialmente desatualizada, ver Cap. 30) |
| Diagnóstico e planilha de uma análise real, enviados pelo usuário durante o desenvolvimento (não versionados) | Números agregados citados nos Caps. 1, 15 e 27; nenhum dado identificável foi reproduzido |

### D.2 Documentação oficial das tecnologias

::: atencao
O ambiente em que este livro foi produzido **não tinha acesso à internet** (as tentativas de acesso foram recusadas pela política de rede da sessão). Os endereços abaixo são as referências oficiais indicadas para aprofundamento, mas **não foram acessados** durante a produção; por isso não há data de acesso. As afirmações sobre essas tecnologias foram restritas ao comportamento observado no código e nos testes executados.
:::

| Título | Organização | Endereço |
|---|---|---|
| Python 3 — documentação (módulos `sqlite3`, `decimal`, `threading`, `queue`) | Python Software Foundation | https://docs.python.org/3/ |
| Flask — documentação | Pallets Projects | https://flask.palletsprojects.com/ |
| openpyxl — modos otimizados (*read-only* e *write-only*) | openpyxl | https://openpyxl.readthedocs.io/en/stable/optimized.html |
| SQLite — Write-Ahead Logging | SQLite | https://www.sqlite.org/wal.html |
| OData — documentação das versões 2 e 4 | OASIS / OData.org | https://www.odata.org/documentation/ |
| Fetch API | MDN Web Docs (Mozilla) | https://developer.mozilla.org/docs/Web/API/Fetch_API |
| Cross-Origin Resource Sharing (CORS) | MDN Web Docs (Mozilla) | https://developer.mozilla.org/docs/Web/HTTP/CORS |
| Chrome Extensions — Manifest V3 e API `scripting` | Google | https://developer.chrome.com/docs/extensions/ |
| SAP Business Technology Platform | SAP | https://help.sap.com/docs/btp |
| pytest — documentação | pytest-dev | https://docs.pytest.org/ |
| Playwright para Python | Microsoft | https://playwright.dev/python/ |
