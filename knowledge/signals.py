"""Keep automatic links current whenever content changes.

Hooking this to the model rather than to a view means every write path gets
it: the DRF API, the Django admin, the seed command and the shell all behave
the same way. That is the point of requirement 5 - new information joins the
knowledge base without anyone remembering to wire it up.
"""

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .linking import content_type_for, relink_document
from .models import Client, Document, Link, Person, Project, Topic


@receiver(post_save, sender=Document)
def link_document_on_save(sender, instance, **kwargs):
    relink_document(instance)


@receiver(post_save, sender=Project)
@receiver(post_save, sender=Person)
@receiver(post_save, sender=Client)
@receiver(post_save, sender=Topic)
def relink_documents_for_new_entity(sender, instance, created, **kwargs):
    """A newly added entity may be named in documents we already stored.

    Without this, adding a person tomorrow would leave them invisible to every
    document written yesterday. Only runs on creation - renames are rare and
    handled by re-saving the affected documents.
    """
    if not created:
        return
    for document in Document.objects.all():
        relink_document(document)


@receiver(post_delete)
def drop_links_for_deleted_entity(sender, instance, **kwargs):
    """Generic relations are not covered by cascade deletes, so clean up here.

    Otherwise a deleted project leaves Link rows whose `target` resolves to
    None, and traversal would silently walk into holes.
    """
    if sender is Link or not hasattr(instance, "pk"):
        return
    try:
        entity_type = content_type_for(sender)
    except Exception:  # not a concrete model we track
        return
    Link.objects.filter(source_type=entity_type, source_id=instance.pk).delete()
    Link.objects.filter(target_type=entity_type, target_id=instance.pk).delete()
