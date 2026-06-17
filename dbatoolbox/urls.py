from django.contrib import admin
from django.urls import include, path
from django.shortcuts import redirect

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", lambda r: redirect("dashboard")),
    path("", include("apps.dbmgr.urls")),
    path("", include("apps.common.urls")),
]
