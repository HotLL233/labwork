import json
import os
import shutil
import time
from contextlib import contextmanager
from pathlib import Path
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from .calculations import FIELDS, calculate, validate_params
from .models import Audit, Method, Project, Record, Workbook, Writeback
from .xlsx import create_demo, detect_mapping, fingerprint, inspect_workbook, prepare_write, workbook_path


def projects_for(user):
    return Project.objects.all() if user.is_staff else Project.objects.filter(members=user)


def records_for(user):
    return Record.objects.filter(project__in=projects_for(user)).select_related('project', 'method', 'workbook', 'author', 'reviewer', 'writeback')


def can_write(user):
    return user.is_authenticated and (user.is_staff or not user.groups.filter(name='只读人员').exists())


def audit(user, project, action, target, detail=None):
    Audit.objects.create(actor=user, project=project, action=action, target=str(target), detail=detail or {})


def submit(record, user):
    if not can_write(user):
        raise ValueError('当前账号仅可查询')
    with transaction.atomic():
        record = Record.objects.select_for_update().select_related('method', 'workbook').get(pk=record.pk)
        if record.status == 'submitted':
            return record
        if not user.is_staff and record.author_id != user.pk:
            raise ValueError('只能提交自己的草稿')
        method = record.method
        if record.method_snapshot.get('version') != method.version:
            raise ValueError('方法已更新，请重新编辑、计算并保存草稿后提交')
        if not method.confirmed and not record.workbook.is_demo:
            raise ValueError('此方法尚未核定，不能向导入工作簿提交回写；请管理员确认方法参数和依据')
        mapping = record.workbook.mapping.get(method.key)
        if not mapping:
            raise ValueError('该工作簿没有可用的回写映射')
        outputs = [calculate(method.kind, item, method.params)[1] for item in record.measurements]
        record.outputs = outputs
        record.method_snapshot = {'name': method.name, 'key': method.key, 'kind': method.kind,
            'params': method.params, 'version': method.version, 'basis': method.basis, 'confirmed': method.confirmed,
            'is_demo': record.workbook.is_demo}
        record.status = 'submitted'
        record.version += 1
        record.save()
        Writeback.objects.create(record=record, mapping_snapshot=mapping)
        audit(user, record.project, '提交检测', record.pk, {'method_version': method.version, 'demo': record.workbook.is_demo})
        return record


@contextmanager
def writer_lock():
    lock = settings.WORKBOOK_ROOT / '.labwork-writer.lock'
    with open(lock, 'a+b') as stream:
        stream.seek(0)
        stream.write(b'0')
        stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError):
            raise RuntimeError('已有回写进程运行，不能同时启动第二个进程') from None
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def finish_job(job, book):
    with transaction.atomic():
        book.fingerprint = job.after_hash
        book.save(update_fields=['fingerprint'])
        job.status = 'done'
        job.message = '回写并回读验证成功'
        job.save()
        audit(None, book.project, '回写成功', job.record_id, {'rows': job.rows, 'after_hash': job.after_hash})


def process_job(job):
    if not settings.WRITEBACK_ENABLED:
        return
    job.refresh_from_db()
    if job.status not in {'pending', 'occupied', 'writing'}:
        return
    book = Workbook.objects.get(pk=job.record.workbook_id)
    path = workbook_path(book)
    candidate = path.with_name(f'.{job.record.token}.pending.xlsx')
    try:
        current_hash = fingerprint(path)
        if job.after_hash and current_hash == job.after_hash:
            finish_job(job, book)
            return
        if current_hash != (job.before_hash or book.fingerprint):
            job.status = 'conflict'
            job.message = '文件内容与平台基线不同，可能被其他软件修改。请管理员核查，禁止盲目覆盖。'
            job.save()
            return
        job.status = 'writing'
        job.before_hash = current_hash
        job.save()
        backup = settings.RUNTIME_DIR / 'backups' / f'{job.record.token}-{current_hash}.xlsx'
        backup.parent.mkdir(parents=True, exist_ok=True)
        if not backup.exists():
            shutil.copyfile(path, backup)
        if fingerprint(backup) != current_hash:
            raise ValueError('备份内容与写前文件不一致')
        job.rows = prepare_write(path, candidate, job.record, job.mapping_snapshot)
        job.after_hash = fingerprint(candidate)
        job.backup = str(backup.relative_to(settings.RUNTIME_DIR))
        job.save()  # Durable intent before replacing the workbook; recover using after_hash.
        if fingerprint(path) != current_hash:
            job.status = 'conflict'
            job.message = '文件在回写期间发生变化，已取消替换。'
            job.save()
            return
        os.replace(candidate, path)
        finish_job(job, book)
    except PermissionError:
        job.status = 'occupied'
        job.message = '文件或备份目录被占用/无写入权限；请关闭 Excel/WPS 并核查权限后重试。'
        job.save()
    except (OSError, ValueError) as exc:
        job.status = 'failed'
        job.message = str(exc)
        job.save()
    finally:
        if candidate.exists():
            candidate.unlink(missing_ok=True)


def register(project, path, is_demo=False):
    path = Path(path).resolve()
    if not path.is_relative_to(settings.WORKBOOK_ROOT):
        raise ValueError('仅能登记 WORKBOOK_ROOT 内的文件')
    sheets = inspect_workbook(path)
    keys = list(Method.objects.filter(project=project).values_list('key', flat=True))
    mapping = detect_mapping(project.code, path, keys)
    return Workbook.objects.create(project=project, name=path.name,
        relative_path=str(path.relative_to(settings.WORKBOOK_ROOT)), fingerprint=fingerprint(path),
        mapping=mapping, sheets=sheets, is_demo=is_demo)


def seed_demo():
    from .calculations import METHODS
    for code in ('A3', 'D006', 'EF', 'FB01', 'FPO', 'J001', 'K001', 'Q', 'Q007', 'RD05', 'S002', 'T001'):
        Project.objects.get_or_create(code=code, defaults={'name': '环糊精' if code == 'T001' else f'{code} 理化检测'})
    project_methods = {
        'D006': ['water', 'water_diluted', 'total_n', 'ammonia_n', 'effective_cl', 'chloride', 'hydrogen', 'ammonia'],
        'Q': ['water', 'fluoride', 'chloride', 'hydrogen', 'loss', 'melting']}
    for code, keys in project_methods.items():
        project = Project.objects.get(code=code)
        for key in keys:
            name, kind, params = METHODS[key]
            Method.objects.get_or_create(project=project, key=key, defaults={'name': name, 'kind': kind,
                'params': params, 'basis': '来自已分析模板的候选参数；正式使用前需业务核定。'})
        if not Workbook.objects.filter(project=project, is_demo=True).exists():
            path = settings.WORKBOOK_ROOT / f'{code}-试用样表.xlsx'
            if path.exists():
                raise ValueError('样表路径已存在，但尚未登记；请核查后手动登记，避免覆盖')
            create_demo(path, keys)
            register(project, path, is_demo=True)
