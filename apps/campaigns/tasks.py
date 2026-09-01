from celery import shared_task
import logging

logger = logging.getLogger('apps.campaigns')


@shared_task
def run_campaign_task(campaign_pk):
    """Send a campaign in the background so large sends never block the browser."""
    from apps.campaigns.services.sending import run_campaign_impl
    try:
        run_campaign_impl(campaign_pk)
    except Exception as exc:
        logger.exception("Campaign task failed for campaign %s: %s", campaign_pk, exc)


@shared_task
def retry_failed_task(campaign_pk, user_id=None):
    """Retry only the failed messages of a campaign in the background."""
    from apps.campaigns.services.sending import retry_failed_impl
    try:
        retry_failed_impl(campaign_pk)
    except Exception as exc:
        logger.exception("Campaign retry task failed for campaign %s: %s", campaign_pk, exc)


@shared_task
def process_scheduled_campaigns():
    """Celery-beat entry point: fire any scheduled campaigns that are due."""
    from django.utils import timezone
    from apps.campaigns.models import SMSCampaign
    from apps.campaigns.services.sending import run_campaign_impl

    due = SMSCampaign.objects.filter(
        status=SMSCampaign.Status.SCHEDULED,
        scheduled_at__lte=timezone.now(),
    )
    for campaign in due:
        try:
            run_campaign_impl(campaign.pk)
        except Exception as exc:
            logger.exception("Scheduled campaign %s failed to run: %s", campaign.pk, exc)
