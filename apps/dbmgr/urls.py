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
    path("db-dashboard/", views.db_dashboard_view, name="db_dashboard"),
    path("db-dashboard/table/", views.db_dashboard_table_view, name="db_dashboard_table"),
    path("db-dashboard/api/", views.db_dashboard_api_view, name="db_dashboard_api"),
    path("db-deploy/", views.deploy_job_list_view, name="db_deploy_list"),
    path(
        "db-deploy/mysql-replica/",
        views.mysql_replica_deploy_list_view,
        name="db_deploy_mysql_replica_list",
    ),
    path(
        "db-deploy/mysql-replica/api/",
        views.mysql_replica_deploy_api_view,
        name="db_deploy_mysql_replica_api",
    ),
    path(
        "db-deploy/mysql-replica/<int:job_id>/",
        views.mysql_replica_deploy_detail_view,
        name="db_deploy_mysql_replica_detail",
    ),
    path(
        "db-deploy/mysql-replica/api/<int:job_id>/",
        views.mysql_replica_deploy_detail_api_view,
        name="db_deploy_mysql_replica_detail_api",
    ),
    path("db-deploy/<int:job_id>/", views.deploy_job_detail_view, name="db_deploy_detail"),
    path("db-deploy/api/", views.deploy_job_api_view, name="db_deploy_api"),
    path("db-deploy/api/<int:job_id>/", views.deploy_job_detail_api_view, name="db_deploy_detail_api"),
    path("db-deploy/profiles/api/", views.deploy_profile_api_view, name="db_deploy_profiles_api"),
    path(
        "db-deploy/mysql-param-template/",
        views.mysql_param_template_list_view,
        name="db_deploy_mysql_param_template_list",
    ),
    path(
        "db-deploy/mysql-param-template/api/",
        views.mysql_param_template_api_view,
        name="db_deploy_mysql_param_template_api",
    ),
]
