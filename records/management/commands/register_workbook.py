from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from records.models import Project
from records.services import register


class Command(BaseCommand):
    help = '登记已位于 WORKBOOK_ROOT 内的工作簿，不复制或改写原件'

    def add_arguments(self, parser):
        parser.add_argument('--project', required=True)
        parser.add_argument('--path', required=True)

    def handle(self, *args, **options):
        try:
            project = Project.objects.get(code=options['project'])
            book = register(project, Path(options['path']))
        except (ValueError, OSError, Project.DoesNotExist) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f'已登记 #{book.pk}：{book.name}，映射 {len(book.mapping)} 个方法；真实方法需核定。')
