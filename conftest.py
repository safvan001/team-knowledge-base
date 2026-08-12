"""Shared fixtures.

Two kinds of test data. `sample_kb` runs the real seed command so the tests
that matter - the ones about connected answers - exercise the same ingestion
path a reviewer would. `small_kb` builds a handful of objects by hand for
tests that need to control exactly what exists.

No test reaches the network: the retrieval cascade is called with
`use_model=False`, and the one test that covers the model path stubs it.
"""

import pytest
from django.core.management import call_command

from knowledge.models import Client, Decision, Document, Person, Project, Topic


@pytest.fixture
def sample_kb(db):
    """The full fictional data set, loaded the way the application loads it."""
    call_command("seed", verbosity=0)
    return None


@pytest.fixture
def small_kb(db):
    """A minimal, fully controlled knowledge base."""
    alice = Person.objects.create(name="Alice Stone", role="Engineer")
    bob = Person.objects.create(name="Bob Rivers", role="Analyst")
    acme = Client.objects.create(name="Acme Corp", industry="Retail")
    topic = Topic.objects.create(name="Structured Retrieval")

    project = Project.objects.create(
        name="Acme Search Revamp", client=acme, lead=alice, status="In Progress"
    )
    project.team.set([alice, bob])
    project.topics.set([topic])

    decision = Decision.objects.create(
        title="Use relationships over similarity",
        summary="Similarity missed the important links.",
        project=project,
        made_by=alice,
        date="2025-05-01",
    )
    decision.participants.set([alice, bob])
    decision.topics.set([topic])

    return {
        "alice": alice,
        "bob": bob,
        "acme": acme,
        "topic": topic,
        "project": project,
        "decision": decision,
    }


@pytest.fixture
def api_user(db):
    """An authenticated user, since writes require one."""
    from django.contrib.auth.models import User

    return User.objects.create_user(username="reviewer", password="reviewer-pass")


@pytest.fixture
def auth_client(api_user):
    from rest_framework.test import APIClient

    client = APIClient()
    client.force_authenticate(user=api_user)
    return client


@pytest.fixture
def anon_client():
    from rest_framework.test import APIClient

    return APIClient()
