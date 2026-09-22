# SPEC — Flight Price Tracker

Rastreador de preços de passagens aéreas. Roda uma vez por dia via GitHub
Actions, consulta o preço de rotas configuradas, grava o histórico em SQLite
e envia um e-mail quando algum preço cai.

## 1. Escopo e decisões fixas

- Python 3.12, sem framework web.
- Coleta via interface `PriceProvider` (Protocol) com duas implementações:
  `MockProvider` (dados determinísticos, para testes e dry-run) e
  `SerpApiProvider` (Google Flights via [SerpApi](https://serpapi.com/google-flights-api)).
  Playwright fica fora do escopo desta versão (YAGNI); a interface permite
  adicioná-lo depois sem alterar o resto do código.
- Persistência: `sqlite3` da stdlib (não SQLAlchemy). Justificativa: duas
  tabelas, meia dúzia de queries, sem relacionamentos — o ORM adicionaria
  uma camada sem benefício proporcional neste tamanho, e `sqlite3` permite
  testes rápidos em `:memory:`.
- Alerta quando `price < previous` OU `price < target`, nunca repetindo
  e-mail para o mesmo valor já notificado (`last_notified_price`).
- E-mail via `smtplib` (Gmail, senha de app), um único e-mail consolidado
  por execução, `multipart/alternative` (HTML + texto).
- Execução agendada via GitHub Actions (`schedule` cron); segredos em
  GitHub Secrets.
- Config de rotas em `config/routes.yaml`.
- Qualidade: pytest, ruff (lint + format), mypy `--strict` em `src`,
  cobertura de testes ≥ 80%.
- Estrutura de pastas: `src` layout (`src/flight_tracker/` + `tests/` +
  `pyproject.toml`).
- `data/prices.db` é versionado no repositório (commitado pelo workflow
  depois de cada execução bem-sucedida), marcado como binário via
  `.gitattributes` para não gerar diffs textuais.

## 2. Estrutura de pastas

```
flightSearch/
├─ pyproject.toml
├─ .gitattributes
├─ config/routes.yaml
├─ data/                       # prices.db vive aqui (versionado)
├─ src/flight_tracker/
│  ├─ __main__.py              # CLI: python -m flight_tracker [--dry-run] [--config] [--db]
│  ├─ config.py                # YAML + env -> dataclasses validadas
│  ├─ models.py                # Route, PriceQuote, Alert
│  ├─ providers/
│  │  ├─ base.py               # PriceProvider (Protocol), ProviderError
│  │  ├─ mock.py                # MockProvider
│  │  └─ serpapi.py             # SerpApiProvider
│  ├─ storage.py                # sqlite3: schema, transação, queries
│  ├─ alerts.py                  # evaluate() — função pura
│  ├─ notifier.py                # build_message() + send()
│  └─ runner.py                  # orquestração, exit codes
├─ tests/
│  ├─ test_alerts.py
│  ├─ test_storage.py
│  ├─ test_providers_serpapi.py
│  ├─ test_notifier.py
│  └─ test_runner.py
└─ .github/workflows/
   ├─ ci.yml
   └─ track.yml
```

## 3. Modelo de dados

### 3.1 Domínio (`models.py`)

```python
@dataclass(frozen=True)
class Route:
    key: str                       # identificador único, ex.: "GRU-LIS"
    origin: str                    # IATA, 3 letras
    destination: str               # IATA, 3 letras
    departure_date: date
    return_date: date | None
    target_price_cents: int | None # > 0 quando presente

@dataclass(frozen=True)
class PriceQuote:
    route_key: str
    price_cents: int
    currency: str                  # ex.: "BRL"
    provider: str                  # "mock" | "serpapi"
    fetched_at: datetime           # UTC, timezone-aware

@dataclass(frozen=True)
class Alert:
    route: Route
    quote: PriceQuote
    previous_price_cents: int | None
    reason: Literal["drop", "below_target", "drop_and_below_target"]
```

Preços são sempre inteiros em centavos (evita erro de ponto flutuante).

### 3.2 Config (`config/routes.yaml`)

```yaml
routes:
  - key: GRU-LIS
    origin: GRU
    destination: LIS
    departure_date: "2026-12-10"
    return_date: "2026-12-20"      # opcional
    target_price_cents: 350000      # opcional
```

Regras de validação (todas verificáveis por teste):
- `origin`/`destination`: exatamente 3 letras maiúsculas (`A-Z`).
- `departure_date`/`return_date`: formato ISO-8601 (`YYYY-MM-DD`); se
  `return_date` presente, deve ser `>= departure_date`.
- `target_price_cents`: se presente, inteiro `> 0`.
- `key`: único dentro do arquivo; se ausente, é derivado como
  `f"{origin}-{destination}-{departure_date}"`.
- Arquivo vazio ou sem a chave `routes` é erro de config (exit 1).

### 3.3 Schema SQLite (`storage.py`)

```sql
CREATE TABLE IF NOT EXISTS price_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    route_key TEXT NOT NULL,
    price_cents INTEGER NOT NULL,
    currency TEXT NOT NULL,
    provider TEXT NOT NULL,
    fetched_at TEXT NOT NULL        -- ISO-8601 UTC
);

CREATE TABLE IF NOT EXISTS route_state (
    route_key TEXT PRIMARY KEY,
    last_notified_price_cents INTEGER,
    last_queried_date TEXT,         -- ISO-8601 date (UTC), data da última
                                     -- consulta bem-sucedida ao provider real
    updated_at TEXT NOT NULL        -- ISO-8601 UTC
);

CREATE INDEX IF NOT EXISTS idx_price_history_route_fetched
    ON price_history (route_key, fetched_at DESC);
```

## 4. Fluxo de execução (`runner.py`)

Toda a execução roda dentro de **uma única transação SQLite**
(`BEGIN` no início; `COMMIT` só depois de qualquer envio de e-mail
necessário ter sido concluído com sucesso; `ROLLBACK` em qualquer falha
fatal). Isso vale tanto localmente quanto no Actions.

Para cada rota com `departure_date >= hoje`:

1. **Ler o preço anterior primeiro.** Antes de inserir a cotação atual,
   consultar `price_history` pela cotação mais recente da rota
   (`ORDER BY fetched_at DESC LIMIT 1`) e guardar como `previous`. Esta
   leitura **precisa** ocorrer antes do `INSERT` da cotação atual — é um
   requisito explícito (ver §6.1), com teste dedicado, porque se a leitura
   ocorrer depois da escrita, `previous` seria a própria cotação recém
   inserida e `drop` nunca dispararia.
2. **Guard entre execuções (só se aplica a `SerpApiProvider`; `MockProvider`
   ignora este guard).** Ler `route_state.last_queried_date` da rota. Se
   for igual à data corrente (UTC), pular a chamada ao provider — não
   conta como falha de provider (não afeta o exit code) e não gera nova
   linha em `price_history` para esta execução; segue para a rota
   seguinte. Isto existe para não estourar a cota mensal da SerpApi
   (250 buscas/mês) quando `workflow_dispatch` é disparado manualmente
   no mesmo dia do cron. Requisito testável explícito (ver §6.4).
3. Chamar `provider.get_price(route)`.
   - Sucesso: inserir `PriceQuote` em `price_history` e atualizar
     `route_state.last_queried_date` para a data corrente (UTC), na
     mesma transação — se a transação sofrer `ROLLBACK` (ver passo 5),
     esta atualização também é desfeita, preservando a atomicidade.
   - Falha (`ProviderError`): registrar aviso, pular a rota (sem
     atualizar `last_queried_date`, já que não houve sucesso), marcar a
     execução para terminar com exit code 2 (ver §7), e continuar para a
     próxima rota.
4. Ler `last_notified_price_cents` de `route_state` (ou `None` se a rota
   é nova).
5. Chamar `alerts.evaluate(quote, previous, route.target_price_cents,
   last_notified)`. Se retornar um `Alert`, acumular numa lista.

Rotas com `departure_date < hoje` são puladas com aviso e não contam como
falha (não afetam o exit code).

Depois de processar todas as rotas:

6. Se houver alertas: montar o e-mail (`notifier.build_message`) e enviar
   (`notifier.send`).
   - Envio falhou: `ROLLBACK` da transação inteira (nada é persistido,
     nem `price_history` nem `route_state` — incluindo os
     `last_queried_date` já atualizados no passo 3 desta execução) e a
     execução termina com exit code 1.
   - Envio bem-sucedido (ou não havia alertas): para cada rota alertada,
     atualizar `route_state.last_notified_price_cents` para o preço da
     cotação atual.
7. `COMMIT`.

### 4.1 Dry-run (`--dry-run`)

- Usa `MockProvider` (mesmo que `SERPAPI_API_KEY` esteja configurada).
- Usa banco `:memory:`, a menos que `--db` seja passado explicitamente.
- Não requer nenhuma variável de ambiente de SMTP.
- Em vez de enviar, **imprime** o `EmailMessage` renderizado (texto) no
  stdout — ou uma linha "nenhuma queda de preço" se não houver alertas.

## 5. Providers

### 5.1 `PriceProvider` (Protocol, `providers/base.py`)

```python
class PriceProvider(Protocol):
    def get_price(self, route: Route) -> PriceQuote: ...

class ProviderError(Exception): ...
```

### 5.2 `MockProvider`

- Preço determinístico por `route.key` + data corrente, permitindo
  também injeção de uma sequência de preços fixa nos testes (para
  simular quedas, subidas e repetições).

### 5.3 `SerpApiProvider`

- Chama `engine=google_flights` (SerpApi), `currency=BRL`, timeout de
  30s.
- Preço final = menor valor entre `best_flights` e `other_flights` na
  resposta.
- Retry: 1 retentativa em caso de 5xx ou timeout; qualquer novo erro
  vira `ProviderError`.
- Erros 4xx, resposta sem campo de voos, ou campo `error` no JSON viram
  `ProviderError` imediatamente (sem retry).
- **Guard interno de retry (por rota, dentro de uma única chamada
  `get_price`):** o guard interno que controla a retentativa (acima) só
  considera a rota "resolvida" **depois** de uma resposta bem-sucedida
  (HTTP 2xx com voos válidos no corpo). Se a primeira tentativa falhar
  com 5xx/timeout, esse estado interno não pode ter sido marcado ainda
  — senão a própria retentativa fica bloqueada por engano dentro da
  mesma chamada. Requisito testável explícito (ver §6.2). Este guard
  vive inteiramente dentro do `SerpApiProvider` e não tem memória entre
  chamadas.
- **Guard entre execuções (dia UTC, por rota, persistido):** implementado
  no `runner`/`storage`, não no provider — ver §4 passo 2 e §6.4. Impede
  uma segunda consulta real à SerpApi para a mesma rota no mesmo dia
  UTC, mesmo em execuções separadas do processo (ex.: cron + disparo
  manual no mesmo dia). Existe para não estourar a cota mensal de 250
  buscas da SerpApi.
- Nunca loga a API key (nem em erro, nem em debug).

## 6. Requisitos testáveis específicos

### 6.1 Ordem leitura-antes-de-escrita (`storage.py` / `runner.py`)

**Requisito:** dentro do processamento de uma rota, a leitura de
`previous` (cotação anterior mais recente) deve ocorrer antes do
`INSERT` da cotação atual na mesma transação.

**Critério de aceite / teste:** um teste que insere uma cotação prévia
(ex.: 500000 centavos), processa uma nova cotação mais barata (ex.:
400000 centavos) através do fluxo completo (não chamando as queries
isoladamente) e verifica que `Alert.reason` inclui `"drop"`. Um teste
adicional (regressão direta) chama a função de processamento de rota do
`runner` com um storage instrumentado (spy) que registra a ordem das
chamadas (`read_latest_price` antes de `insert_price_quote`) e falha se
a ordem for invertida.

### 6.2 Guard de dedupe do SerpApiProvider

**Requisito:** o guard interno que evita consultar a mesma rota duas
vezes na mesma execução do processo só é marcado como "consultado" após
uma resposta bem-sucedida.

**Critério de aceite / teste:** um teste que configura um
`httpx.MockTransport` para responder 500 na primeira chamada e 200 (com
voos válidos) na segunda, chama `get_price` uma única vez, e verifica
que a segunda chamada HTTP (o retry automático dentro do próprio
`get_price`) de fato acontece — ou seja, o guard não bloqueou o retry.
Um segundo teste verifica que, após uma resposta 500 seguida de outro
500 (retry também falha), uma chamada **subsequente e separada** a
`get_price` para a mesma rota **não** é impedida pelo guard (porque
nenhuma resposta bem-sucedida ocorreu ainda).

### 6.3 Atomicidade da transação

**Requisito:** se o envio de e-mail falhar, nenhuma linha nova em
`price_history` ou `route_state` desta execução permanece no banco.

**Critério de aceite / teste:** teste de `runner` com `MockProvider`
retornando uma cotação que dispara alerta e um `notifier.send` fake que
levanta exceção; depois da execução (exit code 1), consultar o banco e
verificar que `price_history` e `route_state` estão exatamente como
antes da execução.

### 6.4 Guard diário entre execuções (SerpApiProvider apenas)

**Requisito:** para uma rota cujo `route_state.last_queried_date` já é
igual à data corrente (UTC), o `runner` não chama `provider.get_price`
novamente nesta execução, mesmo que se trate de um processo novo
(execução separada). `MockProvider` não é afetado por este guard.

**Critério de aceite / teste:**
- Um teste que roda o `runner` duas vezes seguidas (dois processos /
  duas chamadas de orquestração, mesmo banco persistido entre elas) com
  um `SerpApiProvider` fake instrumentado (spy) que conta chamadas;
  depois da primeira execução bem-sucedida, a segunda execução no
  "mesmo dia" (data controlada por injeção/mock de relógio) resulta em
  **zero** chamadas novas ao spy para aquela rota, e a rota é pulada sem
  contar como falha (não afeta o exit code).
- Um teste complementar garante que, se a data mockada avançar um dia, a
  mesma rota volta a ser consultada normalmente.
- Um teste garante que `MockProvider` **não** é bloqueado por este
  guard, mesmo com `last_queried_date` igual à data corrente (o dry-run
  e os testes com `MockProvider` continuam funcionando em execuções
  repetidas no mesmo dia).

## 7. Códigos de saída (`runner.py` / `__main__.py`)

| Exit | Situação | `data/prices.db` deve ser commitado no workflow? |
|---|---|---|
| 0 | todas as rotas processadas sem erro de provider | sim |
| 1 | falha fatal: config inválida, erro de SMTP no envio, erro de banco | não |
| 2 | ≥1 rota falhou no provider, mas as demais foram processadas e o e-mail (se havia alerta) foi enviado com sucesso | sim |

## 8. E-mail (`notifier.py`)

- `build_message(alerts: list[Alert]) -> EmailMessage`: função pura,
  sem I/O, testável sem SMTP.
- `send(message: EmailMessage) -> None`: abre `SMTP_SSL(SMTP_HOST,
  SMTP_PORT)`, autentica com `SMTP_USER`/`SMTP_APP_PASSWORD`, envia para
  `ALERT_TO` (padrão: `SMTP_USER`).
- Assunto: `✈️ N rota(s) com preço em queda` (singular/plural conforme
  `len(alerts)`).
- Corpo `multipart/alternative`:
  - texto puro (fallback);
  - HTML com tabela: Rota | Datas | Anterior | Atual | Variação | Alvo |
    Motivo | Link. Motivo é `"caiu"`, `"abaixo do alvo"` ou
    `"caiu · abaixo do alvo"`. Preços formatados como `R$ 3.450,00`.
    Link aponta para a busca equivalente no Google Flights. Todos os
    campos passam por `html.escape`.
- Variáveis de ambiente: `SMTP_USER`, `SMTP_APP_PASSWORD`, `ALERT_TO`
  (opcional), `SMTP_HOST` (padrão `smtp.gmail.com`), `SMTP_PORT`
  (padrão `465`).
- Sem alertas: nenhum e-mail é montado nem enviado.

## 9. CI/CD

### 9.1 `.github/workflows/ci.yml` (push/PR)

Passos: `ruff check`, `ruff format --check`, `mypy --strict src`,
`pytest --cov=flight_tracker --cov-fail-under=80`.

### 9.2 `.github/workflows/track.yml` (execução diária)

- `on: schedule: cron: "0 11 * * *"` (08:00 BRT) + `workflow_dispatch`.
- `permissions: contents: write`.
- Segredos: `SERPAPI_API_KEY`, `SMTP_USER`, `SMTP_APP_PASSWORD`,
  `ALERT_TO`.
- Passo 1: `python -m flight_tracker`, capturado com `set +e` para não
  abortar o job; o exit code é salvo em `$GITHUB_OUTPUT` (`code`).
- Passo 2 (commit do `.db`): `if: steps.track.outputs.code == '0' ||
  steps.track.outputs.code == '2'`. Faz commit de `data/prices.db` com
  a mensagem `chore: update price history [skip ci]` **somente após**
  este ponto — nunca antes do passo 1 ter concluído com sucesso de
  envio de e-mail (garantido pela atomicidade da transação em §6.3; o
  workflow não duplica essa lógica, apenas respeita o exit code).
- Passo 3 (gate final): se `steps.track.outputs.code != '0'`, o job
  termina com falha (`exit 1`) — isto mantém o job vermelho para os
  casos 1 e 2, mesmo quando o commit ocorre no caso 2.

### 9.3 `.gitattributes`

```
data/prices.db binary
```

## 10. Critérios de aceite verificáveis (resumo)

1. `pytest --cov-fail-under=80` passa, com os testes de §6.1–6.4
   presentes e passando.
2. `ruff check` e `ruff format --check` sem erros.
3. `mypy --strict src` sem erros.
4. `python -m flight_tracker --dry-run` roda sem nenhuma variável de
   ambiente definida, sem acessar rede, e imprime o e-mail simulado (ou
   a mensagem de "nenhuma queda") no stdout.
5. Config inválida (`routes.yaml` malformado ou faltando) faz o
   processo sair com código 1 antes de qualquer chamada de rede.
6. Uma execução onde o SMTP falha não deixa nenhuma linha nova em
   `price_history` nem `route_state` (teste de §6.3).
7. Uma rota cujo preço cai de R$ 500,00 para R$ 400,00 gera alerta com
   `reason` incluindo `"drop"`; se o preço subir para R$ 450,00 e depois
   voltar a R$ 400,00, nenhum e-mail novo é enviado (mesmo valor já
   notificado); se cair para R$ 390,00, um novo alerta é gerado.
8. `data/prices.db` é reconhecido pelo git como binário
   (`.gitattributes` presente e correto).
9. O workflow `track.yml` só faz commit do `.db` quando o exit code do
   passo de tracking é `0` ou `2`, e falha o job (vermelho) sempre que o
   exit code é diferente de `0`.
10. Duas execuções do tracker no mesmo dia UTC (ex.: cron + disparo
    manual) não geram uma segunda chamada real à SerpApi para a mesma
    rota — o `SerpApiProvider` é chamado no máximo uma vez por rota por
    dia UTC, entre execuções (teste de §6.4).

## 11. Fora do escopo (YAGNI)

- Fallback via Playwright.
- Múltiplos destinatários de e-mail, templates HTML avançados,
  dashboard web.
- Suporte a múltiplas moedas simultâneas (assume-se `BRL` fixo por
  enquanto).
- Retenção/limpeza de histórico antigo em `price_history`.
