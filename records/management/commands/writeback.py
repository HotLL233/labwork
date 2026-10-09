import time
from datetime import timedelta
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone
from records.models import Writeback
from records.services import process_job, writer_lock


class Command(BaseCommand):
    help = '单进程串行处理回写任务（--once 仅处理当前可执行任务）'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true')

    def handle(self, *args, **options):
        if not settings.WRITEBACK_ENABLED:
            raise CommandError('WRITEBACK_ENABLED 未开启，拒绝启动回写进程')
        earlier = Writeback.objects.filter(record__workbook_id=OuterRef('record__workbook_id'),
            pk__lt=OuterRef('pk')).exclude(status='done')
        try:
            with writer_lock():
                while True:
                    jobs = Writeback.objects.annotate(blocked=Exists(earlier)).filter(blocked=False).filter(
                        Q(status__in=['pending', 'writing']) |
                        Q(status='occupied', updated_at__lte=timezone.now()-timedelta(seconds=30)))
                    job = jobs.select_related('record').order_by('pk').first()
                    if job:
                        process_job(job)
                    elif options['once']:
                        break
                    else:
                        time.sleep(1)
        except RuntimeError as exc:
            raise CommandError(str(exc)) from exc
