# Design Document

## 1. How I understood the problem

The brief describes a consulting team whose knowledge is spread across Notion,
Slack, Google Docs and people's memories. The stated symptoms — the same
questions being asked repeatedly, new joiners taking a long time, connections
getting lost — all point at the same underlying cause, and it is not that the
documents are hard to find.

It is that **the relationships between things are never written down anywhere.**

The team knows Rahul led FinEdge and also leads Lexora. They know the FinEdge
handover concluded something that later shaped the Lexora architecture. None
of that lives in a document; it lives in whoever was in the room. When that
person is busy, the knowledge is gone.

The sample data makes this concrete. Take the brief's second example question:

> *What did we learn from the FinEdge project that is useful for Lexora?*

Searching for both names returns one document — the FinEdge handover — which
mentions Lexora in a single closing line and says nothing about what was
decided there. The real answer requires four separate facts, none of which sit
together in any file:

```
FinEdge handover states a lesson about relationships mattering more than retrieval
Rahul Mehta led FinEdge
Rahul Mehta leads Lexora
Decision d001 on Lexora applies exactly that lesson
```

No retrieval technique over document text can assemble that, because the
connection is not textual. This shaped the entire build: **the relationships
are the product.** Documents are one input among several, and search is a way
into the data rather than the answer itself.

A second, quieter requirement is easy to overlook: *keep the knowledge useful
when new information is added*. A system that only works on data loaded by a
seed script is a demo. If adding a document tomorrow means hand-wiring six
relationships, nobody will do it, and the base will rot within a month. So new
information has to connect itself.

---

## 2. Architecture

```
                 ┌────────────────────────────────────────┐
   Ask page  ──► │  qa/retrieval.py                       │
   POST /ask     │                                        │
                 │  1. find_entry_points(question)        │
                 │       name match  (free, deterministic)│
                 │       → model     (only if no match)   │
                 │       → text search (last resort)      │
                 │                                        │
                 │  2. collect_related(seeds, hops=2)     │
                 │       walks declared relationships     │
                 │       and Link rows, recording the     │
                 │       path taken to each entity        │
                 └──────────────────┬─────────────────────┘
                                    │  entities + paths
                                    ▼
                 ┌────────────────────────────────────────┐
                 │  qa/answers.py                         │
                 │    build evidence bundle               │
                 │    → Gemini writes prose  (if key set) │
                 │    → else render the same bundle       │
                 └──────────────────┬─────────────────────┘
                                    ▼
                       answer + the connections used

   Writes ──► models ──► post_save signal ──► automatic linking
   (API, admin, seed, shell — all the same path)
```

Four Django apps, split by responsibility rather than by layer:

| App | Responsibility |
|---|---|
| `knowledge` | Entities, relationships, CRUD, linking, seeding |
| `qa` | Retrieval and answer construction |
| `web` | The three server-rendered pages |
| `config` | Settings and routing |

`qa/llm.py` is the only module that talks to a model provider. Swapping
Gemini for something else means editing that one file.

### Stack

Django 5 + DRF, SQLite, optional Gemini, server-rendered templates. Four
runtime dependencies, no build step, no database server, no container.

This was a deliberate constraint rather than a default. The brief says
reviewers will run the system and try to break it; every piece of required
setup is a chance for that to fail on their machine rather than mine. DRF
gives full CRUD across six entity types in about a hundred lines and hands
reviewers a browsable API and an admin UI for free — both worth real hours on
a 72-hour clock.

---

## 3. How data is stored and connected

This is the core of the design, so it gets the most space.

### Two kinds of relationship, stored two ways

**Declared relationships are columns.** A project belongs to at most one
client, has one lead, and has a team. Those are `ForeignKey` and
`ManyToManyField`:

```python
class Project(models.Model):
    client = models.ForeignKey(Client, null=True, on_delete=models.SET_NULL, ...)
    lead   = models.ForeignKey(Person, null=True, on_delete=models.SET_NULL, ...)
    team   = models.ManyToManyField(Person, related_name="projects")
    topics = models.ManyToManyField(Topic, related_name="projects")
```

The structured sample files already contain these as foreign keys
(`"client_id": "c001"`, `"lead": "p002"`). Embedding that as text and
recovering it with similarity search would throw away information that was
handed to us as fact. A join is exact; similarity is a guess that is usually
right. There is no reason to prefer the guess.

Columns also buy validation, indexes, and `select_related`/`prefetch_related`,
which is why a two-hop walk is a handful of queries rather than dozens.

**Discovered relationships are rows in one generic table.** Two things do not
fit columns:

1. A document can mention *any* entity type. As columns that would be five
   `ManyToManyField`s on `Document`, and a sixth when a new entity type is
   added.
2. Some relationships only exist in prose. The FinEdge handover says its
   lesson *"influenced our later thinking on Lexora and the internal KB."*
   That is a project-to-project relationship with no field to live in, and
   adding one would mean a migration — then another for `supersedes`, then
   another for the next one discovered.

So there is one `Link` table using Django's `ContentType` framework:

```python
class Link(models.Model):
    source  = GenericForeignKey("source_type", "source_id")
    target  = GenericForeignKey("target_type", "target_id")
    rel_type     = CharField()        # MENTIONS | INFLUENCED | SUPERSEDES | RELATES_TO
    evidence     = TextField()        # why this link exists
    auto_created = BooleanField()     # machine or human
```

A new relationship type is a row, not a migration.

**The rule:** known shape and always the same two types → a column. Discovered
from text, or able to point at anything → a `Link` row.

### Why relationships are typed

`rel_type` and the labels on declared relationships are not decoration. An
untyped "these two are related" edge makes traversal useless — everything is
related to everything within two hops, and expansion returns the whole
database. Typed, labelled edges are what let a path explain itself:

```
FinEdge Research Assistant --influenced--> Lexora Knowledge Core
```

That line is readable by a person, and it is also what makes the answer
checkable rather than merely plausible.

### Every link carries its evidence

`Link.evidence` stores the sentence a link was derived from. When the system
claims FinEdge influenced Lexora, it can quote the handover note that says so.
An answer that cannot show why it believes something is indistinguishable
from a guess.

### Retrieval

**Step 1 — where to start.** A cheap-first cascade:

1. **Name matching.** Entity names and aliases, matched on word boundaries.
   Free, deterministic, testable offline, and sufficient for most real
   questions, which do tend to name what they are about.
2. **Model extraction.** Only if step 1 finds nothing. The entity catalogue
   goes into the prompt and the model returns which ones the question refers
   to. Handles *"the legal client"* and *"the finance project"*.
3. **Text search.** Last resort, over document and decision text.

Ordering matters for more than cost. The common path never touches the
network, so it cannot fail when a provider is down and it can be tested
without stubbing anything.

**Step 2 — what is connected.** A breadth-first walk from those entities,
following both declared relationships and `Link` rows, recording the path to
each entity found. Breadth-first is not incidental: it guarantees each entity
is recorded with its *shortest* path, so the evidence shown is the most direct
explanation of relevance rather than a rambling one.

**A subtlety worth calling out.** The walk records each entity once, which
hides edges *between* two entry points — and for a question comparing two
things, that edge is the entire answer. Asking about FinEdge and Lexora makes
both projects entry points, so `FinEdge --influenced--> Lexora` never appears
as a hop. `connections_among()` is a second pass that finds exactly those
edges. Without it, the brief's second example question retrieves all the right
entities and still fails to show the one relationship that answers it. This
was caught by running the brief's examples against the system, not by reading
the code.

### The model's role

The model is given the retrieved context and asked to phrase it. It is never
asked what the answer is, and it never sees anything outside the evidence
bundle — a property enforced structurally, and covered by a test that asserts
the prompt equals the bundle exactly.

With no key configured, the same bundle is rendered as a structured summary
instead. Both paths are grounded in identical evidence.

### Keeping knowledge useful as it grows

Automatic linking runs on `post_save`, not in a view. Every write path
benefits: the API, the admin, the seed command, the shell.

Three behaviours that matter more than they look:

- **Re-linking preserves manual links.** Only `auto_created=True` rows are
  replaced, so re-ingesting a document never destroys a relationship a person
  recorded by hand.
- **New entities are linked into existing documents.** A person added tomorrow
  would otherwise be invisible to every note written yesterday.
- **Deletes clean up generic links.** `GenericForeignKey` is not covered by
  cascade deletes, so without a `post_delete` receiver a deleted project
  leaves `Link` rows whose target resolves to `None`, and traversal walks into
  holes.

`on_delete` was chosen deliberately per relationship. Deleting a project sets
`decision.project = NULL` rather than deleting the decision, because the
record of what was decided and why is the thing the system exists to preserve.

---

## 4. Trade-offs

**No vector search.** The obvious choice for a system like this, and it is
absent on purpose.

The brief's own weak-answer examples are retrieval failures: *"returns the
FinEdge handover document or any document that mentions both names."* Adding
embeddings does not fix that, because the FinEdge→Lexora connection is not a
similarity — it is a shared lead and a stated influence. Similarity would rank
the handover document highly and still miss the decision.

Against that, pgvector needs a Postgres extension and an embedding model or
paid API, chunking, and a re-index path — real setup cost on a reviewer's
machine, for five documents, to answer questions that name their subject
directly.

*Where this breaks:* a question that names nothing and paraphrases everything,
over a much larger corpus. The honest position is that semantic search is a
scale feature this data set does not reach, not a capability the design
rejects. Adding it means adding a fourth step to the cascade; nothing
downstream changes.

**The entity catalogue goes in the prompt.** Fine at ~40 entities, broken at
10,000 — the prompt stops fitting. This is the clearest scaling limit in the
system. The fix is a semantic index for the lookup step only, and it is
isolated to one function.

**Literal name matching.** Fast, dependency-free, and easy to explain when a
reviewer asks why a particular link exists. It will not catch a paraphrase,
and a short entity name that is also a common word would over-match — handled
with a minimum length rule, word boundaries, and an alias map. The alias map
is static, which is honest for this data set and would move onto the model in
a real deployment.

**SQLite.** Zero setup, and swapping to Postgres is a settings change. It will
not survive concurrent writers, which does not matter for an internal tool
used by a dozen people.

**Synchronous Gemini calls.** A call blocks a worker for a few seconds. A task
queue would fix it and would add Redis and Celery to the setup instructions —
a bad trade for a system whose answer latency is dominated by a single API
call anyway.

**Two hops by default.** One hop misses the FinEdge→Rahul→Lexora chain. Three
starts returning the whole database on a small graph. Two is the smallest
number that answers the brief's questions, and it is configurable per request.

**Rendered evidence rather than a drawn diagram.** The brief asks to *show the
connections clearly*, which text does. A node-and-edge diagram is a nicer
presentation of the same `/api/related/` payload; it was left out in favour of
getting traversal and linking right, and the endpoint is already shaped to
feed one.

---

## 5. What works, what does not

### Works

- Full CRUD for all six entity types, via API and admin
- Declared relationships plus a generic `Link` table for discovered ones
- Automatic linking on every write, preserving manual links
- Cheap-first entry point cascade; the common path needs no network
- Breadth-first traversal with recorded, human-readable paths
- Edges between entry points surfaced explicitly
- Answers with or without an API key, grounded identically
- Three pages: ask, browse, entity detail
- Seed loading the full sample data through the normal save path, re-runnable
- 83 tests, no network required

### Incomplete, and why

**No visual diagram.** Traversal and linking mattered more, and the JSON
endpoint that would feed a diagram already exists. Cost: about an hour with a
CDN library.

**No Google Docs connector.** The brief says one real integration is enough
and explicitly optional. `Document.source` and `external_ref` already model
what a connector needs — `external_ref` makes re-ingestion update rather than
duplicate — so it is a loader, not a schema change. I chose relationship
quality over an OAuth flow that reviewers would need their own Google Cloud
project to test.

**Aliases are a static map.** Works for this data set, will not generalise.
The right fix is an `aliases` field on each entity, populated on create.

**Entity extraction is not cached.** The same question asked twice makes the
same call. Trivial to add; not worth the code without usage data.

**No relationship history.** `SUPERSEDES` exists in the schema and nothing
writes it. Decisions carry dates, so ordering is recoverable, but "what
changed in our view since last quarter" — the exact gap the FinEdge handover
identified — is not directly answerable. This is the most interesting missing
feature and the first thing I would build next.

**Basic auth only.** Reads open, writes authenticated, as the brief permits.
No per-entity permissions.

### Where I would expect it to break

Honest failure modes, since the brief says reviewers will look for them:

- A question that names nothing recognisable falls to text search and may
  return little. Tested; it says so rather than inventing an answer.
- A very densely connected entity at three hops returns a large context. The
  hop limit is capped at 4 and results are capped at 60 entities.
- A document naming twenty entities creates twenty links, and its entity page
  gets long. Real, unmitigated.
- Renaming an entity does not re-link existing documents; only creation does.
  Re-saving the affected documents fixes it, and `/relink/` exists for that.

---

## 6. Testing

83 tests, all offline. The suite is organised around what would actually go
wrong rather than around code structure.

**The tests that matter most** are in `qa/tests/test_retrieval.py`, under
`TestConnectedAnswers`. They run the brief's own example questions and assert
that the connected entities are reached — the FinEdge lesson reaching the
Lexora decision, the Lexora team and decision arriving together, the Slack
decision pulling in its people and project. If the design claim is wrong,
these fail.

**Adversarial cases** get their own group in `test_api.py`: duplicate names,
an end date before a start date, links to missing entities, links to unknown
entity types, self-links, unauthenticated writes, empty questions, and an
out-of-range hop count. Each should fail cleanly with a 400, and each is
asserted.

**Structural properties** rather than fixed outputs, where possible: that
every reached entity has a non-empty path; that paths are the shortest
available; that a cycle terminates; that one relationship is never reported
twice; that the model's prompt equals the evidence bundle exactly.

**The model is stubbed, never called.** One test asserts the expensive step
does *not* run when name matching succeeds — the cascade's ordering is a
design claim, so it is tested as one.

Not covered: the real Gemini API (stubbed by design), browser rendering, and
load behaviour.

### Testing by hand

```bash
pytest                                  # 83 tests
python manage.py seed --flush           # reload cleanly
```

Worth trying, to see requirement 5 rather than read about it:

1. `POST /api/documents/` with content naming a person and a project.
2. `GET /api/documents/{id}/` — the `mentions` field is already populated;
   nothing wired it up by hand.
3. Ask a question about that person — the new document is in the evidence.

---

## 7. If I had longer

In order:

1. **`SUPERSEDES` on decisions**, with an answer layer that says "this was
   revised on <date>". It closes the exact gap the sample data itself
   identifies.
2. **The diagram**, over the existing `/api/related/` payload.
3. **Aliases on the model**, replacing the static map.
4. **A semantic index as a fourth cascade step**, once the catalogue outgrows
   a prompt.
5. **A Google Docs connector**, as a loader against the existing schema.
