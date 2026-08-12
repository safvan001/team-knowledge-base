"""Serializers for the entity API.

Each entity has a compact ("mini") serializer used when it appears inside
another entity's payload, and a full serializer used at its own endpoint.
Writes take plain ids; reads expand one level so a single GET on a project
shows its client, lead, team, topics and decisions without follow-up calls.
"""

from rest_framework import serializers

from .models import Client, Decision, Document, Link, Person, Project, Topic


class PersonMiniSerializer(serializers.ModelSerializer):
    class Meta:
        model = Person
        fields = ["id", "name", "role"]


class ClientMiniSerializer(serializers.ModelSerializer):
    class Meta:
        model = Client
        fields = ["id", "name", "industry", "status"]


class TopicMiniSerializer(serializers.ModelSerializer):
    class Meta:
        model = Topic
        fields = ["id", "name"]


class ProjectMiniSerializer(serializers.ModelSerializer):
    class Meta:
        model = Project
        fields = ["id", "name", "status"]


class DecisionMiniSerializer(serializers.ModelSerializer):
    class Meta:
        model = Decision
        fields = ["id", "title", "date"]


class DocumentMiniSerializer(serializers.ModelSerializer):
    class Meta:
        model = Document
        fields = ["id", "title", "source", "date"]


class PersonSerializer(serializers.ModelSerializer):
    projects = ProjectMiniSerializer(many=True, read_only=True)
    led_projects = ProjectMiniSerializer(many=True, read_only=True)
    decisions_made = DecisionMiniSerializer(many=True, read_only=True)

    class Meta:
        model = Person
        fields = [
            "id", "name", "role", "email", "skills", "joined",
            "projects", "led_projects", "decisions_made",
        ]


class ClientSerializer(serializers.ModelSerializer):
    projects = ProjectMiniSerializer(many=True, read_only=True)

    class Meta:
        model = Client
        fields = [
            "id", "name", "industry", "size", "primary_contact",
            "status", "notes", "projects",
        ]


class TopicSerializer(serializers.ModelSerializer):
    projects = ProjectMiniSerializer(many=True, read_only=True)
    decisions = DecisionMiniSerializer(many=True, read_only=True)

    class Meta:
        model = Topic
        fields = ["id", "name", "description", "projects", "decisions"]


class ProjectSerializer(serializers.ModelSerializer):
    client_detail = ClientMiniSerializer(source="client", read_only=True)
    lead_detail = PersonMiniSerializer(source="lead", read_only=True)
    team_detail = PersonMiniSerializer(source="team", many=True, read_only=True)
    topics_detail = TopicMiniSerializer(source="topics", many=True, read_only=True)
    decisions = DecisionMiniSerializer(many=True, read_only=True)

    class Meta:
        model = Project
        fields = [
            "id", "name", "description", "status", "start_date", "end_date",
            "client", "lead", "team", "topics",
            "client_detail", "lead_detail", "team_detail", "topics_detail",
            "decisions",
        ]

    def validate(self, attrs):
        start = attrs.get("start_date", getattr(self.instance, "start_date", None))
        end = attrs.get("end_date", getattr(self.instance, "end_date", None))
        if start and end and end < start:
            raise serializers.ValidationError(
                {"end_date": "End date cannot be before the start date."}
            )
        return attrs


class DecisionSerializer(serializers.ModelSerializer):
    project_detail = ProjectMiniSerializer(source="project", read_only=True)
    made_by_detail = PersonMiniSerializer(source="made_by", read_only=True)
    participants_detail = PersonMiniSerializer(
        source="participants", many=True, read_only=True
    )
    topics_detail = TopicMiniSerializer(source="topics", many=True, read_only=True)

    class Meta:
        model = Decision
        fields = [
            "id", "title", "summary", "date",
            "project", "made_by", "participants", "topics",
            "project_detail", "made_by_detail", "participants_detail", "topics_detail",
        ]


class DocumentSerializer(serializers.ModelSerializer):
    author_detail = PersonMiniSerializer(source="author", read_only=True)
    mentions = serializers.SerializerMethodField()

    class Meta:
        model = Document
        fields = [
            "id", "title", "content", "source", "external_ref", "date",
            "author", "author_detail", "mentions",
        ]

    def get_mentions(self, obj):
        """Entities this document was auto-linked to, grouped by type."""
        from .linking import outgoing_links

        grouped = {}
        for link in outgoing_links(obj):
            target = link.target
            if target is None:
                continue
            grouped.setdefault(type(target).__name__.lower(), []).append(
                {"id": target.pk, "name": str(target), "rel_type": link.rel_type}
            )
        return grouped


class LinkSerializer(serializers.ModelSerializer):
    """Relationships that do not fit a fixed column.

    Entity types are given as lowercase model names ("project", "document")
    rather than ContentType ids, so the API stays readable by hand.
    """

    source_model = serializers.CharField(write_only=True)
    target_model = serializers.CharField(write_only=True)
    source_label = serializers.SerializerMethodField()
    target_label = serializers.SerializerMethodField()
    source_model_name = serializers.CharField(source="source_type.model", read_only=True)
    target_model_name = serializers.CharField(source="target_type.model", read_only=True)

    class Meta:
        model = Link
        fields = [
            "id", "rel_type", "evidence", "auto_created",
            "source_model", "source_id", "target_model", "target_id",
            "source_model_name", "target_model_name",
            "source_label", "target_label",
        ]
        read_only_fields = ["auto_created"]

    def get_source_label(self, obj):
        return str(obj.source) if obj.source else None

    def get_target_label(self, obj):
        return str(obj.target) if obj.target else None

    def validate(self, attrs):
        from .linking import LINKABLE_MODELS, content_type_for

        for side in ("source", "target"):
            model_name = attrs.get(f"{side}_model", "").lower()
            if model_name not in LINKABLE_MODELS:
                raise serializers.ValidationError(
                    {f"{side}_model": f"Must be one of {sorted(LINKABLE_MODELS)}."}
                )
            model = LINKABLE_MODELS[model_name]
            obj_id = attrs.get(f"{side}_id")
            if not model.objects.filter(pk=obj_id).exists():
                raise serializers.ValidationError(
                    {f"{side}_id": f"No {model_name} with id {obj_id}."}
                )
            attrs[f"{side}_type"] = content_type_for(model)

        if (
            attrs["source_type"] == attrs["target_type"]
            and attrs["source_id"] == attrs["target_id"]
        ):
            raise serializers.ValidationError("An entity cannot link to itself.")
        return attrs

    def create(self, validated_data):
        validated_data.pop("source_model", None)
        validated_data.pop("target_model", None)
        return super().create(validated_data)

    def update(self, instance, validated_data):
        validated_data.pop("source_model", None)
        validated_data.pop("target_model", None)
        return super().update(instance, validated_data)
