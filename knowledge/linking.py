"""Automatic linking of free text to entities.

Requirement: the knowledge base must stay useful as new information arrives.
If adding a document meant hand-wiring every connection, nobody would do it,
and the base would rot. So whenever a document is saved we scan its text for
names of known entities and record `Link` rows for what we find.

The matching is deliberately literal - name and alias occurrences, on word
boundaries. It is fast, has no dependencies, and is easy to reason about when
a reviewer asks why a particular link exists. Its limits are real and known:
it will not catch a paraphrase like "the legal client", and a short entity
name that happens to be a common word would over-match. Aliases cover the
first case for the entities we care about; the minimum length rule and word
boundaries cover the second.

Links created here are marked `auto_created=True`. Re-saving a document
replaces only those, so relationships a human added by hand are never
destroyed by re-ingestion.
"""

import re

from django.contrib.contenttypes.models import ContentType
from django.db import transaction

from .models import Client, Decision, Document, Link, Person, Project, Topic

# Entity types that can take part in a generic Link.
LINKABLE_MODELS = {
    "person": Person,
    "client": Client,
    "project": Project,
    "decision": Decision,
    "document": Document,
    "topic": Topic,
}

# Short names produce false positives ("RAG" is fine, "AI" is not).
MIN_NAME_LENGTH = 3

# Extra surface forms that should resolve to an entity. Real deployments would
# store these on the model; for this data set a small static map is honest and
# keeps the schema focused.
ALIASES = {
    "project": {
        "Lexora Knowledge Core": ["Lexora"],
        "FinEdge Research Assistant": ["FinEdge"],
        "MediSync Protocol Hub": ["MediSync"],
        "Internal Knowledge Base (v1)": ["internal KB", "internal knowledge base"],
    },
    "client": {
        "Lexora Legal": ["Lexora"],
        "FinEdge Analytics": ["FinEdge"],
        "MediSync Health": ["MediSync"],
        "GreenGrid Energy": ["GreenGrid"],
    },
    "person": {
        "Ananya Sharma": ["Ananya"],
        "Rahul Mehta": ["Rahul"],
        "Priya Nair": ["Priya"],
        "Vikram Singh": ["Vikram"],
        "Sneha Patel": ["Sneha"],
        "Arjun Reddy": ["Arjun"],
    },
}


def content_type_for(model):
    return ContentType.objects.get_for_model(model)


def surface_forms(obj, model_name):
    """Every string that should resolve to this entity."""
    primary = getattr(obj, "name", None) or getattr(obj, "title", "")
    forms = [primary]
    forms.extend(ALIASES.get(model_name, {}).get(primary, []))
    return [f for f in forms if f and len(f) >= MIN_NAME_LENGTH]


def find_mentions(text, exclude=None):
    """Return entities whose name or alias appears in `text`.

    `exclude` is the entity the text belongs to, so a document never links to
    itself. Returns a list of (entity, matched_form) pairs.
    """
    if not text:
        return []

    found = []
    for model_name, model in LINKABLE_MODELS.items():
        for obj in model.objects.all():
            if exclude is not None and obj == exclude:
                continue
            for form in surface_forms(obj, model_name):
                # Word boundaries stop "RAG" matching inside "storage".
                if re.search(rf"\b{re.escape(form)}\b", text, re.IGNORECASE):
                    found.append((obj, form))
                    break
    return found


def _snippet(text, form, width=90):
    """A short quote around the match, so a link can justify itself."""
    match = re.search(rf"\b{re.escape(form)}\b", text, re.IGNORECASE)
    if not match:
        return ""
    start = max(0, match.start() - width // 2)
    end = min(len(text), match.end() + width // 2)
    return ("..." if start > 0 else "") + text[start:end].strip().replace("\n", " ") + (
        "..." if end < len(text) else ""
    )


@transaction.atomic
def relink_document(document):
    """Rebuild this document's automatic links. Returns the links created.

    Only auto-created links are removed first; manual ones survive.
    """
    doc_type = content_type_for(Document)
    Link.objects.filter(
        source_type=doc_type,
        source_id=document.pk,
        rel_type=Link.MENTIONS,
        auto_created=True,
    ).delete()

    searchable = f"{document.title}\n{document.content}"
    created = []
    for entity, form in find_mentions(searchable, exclude=document):
        link, was_created = Link.objects.get_or_create(
            source_type=doc_type,
            source_id=document.pk,
            target_type=content_type_for(type(entity)),
            target_id=entity.pk,
            rel_type=Link.MENTIONS,
            defaults={
                "evidence": _snippet(searchable, form),
                "auto_created": True,
            },
        )
        if was_created:
            created.append(link)
    return created


def outgoing_links(obj):
    return Link.objects.filter(
        source_type=content_type_for(type(obj)), source_id=obj.pk
    ).select_related("source_type", "target_type")


def incoming_links(obj):
    return Link.objects.filter(
        target_type=content_type_for(type(obj)), target_id=obj.pk
    ).select_related("source_type", "target_type")


def create_link(source, target, rel_type, evidence="", auto_created=False):
    """Create a relationship between any two entities."""
    link, _ = Link.objects.get_or_create(
        source_type=content_type_for(type(source)),
        source_id=source.pk,
        target_type=content_type_for(type(target)),
        target_id=target.pk,
        rel_type=rel_type,
        defaults={"evidence": evidence, "auto_created": auto_created},
    )
    return link
