# Mapeamento do processo manual — Agente BTP

Documento derivado dos prints reais enviados pelo usuário. Nada aqui foi presumido
além do que está marcado como **[A CONFIRMAR]**.

## Plataforma

- SAP Fiori Launchpad no SAP BTP (Cloud Foundry, região `br10`).
- URL: `https://claro-prd.launchpad.cfapps.br10.hana.ondemand.com/site#Shell-home`
- Login: SSO SAML (`ipc.claro.com.br`) + MFA no celular — **sempre manual**.
- Acesso aos dados: hipótese forte de API OData (padrão Fiori). **[A CONFIRMAR via Network]**

## Fluxo manual (sequência real)

| # | Tela | Ação do usuário |
|---|------|-----------------|
| 1 | Launchpad (home) | Clica no tile **"Monitor de alertas — Itens pendentes"** (contador ~175 K) |
| 2 | Lista "Items (175.162)" | Clica no ícone de **filtro** (funil) da tabela |
| 3 | Diálogo "Filtrar por: WO" | Pesquisa a WO, marca o checkbox, clica **OK** |
| 4 | Lista filtrada "Items (1)" | Clica na linha (mensagem "Clique para mais detalhes") |
| 5 | Diálogo **"Mensagens"** | Lê os dados da WO — **é aqui que está a informação final** |

### Tela 2 — colunas da lista

WO · Consolidado nº · Fornecedor · Data · Mensagem · Ações (botão **Reprocessar**) · `>`

### Tela 5 — diálogo "Mensagens" (dados a extrair)

| Coluna | Exemplo | Tipo no Excel |
|--------|---------|---------------|
| WO | `88412\|126579742` | Texto |
| Material | `000000000022026169` | Texto (18 dígitos, zeros à esquerda) |
| Fornecedor | `0000137628` | Texto (10 dígitos, zeros à esquerda) |
| Quantidade faltante | `2` | Número |
| Mensagem | `Estoque indisponível` | Texto |

O diálogo tem barra de rolagem horizontal: **[A CONFIRMAR]** se existem colunas
adicionais à direita.

## Formato da WO

`PPPPP|NNNNNNNNN` — prefixo de 5 dígitos (pode começar com zero, ex.: `02235`),
`|`, número de 9 dígitos. Sempre tratado como texto.

## Regras de segurança (somente leitura)

O agente **nunca** aciona:

- **Reprocessar** (tela 2);
- **Efetuar Baixa** (app Consolidação);
- qualquer requisição que altere dados.

## Estados de resultado por WO

| Estado | Condição |
|--------|----------|
| `PROCESSADA` | WO encontrada no monitor e diálogo com ≥ 1 linha |
| `SEM_REGISTROS` | WO encontrada no monitor, diálogo sem linhas |
| `NAO_ENCONTRADA` | WO não consta no Monitor de alertas (sem pendência ou inexistente) |
| `WO_INVALIDA` | Formato de entrada inválido |
| `SESSAO_EXPIRADA` | Resposta redireciona ao SSO / HTML no lugar de JSON |
| `FALHA_COMUNICACAO` | Timeout, erro de rede, HTTP 5xx |
| `ERRO` | Qualquer outra falha inesperada (com motivo no log) |

Observação: o Monitor de alertas só lista WOs **com pendência**. Por isso não é possível
afirmar "WO inexistente" a partir dele — o estado correto é `NAO_ENCONTRADA`.

## Saída Excel (proposta, a aprovar)

- Aba **Resultado**: uma linha por linha do diálogo "Mensagens".
- Aba **Log**: WO · Data/Hora · Status · Registros · Observação.

## Pendências

- [ ] Confirmar endpoints OData (Network, filtro `batch`)
- [ ] Confirmar colunas ocultas no diálogo "Mensagens"
- [ ] Modelo da planilha de entrada (onde estão as WOs)
- [ ] WO com mais de um "Consolidado nº": processar todas as linhas?
