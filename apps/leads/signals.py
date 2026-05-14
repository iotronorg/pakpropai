from django.db.models.signals import pre_save
from django.dispatch import receiver


@receiver(pre_save, sender='leads.Lead')
def record_score_change(sender, instance, **kwargs):
    """
    Before saving a Lead, compare the new score against the DB value.
    If it changed, create a LeadScoreHistory entry after the save completes.
    Uses pre_save to capture old value, then defers the write via post_save.
    """
    if not instance.pk:
        return  # new record — no history yet

    try:
        old = sender.objects.values_list('score', flat=True).get(pk=instance.pk)
    except sender.DoesNotExist:
        return

    if old != instance.score:
        # Stash on the instance so post_save can read it without a second query
        instance._score_changed_from = old


@receiver(pre_save, sender='leads.Lead')
def _noop_post_save_hook(sender, instance, **kwargs):
    pass


from django.db.models.signals import post_save

@receiver(post_save, sender='leads.Lead')
def write_score_history(sender, instance, created, **kwargs):
    if created:
        return
    old = getattr(instance, '_score_changed_from', None)
    if old is None:
        return
    from .models import LeadScoreHistory
    LeadScoreHistory.objects.create(
        lead=instance,
        old_score=old,
        new_score=instance.score,
        reason='AI rescored' if not getattr(instance, '_score_actor', None) else 'manual override',
        changed_by=getattr(instance, '_score_actor', None),
    )
    instance._score_changed_from = None
