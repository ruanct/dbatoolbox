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

---

## 八、小结

| 问题 | 建议 |
|------|------|
| 怎么设计？ | **DeployJob（任务）+ Executor（场景）+ Ansible Role（执行）+ CMDB（结果）** 四层分离 |
| 单实例怎么实现？ | 9 步标准流程，成功后写 `DatabaseInstance` + `DatabaseInstanceHost` + `DatabaseAccount` |
| 怎么扩展？ | 新 job_type + 新 Executor + 组合已有 Ansible role；params 里带 context / cluster 关联已有对象 |
| 现有代码怎么用？ | 复用 Host 台账、Ansible 执行、Celery；**不要**复用 BatchTask 当部署引擎 |
| 模型要改吗？ | CMDB **基本不用改**；新增 DeployJob 相关表即可 |

---

## 相关代码路径

| 路径 | 说明 |
|------|------|
| `apps/dbmgr/models.py` | 实例、部署节点、复制集、账号模型 |
| `apps/dbmgr/probe_services.py` | 实例 / 节点探测 |
| `apps/common/tasks.py` | Ansible 批量执行（`_build_inventory`、Celery） |
| `apps/common/models.py` | Host、BatchTask 等 |

---

*文档版本：初稿 · 与项目 dbatoolbox Django 5.2 + LayUI + Celery + Ansible 技术栈对齐*
