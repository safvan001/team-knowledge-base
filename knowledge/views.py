"""CRUD endpoints for every entity, plus a connections endpoint.

The viewsets are deliberately plain - the interesting behaviour lives in the
models, the linking module and the retrieval layer, not here. Each one gets
filtering and search so a reviewer can explore the data from the browsable
API without writing any code.
"""

from django.shortcuts import get_object_or_404
from rest_framework import viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .linking import LINKABLE_MODELS, relink_document
from .models import Client, Decision, Document, Link, Person, Project, Topic
from .serializers import (
    ClientSerializer,
    DecisionSerializer,
    DocumentSerializer,
    LinkSerializer,
    PersonSerializer,
    ProjectSerializer,
    TopicSerializer,
)


class PersonViewSet(viewsets.ModelViewSet):
    queryset = Person.objects.prefetch_related(
        "projects", "led_projects", "decisions_made"
    )
    serializer_class = PersonSerializer
    filterset_fields = ["role"]
    search_fields = ["name", "role", "email"]


class ClientViewSet(viewsets.ModelViewSet):
    queryset = Client.objects.prefetch_related("projects")
    serializer_class = ClientSerializer
    filterset_fields = ["status", "industry"]
    search_fields = ["name", "industry", "notes"]


class TopicViewSet(viewsets.ModelViewSet):
    queryset = Topic.objects.prefetch_related("projects", "decisions")
    serializer_class = TopicSerializer
    search_fields = ["name", "description"]


class ProjectViewSet(viewsets.ModelViewSet):
    queryset = Project.objects.select_related("client", "lead").prefetch_related(
        "team", "topics", "decisions"
    )
    serializer_class = ProjectSerializer
    filterset_fields = ["status", "client", "lead"]
    search_fields = ["name", "description"]


class DecisionViewSet(viewsets.ModelViewSet):
    queryset = Decision.objects.select_related("project", "made_by").prefetch_related(
        "participants", "topics"
    )
    serializer_class = DecisionSerializer
    filterset_fields = ["project", "made_by"]
    search_fields = ["title", "summary"]


class DocumentViewSet(viewsets.ModelViewSet):
    queryset = Document.objects.select_related("author")
    serializer_class = DocumentSerializer
    filterset_fields = ["source", "author"]
    search_fields = ["title", "content"]

    @action(detail=True, methods=["post"])
    def relink(self, request, pk=None):
        """Rebuild this document's automatic links on demand.

        Saving already does this. The endpoint exists for the case where
        entities were added after the document and a reviewer wants to see
        re-linking happen explicitly.
        """
        document = self.get_object()
        created = relink_document(document)
        return Response(
            {
                "document": document.title,
                "links_created": len(created),
                "links": [str(link) for link in created],
            }
        )


class LinkViewSet(viewsets.ModelViewSet):
    queryset = Link.objects.select_related("source_type", "target_type")
    serializer_class = LinkSerializer
    filterset_fields = ["rel_type", "auto_created"]


@api_view(["GET"])
@permission_classes([AllowAny])
def related_view(request):
    """Everything connected to one entity, with the path to each.

    Powers the connections view in the UI. Same traversal the answer layer
    uses, exposed directly so connections can be explored without asking a
    question.
    """
    from qa.answers import serialise_evidence
    from qa.retrieval import collect_related

    entity_type = request.GET.get("type", "").lower()
    entity_id = request.GET.get("id")
    hops = int(request.GET.get("hops", 2))

    if entity_type not in LINKABLE_MODELS:
        return Response(
            {"detail": f"type must be one of {sorted(LINKABLE_MODELS)}"}, status=400
        )

    entity = get_object_or_404(LINKABLE_MODELS[entity_type], pk=entity_id)
    found = collect_related([entity], max_hops=hops)

    return Response(
        {
            "entity": {"type": entity_type, "id": entity.pk, "label": str(entity)},
            "hops": hops,
            "count": len(found) - 1,
            "related": serialise_evidence({"related": found}),
        }
    )
