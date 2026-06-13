from django.db import models


class HostStatus(models.Model):
    """主机状态"""
    name = models.CharField(max_length=64, verbose_name="名称")

    class Meta:
        db_table = "common_host_status"
        verbose_name = "主机状态"
        verbose_name_plural = verbose_name

    def __str__(self) -> str:
        return self.name


class HostType(models.Model):
    """主机类型"""
    name = models.CharField(max_length=64, verbose_name="名称")

    class Meta:
        db_table = "common_host_type"
        verbose_name = "主机类型"
        verbose_name_plural = verbose_name

    def __str__(self) -> str:
        return self.name


class DataCenter(models.Model):
    """所属机房"""
    name = models.CharField(max_length=64, verbose_name="名称")

    class Meta:
        db_table = "common_data_center"
        verbose_name = "所属机房"
        verbose_name_plural = verbose_name

    def __str__(self) -> str:
        return self.name


class Business(models.Model):
    """所属业务"""
    name = models.CharField(max_length=64, verbose_name="名称")

    class Meta:
        db_table = "common_business"
        verbose_name = "所属业务"
        verbose_name_plural = verbose_name

    def __str__(self) -> str:
        return self.name


class OSType(models.Model):
    """OS类型"""
    name = models.CharField(max_length=64, verbose_name="名称")

    class Meta:
        db_table = "common_os_type"
        verbose_name = "OS类型"
        verbose_name_plural = verbose_name

    def __str__(self) -> str:
        return self.name


class Environment(models.Model):
    """所属环境"""
    code = models.CharField(max_length=16, unique=True, verbose_name="环境编码")
    name = models.CharField(max_length=64, verbose_name="环境名称")

    class Meta:
        db_table = "common_environment"
        verbose_name = "所属环境"
        verbose_name_plural = verbose_name

    def __str__(self) -> str:
        return self.name


class Host(models.Model):
    """主机"""
    display_name = models.CharField(max_length=128, verbose_name="显示名")
    hostname = models.CharField(max_length=128, verbose_name="主机名")
    ssh_port = models.IntegerField(default=22, verbose_name="SSH开放端口")
    host_type = models.ForeignKey(HostType, on_delete=models.PROTECT, verbose_name="主机类型")
    host_status = models.ForeignKey(HostStatus, on_delete=models.PROTECT, verbose_name="主机状态")
    datacenter = models.ForeignKey(DataCenter, on_delete=models.PROTECT, verbose_name="所属机房")
    business = models.ForeignKey(Business, on_delete=models.PROTECT, verbose_name="所属业务")
    os_type = models.ForeignKey(OSType, on_delete=models.PROTECT, verbose_name="OS类型")
    os_version = models.CharField(max_length=64, verbose_name="OS版本")
    remark = models.TextField(blank=True, default="", verbose_name="备注")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="修改时间")

    class Meta:
        db_table = "common_host"
        verbose_name = "主机"
        verbose_name_plural = verbose_name

    def __str__(self) -> str:
        return self.display_name or self.hostname


class HostIP(models.Model):
    """主机IP"""
    IP_TYPE_CHOICES = [
        ("business", "业务IP"),
        ("inner", "内网IP"),
        ("outer", "外网IP"),
    ]
    host = models.ForeignKey(Host, on_delete=models.CASCADE, related_name="ips", verbose_name="关联主机")
    ip_address = models.CharField(max_length=64, verbose_name="IP地址")
    ip_type = models.CharField(max_length=16, choices=IP_TYPE_CHOICES, default="business", verbose_name="IP类型")
    nic = models.CharField(max_length=32, verbose_name="使用网卡")

    class Meta:
        db_table = "common_host_ip"
        verbose_name = "主机IP"
        verbose_name_plural = verbose_name

    def __str__(self) -> str:
        return f"{self.ip_address} ({self.get_ip_type_display()})"


class HostAccount(models.Model):
    """主机账号"""
    ACCOUNT_TYPE_CHOICES = [
        ("adm", "管理员"),
        ("std", "普通用户"),
    ]
    host = models.ForeignKey(Host, on_delete=models.CASCADE, related_name="accounts", verbose_name="关联主机")
    account_type = models.CharField(max_length=16, choices=ACCOUNT_TYPE_CHOICES, default="std", verbose_name="账号类型")
    account_name = models.CharField(max_length=64, verbose_name="账号名称")
    account_pswd = models.CharField(max_length=256, verbose_name="账号密码")

    class Meta:
        db_table = "common_host_account"
        verbose_name = "主机账号"
        verbose_name_plural = verbose_name

    def __str__(self) -> str:
        return f"{self.account_name} ({self.get_account_type_display()})"


class HostDomainName(models.Model):
    """主机域名"""
    DOMAIN_TYPE_CHOICES = [
        ("business", "业务域名"),
        ("admin", "管理后台"),
        ("test", "测试域名"),
    ]
    DOMAIN_STATUS_CHOICES = [
        ("normal", "正常"),
        ("stop", "已停用"),
        ("expired", "已过期"),
        ("pending", "待备案"),
    ]
    host = models.ForeignKey(Host, on_delete=models.CASCADE, related_name="domains", verbose_name="关联主机")
    domain_name = models.CharField(max_length=255, verbose_name="域名全称")
    domain_type = models.CharField(max_length=16, choices=DOMAIN_TYPE_CHOICES, default="business", verbose_name="域名类型")
    dns_server = models.CharField(max_length=255, blank=True, default="", verbose_name="域名服务商")
    resolve_ip = models.CharField(max_length=255, blank=True, default="", verbose_name="解析指向")
    status = models.CharField(max_length=16, choices=DOMAIN_STATUS_CHOICES, default="normal", verbose_name="域名状态")
    is_https = models.BooleanField(default=False, verbose_name="启用HTTPS")
    ssl_expire_time = models.DateTimeField(null=True, blank=True, verbose_name="SSL证书到期时间")
    domain_expire_time = models.DateTimeField(null=True, blank=True, verbose_name="域名到期时间")
    business = models.ForeignKey(Business, on_delete=models.PROTECT, null=True, blank=True, verbose_name="所属业务")
    registrant = models.CharField(max_length=128, blank=True, default="", verbose_name="域名负责人")
    remark = models.TextField(blank=True, default="", verbose_name="备注")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        db_table = "common_host_domain"
        verbose_name = "主机域名"
        verbose_name_plural = verbose_name

    def __str__(self) -> str:
        return self.domain_name


class HostHardware(models.Model):
    """硬件信息"""
    host = models.OneToOneField(Host, on_delete=models.CASCADE, related_name="hardware", verbose_name="关联主机")
    cpu_model = models.CharField(max_length=128, blank=True, default="", verbose_name="CPU型号")
    cpu_cores = models.IntegerField(null=True, blank=True, verbose_name="CPU核心数")
    memory = models.CharField(max_length=32, blank=True, default="", verbose_name="内存大小")
    disk = models.CharField(max_length=64, blank=True, default="", verbose_name="磁盘容量")
    disk_detail = models.TextField(blank=True, default="", verbose_name="磁盘明细")
    raid = models.CharField(max_length=32, blank=True, default="", verbose_name="RAID级别")
    vender = models.CharField(max_length=128, blank=True, default="", verbose_name="厂商")
    sn = models.CharField(max_length=128, blank=True, default="", verbose_name="SN序列号")
    remark = models.TextField(blank=True, default="", verbose_name="备注")
    purchase_date = models.DateField(null=True, blank=True, verbose_name="购买日期")
    warranty_date = models.DateField(null=True, blank=True, verbose_name="维保到期日")

    class Meta:
        db_table = "common_host_hardware"
        verbose_name = "硬件信息"
        verbose_name_plural = verbose_name

    def __str__(self) -> str:
        return f"{self.host} 硬件信息"


class VmInstance(models.Model):
    """虚拟机实例"""
    VIRT_PLATFORM_CHOICES = [
        ("vmware", "VMware"),
        ("kvm", "KVM"),
        ("proxmox", "Proxmox"),
        ("other", "其他"),
    ]
    host = models.OneToOneField(Host, on_delete=models.CASCADE, related_name="vm_instance", verbose_name="关联主机")
    virt_platform = models.CharField(max_length=16, choices=VIRT_PLATFORM_CHOICES, default="kvm", verbose_name="虚拟化平台")
    vm_uuid = models.CharField(max_length=128, blank=True, default="", verbose_name="虚拟机UUID")
    parent_host = models.ForeignKey(Host, on_delete=models.PROTECT, related_name="vm_children", verbose_name="归属物理宿主机")
    resource_pool = models.CharField(max_length=128, blank=True, default="", verbose_name="资源池")
    cluster_name = models.CharField(max_length=128, blank=True, default="", verbose_name="所属集群")
    cpu_quota = models.CharField(max_length=32, blank=True, default="", verbose_name="CPU配额")
    mem_quota = models.CharField(max_length=32, blank=True, default="", verbose_name="内存配额")
    disk_quota = models.CharField(max_length=64, blank=True, default="", verbose_name="磁盘配额")
    snapshot_count = models.IntegerField(default=0, verbose_name="快照数量")
    has_snapshot = models.BooleanField(default=False, verbose_name="是否存在快照")
    auto_start = models.BooleanField(default=False, verbose_name="开机自启")
    remark = models.TextField(blank=True, default="", verbose_name="备注")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        db_table = "common_vm_instance"
        verbose_name = "虚拟机实例"
        verbose_name_plural = verbose_name

    def __str__(self) -> str:
        return f"{self.host} VM"


class DockerContainer(models.Model):
    """Docker容器"""
    CONTAINER_STATUS_CHOICES = [
        ("running", "运行中"),
        ("stopped", "已停止"),
        ("exited", "已退出"),
        ("error", "异常"),
    ]
    NETWORK_MODE_CHOICES = [
        ("bridge", "桥接模式"),
        ("host", "主机模式"),
        ("none", "无网络"),
        ("custom", "自定义网络"),
    ]
    RESTART_POLICY_CHOICES = [
        ("no", "不重启"),
        ("always", "总是重启"),
        ("on-failure", "异常重启"),
    ]

    host = models.ForeignKey(Host, on_delete=models.CASCADE, related_name="containers", verbose_name="归属宿主机")
    business = models.ForeignKey(Business, on_delete=models.PROTECT, null=True, blank=True, verbose_name="所属业务")

    # 2. 容器基础标识
    container_name = models.CharField(max_length=128, verbose_name="容器名称")
    container_id = models.CharField(max_length=128, blank=True, default="", verbose_name="容器ID")
    status = models.CharField(max_length=16, choices=CONTAINER_STATUS_CHOICES, default="running", verbose_name="容器状态")

    # 3. 镜像信息
    image_name = models.CharField(max_length=255, blank=True, default="", verbose_name="镜像名称")
    image_repository = models.CharField(max_length=255, blank=True, default="", verbose_name="镜像仓库")

    # 4. 网络信息
    container_ip = models.CharField(max_length=50, blank=True, default="", verbose_name="容器IP")
    port_mapping = models.TextField(blank=True, default="", verbose_name="端口映射")
    network_mode = models.CharField(max_length=20, choices=NETWORK_MODE_CHOICES, default="bridge", verbose_name="网络模式")
    docker_network = models.CharField(max_length=100, blank=True, default="", verbose_name="所属Docker网络")

    # 5. 存储挂载
    volume_mount = models.TextField(blank=True, default="", verbose_name="目录/数据卷挂载")
    
    # 6. 资源限制
    cpu_limit = models.CharField(max_length=32, blank=True, default="", verbose_name="CPU限制")
    mem_limit = models.CharField(max_length=32, blank=True, default="", verbose_name="内存限制")
    
    # 7. 运行配置
    command = models.TextField(blank=True, default="", verbose_name="启动命令")
    env_list = models.TextField(blank=True, default="", verbose_name="环境变量")
    auto_start = models.BooleanField(default=False, verbose_name="开机自启")
    restart_policy = models.CharField(max_length=20, choices=RESTART_POLICY_CHOICES, default="no", verbose_name="重启策略")
    
    # 8. 运维信息
    started_at = models.DateTimeField(null=True, blank=True, verbose_name="启动时间")
    owner = models.CharField(max_length=50, blank=True, default="", verbose_name="负责人")    
    remark = models.TextField(blank=True, default="", verbose_name="备注")
    
    # 时间戳
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        db_table = "common_docker_container"
        verbose_name = "Docker容器"
        verbose_name_plural = verbose_name

    def __str__(self) -> str:
        return self.container_name


class ScriptCategory(models.Model):
    """脚本分类（支持树形层级）"""
    name = models.CharField(max_length=64, verbose_name="分类名称")
    parent = models.ForeignKey(
        "self", on_delete=models.CASCADE, null=True, blank=True,
        related_name="children", verbose_name="上级分类",
    )
    sort_order = models.IntegerField(default=0, verbose_name="排序")
    remark = models.TextField(blank=True, default="", verbose_name="备注")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="修改时间")

    class Meta:
        db_table = "common_script_category"
        verbose_name = "脚本分类"
        verbose_name_plural = verbose_name
        ordering = ["sort_order", "id"]

    def __str__(self) -> str:
        prefix = f"{self.parent.name} / " if self.parent_id else ""
        return f"{prefix}{self.name}"


class ScriptRepository(models.Model):
    """脚本仓库"""
    SCRIPT_TYPE_CHOICES = [
        ("shell", "Shell"),
        ("python", "Python"),
        ("sql", "SQL"),
        ("bat", "Batch"),
        ("perl", "Perl"),
        ("other", "其他"),
    ]
    SCRIPT_STATUS_CHOICES = [
        ("enabled", "启用"),
        ("disabled", "停用"),
    ]

    name = models.CharField(max_length=128, verbose_name="脚本名称")
    category = models.ForeignKey(
        ScriptCategory, on_delete=models.PROTECT, null=True, blank=True,
        related_name="scripts", verbose_name="所属分类",
    )
    script_type = models.CharField(
        max_length=16, choices=SCRIPT_TYPE_CHOICES, default="shell",
        verbose_name="脚本类型",
    )
    content = models.TextField(blank=True, default="", verbose_name="脚本内容")
    description = models.TextField(blank=True, default="", verbose_name="功能描述")
    version = models.CharField(max_length=32, blank=True, default="1.0", verbose_name="版本号")
    status = models.CharField(
        max_length=16, choices=SCRIPT_STATUS_CHOICES, default="enabled",
        verbose_name="状态",
    )
    creator = models.CharField(max_length=50, blank=True, default="", verbose_name="创建人")
    remark = models.TextField(blank=True, default="", verbose_name="备注")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="修改时间")

    class Meta:
        db_table = "common_script_repository"
        verbose_name = "脚本仓库"
        verbose_name_plural = verbose_name

    def __str__(self) -> str:
        return self.name


class BatchTask(models.Model):
    """批量执行任务"""
    TASK_STATUS_CHOICES = [
        ("pending", "待执行"),
        ("running", "执行中"),
        ("completed", "已完成"),
        ("failed", "执行失败"),
    ]

    script = models.ForeignKey(
        ScriptRepository, on_delete=models.PROTECT, related_name="batch_tasks",
        verbose_name="执行脚本",
    )
    status = models.CharField(
        max_length=16, choices=TASK_STATUS_CHOICES, default="pending",
        verbose_name="任务状态",
    )
    forks = models.IntegerField(default=5, verbose_name="并发数")
    total_count = models.IntegerField(default=0, verbose_name="主机总数")
    success_count = models.IntegerField(default=0, verbose_name="成功数")
    fail_count = models.IntegerField(default=0, verbose_name="失败数")
    creator = models.CharField(max_length=50, blank=True, default="", verbose_name="创建人")
    remark = models.TextField(blank=True, default="", verbose_name="备注")
    started_at = models.DateTimeField(null=True, blank=True, verbose_name="开始时间")
    finished_at = models.DateTimeField(null=True, blank=True, verbose_name="结束时间")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        db_table = "common_batch_task"
        verbose_name = "批量执行任务"
        verbose_name_plural = verbose_name

    def __str__(self) -> str:
        return f"批量任务 #{self.id} - {self.script.name}"


class BatchTaskHost(models.Model):
    """批量执行-单台主机结果"""
    HOST_STATUS_CHOICES = [
        ("pending", "等待中"),
        ("running", "执行中"),
        ("success", "成功"),
        ("failed", "失败"),
    ]

    task = models.ForeignKey(
        BatchTask, on_delete=models.CASCADE, related_name="hosts",
        verbose_name="所属任务",
    )
    host = models.ForeignKey(
        Host, on_delete=models.PROTECT, verbose_name="目标主机",
    )
    status = models.CharField(
        max_length=16, choices=HOST_STATUS_CHOICES, default="pending",
        verbose_name="执行状态",
    )
    output = models.TextField(blank=True, default="", verbose_name="执行输出")
    duration = models.CharField(max_length=32, blank=True, default="", verbose_name="执行耗时")
    started_at = models.DateTimeField(null=True, blank=True, verbose_name="开始时间")
    finished_at = models.DateTimeField(null=True, blank=True, verbose_name="结束时间")

    class Meta:
        db_table = "common_batch_task_host"
        verbose_name = "批量执行-主机结果"
        verbose_name_plural = verbose_name

    def __str__(self) -> str:
        return f"{self.task} / {self.host}"


class FileDistTask(models.Model):
    """文件分发任务"""
    SOURCE_TYPE_CHOICES = [
        ("local", "本地上传"),
        ("remote", "远程主机"),
    ]
    TASK_STATUS_CHOICES = [
        ("pending", "待执行"),
        ("running", "执行中"),
        ("completed", "已完成"),
        ("failed", "执行失败"),
    ]

    name = models.CharField(max_length=200, verbose_name="任务名称")
    source_type = models.CharField(
        max_length=16, choices=SOURCE_TYPE_CHOICES, default="local",
        verbose_name="来源类型",
    )
    local_file = models.FileField(
        upload_to="dist_files/%Y%m/", blank=True,
        verbose_name="本地上传文件",
    )
    source_host = models.ForeignKey(
        "Host", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="file_dist_sources", verbose_name="源主机",
    )
    source_path = models.CharField(
        max_length=500, blank=True, default="",
        verbose_name="远程源路径",
    )
    dest_path = models.CharField(
        max_length=500, verbose_name="目标路径",
        help_text="目标主机上的落地目录，如 /opt/software/",
    )
    dest_owner = models.CharField(
        max_length=32, default="root", blank=True, verbose_name="文件属主",
    )
    dest_group = models.CharField(
        max_length=32, default="root", blank=True, verbose_name="文件属组",
    )
    dest_mode = models.CharField(
        max_length=8, default="0644", blank=True, verbose_name="文件权限",
    )
    backup = models.BooleanField(default=False, verbose_name="备份已有文件")
    status = models.CharField(
        max_length=16, choices=TASK_STATUS_CHOICES, default="pending",
        verbose_name="任务状态",
    )
    total_count = models.IntegerField(default=0, verbose_name="主机总数")
    success_count = models.IntegerField(default=0, verbose_name="成功数")
    fail_count = models.IntegerField(default=0, verbose_name="失败数")
    creator = models.CharField(max_length=50, blank=True, default="", verbose_name="创建人")
    remark = models.TextField(blank=True, default="", verbose_name="备注")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        db_table = "common_file_dist_task"
        verbose_name = "文件分发任务"
        verbose_name_plural = verbose_name

    def __str__(self) -> str:
        return f"分发任务 #{self.id} - {self.name}"


class FileDistTaskHost(models.Model):
    """文件分发-单台主机结果"""
    HOST_STATUS_CHOICES = [
        ("pending", "等待中"),
        ("running", "执行中"),
        ("success", "成功"),
        ("failed", "失败"),
    ]

    task = models.ForeignKey(
        FileDistTask, on_delete=models.CASCADE, related_name="hosts",
        verbose_name="所属任务",
    )
    host = models.ForeignKey(
        "Host", on_delete=models.PROTECT, verbose_name="目标主机",
    )
    status = models.CharField(
        max_length=16, choices=HOST_STATUS_CHOICES, default="pending",
        verbose_name="执行状态",
    )
    output = models.TextField(blank=True, default="", verbose_name="执行输出")
    started_at = models.DateTimeField(null=True, blank=True, verbose_name="开始时间")
    finished_at = models.DateTimeField(null=True, blank=True, verbose_name="结束时间")

    class Meta:
        db_table = "common_file_dist_task_host"
        verbose_name = "文件分发-主机结果"
        verbose_name_plural = verbose_name

    def __str__(self) -> str:
        return f"{self.task} / {self.host}"