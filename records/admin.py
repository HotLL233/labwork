from django.contrib import admin
from .models import Audit, Method, Project, Record, Workbook, Writeback


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ('code', 'name')
    filter_horizontal = ('members',)


@admin.register(Audit, Method, Record, Workbook, Writeback)
class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
