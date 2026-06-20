# 数据库实例部署功能 — 设计与实现指南

> 本文档整理自 dbatoolbox 项目内关于「部署 MySQL / Oracle 单实例及后续扩展（从库、MGR 成员、Oracle RAC 等）」的架构讨论，供后续查阅与迭代参考。

---

## 一、现状：已有能力与缺口

### 1.1 已有能力（可复用）

| 模块 | 作用 | 对部署的意义 |
|------|------|--------------|
| `common.Host` / `HostIP` / `HostAccount` | 主机台账 + SSH 连接信息 | 部署目标机器 |
| `common.BatchTask` + Ansible | 批量执行 shell 脚本 | **远程执行引擎**（已有 `_build_inventory`、Celery 异步） |
| `dbmgr.DatabaseInstance` | 实例 CMDB（拓扑、端口、连接地址） | 部署**完成后**写入台账 |
| `dbmgr.DatabaseInstanceHost` | 部署节点 | 单实例 1 节点；RAC / MGR 多节点 |
| `dbmgr.DatabaseReplicationCluster` | 复制集 | 从库、ADG 等场景 |
| `dbmgr.DatabaseAccount` | 连接账号 | 部署后创建默认运维账号 |

### 1.2 目前缺少的

- **没有「部署任务」模型**（只有一次性脚本执行的 `BatchTask`）
- **没有分步骤编排**（安装 → 初始化 → 建库 → 建账号 → 注册台账）
- **没有按场景区分的 Playbook / 脚本体系**
- **没有部署前校验、失败回滚、参数模板**

**结论**：CMDB 模型已经为扩展准备好了，但「部署编排层」需要新建，不要硬塞进 `DatabaseInstance`，也不宜直接复用 `BatchTask` 当部署引擎。

---

## 二、总体设计原则

### 2.1 部署任务与 CMDB 实例分离

```
用户填参数 → 创建 DeployJob（pending）
    → 预检查 → 执行安装 → 后置验证
    → 成功：写入 DatabaseInstance + Host + Account
    → 失败：Job 标记 failed，CMDB 不写入或保持 draft
```

`DatabaseInstance` 继续只做**台账**，不要塞 `install_path`、`root_password` 等部署过程字段（最多后期加 `source_job_id` 追溯来源）。

### 2.2 场景用「策略 + 模板」扩展

```
DeployJob
  job_type: mysql_standalone | oracle_standalone | mysql_replica | mgr_member | oracle_rac_node ...
  params: JSON（版本、端口、路径、密码、关联主库等）
  status: pending → prechecking → running → verifying → succeeded / failed / cancelled
```

每种 `job_type` 对应一个 **Executor**（Python 编排类）+ 一套 **Ansible Role / Playbook**。

### 2.3 执行引擎：在现有 Ansible 上升级

当前 `BatchTask` 是「上传一个脚本跑完就结束」，适合运维脚本，不适合数据库部署。

建议：

- 部署专用 Celery Task（如 `run_db_deploy_job`）
- 使用 **ansible-playbook + extra-vars**，而不是单文件 script
- Playbook 放 `deploy/playbooks/mysql/standalone/`、`deploy/playbooks/oracle/standalone/` 等目录
- 步骤级日志写入 `DbDeployJobStep`

### 2.4 状态机必须显式建模

数据库部署是长任务、可失败、可部分成功，至少需要：

```
pending → prechecking → running → verifying → succeeded
                              ↘ failed
                              ↘ cancelled（人工中止）
```

每一步记录：开始时间、结束时间、stdout / stderr、是否可重试。

---

## 三、第一版：MySQL / Oracle 单实例

### 3.1 建议新增模型

建议在 `apps/dbmgr` 或独立 `apps/dbdeploy` 中新增：

**最小集合：**

```python
# DbDeployJob — 部署任务主表
- job_type          # mysql_standalone / oracle_standalone
- status            # 状态机
- target_host       # FK → common.Host
- environment, business  # 与实例台账一致
- params            # JSONField：版本、端口、datadir、字符集、密码等
- result            # JSONField：安装路径、实际端口、版本号等
- instance          # FK → DatabaseInstance，nullable，成功后回填
- creator, remark
- started_at, finished_at

# DbDeployJobStep — 可选但强烈建议
- job, step_code, step_name, status, output, started_at, finished_at
```

第一版可以不建 `DbDeployTemplate` 表，模板写死在代码 / JSON 文件里；实例多了再加「版本模板管理页」。

### 3.2 单实例部署参数示例

**MySQL standalone `params`：**

```json
{
  "version": "5.7.42",
  "port": 3306,
  "datadir": "/data/mysql",
  "character_set": "utf8mb4",
  "root_password": "***",
  "admin_account": {"name": "dba", "password": "***", "grant_host": "%"},
  "instance_name": "prod-order-mysql-01",
  "connect_host": "10.1.2.10"
}
```

**Oracle standalone `params`：**

```json
{
  "version": "19c",
  "oracle_base": "/u01/app/oracle",
  "oracle_home": "/u01/app/oracle/product/19c/dbhome_1",
  "sid": "ORCL",
  "service_name": "orcl",
  "port": 1521,
  "sys_password": "***",
  "character_set": "AL32UTF8",
  "memory_target_mb": 4096
}
```

### 3.3 单实例执行流程（统一骨架）

| 步骤 | step_code | 说明 |
|------|-----------|------|
| 1 | precheck | OS / 磁盘 / 内存 / 端口 / 依赖包 / 是否已装库 |
| 2 | prepare | 建用户组、目录、内核参数、limits |
| 3 | install | 安装软件包或解压介质 |
| 4 | configure | my.cnf / listener.ora / tnsnames.ora |
| 5 | initialize | mysqld --initialize / dbca 建库 |
| 6 | start | 启服务、开机自启 |
| 7 | post_config | 建账号、安全基线（删 test 库等） |
| 8 | verify | 本地连接探测 SELECT 1 / SELECT 1 FROM DUAL |
| 9 | register_cmdb | 写 DatabaseInstance + DatabaseInstanceHost + DatabaseAccount |

### 3.4 与现有 CMDB 的映射关系

部署成功后自动创建：

| CMDB 表 | 单实例写入内容 |
|---------|----------------|
| `DatabaseInstance` | `topology=standalone`, `engine=mysql/oracle`, `connect_host/port`, `version`, `sid/service_name` |
| `DatabaseInstanceHost` | 1 条，`host=目标主机`, `listener_port=port`, `is_primary=True` |
| `DatabaseAccount` | 默认运维账号 `is_default=True` |

与监控大屏、探测逻辑（`probe_services.py`）可直接衔接。

### 3.5 前端页面（LayUI）

建议独立菜单：**DB实例管理 → 实例部署**

1. 选 `job_type`（MySQL 单实例 / Oracle 单实例）
2. 选目标主机（从 `Host` 列表，展示 OS、IP、硬件）
3. 填部署参数（按 job_type 动态表单）
4. 提交 → 跳转任务详情页（步骤进度 + 日志）
5. 成功后：链接到「实例列表」新记录

---

## 四、未来扩展：从库 / MGR 成员 / RAC

### 4.1 与现有模型对齐

`DatabaseInstance` 已具备扩展位：

| 字段 | 用途 |
|------|------|
| `topology` | `standalone` / `ha_cluster` / `replication` |
| `cluster_style` | `rac` / `mgr` / `innodb_cluster` 等 |
| `role` | `master` / `slave` |
| `replication_cluster` | 复制集关联 |

部署层应对齐这些字段，而不是另起一套概念。

### 4.2 扩展矩阵

| 场景 | job_type | 前置依赖 | CMDB 写入 |
|------|----------|----------|-----------|
| MySQL 单实例 | `mysql_standalone` | 无 | standalone + 1 deploy_host |
| Oracle 单实例 | `oracle_standalone` | 无 | standalone + 1 deploy_host |
| MySQL 从库 | `mysql_replica` | 已有主库 + 复制集 | replication + role=slave + 关联 cluster |
| MGR 新成员 | `mysql_mgr_member` | 已有 MGR 集群（至少 1 个 seed） | ha_cluster + cluster_style=mgr + 新 deploy_host |
| Oracle RAC 节点 | `oracle_rac_node` | 已有 RAC 集群 + Grid | ha_cluster + cluster_style=rac + 新 deploy_host |

### 4.3 必须提前考虑的 8 件事

#### ① 参数模型分三层

```json
{
  "common": { "version", "port", "paths", "passwords" },
  "context": {
    "replication_cluster_id": 12,
    "master_host": "10.1.1.10",
    "master_port": 3306,
    "repl_user": "repl",
    "binlog_file": "...",
    "binlog_pos": 154
  },
  "cluster": {
    "cluster_style": "mgr",
    "group_name": "mgr_prod",
    "seed_members": ["10.1.1.10:33061", "10.1.1.11:33061"]
  }
}
```

新增 job_type 时只加 Executor + params schema，不改核心 Job 表。

#### ② 关联对象校验下沉到 services

- 加从库：主库必须在线，复制集 engine 一致
- 加 MGR 成员：目标集群必须已存在且 `cluster_style=mgr`
- 加 RAC 节点：必须已有 Grid + SCAN + 集群实例名

#### ③ Playbook 按「原子能力」拆分

```
roles/
  common/precheck/
  mysql/install/
  mysql/configure/
  mysql/replication/setup_slave/
  mysql/mgr/join_group/
  oracle/install/
  oracle/listener/
  oracle/rac/add_instance/
```

新场景 = 组合已有 role，而不是复制粘贴。

#### ④ 介质与版本管理

- MySQL / Oracle 安装包来源（内网 yum 源 / 共享 NFS / 平台 uploads）
- `params.version` 映射到具体包名、安装方式（rpm / tar / dbca 模板）
- 建议后期加 `DbDeployPackage` 或配置表

#### ⑤ OS 差异抽象

利用 `Host.os_type` / `os_version`，precheck 按 OS 分支（CentOS 7 / Rocky 8 / Ubuntu 等）。

#### ⑥ 幂等与重试

- 同一主机同一端口重复部署 → precheck 拦截
- Job failed 后支持「从某 step 重试」
- Ansible task 尽量 idempotent

#### ⑦ 安全

- 部署密码在日志中脱敏
- 成功后密码写入 `DatabaseAccount`，Job 内明文可选择清空或加密存储
- 部署操作加权限（仅 DBA 组可执行）

#### ⑧ 与探测 / 监控联动

部署 verify 通过后：

- 触发 `probe_and_save_instance`
- `status=online`，大屏可见

---

## 五、推荐实现路径（分三期）

### 第一期（MVP）

1. 新建 `DbDeployJob` + `DbDeployJobStep`
2. 实现 `mysql_standalone`、`oracle_standalone` 两个 Executor
3. Ansible playbook 各 1 套（先支持最常用的 OS，如 CentOS 7）
4. LayUI：发起部署 + 任务列表 + 任务详情（步骤日志）
5. 成功后自动注册 CMDB

**不要第一期就做 RAC / MGR**，先把编排框架跑通。

### 第二期

- `mysql_replica`：选已有复制集 + 主库，自动 `CHANGE MASTER` / GTID
- `mysql_mgr_member`：bootstrap / join 两种模式
- 部署前检查页（端口占用、磁盘、内存、已装实例冲突）

### 第三期

- `oracle_rac_node`：Grid 已存在前提下加实例 / 加节点
- 模板化（MySQL 5.7/8.0、Oracle 19c 参数模板）
- 审批流、定时部署、批量部署

---

## 六、代码分层建议

贴合项目 Django 规范（views 轻薄、业务在 services）：

```
apps/dbmgr/   # 或 apps/dbdeploy/
├── models.py              # DbDeployJob, DbDeployJobStep
├── serializers.py         # 各 job_type 参数校验
├── views.py               # 薄视图
├── services.py            # create_job, cancel_job, register_instance_from_job
├── deploy_executors/
│   ├── base.py            # BaseDeployExecutor
│   ├── mysql_standalone.py
│   ├── oracle_standalone.py
│   └── registry.py        # job_type → Executor 映射
├── deploy_tasks.py        # Celery: run_db_deploy_job
└── deploy_schemas/        # JSON schema / 默认参数

deploy/                    # 项目根目录，不进 Python 包
├── playbooks/
│   ├── mysql/standalone/site.yml
│   └── oracle/standalone/site.yml
└── roles/
```

`views.py` 只负责接收请求；编排逻辑全在 `services.py` + `deploy_executors/`；远程执行复用 `common.tasks` 里 Ansible 调用方式，但升级为 playbook + extra-vars。

---

## 七、待拍板的关键决策

1. **Oracle 第一版做到哪一步？**
   - 轻量：只装软件 + listener + 提示 DBA 手工 DBCA
   - 完整：全自动 DBCA silent（工作量大，但符合「部署平台」定位）

2. **安装介质从哪来？**
   - 内网 yum / apt 源（推荐，运维可控）
   - 平台上传 tar / rpm（灵活但占存储）

3. **第一期支持哪些版本？**
   - 建议先锁定生产在用的版本（如 MySQL 5.7.42、Oracle 19c），再逐步加 profile

---

## 八、多版本安装：MySQL / Oracle 版本如何考虑

Oracle 和 MySQL 均存在多个大版本、小版本及不同安装方式。若不在设计阶段单独建模，后期每加一个版本就会复制一套 Playbook，维护成本会迅速失控。

### 8.1 先区分三个概念（不要混为一谈）

| 概念 | 含义 | 示例 |
|------|------|------|
| **版本档案（Version Profile）** | 用户在下拉框里选的「可部署版本」 | `mysql-5.7.42`、`mysql-8.0.36`、`oracle-19c` |
| **安装介质（Package / Media）** | 实际用到的 rpm / tar / 内网 repo | `mysql-community-server-5.7.42-1.el7.x86_64.rpm` |
| **部署配方（Recipe / Playbook 变体）** | 该版本对应的安装步骤与默认参数 | 5.7 用 `--initialize-insecure`；8.0 默认 caching_sha2_password |

**DeployJob.params 里只存 `version_profile_id`（或 profile 编码）+ 用户覆盖项**；介质路径、默认路径、兼容 OS 等从 Profile 解析，不要每次让用户手填。

### 8.2 与现有 CMDB 的关系

`DatabaseInstance.version`（`CharField(max_length=32)`）继续表示**实例实际运行版本**，由部署 verify 步骤探测后写入，例如：

- MySQL：`5.7.42`、`8.0.36`
- Oracle：`19.20.0.0.0`（或台账简写 `19c` + 备注存 RU）

部署任务侧额外记录：

- `version_profile`：发起部署时选择的档案
- `result.detected_version`：安装完成后 `SELECT VERSION()` / `v$instance` 探测结果

两者可能不一致时（如选了 19c profile 实际打了 RU），以 **detected_version 写入 CMDB** 为准。

### 8.3 建议新增：版本档案（Version Profile）

第一期可用 YAML 文件；实例部署多了再落库 `DbDeployVersionProfile`。

**推荐字段：**

```python
# DbDeployVersionProfile（后期）
engine              # mysql / oracle
profile_code        # mysql-5.7.42 / oracle-19c（唯一）
display_name        # MySQL 5.7.42
major_version       # 5.7 / 8.0 / 19 / 21
minor_version       # 42 / 36 / 可选
status              # enabled / deprecated / disabled
supported_os        # JSON: ["centos7", "rocky8"]
supported_job_types # JSON: ["mysql_standalone", "mysql_replica"]
install_method      # yum / rpm / tar / oracle_runinstaller
package_ref         # 介质标识或内网 repo 名
default_params      # JSON: datadir、字符集、oracle_home 模板等
playbook_variant    # install_5_7 / install_8_0 / install_19c
min_memory_gb       # 预检查用
remark
```

**第一期 MVP 可放在 `deploy/profiles/`：**

```
deploy/profiles/
├── mysql/
│   ├── 5.7.42.yml
│   └── 8.0.36.yml
└── oracle/
    ├── 19c.yml
    └── 21c.yml
```

### 8.4 MySQL 多版本差异要点

| 维度 | 5.7.x | 8.0.x |
|------|-------|-------|
| 初始化 | `mysqld --initialize-insecure` / `--initialize` | 同左，默认认证插件不同 |
| 默认认证 | `mysql_native_password` | `caching_sha2_password` |
| 复制 | GTID 可选 | GTID 推荐默认 |
| MGR | 不支持 | 仅 8.0+ |
| 配置 | `my.cnf`，部分参数 8.0 已废弃 | `mysql_native_password` 需显式配置时常见 |
| 服务名 | `mysqld` / `mysql` | 同左，路径可能不同 |

**设计建议：**

1. **大版本分 Playbook 变体**（`install_5_7` / `install_8_0`），小版本（5.7.41 vs 5.7.42）通常只改 `package_ref`。
2. **加从库时**：主从 `major_version` 必须一致或落在兼容矩阵内（5.7→5.7，8.0→8.0；跨大版本只做升级项目，不做部署向导默认路径）。
3. **加 MGR 成员**：Profile 必须 `major_version=8.0` 且 `supported_job_types` 含 `mysql_mgr_member`。
4. 项目当前生产为 **MySQL 5.7.42**（见 `CLAUDE.md`），MVP 优先支持该 profile。

### 8.5 Oracle 多版本差异要点

| 维度 | 说明 |
|------|------|
| 营销版本 vs 补丁 | 用户选 `19c`，实际还有 RU（如 19.20）；Profile 可指向「19c 基线 + 推荐 RU」 |
| ORACLE_HOME | 19c 与 21c 路径、安装包、DBCA 模板不同，必须进 `default_params` |
| 安装方式 | 单实例：`runInstaller` + DBCA silent；RAC 还需 Grid Infrastructure |
| OS 兼容 | Oracle 对 OS 版本要求严，Profile 必须绑 `supported_os` |
| 字符集 / 内存 | DBCA 响应文件随版本略有差异 |

**设计建议：**

1. Profile 用 **`oracle-19c`、`oracle-21c`** 等编码，不要只存模糊字符串 `"19c"`。
2. **RAC / ADG 节点**：新节点 Profile 的 `major_version` 必须与集群一致，precheck 读已有实例的 `version` 或 Grid 版本比对。
3. 第一期若只做「软件 + listener + 手工 DBCA」，Profile 仍要预留 `dbca_response_template` 字段，便于第二期 silent 安装。
4. 台账 `DatabaseInstance.version` 建议存 **探测到的完整版本号**；展示层可再映射为 `19c`。

### 8.6 版本兼容矩阵（预检查 + 扩展场景）

在 `services.py` 部署校验层维护（或 Profile 配置）：

| 场景 | 版本规则 |
|------|----------|
| MySQL 单实例 | Profile.enabled + OS 匹配即可 |
| MySQL 从库 | 从库 major = 主库 major；8.0 主不能挂 5.7 从 |
| MySQL MGR 成员 | 必须 8.0+；与集群已有成员 major 一致 |
| Oracle 单实例 | Profile + OS 匹配 |
| Oracle RAC 新节点 | 与现有 RAC 同 major；Grid 版本已满足 |

预检查失败应 **在 Job 进入 running 前拒绝**，并返回可读原因（如「目标主机 Rocky 8 不支持 oracle-19c profile」）。

### 8.7 前端交互建议

1. 用户先选 **数据库类型** → 再选 **版本档案**（下拉仅显示 `status=enabled` 且与目标主机 OS 兼容的项）。
2. 选中 Profile 后 **自动填充** datadir、端口、字符集、oracle_home 等默认值，用户可改。
3. Profile 标记 `deprecated` 的仍可见但提示「建议新版本」，禁止新生产部署可选 `disabled`。
4. 部署任务详情页展示：`profile_code` + 安装完成后 `detected_version`。

### 8.8 Playbook / 目录组织（按版本变体）

```
deploy/
├── profiles/                    # 版本档案 YAML（或后期改 DB）
├── playbooks/
│   ├── mysql/standalone/site.yml    # 入口，根据 profile  include 变体
│   └── oracle/standalone/site.yml
└── roles/
    ├── mysql/
    │   ├── install_5_7/
    │   ├── install_8_0/
    │   ├── configure_5_7/
    │   └── configure_8_0/
    └── oracle/
        ├── install_19c/
        ├── install_21c/
        └── dbca_19c/
```

入口 Playbook 伪逻辑：

```yaml
# site.yml
- include_role:
    name: "mysql/{{ profile.playbook_variant }}"
  vars:
    package_ref: "{{ profile.package_ref }}"
    default_params: "{{ profile.default_params }}"
```

**共用 role**（precheck、start_service、register_cmdb）与 **版本专属 role**（install、initialize）分离，避免复制。

### 8.9 介质管理（与版本档案配合）

| 方式 | 适用 | 注意 |
|------|------|------|
| 内网 yum / apt 源 | MySQL 5.7/8.0、部分 Oracle | Profile 只存 repo 名 + 包名模式 |
| 共享 NFS 目录 | Oracle tar/runInstaller | Profile 存 `media_path` |
| 平台上传 | 非常规版本 | 后期 `DbDeployPackage` 表：checksum、上传人、过期策略 |

同一 Profile 只指向 **一种主安装方式**；换方式则新建 Profile（如 `mysql-5.7.42-yum` vs `mysql-5.7.42-rpm`）。

### 8.10 分阶段落地建议

| 阶段 | 版本能力 |
|------|----------|
| **第一期 MVP** | 2～3 个 YAML Profile（如 MySQL 5.7.42、Oracle 19c）；代码内解析；不做版本管理页 |
| **第二期** | `DbDeployVersionProfile` 表 + 后台维护；主从 / MGR 版本校验 |
| **第三期** | 介质库、deprecated 策略、RU 升级 playbook（与「新装」分离） |

**不要第一期就支持「任意版本」**；每增加一个 Profile，需配套：precheck 规则、install role、verify 命令、（可选）CMDB 默认值模板，并在一台测试机跑通。

### 8.11 小结（多版本）

| 问题 | 建议 |
|------|------|
| 用户选的 version 是什么？ | **Version Profile**（可选列表项），不是自由文本 |
| 小版本怎么办？ | 同 major 共用 Playbook，只改 package_ref |
| 大版本怎么办？ | 独立 `install_X_Y` role + 独立 default_params |
| 和 CMDB 怎么同步？ | 部署成功写 `DatabaseInstance.version = detected_version` |
| 从库 / RAC 怎么控版本？ | 预检查：与主库 / 集群 major 一致 |
| 第一期做多少？ | 只做生产在用的 1～2 个 Profile，架构按 Profile 扩展 |

---

## 九、小结

| 问题 | 建议 |
|------|------|
| 怎么设计？ | **DeployJob（任务）+ Executor（场景）+ Ansible Role（执行）+ CMDB（结果）** 四层分离 |
| 单实例怎么实现？ | 9 步标准流程，成功后写 `DatabaseInstance` + `DatabaseInstanceHost` + `DatabaseAccount` |
| 怎么扩展？ | 新 job_type + 新 Executor + 组合已有 Ansible role；params 里带 context / cluster 关联已有对象 |
| 现有代码怎么用？ | 复用 Host 台账、Ansible 执行、Celery；**不要**复用 BatchTask 当部署引擎 |
| 模型要改吗？ | CMDB **基本不用改**；新增 DeployJob 相关表即可 |
| 多版本怎么管？ | **Version Profile** 统一档案；大版本分 Playbook 变体，小版本改介质即可 |

---

## 相关代码路径

| 路径 | 说明 |
|------|------|
| `apps/dbmgr/models.py` | 实例、部署节点、复制集、账号模型 |
| `apps/dbmgr/probe_services.py` | 实例 / 节点探测 |
| `apps/common/tasks.py` | Ansible 批量执行（`_build_inventory`、Celery） |
| `apps/common/models.py` | Host、BatchTask 等 |

---

*文档版本：v1.1 · 补充多版本安装设计 · 与项目 dbatoolbox Django 5.2 + LayUI + Celery + Ansible 技术栈对齐*
