"""The API surface: CRUD, relationships, permissions and bad input.

The last group is deliberate. The brief says the reviewers will try to break
the system, so the cases that should fail cleanly are tested as carefully as
the ones that should succeed.
"""

import pytest

from knowledge.models import Client, Decision, Document, Link, Person, Project, Topic


class TestCrud:
    def test_every_entity_type_can_be_listed(self, sample_kb, anon_client):
        for endpoint in ["people", "clients", "projects", "decisions", "documents", "topics"]:
            response = anon_client.get(f"/api/{endpoint}/")
            assert response.status_code == 200, endpoint
            assert response.json()["count"] > 0, endpoint

    def test_a_project_can_be_created_with_its_relationships(self, small_kb, auth_client):
        response = auth_client.post(
            "/api/projects/",
            {
                "name": "Acme Phase Two",
                "status": "Discovery",
                "client": small_kb["acme"].pk,
                "lead": small_kb["alice"].pk,
                "team": [small_kb["alice"].pk, small_kb["bob"].pk],
                "topics": [small_kb["topic"].pk],
            },
            format="json",
        )

        assert response.status_code == 201
        project = Project.objects.get(name="Acme Phase Two")
        assert project.lead == small_kb["alice"]
        assert set(project.team.all()) == {small_kb["alice"], small_kb["bob"]}

    def test_reading_a_project_expands_its_relationships(self, small_kb, anon_client):
        response = anon_client.get(f"/api/projects/{small_kb['project'].pk}/")
        body = response.json()

        assert body["lead_detail"]["name"] == "Alice Stone"
        assert body["client_detail"]["name"] == "Acme Corp"
        assert {p["name"] for p in body["team_detail"]} == {"Alice Stone", "Bob Rivers"}
        assert body["decisions"][0]["title"] == "Use relationships over similarity"

    def test_a_project_can_be_updated(self, small_kb, auth_client):
        response = auth_client.patch(
            f"/api/projects/{small_kb['project'].pk}/", {"status": "Completed"}, format="json"
        )

        assert response.status_code == 200
        small_kb["project"].refresh_from_db()
        assert small_kb["project"].status == "Completed"

    def test_deleting_a_project_keeps_its_decisions(self, small_kb, auth_client):
        """History is the point of the system; deleting a project must not
        erase the record of what was decided."""
        decision_pk = small_kb["decision"].pk

        auth_client.delete(f"/api/projects/{small_kb['project'].pk}/")

        decision = Decision.objects.get(pk=decision_pk)
        assert decision.project is None

    def test_entities_can_be_searched(self, sample_kb, anon_client):
        response = anon_client.get("/api/projects/?search=Lexora")

        assert response.json()["count"] == 1

    def test_entities_can_be_filtered(self, sample_kb, anon_client):
        response = anon_client.get("/api/clients/?status=Past")

        names = [c["name"] for c in response.json()["results"]]
        assert names == ["FinEdge Analytics"]


class TestRelationshipEndpoint:
    def test_links_can_be_created_between_any_two_entity_types(self, small_kb, auth_client):
        response = auth_client.post(
            "/api/links/",
            {
                "source_model": "project",
                "source_id": small_kb["project"].pk,
                "target_model": "person",
                "target_id": small_kb["bob"].pk,
                "rel_type": "RELATES_TO",
                "evidence": "raised in the retro",
            },
            format="json",
        )

        assert response.status_code == 201
        assert Link.objects.filter(rel_type="RELATES_TO").exists()

    def test_related_endpoint_returns_paths_not_just_entities(self, sample_kb, anon_client):
        project = Project.objects.get(name="Lexora Knowledge Core")

        response = anon_client.get(f"/api/related/?type=project&id={project.pk}&hops=2")
        body = response.json()

        assert body["count"] > 0
        assert all("path" in item for item in body["related"])

    def test_relinking_a_document_is_available_on_demand(self, small_kb, auth_client):
        document = Document.objects.create(title="Note", content="Nothing yet.")
        document.content = "Now it mentions Acme Search Revamp."
        Document.objects.filter(pk=document.pk).update(content=document.content)

        response = auth_client.post(f"/api/documents/{document.pk}/relink/")

        assert response.status_code == 200
        assert response.json()["links_created"] >= 1


class TestPermissions:
    def test_reading_does_not_require_authentication(self, sample_kb, anon_client):
        assert anon_client.get("/api/projects/").status_code == 200

    def test_writing_requires_authentication(self, small_kb, anon_client):
        response = anon_client.post("/api/projects/", {"name": "Sneaky"}, format="json")

        assert response.status_code in (401, 403)
        assert not Project.objects.filter(name="Sneaky").exists()


class TestBadInput:
    def test_a_duplicate_name_is_rejected(self, small_kb, auth_client):
        response = auth_client.post(
            "/api/projects/", {"name": "Acme Search Revamp"}, format="json"
        )

        assert response.status_code == 400

    def test_an_end_date_before_the_start_date_is_rejected(self, small_kb, auth_client):
        response = auth_client.post(
            "/api/projects/",
            {"name": "Backwards", "start_date": "2025-06-01", "end_date": "2025-01-01"},
            format="json",
        )

        assert response.status_code == 400
        assert "end_date" in response.json()

    def test_a_link_to_a_missing_entity_is_rejected(self, small_kb, auth_client):
        response = auth_client.post(
            "/api/links/",
            {
                "source_model": "project",
                "source_id": small_kb["project"].pk,
                "target_model": "person",
                "target_id": 99999,
                "rel_type": "RELATES_TO",
            },
            format="json",
        )

        assert response.status_code == 400

    def test_a_link_to_an_unknown_entity_type_is_rejected(self, small_kb, auth_client):
        response = auth_client.post(
            "/api/links/",
            {
                "source_model": "unicorn",
                "source_id": 1,
                "target_model": "person",
                "target_id": small_kb["bob"].pk,
            },
            format="json",
        )

        assert response.status_code == 400

    def test_an_entity_cannot_link_to_itself(self, small_kb, auth_client):
        response = auth_client.post(
            "/api/links/",
            {
                "source_model": "project",
                "source_id": small_kb["project"].pk,
                "target_model": "project",
                "target_id": small_kb["project"].pk,
            },
            format="json",
        )

        assert response.status_code == 400

    def test_several_documents_can_omit_the_external_reference(self, small_kb, auth_client):
        """Blank must store as NULL, not "". A unique constraint allows many
        NULLs but only one empty string, so storing "" would make the second
        manually created document fail with a 500."""
        for title in ("First note", "Second note"):
            response = auth_client.post(
                "/api/documents/",
                {"title": title, "content": "Nothing linked here.", "external_ref": ""},
                format="json",
            )
            assert response.status_code == 201, response.data

        assert Document.objects.filter(external_ref__isnull=True).count() == 2

    def test_a_duplicate_external_reference_is_still_rejected(self, small_kb, auth_client):
        """Allowing blanks must not weaken the constraint where it matters."""
        payload = {"title": "A", "content": "x", "external_ref": "file:same.md"}
        assert auth_client.post("/api/documents/", payload, format="json").status_code == 201

        response = auth_client.post(
            "/api/documents/", dict(payload, title="B"), format="json"
        )
        assert response.status_code == 400

    def test_related_endpoint_rejects_an_unknown_type(self, sample_kb, anon_client):
        assert anon_client.get("/api/related/?type=unicorn&id=1").status_code == 400

    def test_related_endpoint_404s_on_a_missing_entity(self, sample_kb, anon_client):
        assert anon_client.get("/api/related/?type=project&id=99999").status_code == 404


class TestAskEndpoint:
    def test_a_question_returns_an_answer_and_its_evidence(self, sample_kb, anon_client):
        response = anon_client.post(
            "/api/ask/",
            {"question": "What has Rahul Mehta worked on?", "use_model": False},
            format="json",
        )

        body = response.json()
        assert response.status_code == 200
        assert body["answer"]
        assert body["evidence"]
        assert body["entry_method"] == "name_match"

    def test_an_empty_question_is_rejected(self, sample_kb, anon_client):
        response = anon_client.post("/api/ask/", {"question": "   "}, format="json")

        assert response.status_code == 400

    def test_a_missing_question_is_rejected(self, sample_kb, anon_client):
        assert anon_client.post("/api/ask/", {}, format="json").status_code == 400

    def test_the_hop_limit_is_bounded(self, sample_kb, anon_client):
        """An unbounded walk on a large base would be a denial of service."""
        response = anon_client.post(
            "/api/ask/", {"question": "Lexora", "max_hops": 99}, format="json"
        )

        assert response.status_code == 400
