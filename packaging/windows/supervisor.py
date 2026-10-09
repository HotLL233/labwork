import argparse
import json
import os
import signal
import subprocess
import time
from pathlib import Path
from control import pg


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--home', type=Path, required=True)
    home = parser.parse_args().home.resolve()
    release = Path(json.loads((home / 'active.json').read_text(encoding='utf-8'))['release'])
    schema = json.loads((home / 'schema.json').read_text(encoding='utf-8'))
    if schema['schema'] != 1:
        raise RuntimeError('数据库版本不兼容')
    env = dict(os.environ, LABWORK_CONFIG=str(home / 'config.json'),
               XDG_DATA_HOME=str(home / 'certificates'), XDG_CONFIG_HOME=str(home / 'certificates'))
    marker = home / 'supervisor.running'
    stop = home / 'stop.request'
    stop.unlink(missing_ok=True)
    marker.write_text(str(os.getpid()), encoding='ascii')
    children, logs = [], []
    signal.signal(signal.SIGTERM, lambda *_: stop.touch())
    signal.signal(signal.SIGINT, lambda *_: stop.touch())
    try:
        pg(home, 'start', release)
        for name, command in [('web', [release / 'python/python.exe', release / 'app/runtime.py', 'web']),
                              ('worker', [release / 'python/python.exe', release / 'app/runtime.py', 'worker']),
                              ('gateway', [release / 'caddy.exe', 'run', '--config', home / 'Caddyfile', '--adapter', 'caddyfile'])]:
            logfile = open(home / f'logs/{name}.log', 'ab', buffering=0)
            logs.append(logfile)
            children.append(subprocess.Popen([str(x) for x in command], cwd=release / 'app', env=env,
                                              stdout=logfile, stderr=logfile))
        while not stop.exists():
            if any(child.poll() is not None for child in children):
                raise RuntimeError('子服务退出，停止整套服务，由系统服务管理器重新启动')
            time.sleep(1)
    finally:
        for child in reversed(children):
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        try:
            pg(home, 'stop', release)
        finally:
            for log in logs:
                log.close()
            marker.unlink(missing_ok=True)
            stop.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
