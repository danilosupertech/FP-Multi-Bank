# Financial Parser DSPy — AutoDetect

Python project for importing bank statements in PDF format, categorizing transactions, preserving learned rules, and analyzing data through a Streamlit dashboard.

## Main Idea

The system uses a single input folder:

```text
data/raw/
```

Place any supported PDF there:

- ActivoBank
- Wise
- Future supported banks

The system automatically detects the statement format, selects the correct parser, and stores transactions in SQLite with bank metadata.

## Project Overview

- Automatic bank detection
- Statement import pipeline
- Transaction normalization
- Rule-based categorization
- DSPy/Ollama suggestions and auditing
- SQLite persistence
- Streamlit dashboard
- Reallocation agent for category refinement

## Main Structure

```text
app/
  categorization/
  dashboard/
  database/
  parsers/
  services/

data/
  raw/
  processed/
  failed/
  rules/
  storage/
```

## Supported Banks

- ActivoBank
- Wise

## Importing Statements

1. Copy PDFs into:

```text
data/raw/
```

2. Run:

```bash
python main.py
```

The importer will:

- Detect the bank automatically
- Extract transactions
- Prevent duplicates
- Save data to SQLite
- Move processed files to `processed`
- Move invalid files to `failed`

## Categorization Flow

Priority order:

1. Credit detection
2. Learned JSON rules
3. Learned SQLite rules
4. Fixed rules
5. DSPy suggestions
6. Local similarity suggestions

Deterministic rules always take precedence over AI-generated suggestions.

## Duplicate Prevention

When a transaction ID exists, it is used directly.

If the bank statement does not provide an ID, the system creates a stable signature using:

- Bank
- Dates
- Operation
- Amount
- Balance
- Merchant
- Description

This prevents duplicated imports between partial and final statements.

## Dashboard

The Streamlit dashboard provides:

- KPIs
- Global filters
- Audit tools
- Suggestions review
- Category management
- Reports
- Raw data inspection
- Manual entries

Run:

```bash
python -m streamlit run app/dashboard/streamlit_app.py
```

## DSPy Integration

Enable DSPy categorization:

```bash
ENABLE_DSPY_CATEGORY=1
OPENAI_API_KEY=your_key
```

Enable DSPy auditing:

```bash
ENABLE_DSPY_AUDIT=1
```

DSPy suggestions never overwrite categories automatically unless explicitly approved by the reallocation agent.

## Ollama Integration

Example:

```bash
ollama pull qwen2.5:14b
```

Environment:

```bash
ENABLE_WEB_RESEARCH=1
WEB_SEARCH_PROVIDER=ollama
OLLAMA_RESEARCH_MODEL=qwen2.5:14b
```

This mode uses local model knowledge as contextual assistance for categorization.

## Automatic Reallocation Agent

The reallocation agent can:

- Review imported transactions
- Suggest better categories
- Apply approved changes
- Create logs
- Learn new rules

Example:

```bash
python scripts/run_dspy_reallocation_agent.py --limit 20 --confidence high --scope outros
```

Recommended initial settings:

- Scope: `outros`
- Confidence: `high`
- Small execution limits

## Optional Web Research

Supported providers:

- Tavily
- SerpAPI
- Brave Search
- Ollama (local knowledge mode)

Research results are used only as additional context and never as the sole source of categorization decisions.

## Manual Entries

The dashboard allows manual creation of:

- Expenses
- Income transactions

Manual records are stored in SQLite and behave like normal imported transactions.

## Tests

Run:

```bash
make test
```

or

```bash
python -m unittest discover -s tests
```

## Recommended Workflow

1. Import PDFs
2. Open the dashboard
3. Review "Others" category transactions
4. Confirm or correct suggestions
5. Teach new rules
6. Run the reallocation agent conservatively
7. Review logs and audit results
8. Expand scope only after validating accuracy

## Intelligence Persistence

The following assets preserve learned knowledge:

- `data/storage/financial.db`
- `data/rules/merchant_rules.json`
- Categorization rules
- DSPy suggestions
- Local similarity intelligence

## Design Philosophy

The project is intentionally conservative:

- Deterministic rules first
- AI suggestions second
- Human review whenever ambiguity exists

When evidence is insufficient, transactions remain in **Others** or receive a suggestion for manual validation instead of being automatically modified.
