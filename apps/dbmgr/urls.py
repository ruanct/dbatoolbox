from django.urls import path

from . import views

urlpatterns = [
    path("replication-cluster/", views.replication_cluster_list_view, name="replication_cluster_list"),
    path("replication-cluster/api/", views.replication_cluster_api_view, name="replication_cluster_api"),
    path("db-instance/", views.instance_list_view, name="db_instance_list"),
    path("db-instance/api/", views.instance_api_view, name="db_instance_api"),
    path("db-instance-host/", views.deploy_host_list_view, name="db_instance_host_list"),
    path("db-instance-host/api/", views.deploy_host_api_view, name="db_instance_host_api"),
    path("db-account/", views.account_list_view, name="db_account_list"),
    path("db-account/api/", views.account_api_view, name="db_account_api"),
]
