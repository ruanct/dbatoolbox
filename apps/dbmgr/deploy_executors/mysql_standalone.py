from apps.dbmgr.deploy_executors.base import BaseDeployExecutor


class MysqlStandaloneExecutor(BaseDeployExecutor):
    job_type = "mysql_standalone"
