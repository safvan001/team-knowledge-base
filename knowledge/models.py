"""
Entities and relationships for the team knowledge base.

Two kinds of relationship live here:

1. Known-shape relationships are plain ForeignKey / ManyToManyField.
   A project always belongs to at most one client and is always led by one
   person, so those are columns. They are validated, indexed and cheap to
   query, and they cover everything the structured sample data gives us.

2. Discovered relationships live in the `Link` table. A document can mention
   any entity type, and links such as "FinEdge influenced Lexora" only exist
   in prose. Modelling those as columns would mean a migration per new
   relationship type, so they get one generic table instead.

The retrieval layer walks both kinds through `related_entities()`, so callers
never need to care which storage a particular connection came from.
"""

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models


def _rows(pairs):
    """Drop empty values so entity pages do not show blank fields."""
    return [(label, value) for label, value in pairs if value not in (None, "", [])]


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True

    def summary_rows(self):
        """(label, value) pairs for the entity page. Empty values are dropped."""
        return []

    @property
    def entity_type(self):
        return type(self).__name__.lower()


class Person(TimestampedModel):
    name = models.CharField(max_length=200, unique=True)
    role = models.CharField(max_length=200, blank=True)
    email = models.EmailField(blank=True)
    skills = models.JSONField(default=list, blank=True)
    joined = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "people"

    def __str__(self):
        return self.name

    def summary_rows(self):
        return _rows([
            ("Role", self.role),
            ("Email", self.email),
            ("Skills", ", ".join(self.skills or [])),
            ("Joined", self.joined),
        ])


class Client(TimestampedModel):
    STATUS_CHOICES = [("Active", "Active"), ("Past", "Past"), ("Prospect", "Prospect")]

    name = models.CharField(max_length=200, unique=True)
    industry = models.CharField(max_length=200, blank=True)
    size = models.CharField(max_length=100, blank=True)
    primary_contact = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=50, choices=STATUS_CHOICES, default="Active")
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def summary_rows(self):
        return _rows([
            ("Industry", self.industry),
            ("Size", self.size),
            ("Primary contact", self.primary_contact),
            ("Status", self.status),
            ("Notes", self.notes),
        ])


class Topic(TimestampedModel):
    name = models.CharField(max_length=200, unique=True)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def summary_rows(self):
        return _rows([("Description", self.description)])


class Project(TimestampedModel):
    STATUS_CHOICES = [
        ("Discovery", "Discovery"),
        ("In Progress", "In Progress"),
        ("Completed", "Completed"),
        ("On Hold", "On Hold"),
    ]

    name = models.CharField(max_length=200, unique=True)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=50, choices=STATUS_CHOICES, default="Discovery")
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)

    # Internal projects have no client, so this is nullable on purpose.
    client = models.ForeignKey(
        Client, null=True, blank=True, on_delete=models.SET_NULL, related_name="projects"
    )
    # Losing the lead should never silently delete project history.
    lead = models.ForeignKey(
        Person, null=True, blank=True, on_delete=models.SET_NULL, related_name="led_projects"
    )
    team = models.ManyToManyField(Person, blank=True, related_name="projects")
    topics = models.ManyToManyField(Topic, blank=True, related_name="projects")

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def summary_rows(self):
        return _rows([
            ("Status", self.status),
            ("Client", self.client),
            ("Lead", self.lead),
            ("Team", ", ".join(p.name for p in self.team.all())),
            ("Topics", ", ".join(t.name for t in self.topics.all())),
            ("Started", self.start_date),
            ("Ended", self.end_date),
            ("Description", self.description),
        ])


class Decision(TimestampedModel):
    title = models.CharField(max_length=300)
    summary = models.TextField(blank=True)
    date = models.DateField(null=True, blank=True)

    # Company-wide decisions are not attached to any project.
    project = models.ForeignKey(
        Project, null=True, blank=True, on_delete=models.SET_NULL, related_name="decisions"
    )
    made_by = models.ForeignKey(
        Person, null=True, blank=True, on_delete=models.SET_NULL, related_name="decisions_made"
    )
    participants = models.ManyToManyField(Person, blank=True, related_name="decisions_involved_in")
    topics = models.ManyToManyField(Topic, blank=True, related_name="decisions")

    class Meta:
        ordering = ["-date", "title"]

    def __str__(self):
        return self.title

    def summary_rows(self):
        return _rows([
            ("Date", self.date),
            ("Made by", self.made_by),
            ("Project", self.project),
            ("Participants", ", ".join(p.name for p in self.participants.all())),
            ("Topics", ", ".join(t.name for t in self.topics.all())),
            ("Summary", self.summary),
        ])


class Document(TimestampedModel):
    SOURCE_CHOICES = [
        ("markdown", "Markdown file"),
        ("slack", "Slack message"),
        ("manual", "Entered manually"),
        ("google-docs", "Google Docs"),
    ]

    title = models.CharField(max_length=300)
    content = models.TextField(blank=True)
    source = models.CharField(max_length=50, choices=SOURCE_CHOICES, default="manual")
    # Stable identifier from the origin system; lets re-ingestion update rather
    # than duplicate. Unique only when set, hence null=True over blank default.
    external_ref = models.CharField(max_length=500, null=True, blank=True, unique=True)
    date = models.DateField(null=True, blank=True)
    author = models.ForeignKey(
        Person, null=True, blank=True, on_delete=models.SET_NULL, related_name="documents"
    )

    class Meta:
        ordering = ["-date", "title"]

    def __str__(self):
        return self.title

    def summary_rows(self):
        return _rows([
            ("Source", self.get_source_display()),
            ("Date", self.date),
            ("Author", self.author),
            ("Reference", self.external_ref),
            ("Content", self.content),
        ])


class Link(TimestampedModel):
    """A relationship that does not fit a fixed column.

    Used for two things: connections discovered by reading text (a document
    mentioning a project), and connections between entities of the same type
    that only exist in prose (FinEdge influenced Lexora).
    """

    MENTIONS = "MENTIONS"
    INFLUENCED = "INFLUENCED"
    SUPERSEDES = "SUPERSEDES"
    RELATES_TO = "RELATES_TO"

    REL_CHOICES = [
        (MENTIONS, "mentions"),
        (INFLUENCED, "influenced"),
        (SUPERSEDES, "supersedes"),
        (RELATES_TO, "relates to"),
    ]

    source_type = models.ForeignKey(
        ContentType, on_delete=models.CASCADE, related_name="outgoing_links"
    )
    source_id = models.PositiveIntegerField()
    source = GenericForeignKey("source_type", "source_id")

    target_type = models.ForeignKey(
        ContentType, on_delete=models.CASCADE, related_name="incoming_links"
    )
    target_id = models.PositiveIntegerField()
    target = GenericForeignKey("target_type", "target_id")

    rel_type = models.CharField(max_length=50, choices=REL_CHOICES, default=MENTIONS)
    # Where this link came from, so an answer can explain itself and so the
    # auto-linker can safely replace only its own rows.
    evidence = models.TextField(blank=True)
    auto_created = models.BooleanField(default=False)

    class Meta:
        indexes = [
            models.Index(fields=["source_type", "source_id"]),
            models.Index(fields=["target_type", "target_id"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["source_type", "source_id", "target_type", "target_id", "rel_type"],
                name="unique_link",
            )
        ]

    def __str__(self):
        return f"{self.source} --{self.rel_type}--> {self.target}"
