import argparse
import json
import os
from pathlib import Path

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'labwork.settings')


def main():
    parser = argparse.ArgumentParser(description='理化检测平台 v0.0.1 服务管理')
    parser.add_argument('command', choices=['initialize', 'web', 'worker', 'check'])
    args = parser.parse_args()
    import django
    django.setup()
    from django.conf import settings
    from django.core.management import call_command
    marker = settings.RUNTIME_DIR / 'schema.json'
    if marker.exists() and json.loads(marker.read_text(encoding='utf-8')).get('schema') != 1:
        raise RuntimeError('数据库结构不兼容，禁止自动迁移或降级')
    if args.command in {'web', 'worker'} and not marker.exists():
        raise RuntimeError('请先 initialize，不在服务启动时自动迁移正式数据')
    if args.command == 'initialize':
        if marker.exists():
            print('现有配置和数据已保留，不重新初始化。')
            return
        call_command('migrate', interactive=False)
        call_command('bootstrap')
        config_path = os.environ.get('LABWORK_CONFIG')
        if config_path:
            path = Path(config_path)
            config = json.loads(path.read_text(encoding='utf-8-sig'))
            config.pop('ADMIN_PASSWORD', None)
            path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
        marker.write_text(json.dumps({'schema': 1, 'initialized_by': '0.0.1'}), encoding='utf-8')
    elif args.command == 'web':
        from waitress import serve
        from labwork.wsgi import application
        serve(application, host=settings.WEB_BIND,
            port=settings.WEB_PORT, threads=8, max_request_body_size=21*1024*1024)
    elif args.command == 'worker':
        call_command('writeback')
    else:
        call_command('check')


if __name__ == '__main__':
    main()
