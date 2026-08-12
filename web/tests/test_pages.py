"""The pages a human uses.

Kept thin on purpose: the pages call the same retrieval code as the API, so
these tests check wiring and rendering rather than re-testing the logic.
"""

import pytest
from django.urls import reverse

from knowledge.models import Person, Project


class TestAskPage:
    def test_the_page_loads_with_no_question(self, sample_kb, client):
        response = client.get(reverse("ask"))

        assert response.status_code == 200
        assert b"Ask" in response.content

    def test_asking_shows_the_answer_and_the_connections(self, sample_kb, client):
        response = client.get(reverse("ask"), {"q": "What has Rahul Mehta worked on?"})
        body = response.content.decode()

        assert response.status_code == 200
        assert "Lexora Knowledge Core" in body
        assert "Connections used" in body
        # The path, not just the entity name, must reach the page. Arrows are
        # HTML-escaped by the template, which is what we want to see.
        assert "--&gt;" in body
        assert "leads" in body or "led by" in body

    def test_an_unanswerable_question_renders_without_erroring(self, sample_kb, client):
        response = client.get(reverse("ask"), {"q": "capital of Norway"})

        assert response.status_code == 200

    def test_an_empty_question_is_treated_as_no_question(self, sample_kb, client):
        response = client.get(reverse("ask"), {"q": "   "})

        assert response.status_code == 200


class TestBrowseAndEntityPages:
    def test_browse_lists_every_entity_type(self, sample_kb, client):
        body = client.get(reverse("browse")).content.decode()

        for heading in ["Projects", "People", "Clients", "Decisions", "Documents", "Topics"]:
            assert heading in body

    @pytest.mark.parametrize(
        "entity_type,model", [("project", Project), ("person", Person)]
    )
    def test_an_entity_page_shows_its_connections(self, sample_kb, client, entity_type, model):
        entity = model.objects.first()

        response = client.get(reverse("entity", args=[entity_type, entity.pk]))
        body = response.content.decode()

        assert response.status_code == 200
        assert "Directly connected" in body

    def test_a_lexora_page_reaches_its_decision(self, sample_kb, client):
        project = Project.objects.get(name="Lexora Knowledge Core")

        body = client.get(reverse("entity", args=["project", project.pk])).content.decode()

        assert "Prefer structured knowledge over pure vector RAG" in body
        assert "Rahul Mehta" in body

    def test_an_unknown_entity_type_returns_404(self, sample_kb, client):
        assert client.get("/unicorn/1/").status_code == 404

    def test_a_missing_entity_returns_404(self, sample_kb, client):
        assert client.get("/project/99999/").status_code == 404

    def test_an_isolated_entity_page_still_renders(self, small_kb, client):
        loner = Person.objects.create(name="Dana Quill")

        response = client.get(reverse("entity", args=["person", loner.pk]))

        assert response.status_code == 200
        assert b"Nothing connects to this yet" in response.content
