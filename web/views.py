"""Server-rendered pages.

Three pages, no build step and no frontend framework: asking a question,
browsing everything, and viewing one entity with its connections. They call
the same retrieval code as the API, so what a reviewer sees in the browser is
what the API returns.
"""

from django.shortcuts import get_object_or_404, render

from knowledge.linking import LINKABLE_MODELS
from knowledge.models import Client, Decision, Document, Person, Project, Topic
from qa.answers import answer_question
from qa.retrieval import collect_related, entity_label

# Shown on the ask page so a reviewer can try the system without inventing
# questions. Taken from the brief's own examples.
EXAMPLE_QUESTIONS = [
    "Who worked on the Lexora project and what key decisions were made about its approach?",
    "What did we learn from the FinEdge project that is useful for Lexora?",
    "Show me everything related to the decision about not integrating Slack in the internal knowledge base.",
    "What has Rahul Mehta worked on?",
]


def ask_page(request):
    question = request.GET.get("q", "").strip()
    result = answer_question(question) if question else None
    return render(
        request,
        "web/ask.html",
        {
            "question": question,
            "result": result,
            "examples": EXAMPLE_QUESTIONS,
        },
    )


def browse_page(request):
    return render(
        request,
        "web/browse.html",
        {
            "groups": [
                ("Projects", "project", Project.objects.select_related("client", "lead")),
                ("People", "person", Person.objects.all()),
                ("Clients", "client", Client.objects.all()),
                ("Decisions", "decision", Decision.objects.select_related("made_by")),
                ("Documents", "document", Document.objects.all()),
                ("Topics", "topic", Topic.objects.all()),
            ]
        },
    )


def entity_page(request, entity_type, entity_id):
    entity_type = entity_type.lower()
    if entity_type not in LINKABLE_MODELS:
        return render(request, "web/not_found.html", {"what": entity_type}, status=404)

    entity = get_object_or_404(LINKABLE_MODELS[entity_type], pk=entity_id)
    hops = int(request.GET.get("hops", 2))

    found = collect_related([entity], max_hops=hops)
    rows = [_display(f) for f in sorted(found, key=lambda f: f.hops)]

    return render(
        request,
        "web/entity.html",
        {
            "entity": entity,
            "entity_type": entity_type,
            "label": entity_label(entity),
            "direct": [r for r in rows if r["hops"] == 1],
            "indirect": [r for r in rows if r["hops"] > 1],
            "hops": hops,
        },
    )


def _display(found):
    """Flatten a traversal result into what the template needs."""
    return {
        "type": type(found.entity).__name__.lower(),
        "id": found.entity.pk,
        "label": entity_label(found.entity),
        "hops": found.hops,
        "path": found.path_text(),
    }
