import logging
from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def score_property_task(self, property_id: str):
    from .models import Property
    from services.ai_orchestrator import AIOrchestrator

    try:
        prop = Property.objects.get(id=property_id)
    except Property.DoesNotExist:
        logger.warning(f"score_property: {property_id} not found")
        return

    try:
        result = AIOrchestrator.score_property(prop)
    except Exception as exc:
        logger.error(f"AI scoring failed for {property_id}: {exc}")
        raise self.retry(exc=exc)

    prop.ai_score    = result.get('score') or 50
    prop.risk_level  = result.get('risk') or Property.RiskLevel.MEDIUM
    prop.ai_analysis = result
    prop.save(update_fields=['ai_score', 'risk_level', 'ai_analysis'])
    logger.info(f"Scored property {property_id}: {prop.ai_score}/{prop.risk_level}")