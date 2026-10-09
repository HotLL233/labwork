from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from records.services import seed_demo


class Command(BaseCommand):
    help = '初始化项目和试用样表；仅在没有用户时从环境/配置创建管理员'

    def handle(self, *args, **options):
        User = get_user_model()
        if not User.objects.exists():
            password = settings.ADMIN_PASSWORD
            if len(password) < 12:
                raise CommandError('首次初始化需设置至少 12 位的 ADMIN_PASSWORD；没有默认密码')
            name = settings.ADMIN_USERNAME
            User.objects.create_superuser(username=name, password=password, email='')
        Group.objects.get_or_create(name='复核人员')
        Group.objects.get_or_create(name='只读人员')
        seed_demo()
        self.stdout.write(self.style.SUCCESS('12 个项目、D006/Q 试用样表已就绪；真实方法默认待核定。'))
