"""Finding the connected context that answers a question.

Two steps, kept separate on purpose.

`find_entry_points` decides *where in the knowledge base to start*. It is a
cheap-first cascade: literal name matching, then an optional model call for
questions that describe entities without naming them, then full text search.
Most questions never reach step two, which keeps the common path free, fast
and deterministic - and testable without network access.

`collect_related` decides *what is connected to those starting points*. This
is where the value is. It walks outward through both storage kinds - declared
columns and rows in the Link table - recording the path taken to each entity.
The paths are not debug output; they are the evidence the answer is built on
and are shown to the user.
"""

from dataclasses import dataclass, field

from django.conf import settings
from django.db.models import Q

from knowledge.linking import (
    ALIASES,
    LINKABLE_MODELS,
    MIN_NAME_LENGTH,
    content_type_for,
    surface_forms,
)
from knowledge.models import Client, Decision, Document, Link, Person, Project, Topic

import re


def entity_key(obj):
    """Stable identity for an entity across the two storage kinds."""
    return (type(obj).__name__.lower(), obj.pk)


def entity_label(obj):
    return str(obj)


# --------------------------------------------------------------------------
# Step 1: where do we start?
# --------------------------------------------------------------------------


def match_names(question):
    """Entities whose name or alias appears literally in the question.

    Free, deterministic, and correct for most real questions, which do tend to
    name the thing they are about.
    """
    hits = []
    for model_name, model in LINKABLE_MODELS.items():
        for obj in model.objects.all():
            for form in surface_forms(obj, model_name):
                if re.search(rf"\b{re.escape(form)}\b", question, re.IGNORECASE):
                    hits.append(obj)
                    break
    return hits


def entity_catalogue():
    """Every entity name in the base, for the model-assisted lookup."""
    catalogue = []
    for model_name, model in LINKABLE_MODELS.items():
        for obj in model.objects.all():
            catalogue.append({"type": model_name, "id": obj.pk, "name": entity_label(obj)})
    return catalogue


def match_by_model(question):
    """Ask the language model which known entities a question refers to.

    This is the fallback for questions that describe an entity instead of
    naming it - "the legal client", "the finance project". The whole catalogue
    goes in the prompt, which is accurate and cheap at this size and is the
    reason no vector index is needed here. It is also the part that would be
    replaced by a semantic index if the base grew past a few thousand
    entities; nothing downstream would change.
    """
    from .llm import extract_entities, llm_available

    if not llm_available():
        return []

    catalogue = entity_catalogue()
    names = extract_entities(question, [c["name"] for c in catalogue])
    if not names:
        return []

    lowered = {n.lower() for n in names}
    hits = []
    for entry in catalogue:
        if entry["name"].lower() in lowered:
            hits.append(LINKABLE_MODELS[entry["type"]].objects.get(pk=entry["id"]))
    return hits


def search_text(question, limit=5):
    """Last resort: look for the question's meaningful words in stored text."""
    words = [w for w in re.findall(r"\w+", question) if len(w) > 4]
    if not words:
        return []

    doc_filter = Q()
    dec_filter = Q()
    topic_filter = Q()
    for word in words:
        doc_filter |= Q(title__icontains=word) | Q(content__icontains=word)
        dec_filter |= Q(title__icontains=word) | Q(summary__icontains=word)
        topic_filter |= Q(name__icontains=word) | Q(description__icontains=word)

    hits = list(Document.objects.filter(doc_filter)[:limit])
    hits += list(Decision.objects.filter(dec_filter)[:limit])
    hits += list(Topic.objects.filter(topic_filter)[:limit])
    return hits


def find_entry_points(question, use_model=True):
    """Where to start walking. Returns (entities, method_used)."""
    hits = match_names(question)
    if hits:
        return _dedupe(hits), "name_match"

    if use_model:
        hits = match_by_model(question)
        if hits:
            return _dedupe(hits), "model_extraction"

    hits = search_text(question)
    if hits:
        return _dedupe(hits), "text_search"

    return [], "none"


def _dedupe(entities):
    seen, out = set(), []
    for entity in entities:
        key = entity_key(entity)
        if key not in seen:
            seen.add(key)
            out.append(entity)
    return out


# --------------------------------------------------------------------------
# Step 2: what is connected to those starting points?
# --------------------------------------------------------------------------


@dataclass
class Step:
    """One hop: `label` describes the relationship in plain words."""

    source: object
    label: str
    target: object

    def as_text(self):
        return f"{entity_label(self.source)} --{self.label}--> {entity_label(self.target)}"


@dataclass
class Found:
    """An entity reached during the walk, plus how it was reached."""

    entity: object
    hops: int
    path: list = field(default_factory=list)

    def path_text(self):
        if not self.path:
            return f"{entity_label(self.entity)} (named in the question)"
        return " ".join(
            [entity_label(self.path[0].source)]
            + [f"--{s.label}--> {entity_label(s.target)}" for s in self.path]
        )


def declared_neighbours(obj):
    """Neighbours reachable through model columns, both directions.

    Each is returned with a human-readable label, because "led by" is what
    makes a path explain itself; "fk_lead_id" would not.
    """
    out = []

    if isinstance(obj, Project):
        if obj.client:
            out.append((obj.client, "for client"))
        if obj.lead:
            out.append((obj.lead, "led by"))
        out += [(p, "team member") for p in obj.team.all()]
        out += [(t, "about topic") for t in obj.topics.all()]
        out += [(d, "produced decision") for d in obj.decisions.all()]

    elif isinstance(obj, Person):
        out += [(p, "leads") for p in obj.led_projects.all()]
        out += [(p, "works on") for p in obj.projects.all()]
        out += [(d, "made decision") for d in obj.decisions_made.all()]
        out += [(d, "took part in decision") for d in obj.decisions_involved_in.all()]
        out += [(doc, "wrote") for doc in obj.documents.all()]

    elif isinstance(obj, Client):
        out += [(p, "engaged us for") for p in obj.projects.all()]

    elif isinstance(obj, Decision):
        if obj.project:
            out.append((obj.project, "decided on project"))
        if obj.made_by:
            out.append((obj.made_by, "made by"))
        out += [(p, "involved") for p in obj.participants.all()]
        out += [(t, "about topic") for t in obj.topics.all()]

    elif isinstance(obj, Topic):
        out += [(p, "discussed in project") for p in obj.projects.all()]
        out += [(d, "subject of decision") for d in obj.decisions.all()]

    elif isinstance(obj, Document):
        if obj.author:
            out.append((obj.author, "written by"))

    return out


def link_neighbours(obj):
    """Neighbours reachable through the generic Link table, both directions."""
    from knowledge.linking import incoming_links, outgoing_links

    readable = dict(Link.REL_CHOICES)
    out = []

    for link in outgoing_links(obj):
        if link.target is not None:
            out.append((link.target, readable.get(link.rel_type, link.rel_type.lower())))

    for link in incoming_links(obj):
        if link.source is not None:
            label = readable.get(link.rel_type, link.rel_type.lower())
            out.append((link.source, f"{label} by"))

    return out


def neighbours(obj):
    return declared_neighbours(obj) + link_neighbours(obj)


def collect_related(seeds, max_hops=None, limit=60):
    """Breadth-first walk outward from the seed entities.

    Breadth-first matters: it guarantees each entity is recorded with its
    *shortest* path, so the evidence shown to the user is the most direct
    explanation of why that entity is relevant rather than a rambling one.
    """
    if max_hops is None:
        max_hops = settings.RETRIEVAL_MAX_HOPS

    found = {}
    for seed in seeds:
        found[entity_key(seed)] = Found(entity=seed, hops=0, path=[])

    frontier = list(seeds)
    for hop in range(1, max_hops + 1):
        next_frontier = []
        for current in frontier:
            current_found = found[entity_key(current)]
            for neighbour, label in neighbours(current):
                key = entity_key(neighbour)
                if key in found:
                    continue
                step = Step(source=current, label=label, target=neighbour)
                found[key] = Found(
                    entity=neighbour, hops=hop, path=current_found.path + [step]
                )
                next_frontier.append(neighbour)
                if len(found) >= limit:
                    return list(found.values())
        frontier = next_frontier
        if not frontier:
            break

    return list(found.values())


def connections_among(entities):
    """Relationships running directly between the entities a question named.

    The breadth-first walk records each entity once, by its shortest path from
    a seed. That is right for explaining why an entity is relevant, but it
    hides edges *between* two seeds - and for a question comparing two things,
    that edge is the entire answer. "What did we learn from FinEdge that is
    useful for Lexora?" makes both projects entry points, so the recorded
    relationship that FinEdge influenced Lexora would never appear as a hop.

    Scoped to the entry points rather than everything retrieved: across the
    full result set this degenerates into listing every edge in the
    neighbourhood, which is noise. Between the handful of entities the
    question actually named, every edge is worth showing.

    Only the first edge found for a pair is kept, since the walk sees each
    relationship from both ends ("influenced" and "influenced by" describe one
    fact, not two).
    """
    present = {entity_key(e): e for e in entities}
    steps, seen_pairs = [], set()

    for entity in entities:
        for neighbour, label in neighbours(entity):
            key = entity_key(neighbour)
            if key not in present:
                continue
            pair = frozenset((entity_key(entity), key))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            steps.append(Step(source=entity, label=label, target=neighbour))

    return steps


def retrieve(question, max_hops=None, use_model=True):
    """Full retrieval for a question. Returns entry points and what surrounds them."""
    seeds, method = find_entry_points(question, use_model=use_model)
    related = collect_related(seeds, max_hops=max_hops) if seeds else []
    cross = connections_among(seeds) if len(seeds) > 1 else []
    return {
        "question": question,
        "entry_points": seeds,
        "entry_method": method,
        "related": related,
        "connections": cross,
    }
