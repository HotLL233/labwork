import ipaddress
from django.conf import settings
from django.http import HttpResponseForbidden


class AccessMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.networks = [ipaddress.ip_network(x.strip()) for x in settings.ALLOWED_NETWORKS.split(',')]
        self.proxies = [ipaddress.ip_network(x.strip()) for x in settings.TRUSTED_PROXIES.split(',')]

    def __call__(self, request):
        remote = request.META.get('REMOTE_ADDR', '127.0.0.1')
        try:
            if any(ipaddress.ip_address(remote) in net for net in self.proxies):
                remote = request.META.get('HTTP_X_REAL_IP', remote)
            else:
                request.META.pop('HTTP_X_FORWARDED_PROTO', None)
            allowed = any(ipaddress.ip_address(remote) in net for net in self.networks)
        except ValueError:
            allowed = False
        if not allowed:
            return HttpResponseForbidden('当前地址不在允许的局域网范围内。')
        request.client_ip = remote
        response = self.get_response(request)
        if request.path != '/static/' and not request.path.startswith('/static/'):
            response['Cache-Control'] = 'no-store'
        response['Referrer-Policy'] = 'same-origin'
        return response
