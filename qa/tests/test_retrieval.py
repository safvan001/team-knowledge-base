"""Retrieval: does the system start in the right place and walk far enough?

These tests never touch the network. The cascade is exercised with
`use_model=False` except for the one test that stubs the model call, which is
the point of ordering the cascade cheap-first in the first place.
"""

import pytest

from knowledge.models import Decision, Document, Person, Project
from qa.retrieval import (
    collect_related,
    connections_among,
    find_entry_points,
    match_names,
    retrieve,
)


class TestEntryPoints:
    def test_a_named_entity_is_found_without_a_model_call(self, small_kb):
        entities, method = find_entry_points("How is Acme Search Revamp going?", use_model=False)

        assert method == "name_match"
        assert small_kb["project"] in entities

    def test_falls_back_to_text_search_when_nothing_is_named(self, small_kb):
        Document.objects.create(
            title="Migration retro", content="The migration overran because of similarity ranking."
        )

        entities, method = find_entry_points("why did the migration overrun", use_model=False)

        assert method == "text_search"
        assert entities

    def test_returns_nothing_for_an_unrelated_question(self, small_kb):
        entities, method = find_entry_points("what is the weather in Oslo", use_model=False)

        assert entities == []
        assert method == "none"

    def test_model_extraction_runs_only_when_name_matching_fails(self, small_kb, monkeypatch):
        """The expensive step must not fire for questions the cheap step handles."""
        calls = []

        def spy(question, names):
            calls.append(question)
            return []

        monkeypatch.setattr("qa.llm.llm_available", lambda: True)
        monkeypatch.setattr("qa.llm.extract_entities", spy)

        find_entry_points("How is Acme Search Revamp going?", use_model=True)
        assert calls == []

        find_entry_points("tell me about the retail client", use_model=True)
        assert len(calls) == 1

    def test_model_extraction_resolves_a_description_to_an_entity(self, small_kb, monkeypatch):
        monkeypatch.setattr("qa.llm.llm_available", lambda: True)
        monkeypatch.setattr("qa.llm.extract_entities", lambda q, names: ["Acme Corp"])

        entities, method = find_entry_points("tell me about the retail client", use_model=True)

        assert method == "model_extraction"
        assert small_kb["acme"] in entities

    def test_entry_points_are_not_duplicated(self, sample_kb):
        entities, _ = find_entry_points("Lexora Lexora Lexora", use_model=False)

        keys = [(type(e).__name__, e.pk) for e in entities]
        assert len(keys) == len(set(keys))


class TestTraversal:
    def test_reaches_declared_relationships(self, small_kb):
        found = collect_related([small_kb["project"]], max_hops=1)
        reached = {f.entity for f in found}

        assert small_kb["alice"] in reached
        assert small_kb["acme"] in reached
        assert small_kb["decision"] in reached

    def test_walks_further_than_one_step(self, small_kb):
        """Bob is two hops from the client: client -> project -> team member."""
        found = collect_related([small_kb["acme"]], max_hops=2)
        reached = {f.entity: f for f in found}

        assert small_kb["bob"] in reached
        assert reached[small_kb["bob"]].hops == 2

    def test_hop_limit_is_respected(self, small_kb):
        found = collect_related([small_kb["acme"]], max_hops=1)

        assert small_kb["bob"] not in {f.entity for f in found}

    def test_every_entity_records_how_it_was_reached(self, small_kb):
        found = collect_related([small_kb["project"]], max_hops=2)

        for item in found:
            if item.hops > 0:
                assert item.path, f"{item.entity} has no path"
                assert "-->" in item.path_text()

    def test_paths_are_the_shortest_available(self, small_kb):
        """Breadth-first order matters: evidence should be the most direct."""
        found = {f.entity: f for f in collect_related([small_kb["project"]], max_hops=3)}

        assert found[small_kb["alice"]].hops == 1
        assert len(found[small_kb["alice"]].path) == 1

    def test_cycles_do_not_cause_infinite_walks(self, small_kb):
        """Person -> project -> person is a cycle; the walk must still terminate."""
        found = collect_related([small_kb["alice"]], max_hops=4)

        keys = [(type(f.entity).__name__, f.entity.pk) for f in found]
        assert len(keys) == len(set(keys))

    def test_walking_from_an_isolated_entity_returns_only_itself(self, small_kb):
        loner = Person.objects.create(name="Dana Quill", role="Advisor")

        found = collect_related([loner], max_hops=2)

        assert [f.entity for f in found] == [loner]


class TestConnectionsBetweenEntryPoints:
    def test_an_edge_between_two_named_entities_is_surfaced(self, sample_kb):
        """The FinEdge -> Lexora influence is the whole answer to the brief's
        second example, and breadth-first traversal alone would hide it."""
        result = retrieve(
            "What did we learn from the FinEdge project that is useful for Lexora?",
            use_model=False,
        )

        described = {step.as_text() for step in result["connections"]}
        assert any(
            "FinEdge Research Assistant --influenced--> Lexora Knowledge Core" in text
            for text in described
        )

    def test_one_relationship_is_not_reported_twice(self, small_kb):
        """The walk sees each edge from both ends; the output should not."""
        steps = connections_among([small_kb["project"], small_kb["alice"]])

        pairs = [frozenset(((type(s.source).__name__, s.source.pk),
                            (type(s.target).__name__, s.target.pk))) for s in steps]
        assert len(pairs) == len(set(pairs))


class TestConnectedAnswers:
    """The questions the brief itself uses to describe a strong answer."""

    def test_who_worked_on_lexora_and_what_was_decided(self, sample_kb):
        result = retrieve(
            "Who worked on the Lexora project and what key decisions were made "
            "about its approach?",
            use_model=False,
        )
        reached = {str(f.entity) for f in result["related"]}

        # The team, from the project's declared relationships.
        assert {"Rahul Mehta", "Priya Nair", "Sneha Patel"} <= reached
        # The decision, and it is not in the same document as the team list.
        assert "Prefer structured knowledge over pure vector RAG for Lexora" in reached

    def test_finedge_lesson_connects_to_the_lexora_decision(self, sample_kb):
        """Neither project's name appears in the other's structured record.
        Only the recorded influence relationship connects them."""
        result = retrieve(
            "What did we learn from the FinEdge project that is useful for Lexora?",
            use_model=False,
        )
        reached = {str(f.entity) for f in result["related"]}

        assert "Prefer structured knowledge over pure vector RAG for Lexora" in reached
        assert any("Handover" in name for name in reached)

    def test_slack_decision_pulls_in_people_project_and_discussion(self, sample_kb):
        result = retrieve(
            "Show me everything related to the decision about not integrating "
            "Slack in the internal knowledge base.",
            use_model=False,
        )
        reached = {str(f.entity) for f in result["related"]}

        assert "Do not build full Slack integration in v1 of internal KB" in reached
        assert "Ananya Sharma" in reached          # who made it
        assert "Internal Knowledge Base (v1)" in reached  # the project it belongs to

    def test_a_question_naming_a_person_finds_their_work(self, sample_kb):
        result = retrieve("What has Rahul Mehta worked on?", use_model=False)
        reached = {str(f.entity) for f in result["related"]}

        assert "Lexora Knowledge Core" in reached
        assert "FinEdge Research Assistant" in reached
