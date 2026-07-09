from django.db import models


class DatabaseReplicationCluster(models.Model):
    """数据库复制集（仅 topology=replication 使用）"""

    REPLICATION_TYPE_CHOICES = [
        ("mysql_replication", "MySQL主从复制"),
        ("pg_streaming", "PostgreSQL流复制"),
        ("oracle_adg", "Oracle ADG"),
        ("mssql_mirror", "SQL Server镜像/日志传送"),
    ]

    name = models.CharField(max_length=128, unique=True, verbose_name="复制集名称")
    engine = models.CharField(
        max_length=16,
        choices=[
            ("mysql", "MySQL"),
            ("postgresql", "PostgreSQL"),
            ("oracle", "Oracle"),
            ("mssql", "SQL Server"),
        ],
        verbose_name="数据库类型",
    )
    replication_type = models.CharField(
        max_length=32, choices=REPLICATION_TYPE_CHOICES, verbose_name="复制类型",
    )
    primary_instance = models.ForeignKey(
        "DatabaseInstance",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="as_primary_of_replication_clusters",
        verbose_name="当前主实例",
    )
    remark = models.TextField(blank=True, default="", verbose_name="备注")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        db_table = "dbmgr_database_replication_cluster"
        verbose_name = "数据库复制集"
        verbose_name_plural = verbose_name

    def __str__(self) -> str:
        return self.name


class DatabaseInstance(models.Model):
    """数据库实例"""

    ENGINE_CHOICES = [
        ("mysql", "MySQL"),
        ("postgresql", "PostgreSQL"),
        ("oracle", "Oracle"),
        ("mssql", "SQL Server"),
    ]
    TOPOLOGY_CHOICES = [
        ("standalone", "单实例"),
        ("ha_cluster", "高可用集群"),
        ("replication", "复制集成员"),
    ]
    CLUSTER_STYLE_CHOICES = [
        ("rac", "Oracle RAC"),
        ("galera", "Galera Cluster"),
        ("mysql_vip", "MySQL VIP/代理"),
        ("proxysql", "ProxySQL"),
        ("innodb_cluster", "MySQL InnoDB Cluster"),
        ("mgr", "MySQL Group Replication"),
        ("patroni", "PostgreSQL Patroni"),
        ("pg_vip", "PostgreSQL VIP/代理"),
        ("repmgr", "PostgreSQL repmgr"),
        ("alwayson", "SQL Server AlwaysOn"),
        ("fci", "SQL Server FCI"),
    ]
    ROLE_CHOICES = [
        ("master", "主库"),
        ("slave", "从库"),
    ]
    STATUS_CHOICES = [
        ("online", "在线"),
        ("offline", "离线"),
        ("maintenance", "维护中"),
    ]
    PROBE_STATUS_CHOICES = [
        ("alive", "正常"),
        ("dead", "异常"),
        ("unknown", "未探测"),
        ("maintenance", "维护中"),
    ]

    instance_name = models.CharField(max_length=128, unique=True, verbose_name="实例名称")
    engine = models.CharField(max_length=16, choices=ENGINE_CHOICES, verbose_name="数据库类型")
    topology = models.CharField(
        max_length=16, choices=TOPOLOGY_CHOICES, default="standalone", verbose_name="部署拓扑",
    )
    cluster_style = models.CharField(
        max_length=32, choices=CLUSTER_STYLE_CHOICES, blank=True, default="",
        verbose_name="集群类型",
        help_text="仅 topology=ha_cluster 时填写",
    )
    role = models.CharField(
        max_length=16, choices=ROLE_CHOICES, default="master", verbose_name="实例角色",
    )
    status = models.CharField(
        max_length=16, choices=STATUS_CHOICES, default="online", verbose_name="运行状态",
    )
    probe_status = models.CharField(
        max_length=16,
        choices=PROBE_STATUS_CHOICES,
        default="unknown",
        verbose_name="探测状态",
    )
    probe_message = models.CharField(
        max_length=255, blank=True, default="", verbose_name="探测信息",
    )
    latency_ms = models.PositiveIntegerField(
        null=True, blank=True, verbose_name="探测延迟(ms)",
    )
    last_probed_at = models.DateTimeField(
        null=True, blank=True, verbose_name="最后探测时间",
    )
    version = models.CharField(max_length=32, blank=True, default="", verbose_name="版本号")

    environment = models.ForeignKey(
        "common.Environment", on_delete=models.PROTECT, verbose_name="所属环境",
    )
    business = models.ForeignKey(
        "common.Business", on_delete=models.PROTECT, verbose_name="所属业务",
    )
    replication_cluster = models.ForeignKey(
        DatabaseReplicationCluster,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="instances",
        verbose_name="所属复制集",
        help_text="仅 topology=replication 时使用",
    )

    connect_host = models.CharField(
        max_length=128, verbose_name="连接地址",
        help_text="本机IP / VIP / SCAN / Listener / 代理地址",
    )
    port = models.PositiveIntegerField(verbose_name="连接端口")
    server_id = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name="MySQL server_id",
        help_text="MySQL 复制 server_id，仅 engine=mysql 且开启 binlog 时登记",
    )
    read_connect_host = models.CharField(
        max_length=128, blank=True, default="", verbose_name="只读连接地址",
        help_text="MySQL Router/ProxySQL 只读入口，可选",
    )
    read_port = models.PositiveIntegerField(
        null=True, blank=True, verbose_name="只读连接端口",
    )

    db_name = models.CharField(max_length=128, blank=True, default="", verbose_name="默认库名")
    charset = models.CharField(max_length=32, blank=True, default="", verbose_name="字符集")

    sid = models.CharField(max_length=64, blank=True, default="", verbose_name="Oracle SID")
    service_name = models.CharField(
        max_length=128, blank=True, default="", verbose_name="Oracle Service Name",
    )

    is_ssl = models.BooleanField(default=False, verbose_name="启用SSL")
    remark = models.TextField(blank=True, default="", verbose_name="备注")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        db_table = "dbmgr_database_instance"
        verbose_name = "数据库实例"
        verbose_name_plural = verbose_name
        constraints = [
            models.UniqueConstraint(
                fields=["engine", "connect_host", "port", "db_name"],
                name="uniq_dbmgr_instance_endpoint",
            ),
        ]
        indexes = [
            models.Index(fields=["engine", "topology", "status"]),
            models.Index(fields=["environment", "business"]),
            models.Index(fields=["replication_cluster"]),
            models.Index(fields=["engine", "server_id"], name="dbmgr_db_engine_server_idx"),
        ]

    def __str__(self) -> str:
        return self.instance_name


class DatabaseInstanceHost(models.Model):
    """数据库实例部署节点"""

    instance = models.ForeignKey(
        DatabaseInstance,
        on_delete=models.CASCADE,
        related_name="deploy_hosts",
        verbose_name="关联实例",
    )
    host = models.ForeignKey(
        "common.Host",
        on_delete=models.PROTECT,
        related_name="database_instances",
        verbose_name="部署主机",
    )
    node_name = models.CharField(max_length=64, blank=True, default="", verbose_name="节点名称")
    node_sid = models.CharField(
        max_length=64, blank=True, default="", verbose_name="节点SID",
        help_text="Oracle RAC 节点实例 SID，如 ORCL1",
    )
    node_service_name = models.CharField(
        max_length=128, blank=True, default="", verbose_name="节点Service Name",
    )
    listener_host = models.CharField(
        max_length=128, blank=True, default="", verbose_name="节点连接地址",
        help_text="留空则使用关联主机的业务IP",
    )
    listener_port = models.PositiveIntegerField(
        null=True, blank=True, verbose_name="节点监听端口",
        help_text="Oracle RAC 节点监听端口，如 11521；MySQL 默认 3306",
    )
    is_primary = models.BooleanField(default=False, verbose_name="首选运维节点")
    sort_order = models.PositiveSmallIntegerField(default=0, verbose_name="排序")
    probe_status = models.CharField(
        max_length=16,
        choices=DatabaseInstance.PROBE_STATUS_CHOICES,
        default="unknown",
        verbose_name="探测状态",
    )
    probe_message = models.CharField(
        max_length=255, blank=True, default="", verbose_name="探测信息",
    )
    latency_ms = models.PositiveIntegerField(
        null=True, blank=True, verbose_name="探测延迟(ms)",
    )
    last_probed_at = models.DateTimeField(
        null=True, blank=True, verbose_name="最后探测时间",
    )
    remark = models.TextField(blank=True, default="", verbose_name="备注")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        db_table = "dbmgr_database_instance_host"
        verbose_name = "实例部署节点"
        verbose_name_plural = verbose_name
        constraints = [
            models.UniqueConstraint(
                fields=["instance", "host"],
                name="uniq_dbmgr_instance_host",
            ),
        ]
        indexes = [
            models.Index(fields=["instance", "is_primary"]),
        ]
        ordering = ["sort_order", "id"]

    def __str__(self) -> str:
        label = self.node_name or str(self.host)
        return f"{self.instance.instance_name} / {label}"


class DatabaseAccount(models.Model):
    """数据库连接账号"""

    ACCOUNT_TYPE_CHOICES = [
        ("user_adm", "超级管理员"),
        ("user_dba", "高级DBA用户"),
        ("user_ops", "日常运维用户"),
        ("user_app", "业务应用账号"),
        ("user_read", "只读分析账号"),
        ("user_repl", "数据复制账号"),
        ("user_exp", "数据导出账号"),
        ("user_mon", "系统监控账号"),
        ("user_bak", "系统备份账号"),
    ]

    instance = models.ForeignKey(
        DatabaseInstance,
        on_delete=models.CASCADE,
        related_name="accounts",
        verbose_name="关联实例",
    )
    account_type = models.CharField(
        max_length=16, choices=ACCOUNT_TYPE_CHOICES, default="user_dba", verbose_name="账号类型",
    )
    account_name = models.CharField(max_length=128, verbose_name="账号名称")
    grant_host = models.CharField(
        max_length=255,
        blank=True,
        default="",
        verbose_name="授权主机",
        help_text="MySQL 专用，对应 mysql.user.Host，如 %、localhost、10.1.%",
    )
    account_pswd = models.CharField(max_length=256, verbose_name="账号密码")
    default_schema = models.CharField(max_length=128, blank=True, default="", verbose_name="默认Schema")
    is_default = models.BooleanField(default=False, verbose_name="默认运维账号")
    remark = models.TextField(blank=True, default="", verbose_name="备注")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        db_table = "dbmgr_database_account"
        verbose_name = "数据库账号"
        verbose_name_plural = verbose_name
        constraints = [
            models.UniqueConstraint(
                fields=["instance", "account_name", "grant_host"],
                name="uniq_dbmgr_instance_account_identity",
            ),
        ]
        indexes = [
            models.Index(fields=["instance", "is_default"]),
        ]

    def __str__(self) -> str:
        if self.instance.engine == "mysql" and self.grant_host:
            return f"{self.account_name}@{self.grant_host}"
        return self.account_name


class DbDeployJob(models.Model):
    """数据库实例部署任务"""

    JOB_TYPE_CHOICES = [
        ("mysql_standalone", "MySQL 单实例"),
        ("mysql_replica", "MySQL 从库"),
        ("oracle_standalone", "Oracle 单实例"),
    ]
    STATUS_CHOICES = [
        ("pending", "待执行"),
        ("prechecking", "预检查中"),
        ("running", "执行中"),
        ("verifying", "验证中"),
        ("succeeded", "成功"),
        ("failed", "失败"),
        ("cancelled", "已取消"),
    ]

    job_type = models.CharField(max_length=32, choices=JOB_TYPE_CHOICES, verbose_name="部署类型")
    status = models.CharField(
        max_length=16, choices=STATUS_CHOICES, default="pending", verbose_name="任务状态",
    )
    target_host = models.ForeignKey(
        "common.Host", on_delete=models.PROTECT, related_name="db_deploy_jobs", verbose_name="目标主机",
    )
    environment = models.ForeignKey(
        "common.Environment", on_delete=models.PROTECT, verbose_name="所属环境",
    )
    business = models.ForeignKey(
        "common.Business", on_delete=models.PROTECT, verbose_name="所属业务",
    )
    params = models.JSONField(default=dict, verbose_name="用户参数")
    resolved_params = models.JSONField(default=dict, blank=True, verbose_name="合并参数快照")
    result = models.JSONField(default=dict, blank=True, verbose_name="执行结果")
    instance = models.ForeignKey(
        DatabaseInstance,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="deploy_jobs",
        verbose_name="注册实例",
    )
    creator = models.CharField(max_length=50, blank=True, default="", verbose_name="创建人")
    remark = models.TextField(blank=True, default="", verbose_name="备注")
    error_message = models.CharField(max_length=512, blank=True, default="", verbose_name="错误信息")
    started_at = models.DateTimeField(null=True, blank=True, verbose_name="开始时间")
    finished_at = models.DateTimeField(null=True, blank=True, verbose_name="结束时间")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        db_table = "dbmgr_deploy_job"
        verbose_name = "数据库部署任务"
        verbose_name_plural = verbose_name
        ordering = ["-id"]

    def __str__(self) -> str:
        return f"部署任务 #{self.id} ({self.get_job_type_display()})"


class DbDeployJobStep(models.Model):
    """数据库部署任务步骤"""

    STEP_STATUS_CHOICES = [
        ("pending", "待执行"),
        ("running", "执行中"),
        ("succeeded", "成功"),
        ("failed", "失败"),
        ("skipped", "已跳过"),
    ]

    job = models.ForeignKey(
        DbDeployJob, on_delete=models.CASCADE, related_name="steps", verbose_name="部署任务",
    )
    step_code = models.CharField(max_length=32, verbose_name="步骤编码")
    step_name = models.CharField(max_length=64, verbose_name="步骤名称")
    status = models.CharField(
        max_length=16, choices=STEP_STATUS_CHOICES, default="pending", verbose_name="步骤状态",
    )
    output = models.TextField(blank=True, default="", verbose_name="步骤输出")
    sort_order = models.PositiveSmallIntegerField(default=0, verbose_name="排序")
    started_at = models.DateTimeField(null=True, blank=True, verbose_name="开始时间")
    finished_at = models.DateTimeField(null=True, blank=True, verbose_name="结束时间")

    class Meta:
        db_table = "dbmgr_deploy_job_step"
        verbose_name = "数据库部署步骤"
        verbose_name_plural = verbose_name
        ordering = ["sort_order", "id"]
        constraints = [
            models.UniqueConstraint(fields=["job", "step_code"], name="uniq_dbmgr_deploy_job_step"),
        ]

    def __str__(self) -> str:
        return f"{self.job_id} / {self.step_code}"


class DbDeployMysqlParamTemplate(models.Model):
    """MySQL 单实例部署参数模板（my.cnf 运行参数）"""

    STATUS_CHOICES = [
        ("enabled", "启用"),
        ("disabled", "禁用"),
    ]
    MAJOR_VERSION_CHOICES = [
        ("5.7", "MySQL 5.7"),
        ("8.0", "MySQL 8.0"),
    ]

    template_code = models.CharField(max_length=64, unique=True, verbose_name="模板编码")
    title = models.CharField(max_length=128, verbose_name="模板标题")
    major_version = models.CharField(
        max_length=16, choices=MAJOR_VERSION_CHOICES, verbose_name="MySQL major 版本",
    )
    status = models.CharField(
        max_length=16, choices=STATUS_CHOICES, default="enabled", verbose_name="状态",
    )
    is_default = models.BooleanField(default=False, verbose_name="同 major 默认模板")
    remark = models.TextField(blank=True, default="", verbose_name="备注")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        db_table = "dbmgr_deploy_mysql_param_template"
        verbose_name = "MySQL 部署参数模板"
        verbose_name_plural = verbose_name
        ordering = ["major_version", "title", "id"]

    def __str__(self) -> str:
        return self.title


class DbDeployMysqlParamTemplateItem(models.Model):
    """MySQL 部署参数模板明细行"""

    SECTION_CHOICES = [
        ("mysqld", "[mysqld]"),
        ("client", "[client]"),
    ]

    template = models.ForeignKey(
        DbDeployMysqlParamTemplate,
        on_delete=models.CASCADE,
        related_name="items",
        verbose_name="所属模板",
    )
    sort_order = models.PositiveSmallIntegerField(default=0, verbose_name="排序")
    section = models.CharField(
        max_length=32, choices=SECTION_CHOICES, default="mysqld", verbose_name="配置段",
    )
    param_name = models.CharField(max_length=128, verbose_name="参数名")
    param_value = models.CharField(max_length=512, verbose_name="参数值")
    default_value = models.CharField(max_length=512, blank=True, default="", verbose_name="参考默认值")
    remark = models.CharField(max_length=256, blank=True, default="", verbose_name="备注")

    class Meta:
        db_table = "dbmgr_deploy_mysql_param_template_item"
        verbose_name = "MySQL 部署参数模板明细"
        verbose_name_plural = verbose_name
        ordering = ["sort_order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["template", "section", "param_name"],
                name="uniq_dbmgr_mysql_param_tpl_item",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.param_name}={self.param_value}"
