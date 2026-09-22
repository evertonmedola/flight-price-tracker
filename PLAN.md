# PLAN — Flight Price Tracker

Implementação em tarefas pequenas, na ordem de dependência. Cada tarefa
tem seu critério de sucesso e o teste que o valida. Referências entre
colchetes apontam para a seção correspondente do `SPEC.md`.

Convenção: "verde" = comando roda com exit code 0.

---

## Fase 0 — Scaffolding do projeto

- [x] **0.1 Criar `pyproject.toml`**
  Dependências: `pyyaml`, `httpx`. Dev: `pytest`, `pytest-cov`, `ruff`,
  `mypy`. Configurar `[tool.ruff]`, `[tool.mypy]` (`strict = true`),
  `[tool.pytest.ini_options]` (`testpaths = ["tests"]`), build backend
  com `src` layout (`[tool.setuptools.packages.find] where = ["src"]`
  ou equivalente do backend escolhido).
  **Sucesso:** `pip install -e ".[dev]"` termina sem erro.
  **Teste:** `python -c "import flight_tracker"` roda sem `ModuleNotFoundError`.

- [x] **0.2 Criar esqueleto de pastas** [§2]
  `src/flight_tracker/` com `__init__.py` vazio, `providers/__init__.py`
  vazio, `tests/__init__.py` (se necessário), `config/`, `data/`.
  **Sucesso:** a árvore bate com §2 do SPEC.
  **Teste:** `python -m flight_tracker` falha com erro claro de "not
  implemented" (não com `ImportError`/`ModuleNotFoundError`) — confirma
  que a estrutura de import está correta antes de implementar nada.

- [x] **0.3 Criar `.gitattributes`** [§9.3]
  Conteúdo: `data/prices.db binary`.
  **Sucesso:** arquivo existe com a linha exata.
  **Teste:** `git check-attr binary data/prices.db` (após `git init` e
  `git add`) retorna `binary: set`.

- [x] **0.4 `git init` + primeiro commit**
  Inclui `.gitignore` (ignora `__pycache__`, `.venv`, `.pytest_cache`,
  `.mypy_cache`, `.coverage`, **não** ignora `data/prices.db`, que é
  versionado por decisão do SPEC).
  **Sucesso:** `git status` limpo após o commit.
  **Teste:** `git log --oneline` mostra o commit inicial.

---

## Fase 1 — Domínio e configuração

- [x] **1.1 `models.py`: `Route`, `PriceQuote`, `Alert`** [§3.1]
  Dataclasses `frozen=True` exatamente como especificado.
  **Sucesso:** `mypy --strict src/flight_tracker/models.py` verde.
  **Teste:** `tests/test_models.py` instancia cada dataclass com valores
  válidos e confirma imutabilidade (`FrozenInstanceError` ao tentar
  alterar um campo).

- [x] **1.2 `config.py`: parsing e validação de `routes.yaml`** [§3.2]
  Função `load_routes(path: Path) -> list[Route]`. Implementa todas as
  regras de validação da §3.2 (IATA 3 letras, datas ISO, `return_date >=
  departure_date`, `target_price_cents > 0`, `key` único/derivado,
  arquivo vazio/sem `routes` = erro).
  **Sucesso:** carrega o exemplo de §3.2 sem erro.
  **Teste:** `tests/test_config.py`, parametrizado, cobrindo cada regra
  de validação individualmente (um teste por regra violada + um teste
  do caminho feliz + um teste de `key` derivado quando ausente).

- [x] **1.3 Criar `config/routes.yaml` de exemplo real**
  Com 2-3 rotas plausíveis (usadas depois em dry-run manual).
  **Sucesso:** `config.load_routes("config/routes.yaml")` não levanta
  exceção.
  **Teste:** reaproveita o teste de carregamento de 1.2 apontando para
  este arquivo.

---

## Fase 2 — Armazenamento

- [x] **2.1 `storage.py`: criação de schema** [§3.3]
  Função `init_db(conn: sqlite3.Connection) -> None` cria as duas
  tabelas e o índice com `IF NOT EXISTS`.
  **Sucesso:** rodar duas vezes seguidas não levanta erro (idempotente).
  **Teste:** `tests/test_storage.py::test_init_db_idempotent`.

- [x] **2.2 `storage.py`: `insert_price_quote` e `read_latest_price`**
  [§3.3, §4 passo 1]
  `read_latest_price(conn, route_key) -> PriceQuote | None` (mais
  recente por `fetched_at DESC`); `insert_price_quote(conn, quote)`.
  **Sucesso:** inserir 3 cotações e ler a mais recente retorna a
  correta.
  **Teste:** `test_storage.py::test_read_latest_price_returns_most_recent`
  e `::test_read_latest_price_returns_none_when_empty`.

- [x] **2.3 `storage.py`: `route_state` — leitura/escrita de
  `last_notified_price_cents` e `last_queried_date`** [§3.3, §4]
  `read_route_state(conn, route_key) -> RouteState | None`,
  `upsert_last_notified(conn, route_key, price_cents)`,
  `upsert_last_queried_date(conn, route_key, date_iso)`.
  **Sucesso:** rota nova retorna `None`/campos `None`; após upsert,
  retorna os valores gravados.
  **Teste:** `test_storage.py::test_route_state_upsert_roundtrip`.

- [x] **2.4 Requisito §6.1 — ordem leitura-antes-escrita (teste
  dedicado)** [§6.1] — adiada e formalizada em 5.2, como esta tarefa
  já previa (`test_runner.py::test_previous_read_before_insert_via_spy`).
  Implementar o spy de storage descrito em §6.1 (wrapper que registra a
  ordem de chamadas) e usá-lo num teste de integração leve do fluxo
  "processar uma rota" (pode already usar uma versão mínima da função
  de processamento que será formalizada na Fase 5, ou testar
  diretamente a sequência exigida em `storage`/`runner` conforme a
  implementação evoluir nesta fase — se a função ainda não existir,
  esta tarefa é adiada para 5.2 e marcada aqui como dependência).
  **Sucesso:** teste falha propositalmente se a ordem for invertida
  (verificado manualmente invertendo a implementação uma vez, depois
  revertendo).
  **Teste:** `test_storage.py::test_previous_read_happens_before_insert`
  ou `test_runner.py` equivalente (decidir na implementação; documentar
  onde ficou).

- [x] **2.5 Requisito §6.3 — atomicidade via transação** [§6.3]
  `storage.py` expõe um context manager `transaction(conn)` que faz
  `BEGIN`/`COMMIT`/`ROLLBACK`.
  **Sucesso:** uma exceção dentro do bloco desfaz todas as escritas.
  **Teste:** `test_storage.py::test_transaction_rolls_back_on_exception`
  — insere dado, levanta exceção, confirma que o dado não persiste.

---

## Fase 3 — Regra de alerta

- [x] **3.1 `alerts.py`: `evaluate()`** [§4 passo 5, seção "Regra de
  alerta" da Parte 2 aprovada]
  Função pura: `evaluate(quote, previous, target_price_cents,
  last_notified_price_cents) -> Alert | None`.
  **Sucesso:** cobre `drop`, `below_target`, `drop_and_below_target`,
  `None` quando não se aplica, e a supressão por repetição de valor.
  **Teste:** `tests/test_alerts.py`, tabela parametrizada com pelo menos
  estes casos:
  - `previous=None, target=None` → nunca alerta.
  - `previous=500000, quote=400000` → `"drop"`.
  - `previous=None, target=450000, quote=400000` → `"below_target"`.
  - `previous=500000, target=450000, quote=400000` → `"drop_and_below_target"`.
  - `quote == last_notified` → `None` (suprimido), mesmo se `drop` ou
    `below_target` seriam verdadeiros isoladamente.
  - Critério de aceite §10 item 7 (sequência 500 → 400 → 450 → 400 →
    390) como teste de sequência dedicado.

---

## Fase 4 — Providers

- [x] **4.1 `providers/base.py`: `PriceProvider` e `ProviderError`**
  [§5.1]
  **Sucesso:** `mypy --strict` aceita uma implementação mínima do
  Protocol.
  **Teste:** `test_providers_base.py::test_protocol_is_structural` (uma
  classe duck-typed sem herança satisfaz o Protocol via `isinstance`
  com `@runtime_checkable`, ou verificação estática apenas — decidir e
  documentar).

- [x] **4.2 `providers/mock.py`: `MockProvider`** [§5.2]
  Suporta injeção de sequência fixa de preços para testes.
  **Sucesso:** duas chamadas consecutivas para a mesma rota com
  sequência `[500000, 400000]` retornam nessa ordem.
  **Teste:** `test_providers_mock.py::test_sequence_injection` e
  `::test_deterministic_default_price`.

- [x] **4.3 `providers/serpapi.py`: requisição básica e parsing**
  [§5.3]
  `get_price()` monta a URL/params (`engine=google_flights`,
  `currency=BRL`), chama via `httpx.Client`, extrai o menor preço entre
  `best_flights`/`other_flights`.
  **Sucesso:** com uma fixture JSON de sucesso, retorna o `PriceQuote`
  esperado.
  **Teste:** `test_providers_serpapi.py::test_parses_lowest_price`
  usando `httpx.MockTransport` (sem rede real).

- [x] **4.4 `providers/serpapi.py`: erros e retry** [§5.3]
  4xx/`error`/sem voos → `ProviderError` imediato; 5xx/timeout → 1
  retry, depois `ProviderError`.
  **Sucesso:** cada categoria de erro é coberta.
  **Teste:** `test_providers_serpapi.py::test_4xx_raises_immediately`,
  `::test_error_field_raises`, `::test_no_flights_raises`,
  `::test_5xx_then_success_retries_once`,
  `::test_5xx_twice_raises_provider_error`.

- [x] **4.5 Requisito §6.2 — guard interno de retry** [§6.2]
  **Sucesso:** o estado interno só é marcado após 2xx válido.
  **Teste:** os dois testes descritos em §6.2, literalmente:
  `test_providers_serpapi.py::test_retry_not_blocked_by_guard_after_first_failure`
  e
  `::test_guard_does_not_block_separate_call_after_failed_retry`.

- [x] **4.6 Nunca logar segredos** [§5.3]
  **Sucesso:** nenhuma mensagem de log/exceção contém o valor da API
  key.
  **Teste:** `test_providers_serpapi.py::test_api_key_never_in_logs_or_errors`
  — captura logs (`caplog`) e o texto de exceções levantadas durante um
  erro simulado, confirma ausência da chave usada no teste.

---

## Fase 5 — Runner (orquestração)

- [x] **5.1 `runner.py`: função de processamento de uma rota**
  [§4 passos 1–5]
  `process_route(conn, provider, route, today) -> Alert | None`,
  incluindo os guards de §4 passo 2 (dia UTC) e a leitura de `previous`
  antes do insert.
  **Sucesso:** implementa a ordem exata do §4.
  **Teste:** reusa/formaliza o teste de 2.4 aqui se ainda não
  formalizado; adiciona `test_runner.py::test_route_skipped_when_departure_in_past`.

- [x] **5.2 Requisito §6.1 formalizado no runner** [§6.1]
  Se a tarefa 2.4 foi adiada, implementar aqui definitivamente.
  **Sucesso:** teste de regressão passa e falha propositalmente se a
  ordem for invertida (verificação manual único).
  **Teste:** `test_runner.py::test_previous_read_before_insert_via_spy`.

- [x] **5.3 Requisito §6.4 — guard diário entre execuções** [§6.4]
  Implementado em `process_route`, usando `route_state.last_queried_date`.
  **Sucesso:** os três casos de §6.4 passam.
  **Teste:** `test_runner.py::test_daily_guard_blocks_second_real_call`,
  `::test_daily_guard_releases_next_day`,
  `::test_daily_guard_does_not_affect_mock_provider`.

- [x] **5.4 `runner.py`: orquestração completa + transação + exit
  codes** [§4, §7]
  `run(config_path, db_path, dry_run) -> int`, envolvendo tudo em
  `storage.transaction`, chamando `notifier` quando há alertas,
  aplicando exit codes 0/1/2 de §7.
  **Sucesso:** os três exit codes ocorrem nos cenários corretos.
  **Teste:** `test_runner.py::test_exit_0_all_ok`,
  `::test_exit_2_partial_provider_failure`,
  `::test_exit_1_config_error`, `::test_exit_1_smtp_error`.

- [x] **5.5 Requisito §6.3 formalizado end-to-end no runner** [§6.3, §10.6]
  **Sucesso:** falha de SMTP não deixa nenhuma linha nova (incluindo
  `last_queried_date`, conforme observação do usuário sobre o guard
  sendo desfeito junto — comportamento aceito, sem mudança de SPEC).
  **Teste:** `test_runner.py::test_smtp_failure_rolls_back_everything`.

- [x] **5.6 Dry-run** [§4.1]
  `--dry-run`: força `MockProvider`, banco `:memory:` por padrão, sem
  exigir env vars de SMTP, imprime o e-mail no stdout.
  **Sucesso:** roda sem nenhuma env var definida.
  **Teste:** `test_runner.py::test_dry_run_requires_no_env_vars` (usa
  `monkeypatch.delenv` para garantir ausência) e
  `::test_dry_run_prints_email_or_no_drop_message`.

---

## Fase 6 — Notificação por e-mail

- [x] **6.1 `notifier.py`: `build_message()`** [§8]
  Monta `EmailMessage` `multipart/alternative`, assunto
  singular/plural, tabela HTML com `html.escape`, formatação `R$
  X.XXX,XX`.
  **Sucesso:** com 1 e com 2+ alertas, assunto e conteúdo batem com
  §8.
  **Teste:** `test_notifier.py::test_subject_singular`,
  `::test_subject_plural`, `::test_html_table_contains_all_fields`,
  `::test_html_escapes_untrusted_fields`, `::test_text_fallback_present`.

- [x] **6.2 `notifier.py`: `send()`** [§8]
  `SMTP_SSL`, autenticação, envio para `ALERT_TO` (default
  `SMTP_USER`).
  **Sucesso:** com um SMTP fake injetado, `send()` chama `login` e
  `send_message` com os parâmetros corretos.
  **Teste:** `test_notifier.py::test_send_uses_correct_credentials_and_recipient`.

---

## Fase 7 — CLI

- [x] **7.1 `__main__.py`: parsing de argumentos** [§4.1, §7]
  `--dry-run`, `--config` (padrão `config/routes.yaml`), `--db`
  (padrão `data/prices.db`, ou `:memory:` se `--dry-run` sem `--db`
  explícito).
  **Sucesso:** `python -m flight_tracker --help` lista as três flags.
  **Teste:** `test_cli.py::test_help_lists_flags`,
  `::test_default_db_path_used_when_not_dry_run`,
  `::test_dry_run_defaults_to_memory_db`.

- [x] **7.2 CLI retorna o exit code do `runner.run()`**
  **Sucesso:** `sys.exit(code)` propaga corretamente.
  **Teste:** `test_cli.py::test_exit_code_propagates` (subprocess ou
  chamada direta de `main()` capturando `SystemExit`).

---

## Fase 8 — CI/CD

- [x] **8.1 `.github/workflows/ci.yml`** [§9.1]
  `ruff check`, `ruff format --check`, `mypy --strict src`, `pytest
  --cov=flight_tracker --cov-fail-under=80`.
  **Sucesso:** workflow roda verde num PR de teste.
  **Teste:** validação manual — abrir um PR (ou usar `act`/execução
  local dos mesmos comandos) e confirmar os 4 passos verdes.

- [x] **8.2 `.github/workflows/track.yml`** [§9.2]
  Cron `"0 11 * * *"` + `workflow_dispatch`; `permissions: contents:
  write`; passo do tracker com `set +e` e captura de exit code; commit
  condicional (`code == '0' || code == '2'`); gate final que falha o
  job se `code != '0'`.
  **Sucesso:** os três cenários de exit code (0/1/2) produzem o
  comportamento certo de commit + status do job.
  **Teste:** validação manual via `workflow_dispatch` em modo dry-run
  primeiro (adicionar uma flag/branch de teste que force `--dry-run`
  temporariamente, ou testar localmente o script de commit condicional
  com `bash -x` simulando cada exit code) — documentar no `README.md`
  como foi validado, já que GitHub Actions não é testável por `pytest`.

- [ ] **8.3 Segredos no GitHub** (ação manual, não código)
  Cadastrar `SERPAPI_API_KEY`, `SMTP_USER`, `SMTP_APP_PASSWORD`,
  `ALERT_TO` em *Settings → Secrets and variables → Actions*.
  **Sucesso:** `workflow_dispatch` manual roda sem erro de env var
  ausente.
  **Teste:** execução real (ou dry-run) do workflow no GitHub, checando
  o log.

---

## Fase 9 — Documentação e fechamento

- [x] **9.1 `README.md`**
  Conteúdo mínimo: o que o projeto faz, como rodar localmente
  (`--dry-run` primeiro), como configurar `routes.yaml`, como cadastrar
  os 4 segredos, como o cron funciona, e a observação combinada sobre o
  guard diário: **se o SMTP falhar repetidamente no mesmo dia, o
  `ROLLBACK` desfaz também `last_queried_date`, então mais de uma
  consulta real à SerpApi pode ocorrer nesse cenário raro** (referência
  a §6.4/§6.3 do SPEC).
  **Sucesso:** um leitor novo consegue rodar `--dry-run` só com o
  README.
  **Teste:** validação manual — seguir o README do zero (ambiente
  limpo) e confirmar que os comandos batem.

- [x] **9.2 Cobertura final e checagens de qualidade**
  **Sucesso:** todos os critérios de §10 do SPEC (itens 1–10) passam.
  **Teste:** rodar em sequência: `ruff check`, `ruff format --check`,
  `mypy --strict src`, `pytest --cov=flight_tracker
  --cov-fail-under=80`; todos verdes.

- [ ] **9.3 Primeira execução real controlada**
  Rodar `workflow_dispatch` manual (não dry-run) uma vez, com as rotas
  reais de `config/routes.yaml`, e confirmar o commit do `.db`.
  **Sucesso:** `data/prices.db` aparece no repositório com uma linha
  por rota em `price_history`.
  **Teste:** `git log -- data/prices.db` mostra o commit
  `chore: update price history [skip ci]`; `sqlite3 data/prices.db
  "select * from price_history"` mostra os dados.

---

## Ordem de execução recomendada

Fase 0 → 1 → 2 → 3 → 4 → 5 → 6 → 7 → (8 e 9 podem ser paralelas, mas
9.1 depende do comportamento final de 5.5) → 9.2 → 9.3.

As Fases 1–7 seguem TDD: para cada tarefa, escrever o teste listado
antes da implementação.
