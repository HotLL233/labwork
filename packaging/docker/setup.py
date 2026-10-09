"""Interactive setup inside application container; no Windows host-directory mounts."""
import getpass
import ipaddress
import json
import os
import re
import secrets
from pathlib import Path


def main():
    root = Path('/runtime')
    if (root / '.env').exists() or (root / 'config.json').exists():
        raise SystemExit('已有配置，禁止覆盖。请先备份，升级时保留原配置。')
    host = input('服务器固定 IPv4 或内网域名：').strip()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.\-]{0,252}', host):
        raise SystemExit('地址格式错误：不要输入协议、端口或路径。')
    port = int(input('HTTPS 端口 [8443]：') or '8443')
    if not 1024 <= port <= 65535:
        raise SystemExit('端口须为 1024–65535。')
    try:
        default_bind = str(ipaddress.IPv4Address(host))
    except ValueError:
        default_bind = '127.0.0.1'
    bind = ipaddress.IPv4Address(input(f'HTTPS 绑定本机内网 IPv4 [{default_bind}]：').strip() or default_bind)
    if not bind.is_private or bind.is_unspecified:
        raise SystemExit('仅绑定服务器实际内网 IPv4 或本机回环地址。')
    networks = input('允许的局域网 CIDR，逗号分隔 [192.168.0.0/16]：') or '192.168.0.0/16'
    for item in networks.split(','):
        ipaddress.ip_network(item.strip())
    subnet = ipaddress.ip_network(input('Docker 私有网段 [172.30.86.0/24]：').strip() or '172.30.86.0/24')
    if subnet.version != 4 or subnet.prefixlen != 24 or not subnet.is_private:
        raise SystemExit('Docker 网段须为私有 IPv4 /24，且不得与实验室网段冲突。')
    proxy = str(subnet.network_address + 10)
    username = input('管理员用户名 [admin]：').strip() or 'admin'
    if not re.fullmatch(r'[\w.@+\-]{1,150}', username):
        raise SystemExit('用户名格式不正确。')
    password = getpass.getpass('管理员密码（至少 12 位，无默认密码）：')
    if len(password) < 12 or password != getpass.getpass('再次输入：'):
        raise SystemExit('密码长度不足或两次不一致。')
    db_password = secrets.token_hex(32)
    config = {'APP_ENV': 'prod', 'SECRET_KEY': secrets.token_urlsafe(48), 'RUNTIME_DIR': '/runtime',
        'WORKBOOK_ROOT': '/runtime/workbooks', 'DATABASE_URL': f'postgresql://labwork:{db_password}@db:5432/labwork_prod',
        'ALLOWED_HOSTS': f'{host},localhost,127.0.0.1', 'ALLOWED_NETWORKS': f'{networks},127.0.0.0/8,::1/128,{subnet.network_address + 1}/32',
        'TRUSTED_PROXIES': f'{proxy}/32', 'WEB_BIND': '0.0.0.0', 'WEB_PORT': '18801',
        'HTTPS_ENABLED': 'true', 'CSRF_TRUSTED_ORIGINS': f'https://{host}:{port}',
        'WRITEBACK_ENABLED': 'true', 'ADMIN_USERNAME': username, 'ADMIN_PASSWORD': password}
    directory = root
    path = root / 'config.json'
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
    env = root / '.env'
    env.write_text(f'LABWORK_HOST={host}\nBIND_ADDRESS={bind}\nHTTPS_PORT={port}\nPOSTGRES_PASSWORD={secrets.token_hex(32)}\nLABWORK_DB_PASSWORD={db_password}\nDOCKER_SUBNET={subnet}\nPROXY_IP={proxy}\nRUNTIME_VOLUME=labwork_runtime\n', encoding='utf-8')
    for item in (directory, path, env):
        item.chmod(0o700 if item.is_dir() else 0o600)
        if hasattr(os, 'chown'):
            os.chown(item, 10001, 10001)
    print(f'配置已生成。运行 docker compose up -d；地址：https://{host}:{port}')


if __name__ == '__main__':
    main()
