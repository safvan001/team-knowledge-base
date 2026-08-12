"""The ask endpoint."""

from rest_framework import serializers, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .answers import answer_question


class AskSerializer(serializers.Serializer):
    question = serializers.CharField(max_length=2000, trim_whitespace=True)
    max_hops = serializers.IntegerField(required=False, min_value=1, max_value=4)
    # Lets tests and offline demos force the deterministic path.
    use_model = serializers.BooleanField(required=False, default=True)

    def validate_question(self, value):
        if not value.strip():
            raise serializers.ValidationError("Question cannot be empty.")
        return value.strip()


@api_view(["POST"])
@permission_classes([AllowAny])
def ask_view(request):
    """Answer a question using the relationships between stored entities."""
    payload = AskSerializer(data=request.data)
    payload.is_valid(raise_exception=True)
    data = payload.validated_data

    result = answer_question(
        data["question"],
        max_hops=data.get("max_hops"),
        use_model=data.get("use_model", True),
    )
    return Response(result, status=status.HTTP_200_OK)
