"""Admin registration.

Gives reviewers a working UI for creating and editing every entity and
relationship without any frontend work on our side. Saving here goes through
the same signals as the API, so automatic linking happens either way.
"""

from django.contrib import admin

from .models import Client, Decision, Document, Link, Person, Project, Topic


@admin.register(Person)
class PersonAdmin(admin.ModelAdmin):
    list_display = ["name", "role", "email", "joined"]
    search_fields = ["name", "role", "email"]


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ["name", "industry", "size", "status"]
    list_filter = ["status", "industry"]
    search_fields = ["name", "notes"]


@admin.register(Topic)
class TopicAdmin(admin.ModelAdmin):
    list_display = ["name"]
    search_fields = ["name", "description"]


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ["name", "client", "lead", "status", "start_date"]
    list_filter = ["status", "client"]
    search_fields = ["name", "description"]
    filter_horizontal = ["team", "topics"]


@admin.register(Decision)
class DecisionAdmin(admin.ModelAdmin):
    list_display = ["title", "date", "made_by", "project"]
    list_filter = ["project", "made_by"]
    search_fields = ["title", "summary"]
    filter_horizontal = ["participants", "topics"]


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ["title", "source", "date", "author"]
    list_filter = ["source", "author"]
    search_fields = ["title", "content"]


@admin.register(Link)
class LinkAdmin(admin.ModelAdmin):
    list_display = ["__str__", "rel_type", "auto_created"]
    list_filter = ["rel_type", "auto_created"]
    readonly_fields = ["auto_created"]
