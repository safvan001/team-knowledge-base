# Team Knowledge Base

A knowledge system for a small AI consulting team. It stores people, clients,
projects, decisions, documents and topics, keeps the relationships between
them, and answers questions by **following those relationships** rather than
by searching text.

Every answer shows the connections it was built from, so you can check it.

> The data in `sample-data/` is fictional and was provided with the
> assignment. It is not real information about any real organisation.

---

## Setup

Requires Python 3.10+. No database server, no Docker, no Node.

```bash
git clone <this-repo>
cd team-knowledge-base

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -r requirements.txt

python manage.py migrate
python manage.py seed              # loads the fictional sample data
python manage.py runserver
```

Open <http://127.0.0.1:8000/>.

That is the whole setup. **No API key is required** — see
[Answer generation](#answer-generation) below.

To use the Django admin as a CRUD UI:

```bash
python manage.py createsuperuser
```

### Optional: Gemini for natural-language answers

```bash
cp .env.example .env
# then set GEMINI_API_KEY=...
```

Defaults to `gemini-3.6-flash` via the Interactions API. Gemini 2.5 models are
retired for new API keys, so an older model name will 404 — the system logs a
warning and falls back to the template answer rather than failing the request.

---

## Try it

Open <http://127.0.0.1:8000/> and ask one of the suggested questions, or:

```bash
curl -X POST http://127.0.0.1:8000/api/ask/ \
  -H "Content-Type: application/json" \
  -d '{"question": "What did we learn from the FinEdge project that is useful for Lexora?"}'
```

Neither project's stored record mentions the other. The answer comes from a
recorded relationship between them:

```
Direct relationships between these:
  - FinEdge Analytics --engaged us for--> FinEdge Research Assistant
  - Lexora Legal --engaged us for--> Lexora Knowledge Core
  - FinEdge Research Assistant --influenced--> Lexora Knowledge Core

Decisions:
  - Prefer structured knowledge over pure vector RAG for Lexora
    via Lexora Knowledge Core --produced decision--> Prefer structured
        knowledge over pure vector RAG for Lexora
```

Other questions worth trying:

| Question | What it demonstrates |
|---|---|
| `Who worked on the Lexora project and what key decisions were made about its approach?` | Team and decisions pulled from relationships, not from one document |
| `Show me everything related to the decision about not integrating Slack in the internal knowledge base.` | Exploration outward from a decision |
| `What has Rahul Mehta worked on?` | One person connecting two separate projects |

---

## What is where

| Path | Contains |
|---|---|
| `knowledge/models.py` | The six entity types and the generic `Link` table |
| `knowledge/linking.py` | Matching free text to entities |
| `knowledge/signals.py` | Keeping links current on every write |
| `knowledge/views.py` | CRUD endpoints and `/api/related/` |
| `knowledge/management/commands/seed.py` | Loading the sample data |
| `qa/retrieval.py` | Finding entry points, walking relationships |
| `qa/answers.py` | Building evidence, writing the answer |
| `qa/llm.py` | The only file that talks to Gemini |
| `web/views.py`, `templates/web/` | The three pages |

The reasoning behind these choices is in **[DESIGN.md](DESIGN.md)**.

---

## Pages

| URL | Purpose |
|---|---|
| `/` | Ask a question; see the answer and the connections behind it |
| `/browse/` | Everything stored, by type |
| `/<type>/<id>/` | One entity, its details, and what it connects to |
| `/admin/` | Full CRUD UI |
| `/api/` | Browsable API |

## API

Reads are open. Writes need a token or an admin session.

| Endpoint | Purpose |
|---|---|
| `GET/POST /api/{people,clients,projects,decisions,documents,topics}/` | CRUD |
| `GET/POST /api/links/` | Relationships that are not fixed columns |
| `POST /api/ask/` | Ask a question, get an answer plus its evidence |
| `GET /api/related/?type=project&id=1&hops=2` | Everything connected to one entity |
| `POST /api/documents/{id}/relink/` | Rebuild a document's automatic links |
| `POST /api/auth/token/` | Get a token |

Getting a token:

```bash
curl -X POST http://127.0.0.1:8000/api/auth/token/ \
  -d "username=<user>&password=<pass>"
```

`/api/ask/` accepts `max_hops` (1–4) and `use_model` (set `false` to force the
deterministic path).

---

## Answer generation

The system works with or without an API key.

| | With `GEMINI_API_KEY` | Without |
|---|---|---|
| Finding what a question is about | Name matching, then the model for questions that describe rather than name | Name matching, then text search |
| Writing the answer | Gemini, from the retrieved context only | The same context, rendered as a structured summary |
| Evidence shown | Identical | Identical |

Retrieval is the same either way. The model phrases the answer; it never
decides what the answer is, and it is never given anything beyond the
retrieved context.

---

## Tests

```bash
pytest
```

83 tests, no network access required. Grouped by what they protect:

| File | Covers |
|---|---|
| `qa/tests/test_retrieval.py` | Entry points, traversal, hop limits, cycles, and the brief's own example questions |
| `qa/tests/test_answers.py` | Grounding, evidence, behaviour with and without a model, provider failure |
| `knowledge/tests/test_linking.py` | Automatic linking, re-linking, protecting manual links, deletion cleanup |
| `knowledge/tests/test_api.py` | CRUD, permissions, and bad input |
| `knowledge/tests/test_seed.py` | Sample data loads with its relationships intact, and is re-runnable |
| `web/tests/test_pages.py` | Pages render, including empty and error states |

The one test that exercises the model path stubs it, so the suite is fast and
deterministic.

Testing notes, including what is deliberately not covered, are in
[DESIGN.md](DESIGN.md#testing).
