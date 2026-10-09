import uuid
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.http import FileResponse, HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from .calculations import FIELDS
from .forms import MethodForm, RecordForm, UploadForm
from .models import Audit, LoginAttempt, Method, Record, Workbook, Writeback
from .services import audit, can_write, projects_for, records_for, register, submit
from .xlsx import browse_sheet, read_archive, workbook_path


def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    form = AuthenticationForm(request, data=request.POST or None)
    form.fields['username'].label = '用户名'
    form.fields['password'].label = '密码'
    if request.method == 'POST':
        with transaction.atomic():
            attempt, _ = LoginAttempt.objects.get_or_create(address=request.client_ip)
            attempt = LoginAttempt.objects.select_for_update().get(pk=attempt.pk)
            if timezone.now() - attempt.window > timedelta(minutes=15):
                attempt.failures = 0
                attempt.window = timezone.now()
            if attempt.failures >= 10:
                return render(request, 'login.html', {'form': form, 'locked': True}, status=429)
            if form.is_valid():
                attempt.failures = 0
                attempt.save()
                login(request, form.get_user())
                return redirect('dashboard')
            attempt.failures += 1
            attempt.save()
    return render(request, 'login.html', {'form': form})


def health(request):
    from django.db import connection
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
        return JsonResponse({'status': 'ok', 'version': '0.0.1'})
    except Exception:
        return JsonResponse({'status': 'unavailable'}, status=503)


@login_required
def dashboard(request):
    projects = projects_for(request.user)
    records = records_for(request.user)
    for query, field in [('project', 'project__code'), ('batch', 'batch__icontains'), ('method', 'method__key')]:
        if request.GET.get(query):
            records = records.filter(**{field: request.GET[query]})
    if request.GET.get('status'):
        records = records.filter(writeback__status=request.GET['status'])
    for query, lookup in [('from', 'detected_on__gte'), ('to', 'detected_on__lte')]:
        if request.GET.get(query):
            try:
                from datetime import date
                value = date.fromisoformat(request.GET[query])
                records = records.filter(**{lookup: value})
            except ValueError:
                messages.error(request, '日期筛选格式不正确')
    scope = records_for(request.user)
    return render(request, 'dashboard.html', {'projects': projects, 'page': Paginator(records, 30).get_page(request.GET.get('page')),
        'total': scope.count(), 'pending': scope.filter(writeback__status__in=['pending', 'writing', 'occupied']).count(),
        'exceptions': scope.filter(writeback__status__in=['conflict', 'failed']).count(),
        'books_count': Workbook.objects.filter(project__in=projects).count()})


def editable(record, user):
    return can_write(user) and (user.is_staff or record.author_id == user.pk)


@login_required
def edit_record(request, pk=None):
    if not can_write(request.user):
        return HttpResponseForbidden('当前账号仅可查询')
    record = get_object_or_404(records_for(request.user), pk=pk) if pk else None
    if record and (record.status != 'draft' or not editable(record, request.user)):
        return HttpResponseForbidden('已提交记录不能原地修改；请使用修订功能。')
    books = Workbook.objects.filter(project__in=projects_for(request.user))
    book_id = record.workbook_id if record else request.POST.get('book') or request.GET.get('book')
    book = get_object_or_404(books, pk=book_id) if book_id else None
    methods = Method.objects.filter(project=book.project, key__in=book.mapping) if book else Method.objects.none()
    method_id = record.method_id if record else request.POST.get('method') or request.GET.get('method')
    method = get_object_or_404(methods, pk=method_id) if method_id and book else None
    form = None
    if method:
        initial = {'detected_on': timezone.localdate(), 'version': record.version if record else 1}
        if record:
            initial.update({'detected_on': record.detected_on, 'batch': record.batch, 'notes': record.notes, 'reagent_batch': record.reagent_batch})
            for index, measurement in enumerate(record.measurements, 1):
                initial.update({f'{key}_{index}': value for key, value in measurement.items()})
        form = RecordForm(method, request.POST or None, initial=initial)
        if request.method == 'POST' and form.is_valid():
            with transaction.atomic():
                if record:
                    record = Record.objects.select_for_update().get(pk=record.pk)
                    if record.status != 'draft' or record.version != form.cleaned_data['version']:
                        form.add_error(None, '记录已被其他页面修改或提交，请刷新后重试。')
                        return render(request, 'record_form.html', {'books': books, 'book': book, 'methods': methods, 'method': method, 'form': form, 'record': record}, status=409)
                else:
                    token = request.POST.get('token')
                    try:
                        token = uuid.UUID(token)
                    except (ValueError, TypeError, AttributeError):
                        return HttpResponse('提交标识无效，请刷新页面。', status=400)
                    existing = Record.objects.filter(token=token).first()
                    if existing:
                        if existing.author_id != request.user.pk:
                            return HttpResponseForbidden()
                        return redirect('record_detail', pk=existing.pk)
                    record, created = Record.objects.get_or_create(token=token, defaults={
                        'project': book.project, 'workbook': book, 'method': method, 'author': request.user,
                        'detected_on': form.cleaned_data['detected_on'], 'batch': form.cleaned_data['batch']})
                    if not created:
                        if record.author_id != request.user.pk:
                            return HttpResponseForbidden()
                        return redirect('record_detail', pk=record.pk)
                for key in ('detected_on', 'batch', 'notes', 'reagent_batch'):
                    setattr(record, key, form.cleaned_data[key])
                record.measurements = form.values
                record.outputs = form.outputs
                record.method_snapshot = {'kind': method.kind, 'params': method.params, 'version': method.version}
                record.version += 1
                record.save()
                audit(request.user, record.project, '保存草稿', record.pk)
            if request.POST.get('action') == 'submit':
                try:
                    submit(record, request.user)
                    messages.success(request, '已提交并创建回写任务；请查看同步状态。')
                except ValueError as exc:
                    messages.error(request, str(exc))
            else:
                messages.success(request, '草稿与计算结果已保存。')
            return redirect('record_detail', pk=record.pk)
    return render(request, 'record_form.html', {'books': books, 'book': book, 'methods': methods,
        'method': method, 'form': form, 'record': record, 'token': uuid.uuid4()})


@login_required
def record_detail(request, pk):
    record = get_object_or_404(records_for(request.user), pk=pk)
    kind = record.method_snapshot.get('kind', record.method.kind)
    measurements = []
    for raw, output in zip(record.measurements, record.outputs):
        measurements.append({'values': [{'label': label, 'unit': unit, 'value': raw.get(key)} for key, label, unit in FIELDS[kind]], 'output': output})
    mean = None
    if len(record.outputs) > 1 and all('result' in output for output in record.outputs):
        mean = f"{sum(Decimal(output['result']) for output in record.outputs) / len(record.outputs):.5f}"
    return render(request, 'record_detail.html', {'record': record, 'measurements': measurements, 'mean': mean,
        'can_edit': editable(record, request.user), 'snapshot': record.method_snapshot or {'params': record.method.params, 'version': record.method.version},
        'audit_items': Audit.objects.filter(project=record.project, target=str(record.pk))[:20]})


@login_required
@require_POST
def submit_record(request, pk):
    record = get_object_or_404(records_for(request.user), pk=pk)
    try:
        submit(record, request.user)
        messages.success(request, '已提交；重复提交不会重复创建任务。')
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect('record_detail', pk=pk)


@login_required
@require_POST
def retry_record(request, pk):
    record = get_object_or_404(records_for(request.user), pk=pk)
    if not editable(record, request.user):
        return HttpResponseForbidden()
    with transaction.atomic():
        job = get_object_or_404(Writeback.objects.select_for_update(), record=record)
        if job.status in {'occupied', 'failed'}:
            job.status = 'pending'
            job.save()
            audit(request.user, record.project, '重试回写', pk)
        elif job.status == 'conflict':
            messages.error(request, '冲突不能直接重试，请管理员核对外部修改并重新登记文件。')
    return redirect('record_detail', pk=pk)


@login_required
@require_POST
def review_record(request, pk):
    if not can_write(request.user) or (not request.user.is_staff and not request.user.groups.filter(name='复核人员').exists()):
        return HttpResponseForbidden('没有复核权限')
    with transaction.atomic():
        record = get_object_or_404(records_for(request.user).select_for_update(), pk=pk)
        if record.status != 'submitted' or record.author_id == request.user.pk:
            messages.error(request, '只能独立复核其他人员已提交的记录。')
        else:
            record.reviewer = request.user
            record.reviewed_at = timezone.now()
            record.save(update_fields=['reviewer', 'reviewed_at'])
            audit(request.user, record.project, '复核记录', pk)
    return redirect('record_detail', pk=pk)


@login_required
@require_POST
def revise_record(request, pk):
    source = get_object_or_404(records_for(request.user), pk=pk)
    if source.status != 'submitted' or not editable(source, request.user):
        return HttpResponseForbidden()
    reason = request.POST.get('reason', '').strip()
    if not reason or len(reason) > 500:
        messages.error(request, '修订理由为必填，最多 500 字。')
        return redirect('record_detail', pk=pk)
    record = Record.objects.create(project=source.project, workbook=source.workbook, method=source.method,
        detected_on=source.detected_on, batch=source.batch, reagent_batch=source.reagent_batch,
        measurements=source.measurements, outputs=source.outputs, notes=reason, author=request.user, supersedes=source)
    audit(request.user, source.project, '创建修订', record.pk, {'original': source.pk, 'reason': reason})
    return redirect('edit_record', pk=record.pk)


@login_required
def workbooks(request):
    projects = projects_for(request.user)
    form = UploadForm(projects, request.POST or None, request.FILES or None)
    if request.method == 'POST':
        if not request.user.is_staff:
            return HttpResponseForbidden('仅管理员可以导入和登记工作簿')
        if form.is_valid():
            upload = form.cleaned_data['workbook']
            path = settings.WORKBOOK_ROOT / f'{uuid.uuid4().hex}.xlsx'
            try:
                with path.open('xb') as stream:
                    for chunk in upload.chunks():
                        stream.write(chunk)
                book = register(form.cleaned_data['project'], path)
                book.name = Path(upload.name).name[:180]
                book.save(update_fields=['name'])
                audit(request.user, book.project, '导入工作簿副本', book.pk, {'name': book.name, 'fingerprint': book.fingerprint})
                messages.success(request, '已保存独立副本；不会自动修改上传文件在你电脑中的原件。')
                return redirect('workbook_detail', pk=book.pk)
            except (ValueError, OSError) as exc:
                path.unlink(missing_ok=True)
                form.add_error('workbook', str(exc))
    return render(request, 'workbooks.html', {'books': Workbook.objects.filter(project__in=projects).select_related('project').order_by('-id'), 'form': form})


@login_required
def workbook_detail(request, pk):
    book = get_object_or_404(Workbook.objects.filter(project__in=projects_for(request.user)), pk=pk)
    sheet = request.GET.get('sheet') or (book.sheets[0] if book.sheets else '')
    try:
        start = max(1, min(int(request.GET.get('start', 1)), 1048576))
    except ValueError:
        start = 1
    rows = []
    if sheet:
        try:
            rows = browse_sheet(workbook_path(book), sheet, start=start)
        except (ValueError, OSError) as exc:
            messages.error(request, str(exc))
    methods = Method.objects.filter(project=book.project, key__in=book.mapping)
    return render(request, 'workbook_detail.html', {'book': book, 'sheet': sheet, 'rows': rows,
        'methods': methods, 'next_start': rows[-1]['number'] + 1 if rows else start})


@login_required
def download_workbook(request, pk):
    book = get_object_or_404(Workbook.objects.filter(project__in=projects_for(request.user)), pk=pk)
    try:
        stream = workbook_path(book).open('rb')
    except (OSError, ValueError):
        return HttpResponse('文件暂不可访问', status=409)
    return FileResponse(stream, as_attachment=True, filename=book.name,
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


@login_required
def method_edit(request, pk):
    if not request.user.is_staff:
        return HttpResponseForbidden()
    method = get_object_or_404(Method.objects.filter(project__in=projects_for(request.user)), pk=pk)
    initial = {'c': method.params.get('c'), 'blank': method.params.get('blank'), 'basis': method.basis,
        'confirmed': method.confirmed, 'version': method.version}
    form = MethodForm(method, request.POST or None, initial=initial)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            method = Method.objects.select_for_update().get(pk=pk)
            if method.version != form.cleaned_data['version']:
                form.add_error(None, '方法已被更新，请刷新后核定。')
            else:
                old = {'params': method.params.copy(), 'basis': method.basis, 'version': method.version, 'confirmed': method.confirmed}
                if method.kind == 'titration':
                    method.params = dict(method.params, c=str(form.cleaned_data['c']), blank=str(form.cleaned_data['blank']))
                method.basis = form.cleaned_data['basis']
                method.confirmed = form.cleaned_data['confirmed']
                method.version += 1
                method.updated_by = request.user
                method.save()
                audit(request.user, method.project, '核定方法', f'method:{pk}', {'before': old, 'after': {'params': method.params, 'version': method.version, 'confirmed': method.confirmed, 'basis': method.basis}})
                messages.success(request, '方法已生成新版本；已提交的历史记录与待回写任务保持原参数快照。')
                return redirect('method_edit', pk=pk)
    return render(request, 'method.html', {'method': method, 'form': form, 'fields': FIELDS[method.kind]})
