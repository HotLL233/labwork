import uuid
from django.utils import timezone
from django.conf import settings
from django.db import models


class Project(models.Model):
    code = models.CharField('项目代码', max_length=32, unique=True)
    name = models.CharField('项目名称', max_length=100)
    members = models.ManyToManyField(settings.AUTH_USER_MODEL, verbose_name='授权人员', blank=True)

    def __str__(self):
        return f'{self.code} · {self.name}'

    class Meta:
        verbose_name = '项目'
        verbose_name_plural = '项目'
        ordering = ['code']


class Method(models.Model):
    project = models.ForeignKey(Project, on_delete=models.PROTECT)
    key = models.CharField(max_length=40)
    name = models.CharField('检测名称', max_length=100)
    kind = models.CharField(max_length=30)
    params = models.JSONField(default=dict)
    version = models.PositiveIntegerField(default=1)
    confirmed = models.BooleanField('已核定', default=False)
    basis = models.TextField('核定依据', blank=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True)

    def __str__(self):
        return f'{self.project.code} / {self.name} v{self.version}'

    class Meta:
        constraints = [models.UniqueConstraint(fields=['project', 'key'], name='method_project_key')]
        ordering = ['project__code', 'id']


class Workbook(models.Model):
    project = models.ForeignKey(Project, on_delete=models.PROTECT)
    name = models.CharField(max_length=180)
    relative_path = models.CharField(max_length=240, unique=True)
    fingerprint = models.CharField(max_length=64)
    mapping = models.JSONField(default=dict)
    sheets = models.JSONField(default=list)
    is_demo = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.project.code} / {self.name}'


class Record(models.Model):
    STATUS = [('draft', '草稿'), ('submitted', '已提交')]
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    project = models.ForeignKey(Project, on_delete=models.PROTECT)
    workbook = models.ForeignKey(Workbook, on_delete=models.PROTECT)
    method = models.ForeignKey(Method, on_delete=models.PROTECT)
    detected_on = models.DateField('检测日期')
    batch = models.CharField('样品批号', max_length=100)
    reagent_batch = models.CharField('试剂/标定批次', max_length=100, blank=True)
    measurements = models.JSONField(default=list)
    outputs = models.JSONField(default=list)
    method_snapshot = models.JSONField(default=dict)
    notes = models.TextField('备注', blank=True)
    status = models.CharField(max_length=20, choices=STATUS, default='draft')
    version = models.PositiveIntegerField(default=1)
    supersedes = models.ForeignKey('self', on_delete=models.PROTECT, null=True, blank=True)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='authored_records')
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, related_name='reviewed_records')
    reviewed_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-id']


class Writeback(models.Model):
    STATUS = [('pending', '待回写'), ('writing', '回写中'), ('done', '已成功'),
        ('occupied', '文件占用'), ('conflict', '内容冲突'), ('failed', '失败')]
    record = models.OneToOneField(Record, on_delete=models.PROTECT, related_name='writeback')
    status = models.CharField(max_length=20, choices=STATUS, default='pending')
    message = models.TextField(blank=True)
    before_hash = models.CharField(max_length=64, blank=True)
    after_hash = models.CharField(max_length=64, blank=True)
    rows = models.JSONField(default=list)
    mapping_snapshot = models.JSONField(default=dict)
    backup = models.CharField(max_length=240, blank=True)
    updated_at = models.DateTimeField(auto_now=True)


class Audit(models.Model):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True)
    project = models.ForeignKey(Project, on_delete=models.PROTECT, null=True)
    action = models.CharField(max_length=60)
    target = models.CharField(max_length=100)
    detail = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-id']


class LoginAttempt(models.Model):
    address = models.CharField(max_length=64, unique=True)
    failures = models.PositiveIntegerField(default=0)
    window = models.DateTimeField(default=timezone.now)
