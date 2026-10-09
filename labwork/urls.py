from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path
from records import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('login/', views.login_view, name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('password/', auth_views.PasswordChangeView.as_view(template_name='password.html', success_url='/'), name='password'),
    path('health/', views.health, name='health'),
    path('records/new/', views.edit_record, name='new_record'),
    path('records/<int:pk>/', views.record_detail, name='record_detail'),
    path('records/<int:pk>/edit/', views.edit_record, name='edit_record'),
    path('records/<int:pk>/submit/', views.submit_record, name='submit_record'),
    path('records/<int:pk>/retry/', views.retry_record, name='retry_record'),
    path('records/<int:pk>/review/', views.review_record, name='review_record'),
    path('records/<int:pk>/revise/', views.revise_record, name='revise_record'),
    path('workbooks/', views.workbooks, name='workbooks'),
    path('workbooks/<int:pk>/', views.workbook_detail, name='workbook_detail'),
    path('workbooks/<int:pk>/download/', views.download_workbook, name='download_workbook'),
    path('methods/<int:pk>/', views.method_edit, name='method_edit'),
    path('admin/', admin.site.urls),
]
admin.site.site_header = '理化检测平台管理'
admin.site.site_title = '理化检测管理'
admin.site.index_title = '项目、用户和授权管理'
