# Flight Price Tracker

Rastreador de preços de passagens aéreas. Roda uma vez por dia via GitHub
Actions, consulta o preço de rotas configuradas em `config/routes.yaml`,
grava o histórico em SQLite (`data/prices.db`, versionado no repositório) e
envia um e-mail consolidado quando algum preço cai ou fica abaixo do
preço-alvo.

Detalhes de requisitos e decisões de design estão em [`SPEC.md`](SPEC.md).
O checklist de implementação está em [`PLAN.md`](PLAN.md).

## Como funciona, em resumo

- Coleta via `PriceProvider`: `MockProvider` (dados fake, para testes e
  dry-run) ou `SerpApiProvider` (preços reais do Google Flights via
  [SerpApi](https://serpapi.com/google-flights-api)).
- Alerta quando o preço cai em relação à última cotação **ou** fica abaixo
  do `target_price_cents` da rota — sem repetir e-mail para o mesmo valor
  já notificado.
- Um único e-mail por execução (HTML + texto), enviado via Gmail
  (`smtplib` + senha de app).
- Execução agendada via GitHub Actions (`cron` diário); o workflow commita
  `data/prices.db` de volta no repositório depois de cada execução
  bem-sucedida.

## Rodando localmente

Requer Python 3.12+.

```bash
python -m venv .venv
# Windows:
.venv\Scripts\python -m pip install -e ".[dev]"
# Linux/macOS:
.venv/bin/pip install -e ".[dev]"
```

**Primeiro passo: sempre rode o dry-run.** Ele usa `MockProvider`, um
banco em memória e imprime o e-mail simulado no stdout em vez de enviar —
não precisa de nenhuma variável de ambiente:

```bash
python -m flight_tracker --dry-run
```

Para rodar de verdade (consulta real à SerpApi e envio de e-mail real),
defina as variáveis de ambiente da seção seguinte e rode sem `--dry-run`:

```bash
python -m flight_tracker
```

Outras flags:

- `--config CAMINHO`: arquivo de rotas (padrão: `config/routes.yaml`).
- `--db CAMINHO`: banco SQLite (padrão: `data/prices.db`; em `--dry-run`
  sem `--db` explícito, usa `:memory:`).

## Configurando rotas

Edite `config/routes.yaml`:

```yaml
routes:
  - key: GRU-LIS # opcional; derivado de origin-destination-departure_date se ausente
    origin: GRU # IATA, 3 letras
    destination: LIS
    departure_date: "2026-12-10" # ISO-8601
    return_date: "2026-12-20" # opcional
    target_price_cents: 350000 # opcional; em centavos (R$ 3.500,00)
```

## Segredos necessários

Para rodar de verdade (local ou no GitHub Actions), defina:

| Variável | Obrigatória | Descrição |
|---|---|---|
| `SERPAPI_API_KEY` | sim | chave da [SerpApi](https://serpapi.com/) |
| `SMTP_USER` | sim | e-mail do Gmail que envia os alertas |
| `SMTP_APP_PASSWORD` | sim | [senha de app](https://support.google.com/accounts/answer/185833) do Gmail (não é a senha normal da conta) |
| `ALERT_TO` | não | destinatário dos alertas (padrão: o próprio `SMTP_USER`) |

No GitHub: *Settings → Secrets and variables → Actions → New repository
secret*, uma por uma, com esses mesmos nomes.

## Como o cron funciona

`.github/workflows/track.yml` roda diariamente às 08:00 (horário de
Brasília) via `cron: "0 11 * * *"` (UTC), e também pode ser disparado
manualmente pela aba *Actions* do GitHub (`workflow_dispatch`).

Depois de cada execução, o exit code decide o que acontece:

| Exit code | Situação | Commit do `.db`? | Status do job |
|---|---|---|---|
| `0` | tudo certo | sim | verde |
| `1` | falha fatal (config, SMTP, banco) | não | vermelho |
| `2` | alguma rota falhou no provider, mas o resto foi processado | sim | vermelho (mesmo commitando) |

**Observação sobre o guard diário e falhas de SMTP:** para não estourar a
cota gratuita da SerpApi (250 buscas/mês), o tracker consulta cada rota no
máximo uma vez por dia UTC, mesmo entre execuções separadas (cron +
disparo manual no mesmo dia) — ver SPEC §6.4. Essa marcação de "já
consultada hoje" é gravada na mesma transação que o resto da execução, e
por isso é desfeita junto se o envio de e-mail falhar (SPEC §6.3). Na
prática: **se o SMTP falhar repetidamente no mesmo dia**, cada tentativa
volta a consultar a SerpApi de verdade, porque o guard nunca chega a ser
persistido com sucesso. É um cenário raro (SMTP falhando várias vezes no
mesmo dia) e aceito conscientemente — não é tratado como bug.

## Desenvolvimento

```bash
ruff check .
ruff format --check .
mypy --strict src
pytest --cov=flight_tracker --cov-report=term-missing --cov-fail-under=80
```

Os mesmos comandos rodam no CI (`.github/workflows/ci.yml`) a cada push e
pull request.
