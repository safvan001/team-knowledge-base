"""Turning retrieved context into an answer the user can check.

Every answer ships with the connections it was built from. That is deliberate:
an answer that cannot show its path is indistinguishable from a guess, and the
whole point of this system is that the connections - not the documents - carry
the knowledge.

Generation is optional. With a Gemini key the prose is written by the model;
without one, the same retrieved context is rendered as a structured summary.
Both are grounded in exactly the same evidence, so the system is fully
testable and demonstrable with no credentials.
"""

from knowledge.models import Client, Decision, Document, Person, Project, Topic

from .llm import llm_available, write_answer
from .retrieval import entity_label, retrieve

# Ordering for grouped output: the things people ask about first.
TYPE_ORDER = ["project", "decision", "person", "client", "document", "topic"]

TYPE_LABELS = {
    "project": "Projects",
    "decision": "Decisions",
    "person": "People",
    "client": "Clients",
    "document": "Documents",
    "topic": "Topics",
}


def describe(entity):
    """One line of substance about an entity, for the context bundle."""
    if isinstance(entity, Project):
        bits = [f"Project '{entity.name}' ({entity.status})"]
        if entity.client:
            bits.append(f"for client {entity.client.name}")
        if entity.lead:
            bits.append(f"led by {entity.lead.name}")
        if entity.start_date:
            bits.append(f"started {entity.start_date}")
        if entity.end_date:
            bits.append(f"ended {entity.end_date}")
        line = ", ".join(bits)
        return f"{line}. {entity.description}".strip()

    if isinstance(entity, Decision):
        bits = [f"Decision '{entity.title}'"]
        if entity.date:
            bits.append(f"made {entity.date}")
        if entity.made_by:
            bits.append(f"by {entity.made_by.name}")
        if entity.project:
            bits.append(f"on project {entity.project.name}")
        line = ", ".join(bits)
        return f"{line}. {entity.summary}".strip()

    if isinstance(entity, Person):
        skills = ", ".join(entity.skills or [])
        return f"Person {entity.name}, {entity.role}. Skills: {skills}".strip()

    if isinstance(entity, Client):
        return (
            f"Client {entity.name} ({entity.industry}, {entity.status}). {entity.notes}"
        ).strip()

    if isinstance(entity, Topic):
        return f"Topic '{entity.name}'. {entity.description}".strip()

    if isinstance(entity, Document):
        body = (entity.content or "").strip()
        if len(body) > 1200:
            body = body[:1200] + "..."
        header = f"Document '{entity.title}'"
        if entity.date:
            header += f" ({entity.date})"
        return f"{header}:\n{body}"

    return entity_label(entity)


def build_context(result):
    """The text handed to the model, and shown as evidence when there is none.

    Each entity is listed with the path that reached it. Those paths are what
    let the answer talk about connections rather than reciting facts.
    """
    lines = []

    if result["entry_points"]:
        lines.append("STARTING POINTS (entities the question refers to):")
        for entity in result["entry_points"]:
            lines.append(f"- {describe(entity)}")
        lines.append("")

    others = [f for f in result["related"] if f.hops > 0]
    if others:
        lines.append("CONNECTED INFORMATION (and how it connects):")
        for found in sorted(others, key=lambda f: f.hops):
            lines.append(f"- {describe(found.entity)}")
            lines.append(f"  Connection: {found.path_text()}")
        lines.append("")

    # Listed separately and last because these are the edges that tie the
    # retrieved set together, and they are what a "how does X relate to Y"
    # question actually turns on.
    if result.get("connections"):
        lines.append("DIRECT RELATIONSHIPS BETWEEN THE ABOVE:")
        for step in result["connections"]:
            lines.append(f"- {step.as_text()}")
        lines.append("")

    return "\n".join(lines).strip()


def group_by_type(found_items):
    grouped = {}
    for found in found_items:
        grouped.setdefault(type(found.entity).__name__.lower(), []).append(found)
    return grouped


def template_answer(result):
    """A grounded answer without a language model.

    Not a placeholder - it is the same evidence, rendered rather than
    paraphrased, and it keeps the system fully usable with no API key.
    """
    if not result["entry_points"]:
        return (
            "Nothing in the knowledge base matches this question. Try naming a "
            "project, person, client or topic."
        )

    starts = ", ".join(entity_label(e) for e in result["entry_points"])
    lines = [f"Starting from {starts}, the knowledge base connects to the following."]

    if result.get("connections"):
        lines.append("")
        lines.append("Direct relationships between these:")
        for step in result["connections"]:
            lines.append(f"  - {step.as_text()}")

    grouped = group_by_type([f for f in result["related"] if f.hops > 0])
    for type_name in TYPE_ORDER:
        items = grouped.get(type_name)
        if not items:
            continue
        lines.append("")
        lines.append(f"{TYPE_LABELS[type_name]}:")
        for found in sorted(items, key=lambda f: f.hops)[:6]:
            lines.append(f"  - {entity_label(found.entity)}")
            lines.append(f"    via {found.path_text()}")

    return "\n".join(lines)


def serialise_evidence(result):
    """The connections behind an answer, as data the UI and API can render."""
    entries = []
    for found in sorted(result["related"], key=lambda f: f.hops):
        entity = found.entity
        entries.append(
            {
                "type": type(entity).__name__.lower(),
                "id": entity.pk,
                "label": entity_label(entity),
                "hops": found.hops,
                "path": found.path_text(),
                "steps": [
                    {
                        "from": entity_label(step.source),
                        "relationship": step.label,
                        "to": entity_label(step.target),
                    }
                    for step in found.path
                ],
            }
        )
    return entries


def answer_question(question, max_hops=None, use_model=True):
    """Answer a question and return the evidence it rests on."""
    result = retrieve(question, max_hops=max_hops, use_model=use_model)
    context = build_context(result)

    generated = None
    if use_model and llm_available() and result["entry_points"]:
        generated = write_answer(question, context)

    return {
        "question": question,
        "answer": generated or template_answer(result),
        "generated_by": "gemini" if generated else "template",
        "entry_method": result["entry_method"],
        "entry_points": [
            {
                "type": type(e).__name__.lower(),
                "id": e.pk,
                "label": entity_label(e),
            }
            for e in result["entry_points"]
        ],
        "evidence": serialise_evidence(result),
        "connections": [
            {
                "from": entity_label(step.source),
                "relationship": step.label,
                "to": entity_label(step.target),
            }
            for step in result.get("connections", [])
        ],
        "context_used": context,
    }
