# Auditoria técnica, funcional e estrutural — Agente BTP

- **Data:** 30/10/2026
- **Versão auditada:** `main` em `cc6951c` (após o PR #1)
- **Ambiente da auditoria:** Linux, Python 3.13.16, Flask 3.1.3, openpyxl 3.1.5, Chromium (Playwright) com o SAP falso dos testes
- **Fora do alcance:** o BTP real e o Windows, aos quais este ambiente não tem acesso. Tudo o que depende deles está marcado como **não testado**.

Convenção usada no relatório:

- **COMPROVADO:** reproduzido com teste ou script; o comando está indicado.
- **SUSPEITA:** decorre do código, mas não foi reproduzido.
- **LIMITAÇÃO:** comportamento previsto pelo projeto, mas com risco.

---

## PARTE I — Diagnóstico geral

### Estado atual

O Agente BTP é um programa local em Python (Flask, `127.0.0.1:8767`) com três peças:

- uma tela web;
- um script injetado na aba do BTP já logada, pela extensão do Chrome ou por um favorito;
- uma base SQLite.

O programa não navega nem clica no BTP. Ele pede à aba que faça consultas `GET` ao serviço OData do Monitor de Alertas (`Pendencias` → `UltimoRetornoView`). Cada WO é lida pelo menos três vezes (extração e duas auditorias), e os resultados são comparados campo a campo. Por fim, gera um Excel e o confere célula a célula depois de gravar.

### Mapa de funcionamento

| Etapa | Como acontece | Onde está |
|---|---|---|
| 1. Início | `Abrir Agente BTP.bat` → `python -m agente_btp` → Flask e o navegador abrem a tela | `__main__.py`, `servidor.py` |
| 2. Autenticação | O agente **não** autentica. Usa a sessão do navegador do usuário; as consultas saem da aba logada (`credentials: 'same-origin'`) | `web/agente_btp.js` |
| 3. Acesso ao BTP | Fila *long-poll*: o programa enfileira consultas e a aba as executa e devolve as respostas | `ponte.py`, `agente_btp.js` |
| 4. Identificação dos dados | Receita confirmada (`Pendencias` → `UltimoRetornoView`, `status 'E'`); na primeira vez, o usuário confere uma amostra | `autoconfig.py`, `agente.py` |
| 5. "Navegação" | Não há navegação de tela: consultas OData montadas a partir de modelos de URL | `cliente.py` |
| 6. Extração | Em lote (filtro OR), verificada contra a consulta individual, ou WO a WO | `processador.py`, `cliente.py` |
| 7. Processamento | Leituras repetidas comparadas campo a campo; divergência nunca é aprovada sem evidência do BTP | `auditoria.py` |
| 8. Inconsistências | Estados Divergente, Incompleto e Não concluído, com nota por campo e motivo de diagnóstico | `auditoria.py`, `loja.py`, `diagnostico.py` |
| 9. Resultados | Excel com 5 abas, gravado em escrita contínua e conferido depois | `exportacao.py` |
| 10. Conclusão | Selo na tela, botão de download e registro no histórico | `agente.py`, `index.html` |

A descrição completa está no e-book: `docs/ebook/saida/Agente_BTP_Documentacao_Completa.pdf`.

### Principais problemas encontrados

1. **Segurança (ALTO):** a API local aceita pedidos de qualquer site aberto no mesmo navegador. Esses pedidos alteram as configurações (inclusive desligam as auditorias), param ou iniciam análises e descartam a fonte. O endereço chamado também não é conferido, o que permite a leitura de dados por *DNS rebinding*.
2. **Lógica da planilha (MÉDIO):** com "Exportar resultados não validados" desligado, a planilha afirma "Nenhum item com 'Estoque indisponível'" quando o item existe, mas foi omitido por estar divergente.
3. **Robustez (MÉDIO):** um histórico corrompido é descartado em silêncio e sobrescrito na análise seguinte. Uma tabela de valores corrompida derruba a tela de Configurações e a geração de toda planilha, com mensagem técnica.
4. **Problemas menores:**
   - valores em texto com separador ambíguo são lidos errado;
   - upload acima de 20 MB fica sem mensagem;
   - dependências sem versão máxima;
   - duas exceções ignoradas em silêncio;
   - README desatualizado.

O núcleo de extração, auditoria, base, retomada e conferência do Excel **passou** em todos os testes e não teve defeito comprovado nesta auditoria.

---

## PARTE II — Inventário de erros

### E1 — A API da tela aceita pedidos de outros sites (CSRF)

- **Arquivo:** `agente_btp/servidor.py`, rotas `POST`/`DELETE` fora de `/api/agente/*` (por exemplo, `/api/configuracoes`, `/api/parar`, `/api/fonte/refazer`, `/api/executar`).
- **Descrição:**
  - As rotas usam `request.get_json(force=True)`, que lê o corpo como JSON mesmo quando ele chega com `Content-Type: text/plain`, ou `request.form`.
  - Os dois formatos são "pedidos simples" para o navegador: qualquer página aberta no mesmo navegador consegue enviá-los sem verificação prévia.
  - Nenhuma dessas rotas confere o cabeçalho `Origin`.
- **Causa raiz:** a proteção de origem (`exigir_origem_sap`) existe só para o canal da aba. As rotas da tela partem do pressuposto de que apenas a própria tela as chama.
- **Consequência prática:** uma página maliciosa, aberta enquanto o agente roda, pode:
  - zerar as auditorias e esvaziar o filtro "Estoque indisponível";
  - parar a análise em curso;
  - descartar a fonte confirmada;
  - iniciar análises com a sessão do usuário (somente leitura).

  Os navegadores mais recentes começam a bloquear o acesso de sites públicos à rede local, mas o servidor não pode depender disso.
- **Reprodução (COMPROVADO):** cliente de teste do Flask, com `Origin: https://site-malicioso.example` e `Content-Type: text/plain`.
  - `POST /api/configuracoes` com `{"auditorias_por_wo":0,"mensagem_planilha":""}` → **200**; `preferencias.carregar()` passa a devolver `0` e `''`.
  - `/api/parar` → 200.
  - `/api/fonte/refazer` → 200.
- **Solução:** recusar (403) pedidos `POST`/`PUT`/`PATCH`/`DELETE` fora do canal da aba cujo `Origin` não seja o próprio programa (`127.0.0.1`/`localhost`), inclusive `Origin: null`. Pedidos sem `Origin`, como scripts locais e testes, continuam aceitos.

### E2 — O endereço chamado não é conferido (DNS rebinding)

- **Arquivo:** `agente_btp/servidor.py` (todas as rotas).
- **Descrição:** o servidor responde a qualquer cabeçalho `Host`.
- **Causa raiz:** ausência de lista de endereços permitidos.
- **Consequência prática:** uma página externa pode apontar o próprio domínio para 127.0.0.1 e, como passa a ser "a mesma origem", ler as respostas. Isso alcança:
  - `/api/historico` (listas de WOs);
  - `/api/diagnostico`;
  - `/api/download/<arquivo>` (planilhas com materiais, fornecedores e valores).
- **Reprodução (COMPROVADO):** `GET /api/estado` e `GET /api/historico` com `Host: atacante.example:8767` → **200** com os dados.
- **Solução:** aceitar somente `Host` igual a `127.0.0.1` ou `localhost` (qualquer porta); os demais recebem 403.

### E3 — "Nenhum item com 'Estoque indisponível'" falso quando a exportação de não validados está desligada

- **Arquivo:** `agente_btp/exportacao.py`, funções `_linhas_dados` (linha "WO sem itens na planilha") e `gerar_planilha` (aba Análise).
- **Descrição:** com `exportar_incompletos = False`, os itens não validados saem antes da separação por mensagem. Se o único item "Estoque indisponível" da WO estava divergente e havia outros itens validados de outra mensagem, a linha da WO diz "Nenhum item com 'Estoque indisponível'". Isso é falso: o item existe e foi omitido.
  - Quando não há outros itens, a mensagem "N item(ns) com divergência omitido(s)" conta também os itens de outra mensagem.
  - A célula de Observação não recebe o destaque amarelo, apesar de pedir conferência.
  - Na aba Análise, essas WOs entram como "só com itens de outra mensagem", e a situação por WO repete o erro.
- **Causa raiz:** a função `_itens_da_wo` aplica primeiro o filtro "validado" e depois o filtro de mensagem. A linha da WO perde a informação de que havia um item de estoque omitido.
- **Consequência prática:** um item de estoque que precisa de conferência fica descrito como inexistente, e quem lê a planilha não confere.
- **Reprodução (COMPROVADO):** `linhas_dados([r], False, None, "Estoque indisponível")`, com uma WO que tem um item "Estoque indisponível" divergente e um item "Estoque insuficiente" validado → Observação "Nenhum item com "Estoque indisponível". Outros itens…".
- **Solução:** contar os itens com a mensagem do filtro que foram omitidos por não estarem validados, dizer isso na Observação (com destaque) e usar a mesma regra na aba Análise.
- **Alcance:** só ocorre com a opção desligada; o padrão é ligada. Os relatórios com o padrão não são afetados.

### E4 — Histórico corrompido é descartado em silêncio e sobrescrito

- **Arquivo:** `agente_btp/historico.py`, função `_ler`.
- **Descrição:** se `historico.json` não for um JSON válido (por exemplo, por queda de energia durante uma gravação feita fora do programa, ou por edição manual), `_ler` devolve uma lista vazia, sem aviso. Na análise seguinte, `registrar` grava por cima apenas a nova análise.
- **Causa raiz:** tratamento de exceção que confunde "arquivo inexistente" com "arquivo ilegível".
- **Consequência prática:** perda de todo o histórico sem nenhum registro do ocorrido.
- **Reprodução (COMPROVADO):** com `historico/historico.json` contendo `{corrompido`, `historico.listar()` → `[]`.
- **Solução:** guardar o arquivo ilegível como `historico.corrompido-<data>.json` antes de seguir e registrar um aviso no log.

### E5 — Tabela de valores corrompida derruba a tela de Configurações e toda planilha

- **Arquivos:** `agente_btp/materiais.py` (`carregar`), `agente_btp/servidor.py` (`GET /api/materiais/tabela`), `agente_btp/web/index.html` (`carregarTabela`).
- **Descrição:** se `config/tabela_materiais.json` estiver ilegível:
  - `carregar` propaga um `JSONDecodeError`;
  - a rota responde **500 em HTML**, e a tela mostra "Não foi possível consultar a tabela (o programa está aberto?)", uma mensagem que engana;
  - toda geração de planilha falha com "Erro ao gerar ou gravar o Excel: Expecting value: line 1 column 1".
- **Causa raiz:** o erro de leitura não é convertido em erro de domínio (`TabelaInvalida`) com instrução ao usuário.
- **Consequência prática:** análises concluídas ficam sem planilha até alguém descobrir a causa. Nenhum dado é perdido, porque o relatório pode ser regerado depois.
- **Reprodução (COMPROVADO):** com `config/tabela_materiais.json` contendo `lixo`, `GET /api/materiais/tabela` → **500 text/html**.
- **Solução:** `carregar` levanta `TabelaInvalida` com a mensagem "A tabela de valores gravada está ilegível… importe-a de novo em Configurações"; a rota devolve JSON com `erro`, e a tela exibe esse erro.

### E6 — Valor unitário em texto com separador ambíguo é lido errado

- **Arquivo:** `agente_btp/materiais.py`, função `_valor`.
- **Descrição:**
  - `"1.500"` (mil e quinhentos, no formato brasileiro) é lido como **1,5**;
  - `"1,234.56"` (formato americano) é lido como **1,23456**.
- **Causa raiz:** só a vírgula é tratada como separador decimal; o ponto é sempre lido como decimal.
- **Consequência prática:** valor total da aba Materiais errado por fator de 1.000, sem nenhum aviso.
- **Reprodução (COMPROVADO):** `_valor('1.500')` → `1.500`; `_valor('1,234.56')` → `1.23456`.
- **Alcance:** a tabela real enviada (`TABELA_VALORES_07_2026.xlsx`, 1.305 linhas) tem **apenas células numéricas** (1.114 decimais e 191 inteiros). O defeito não a afeta; o risco é para tabelas futuras com valores digitados como texto.
- **Solução:**
  - com ponto e vírgula juntos, o último separador é o decimal;
  - só com pontos em grupos de três dígitos (`1.500`, `12.345.678`), o número é milhar no formato brasileiro;
  - nos demais casos, mantém-se o comportamento atual.

### E7 — Upload acima de 20 MB fica sem mensagem

- **Arquivo:** `agente_btp/servidor.py` (`MAX_CONTENT_LENGTH`).
- **Descrição:** o Flask responde 413 em HTML; a tela tenta ler JSON e não mostra o motivo.
- **Reprodução (COMPROVADO):** `POST /api/entrada/planilha` com 21 MB → `413 text/html`.
- **Solução:** responder ao 413 em JSON: `{"ok": false, "erro": "Arquivo maior que 20 MB…"}`.

### E8 — Dependências sem versão máxima

- **Arquivo:** `requirements.txt` (`flask>=3.0`, `openpyxl>=3.1`).
- **Consequência:** o `.bat` roda `pip install` a cada abertura. Uma versão nova e incompatível de Flask ou openpyxl seria instalada automaticamente e poderia quebrar o programa sem nenhuma mudança no código. **SUSPEITA:** não reproduzido, porque não existe hoje uma versão incompatível.
- **Solução:** limitar à série testada: `flask>=3.0,<4` e `openpyxl>=3.1,<3.2`.

### E9 — Exceções ignoradas em silêncio

- **Arquivo:** `agente_btp/servidor.py`, em `sincronizar_extensao` e `abrir_pasta_extensao` (`except OSError: pass`).
- **Consequência:** se a cópia do `agente_btp.js` para a pasta da extensão falhar (permissão, antivírus), a extensão continua com o script antigo e nada fica registrado. **SUSPEITA**, não reproduzido.
- **Solução:** registrar o erro no log.

### E10 — Documentação do repositório desatualizada

- **Arquivo:** `README.md`, linha 30 ("conferida N vezes (padrão 5)").
- **Descrição:** o padrão atual é 2 auditorias, com até 3 leituras extras em caso de divergência (`preferencias.py`).
- **Solução:** corrigir o texto.

---

## PARTE III — Classificação de gravidade

| Gravidade | Itens | Justificativa |
|---|---|---|
| CRÍTICO | — | Nenhum defeito comprovado compromete, sozinho e em uso normal, a integridade dos dados extraídos |
| ALTO | E1, E2 | Permitem que um terceiro altere as regras de conferência ou leia dados corporativos |
| MÉDIO | E3, E4, E5 | Informação falsa na planilha (numa configuração específica), perda silenciosa de histórico, falha geral da planilha |
| BAIXO | E6, E7, E8, E9, E10 | Risco latente (sem efeito nos dados atuais), mensagens de erro, manutenção e documentação |

Sobre E1 não ser CRÍTICO: zerar as auditorias **não** gera dados falsamente validados. Os itens passam a "Incompleto" ("Auditoria desativada nas configurações"), o que fica visível na planilha. O dano é real, mas aparece para quem lê.

---

## PARTE IV — Funcionalidades

| Funcionalidade | Estado | Evidência | Problema identificado |
|---|---|---|---|
| Inicialização e tela | Aprovada | `rodar_tela.py`: 136/136 verificações | — |
| Conexão com a aba (favorito) | Aprovada | `rodar_aba.py` | — |
| Conexão pela extensão | Aprovada | `rodar_extensao.py` | E9 (cópia do script sem log) |
| Preparação e confirmação da fonte | Aprovada (simulada) | `test_preparo.py` (21 testes) | Depende de conferência humana (limitação documentada) |
| Extração em lote com verificação | Aprovada | `test_r4_equivalencia_lote…`, `test_lote_*` | — |
| Auditoria (leituras repetidas) | Aprovada | `test_auditoria.py` (49 testes) | — |
| Sessão expirada: pausa e retomada | Aprovada | `rodar_sessao.py`: 500 WOs, queda com 60 feitas, nenhuma falha | — |
| Retomada após fechar o programa | Aprovada | `test_programa_fechado_no_meio_retoma_de_onde_parou` | — |
| Planilha: abas, filtro e conferência | **Parcial** | testes de planilha | E3 (com a exportação de não validados desligada) |
| Tabela de valores | **Parcial** | `test_materiais.py` | E5, E6 |
| Histórico | **Parcial** | `test_historico.py` | E4 |
| Diagnóstico | Aprovada | `test_toda_wo_nao_validada_tem_motivo_no_diagnostico` | — |
| Segurança da API local | **Reprovada** | Reprodução E1/E2 | E1, E2 |
| Upload de planilha de WOs | Parcial | Reprodução E7 | E7 |
| Uso no BTP real | **Não testada** | Sem acesso a partir deste ambiente | — |
| Uso no Windows (`.bat`, console) | **Não testada** | Sem Windows neste ambiente | — |

---

## PARTE V — Causas raiz

1. **Confiança implícita na rede local (E1, E2).** O servidor protege bem o canal com a aba do BTP (`exigir_origem_sap`, CORS restrito), mas trata as rotas da tela como se só a própria tela pudesse chamá-las. "Escutar só em 127.0.0.1" impede o acesso de outras máquinas, mas não o de outras páginas no mesmo navegador.
2. **Ordem de filtros que perde informação (E3).** `_itens_da_wo` aplica primeiro "validado" e depois "mensagem". A frase que explica a linha da WO só enxerga o resultado final e não sabe que algo foi descartado por estar divergente.
3. **Erros de leitura sem tradução para o domínio (E4, E5).** A leitura de arquivos JSON locais não distingue "não existe" de "existe, mas está ilegível". Em um caso o erro é engolido; no outro, chega cru ao usuário.
4. **Entrada livre sem formato definido (E6).** A tabela de valores aceita texto, mas a interpretação dos separadores só cobre o formato brasileiro com vírgula.
5. **Ambiente instalado a cada abertura (E8).** O `.bat` atualiza dependências sem limite de versão. O comportamento do programa pode mudar sem que o código mude.

---

## PARTE VI — Plano de correção

1. **Críticos:** nenhum.
2. **Alta gravidade:**
   - E1: verificação de `Origin` nas rotas de alteração;
   - E2: lista de endereços permitidos (`Host`).
3. **Média gravidade:**
   - E3: Observação e aba Análise com os itens omitidos;
   - E4: cópia de segurança do histórico ilegível;
   - E5: erro claro para a tabela ilegível.
4. **Robustez e mensagens:**
   - E6: separadores de milhar e decimal;
   - E7: 413 em JSON;
   - E9: registrar no log.
5. **Estruturais e manutenção:**
   - E8: versões limitadas;
   - E10: README.
6. **Melhorias futuras (não executadas, sem defeito comprovado):**
   - rotação do log `logs/agente_btp.log`;
   - limpeza de análises antigas na base;
   - colar WOs copiadas de várias colunas do Excel (hoje cada linha precisa ter só a WO);
   - pontuação ".;" na Observação quando há nota de conferência e nota de outros itens.

Cada correção recebe um teste que falha antes e passa depois. Ao final, rodam de novo a suíte completa e os testes de navegador.

---

## PARTE VII — Parecer final

- **O agente está funcional?** Sim. O fluxo completo (preparo, extração, auditoria, base, retomada, planilha conferida) funciona em todos os testes automatizados e nos testes de navegador com o SAP simulado.
- **É confiável para uso profissional?** Para a finalidade de leitura e conferência, sim, **depois** das correções E1 e E2. Antes delas, a API local pode ser manipulada por uma página aberta no mesmo navegador.
- **Os resultados são confiáveis?** Com as configurações padrão, sim:
  - cada valor marcado como "Validada" foi lido de forma idêntica pelo menos três vezes;
  - divergências nunca são aprovadas sem evidência do BTP;
  - o arquivo é conferido depois de gravado.

  Com "Exportar resultados não validados" desligado, a planilha pode descrever errado WOs com itens divergentes (E3).
- **O que corrigir imediatamente:** E1, E2 e E3.
- **Riscos que permanecem depois das correções:**
  - a escolha da fonte depende da conferência humana da amostra;
  - leituras repetidas medem a estabilidade da leitura, não a correção do dado na origem;
  - nada foi testado no BTP real nem no Windows;
  - análises gravadas antes de 30/10 não têm `mensagens_lidas`.
- **O que falta para considerar o sistema estável:**
  1. Aplicar as correções E1–E10 com seus testes.
  2. Rodar uma análise curta no BTP real (por exemplo, 20 WOs conhecidas) e conferir 5 delas manualmente na janela Mensagens.
  3. Repetir a conferência depois de cada atualização do BTP.

### Dúvidas de regra de negócio (não alteradas)

1. **Divergência explicada pelo BTP continua "Divergente".**
   - Quando o próprio BTP registra a alteração do item durante a análise, o agente aceita o valor atual, mas mantém o item como Divergente.
   - Com isso, o item fica fora da soma da aba Materiais.
   - O teste `test_divergencia_resolvida_pelo_registro_de_alteracao_do_btp` exige esse comportamento.
   - **A confirmar:** deve contar como Validado?
2. **Item sem mensagem própria.**
   - Um item cuja janela Mensagens não tem texto (a planilha mostraria a mensagem do alerta) fica fora da planilha com o filtro "Estoque indisponível".
   - **A confirmar** se esse caso existe no BTP real e como deve ser tratado.
