from django.conf import settings
from labwork import VERSION
from .services import can_write


def environment(request):
    return {'app_version': VERSION, 'app_env': settings.APP_ENV,
        'writeback_enabled': settings.WRITEBACK_ENABLED, 'user_can_write': can_write(request.user)}
