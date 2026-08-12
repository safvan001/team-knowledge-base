"""Load the sample data set into the knowledge base.

Everything goes through the ORM and the normal save path, so the automatic
linking that runs for a document added through the API also runs here. The
seed therefore exercises the ingestion pipeline rather than bypassing it - if
seeding works, adding data at runtime works.

Ordering matters: entities that others point at are created first, then the
relationships, then the documents (which link themselves against whatever
already exists).

Re-running is safe. Everything is matched on a natural key and updated in
place, so the command can be run repeatedly without duplicating rows.
"""

import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from knowledge.linking import create_link, relink_document
from knowledge.models import Client, Decision, Document, Link, Person, Project, Topic


class Command(BaseCommand):
    help = "Load the fictional sample data into the knowledge base."

    def add_arguments(self, parser):
        parser.add_argument(
            "--path",
            default=str(settings.SAMPLE_DATA_DIR),
            help="Directory holding the sample data files.",
        )
        parser.add_argument(
            "--flush",
            action="store_true",
            help="Delete existing knowledge base rows before loading.",
        )

    def handle(self, *args, **options):
        root = Path(options["path"])
        if not root.exists():
            self.stderr.write(self.style.ERROR(f"No sample data at {root}"))
            return

        if options["flush"]:
            self._flush()

        with transaction.atomic():
            people = self._load_people(root)
            clients = self._load_clients(root)
            topics = self._load_topics(root)
            projects = self._load_projects(root, clients, people, topics)
            self._load_decisions(root, projects, people, topics)
            self._load_documents(root, people)
            self._load_slack(root, people)
            self._load_curated_links(projects)

        self._report()

    # -- loaders ----------------------------------------------------------

    def _read(self, root, name):
        path = root / name
        if not path.exists():
            self.stdout.write(self.style.WARNING(f"Skipping missing file {name}"))
            return []
        return json.loads(path.read_text(encoding="utf-8"))

    def _load_people(self, root):
        by_ref = {}
        for row in self._read(root, "people.json"):
            person, _ = Person.objects.update_or_create(
                name=row["name"],
                defaults={
                    "role": row.get("role", ""),
                    "email": row.get("email", ""),
                    "skills": row.get("skills", []),
                    "joined": row.get("joined") or None,
                },
            )
            by_ref[row["id"]] = person
        return by_ref

    def _load_clients(self, root):
        by_ref = {}
        for row in self._read(root, "clients.json"):
            client, _ = Client.objects.update_or_create(
                name=row["name"],
                defaults={
                    "industry": row.get("industry", ""),
                    "size": row.get("size", ""),
                    "primary_contact": row.get("primary_contact", ""),
                    "status": row.get("status", "Active"),
                    "notes": row.get("notes", ""),
                },
            )
            by_ref[row["id"]] = client
        return by_ref

    def _load_topics(self, root):
        by_name = {}
        for row in self._read(root, "topics.json"):
            topic, _ = Topic.objects.update_or_create(
                name=row["name"], defaults={"description": row.get("description", "")}
            )
            by_name[row["name"]] = topic
        return by_name

    def _topic(self, topics, name):
        """Projects reference topics by name, and some are not in topics.json.

        Creating the missing ones keeps the relationship rather than dropping
        it, which is the behaviour we want at runtime too.
        """
        if name not in topics:
            topics[name], _ = Topic.objects.get_or_create(name=name)
        return topics[name]

    def _load_projects(self, root, clients, people, topics):
        by_ref = {}
        for row in self._read(root, "projects.json"):
            project, _ = Project.objects.update_or_create(
                name=row["name"],
                defaults={
                    "description": row.get("description", ""),
                    "status": row.get("status", "Discovery"),
                    "start_date": row.get("start_date") or None,
                    "end_date": row.get("end_date") or None,
                    "client": clients.get(row.get("client_id")),
                    "lead": people.get(row.get("lead")),
                },
            )
            project.team.set([people[p] for p in row.get("team", []) if p in people])
            project.topics.set(
                [self._topic(topics, name) for name in row.get("key_topics", [])]
            )
            by_ref[row["id"]] = project
        return by_ref

    def _load_decisions(self, root, projects, people, topics):
        for row in self._read(root, "decisions.json"):
            decision, _ = Decision.objects.update_or_create(
                title=row["title"],
                defaults={
                    "summary": row.get("summary", ""),
                    "date": row.get("date") or None,
                    "project": projects.get(row.get("project_id")),
                    "made_by": people.get(row.get("made_by")),
                },
            )
            decision.participants.set(
                [people[p] for p in row.get("participants", []) if p in people]
            )
            decision.topics.set(
                [self._topic(topics, name) for name in row.get("related_topics", [])]
            )

    def _load_documents(self, root, people):
        folder = root / "documents"
        if not folder.exists():
            return

        for path in sorted(folder.glob("*.md")):
            text = path.read_text(encoding="utf-8")
            title = self._title_from_markdown(text, fallback=path.stem)
            document, _ = Document.objects.update_or_create(
                external_ref=f"file:{path.name}",
                defaults={
                    "title": title,
                    "content": text,
                    "source": "markdown",
                    "date": self._date_from_markdown(text),
                    "author": self._author_from_markdown(text, people),
                },
            )
            # update_or_create fires post_save, which links the document. This
            # call is here so a re-run also picks up entities added since.
            relink_document(document)

    def _load_slack(self, root, people):
        folder = root / "slack-exports"
        if not folder.exists():
            return

        by_name = {p.name: p for p in people.values()}
        for path in sorted(folder.glob("*.json")):
            for index, message in enumerate(json.loads(path.read_text(encoding="utf-8"))):
                timestamp = message.get("ts", "")
                author = by_name.get(message.get("user", ""))
                document, _ = Document.objects.update_or_create(
                    external_ref=f"slack:{path.stem}:{index}",
                    defaults={
                        "title": f"Slack message from {message.get('user', 'unknown')}"
                        f" ({timestamp[:10]})",
                        "content": message.get("text", ""),
                        "source": "slack",
                        "date": timestamp[:10] or None,
                        "author": author,
                    },
                )
                relink_document(document)

    def _load_curated_links(self, projects):
        """Relationships stated in prose but absent from the structured files.

        The FinEdge handover says its lesson "influenced our later thinking on
        Lexora and the internal KB". No JSON field carries that, and it is
        exactly the kind of connection the team loses today, so it is recorded
        as an explicit relationship. This is the one place where reading the
        source material produced a link a script could not infer reliably.
        """
        finedge = projects.get("proj003")
        lexora = projects.get("proj001")
        internal = projects.get("proj004")
        if not finedge:
            return

        evidence = (
            "FinEdge handover notes: 'Pure document retrieval is useful but "
            "limited. For knowledge work, relationships and evolution of ideas "
            "matter a lot. This lesson influenced our later thinking on Lexora "
            "and the internal KB.'"
        )
        for target in (lexora, internal):
            if target:
                create_link(finedge, target, Link.INFLUENCED, evidence=evidence)

    # -- markdown helpers -------------------------------------------------

    def _title_from_markdown(self, text, fallback):
        for line in text.splitlines():
            if line.startswith("# "):
                return line[2:].strip()
        return fallback.replace("-", " ").title()

    def _date_from_markdown(self, text):
        import re

        match = re.search(r"Date:\s*(\d{4}-\d{2}-\d{2})", text)
        return match.group(1) if match else None

    def _author_from_markdown(self, text, people):
        """First known person named on an Author/Authors line."""
        import re

        match = re.search(r"Authors?:\s*(.+)", text)
        if not match:
            return None
        names = [n.strip() for n in match.group(1).split(",")]
        for person in people.values():
            if person.name in names:
                return person
        return None

    # -- reporting --------------------------------------------------------

    def _flush(self):
        Link.objects.all().delete()
        Document.objects.all().delete()
        Decision.objects.all().delete()
        Project.objects.all().delete()
        Client.objects.all().delete()
        Topic.objects.all().delete()
        Person.objects.all().delete()
        self.stdout.write(self.style.WARNING("Cleared existing knowledge base rows."))

    def _report(self):
        counts = [
            ("people", Person.objects.count()),
            ("clients", Client.objects.count()),
            ("projects", Project.objects.count()),
            ("decisions", Decision.objects.count()),
            ("documents", Document.objects.count()),
            ("topics", Topic.objects.count()),
            ("links", Link.objects.count()),
        ]
        self.stdout.write(self.style.SUCCESS("Seeded the knowledge base:"))
        for label, count in counts:
            self.stdout.write(f"  {count:>4}  {label}")
