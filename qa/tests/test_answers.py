"""The answer layer: is the output grounded, and does it show its working?

The value here is not the prose. It is that the prose is built from an
evidence bundle the user can inspect, and that the system still answers
usefully when no model is configured.
"""

from knowledge.models import Document
from qa.answers import answer_question, build_context, template_answer
from qa.retrieval import retrieve


class TestGroundedOutput:
    def test_answer_ships_with_the_connections_behind_it(self, sample_kb):
        result = answer_question("What has Rahul Mehta worked on?", use_model=False)

        assert result["evidence"]
        for item in result["evidence"]:
            if item["hops"] > 0:
                assert item["path"], "every connected entity must explain itself"
                assert item["steps"]

    def test_evidence_steps_name_the_relationship(self, sample_kb):
        result = answer_question("Tell me about Lexora Knowledge Core", use_model=False)

        steps = [s for item in result["evidence"] for s in item["steps"]]
        labels = {s["relationship"] for s in steps}

        # Untyped "related to" edges would make the evidence meaningless.
        assert "led by" in labels or "team member" in labels

    def test_context_contains_the_facts_needed_to_answer(self, sample_kb):
        result = retrieve("What has Rahul Mehta worked on?", use_model=False)
        context = build_context(result)

        assert "Rahul Mehta" in context
        assert "Lexora Knowledge Core" in context
        assert "Connection:" in context


class TestWithoutAModel:
    def test_system_answers_with_no_api_key_configured(self, sample_kb, settings):
        settings.GEMINI_API_KEY = ""

        result = answer_question("Who worked on Lexora?", use_model=True)

        assert result["generated_by"] == "template"
        assert result["answer"].strip()
        assert result["evidence"]

    def test_template_answer_names_real_connected_entities(self, sample_kb):
        result = retrieve("What has Rahul Mehta worked on?", use_model=False)

        text = template_answer(result)

        assert "Lexora Knowledge Core" in text
        assert "via" in text  # each item states how it was reached

    def test_unanswerable_question_says_so_rather_than_inventing(self, sample_kb):
        result = answer_question("What is the capital of Norway?", use_model=False)

        assert result["entry_points"] == []
        assert "Nothing in the knowledge base" in result["answer"]


class TestWithAModel:
    def test_generated_prose_is_used_when_available(self, sample_kb, settings, monkeypatch):
        settings.GEMINI_API_KEY = "test-key"
        monkeypatch.setattr(
            "qa.answers.write_answer", lambda q, c: "Rahul led both projects."
        )

        result = answer_question("What has Rahul Mehta worked on?", use_model=True)

        assert result["generated_by"] == "gemini"
        assert result["answer"] == "Rahul led both projects."

    def test_a_failing_model_degrades_instead_of_erroring(self, sample_kb, settings, monkeypatch):
        """A provider outage must not take the knowledge base down with it."""
        settings.GEMINI_API_KEY = "test-key"
        monkeypatch.setattr("qa.answers.write_answer", lambda q, c: None)

        result = answer_question("What has Rahul Mehta worked on?", use_model=True)

        assert result["generated_by"] == "template"
        assert result["evidence"]

    def test_the_model_only_ever_sees_retrieved_context(self, sample_kb, settings, monkeypatch):
        """Grounding is structural: nothing outside the evidence bundle is sent."""
        settings.GEMINI_API_KEY = "test-key"
        captured = {}

        def capture(question, context):
            captured["context"] = context
            return "answer"

        monkeypatch.setattr("qa.answers.write_answer", capture)
        answer_question("What has Rahul Mehta worked on?", use_model=True)

        expected = build_context(retrieve("What has Rahul Mehta worked on?", use_model=False))
        assert captured["context"] == expected


class TestAnswersFollowNewData:
    def test_a_document_added_now_changes_the_next_answer(self, sample_kb):
        """The knowledge base must stay useful as information arrives."""
        before = answer_question("What has Priya Nair worked on?", use_model=False)
        assert "GreenGrid" not in str(before["evidence"])

        Document.objects.create(
            title="GreenGrid scoping call",
            content="Priya Nair will run the GreenGrid Energy technical assessment.",
        )

        after = answer_question("What has Priya Nair worked on?", use_model=False)
        assert "GreenGrid" in str(after["evidence"])
