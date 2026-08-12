"""Automatic linking: does new information join the knowledge base by itself?

This is the behaviour behind "keep the knowledge useful when new information
is added". If these break, documents added later become orphans and the
answers quietly get worse rather than failing loudly.
"""

import pytest

from knowledge.linking import create_link, find_mentions, outgoing_links, relink_document
from knowledge.models import Document, Link, Person, Project


def test_document_links_itself_to_entities_it_names(small_kb):
    document = Document.objects.create(
        title="Retro notes",
        content="Alice Stone and Bob Rivers reviewed Acme Search Revamp last week.",
    )

    linked = {link.target for link in outgoing_links(document)}

    assert small_kb["alice"] in linked
    assert small_kb["bob"] in linked
    assert small_kb["project"] in linked


def test_linking_happens_on_save_without_being_asked(small_kb):
    """The signal, not the caller, is responsible. Every write path benefits."""
    document = Document.objects.create(title="Note", content="About Acme Search Revamp.")

    assert outgoing_links(document).filter(auto_created=True).exists()


def test_entity_added_later_is_linked_into_existing_documents(small_kb):
    """A person who joins tomorrow should not be invisible to yesterday's notes."""
    Document.objects.create(
        title="Planning note", content="Carol Diaz will take over the migration."
    )
    assert not Person.objects.filter(name="Carol Diaz").exists()

    carol = Person.objects.create(name="Carol Diaz", role="Engineer")

    document = Document.objects.get(title="Planning note")
    assert carol in {link.target for link in outgoing_links(document)}


def test_relinking_preserves_manually_created_links(small_kb):
    """Re-ingestion must never destroy a relationship a human recorded."""
    document = Document.objects.create(title="Note", content="Nothing relevant here.")
    manual = create_link(
        document, small_kb["project"], Link.RELATES_TO, evidence="added by hand"
    )

    relink_document(document)

    assert Link.objects.filter(pk=manual.pk).exists()


def test_relinking_replaces_stale_automatic_links(small_kb):
    document = Document.objects.create(title="Note", content="About Acme Search Revamp.")
    assert document.pk and outgoing_links(document).filter(auto_created=True).exists()

    document.content = "This paragraph no longer names any project."
    document.save()

    auto_targets = {
        link.target for link in outgoing_links(document).filter(auto_created=True)
    }
    assert small_kb["project"] not in auto_targets


def test_short_names_do_not_match(small_kb):
    """Guard against a two-letter entity name matching half the corpus."""
    Person.objects.create(name="Jo")

    mentions = find_mentions("Jo went to the shop and so did everyone else.")

    assert all(entity.pk != Person.objects.get(name="Jo").pk for entity, _ in mentions)


def test_matching_respects_word_boundaries(small_kb):
    """'RAG' must not match inside 'storage'."""
    from knowledge.models import Topic

    rag = Topic.objects.create(name="RAG")

    matched = {entity for entity, _ in find_mentions("We reviewed the storage layer.")}

    assert rag not in matched


def test_document_does_not_link_to_itself(small_kb):
    document = Document.objects.create(
        title="Acme Search Revamp", content="Acme Search Revamp progress update."
    )

    targets = {link.target for link in outgoing_links(document)}

    assert document not in targets


def test_deleting_an_entity_removes_its_links(small_kb):
    """Generic relations are not covered by cascades, so this is easy to get wrong."""
    document = Document.objects.create(title="Note", content="About Acme Search Revamp.")
    project_pk = small_kb["project"].pk
    assert Link.objects.filter(target_id=project_pk).exists()

    small_kb["project"].delete()

    assert not Link.objects.filter(target_id=project_pk, target_type__model="project").exists()


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Lexora is going well", "Lexora Knowledge Core"),
        ("FinEdge wrapped up in March", "FinEdge Research Assistant"),
    ],
)
def test_aliases_resolve_to_full_entity_names(sample_kb, text, expected):
    """Nobody writes 'Lexora Knowledge Core' in a Slack message."""
    matched = {str(entity) for entity, _ in find_mentions(text)}

    assert expected in matched
