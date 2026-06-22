from apps.dbmgr.deploy_executors.base import BaseDeployExecutor


class OracleStandaloneExecutor(BaseDeployExecutor):
    job_type = "oracle_standalone"
