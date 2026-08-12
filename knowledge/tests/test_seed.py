"""Seeding: does loading the sample data reconstruct the relationships?

The seed runs through the normal save path, so these tests double as
ingestion tests. If seeding produces the right connections, adding the same
information through the API produces them too.
"""

from django.core.management import call_command

from knowledge.models import Client, Decision, Document, Link, Person, Project, Topic


def test_all_entity_types_are_loaded(sample_kb):
    assert Person.objects.count() == 6
    assert Client.objects.count() == 4
    assert Project.objects.count() == 4
    assert Decision.objects.count() == 4
    assert Document.objects.exists()
    assert Topic.objects.exists()


def test_structured_relationships_survive_the_import(sample_kb):
    lexora = Project.objects.get(name="Lexora Knowledge Core")

    assert lexora.client.name == "Lexora Legal"
    assert lexora.lead.name == "Rahul Mehta"
    assert {p.name for p in lexora.team.all()} == {"Rahul Mehta", "Priya Nair", "Sneha Patel"}
    assert "Structured Retrieval" in {t.name for t in lexora.topics.all()}


def test_decisions_keep_their_people_and_project(sample_kb):
    decision = Decision.objects.get(title__startswith="Prefer structured knowledge")

    assert decision.made_by.name == "Rahul Mehta"
    assert decision.project.name == "Lexora Knowledge Core"
    assert decision.participants.count() == 4


def test_entities_without_a_parent_are_allowed(sample_kb):
    """The internal project has no client and one decision has no project.
    Both are real states in the data, not errors."""
    assert Project.objects.get(name="Internal Knowledge Base (v1)").client is None
    assert Decision.objects.get(title__startswith="Use Gemini").project is None


def test_documents_and_slack_messages_are_both_stored(sample_kb):
    assert Document.objects.filter(source="markdown").count() == 5
    assert Document.objects.filter(source="slack").count() == 5


def test_documents_are_linked_to_what_they_mention(sample_kb):
    handover = Document.objects.get(title__contains="Handover")

    linked = {str(link.target) for link in Link.objects.filter(source_id=handover.pk,
                                                              source_type__model="document")}
    assert "Rahul Mehta" in linked
    assert "FinEdge Research Assistant" in linked


def test_the_influence_stated_in_prose_is_recorded_as_a_relationship(sample_kb):
    """The handover says the FinEdge lesson shaped Lexora. No JSON field
    carries that, and without it the brief's second example is unanswerable."""
    finedge = Project.objects.get(name="FinEdge Research Assistant")
    lexora = Project.objects.get(name="Lexora Knowledge Core")

    link = Link.objects.get(
        source_id=finedge.pk, target_id=lexora.pk, rel_type=Link.INFLUENCED
    )

    assert "influenced our later thinking" in link.evidence


def test_seeding_twice_does_not_duplicate_anything(sample_kb):
    counts = (Person.objects.count(), Project.objects.count(),
              Document.objects.count(), Link.objects.count())

    call_command("seed", verbosity=0)

    assert (Person.objects.count(), Project.objects.count(),
            Document.objects.count(), Link.objects.count()) == counts


def test_a_topic_named_only_by_a_project_is_still_created(sample_kb):
    """projects.json references topics absent from topics.json. Dropping them
    would silently lose the relationship."""
    assert Topic.objects.filter(name="Finance Research").exists()
