from django.contrib import admin
from django.urls import include, path
from rest_framework.authtoken import views as authtoken_views
from rest_framework.routers import DefaultRouter

from knowledge import views as knowledge_views
from qa import views as qa_views
from web import views as web_views

router = DefaultRouter()
router.register(r"people", knowledge_views.PersonViewSet)
router.register(r"clients", knowledge_views.ClientViewSet)
router.register(r"projects", knowledge_views.ProjectViewSet)
router.register(r"decisions", knowledge_views.DecisionViewSet)
router.register(r"documents", knowledge_views.DocumentViewSet)
router.register(r"topics", knowledge_views.TopicViewSet)
router.register(r"links", knowledge_views.LinkViewSet)

urlpatterns = [
    path("admin/", admin.site.urls),
    # API
    path("api/", include(router.urls)),
    path("api/ask/", qa_views.ask_view, name="api-ask"),
    path("api/related/", knowledge_views.related_view, name="api-related"),
    path("api/auth/token/", authtoken_views.obtain_auth_token, name="api-token"),
    path("api-auth/", include("rest_framework.urls")),
    # Web UI
    path("", web_views.ask_page, name="ask"),
    path("browse/", web_views.browse_page, name="browse"),
    path("<str:entity_type>/<int:entity_id>/", web_views.entity_page, name="entity"),
]
