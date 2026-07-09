# 数据库实例部署功能 — 设计与实现指南

> 本文档整理自 dbatoolbox 项目内关于「部署 MySQL / Oracle 单实例及后续扩展」的架构讨论。**§1～§3、§6～§7 描述当前已实现能力**；§4～§5、§8～§9 中标注「规划/第二期」的条目尚未全部落地。逐步说明见 [db-deploy-mysql5.7-step-action.md](./db-deploy-mysql5.7-step-action.md)。

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

### 1.2 部署编排层（已实现）

原先规划的缺口均已落地，与 `BatchTask` 分离的专用部署链路如下：

| 规划项 | 实现状态 | 代码位置 |
|--------|----------|----------|
| **部署任务模型** | ✅ | `DbDeployJob`、`DbDeployJobStep`（`apps/dbmgr/models.py`）；迁移 `0005_dbdeployjob_dbdeployjobstep.py` |
| **分步骤编排** | ✅ | `DEPLOY_STEPS`（9 步）；`BaseDeployExecutor.run()`（`deploy_executors/base.py`）；Celery `run_db_deploy_job`（`deploy_tasks.py`） |
| **按场景区分 Playbook** | ✅ | `JOB_TYPE_PLAYBOOK_MAP` + `EXECUTOR_REGISTRY`；`deploy/playbooks/mysql/standalone/site.yml`、`oracle/standalone/site.yml` |
| **部署前校验 / 失败恢复 / 参数模板** | ✅ | 创建时：`resolve_deploy_params`、`ensure_deploy_endpoint_available`、主机互斥；Ansible `precheck`；Profile YAML（`deploy/profiles/`）；失败续跑 / `force_rebuild` / `release_endpoint` |

**结论**：CMDB 与部署任务分离；编排逻辑在 `deploy_services.py` + `deploy_executors/`，**不复用** `BatchTask` 当部署引擎。运维批量脚本仍走 `common.BatchTask`。

**失败恢复说明**（非 Ansible 逐步自动回滚）：失败步骤及之后可「继续执行」；实例目录残留可用 `force_rebuild`；`failed` 任务占端点可用 `release_endpoint` 释放。详见 [db-deploy-mysql-install-decision-guide.md](./db-deploy-mysql-install-decision-guide.md)。

### 1.3 端到端流程（当前）

```
LayUI 表单 → POST /db-deploy/api/ → create_deploy_job()
    → resolve_deploy_params()（Profile + 用户参数 → resolved_params 快照）
    → 写入 DbDeployJob + DbDeployJobStep（pending）
    → Celery run_db_deploy_job → get_executor(job_type).run()
         precheck → prepare → install → configure → initialize → start → post_config → verify → register_cmdb
    → 成功：DatabaseInstance + DatabaseInstanceHost + DatabaseAccount
    → 失败：Job.status=failed，CMDB 不写（除非已成功 register_cmdb）
```

页面与 API：`templates/dbmgr/deploy_job_*.html`、`templates/dbmgr/mysql_param_template_list.html`；路由 `apps/dbmgr/urls.py`（`db-deploy/`）。

---

## 二、总体设计原则

### 2.1 部署任务与 CMDB 实例分离

```
用户填参数 → 创建 DeployJob（pending）
    → 预检查 → 执行安装 → 后置验证
    → 成功：写入 DatabaseInstance + Host + Account
    → 失败：Job 标记 failed，CMDB 不写入（`instance` 为空）
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

### 2.3 执行引擎（已采用）

`BatchTask` 仍用于「上传脚本一次性执行」；数据库部署走独立链路：

- Celery `run_db_deploy_job`（`apps/dbmgr/deploy_tasks.py`）
- `ansible-playbook --tags <step>` + `-e @resolved_params.json`（`deploy_ansible.py`）
- Playbook：`deploy/playbooks/mysql/standalone/`、`oracle/standalone/`
- 步骤日志写入 `DbDeployJobStep.output`；Inventory 复用 `common.tasks._build_inventory`

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

### 3.1 数据模型（已实现）

已在 `apps/dbmgr/models.py` 落地：

```python
# DbDeployJob
- job_type          # mysql_standalone / oracle_standalone
- status            # pending → prechecking → running → verifying → succeeded / failed / cancelled
- target_host       # FK → common.Host
- environment, business
- params            # 用户提交的 JSON（分段结构，见 §4.3）
- resolved_params   # Profile 合并 + 路径/server_id 派生后的快照（JSONField）
- result            # verify 写入 detected_version 等
- instance          # FK → DatabaseInstance，nullable，register_cmdb 后回填
- creator, remark, error_message
- started_at, finished_at, created_at, updated_at

# DbDeployJobStep
- job, step_code, step_name, status, output, sort_order
- started_at, finished_at
```

Version Profile 第一版为 YAML 文件（`deploy/profiles/`），未建 `DbDeployTemplate` / `DbDeployVersionProfile` 表。

### 3.2 单实例部署参数示例（实际结构）

用户通过 API / LayUI 提交；`create_deploy_job` 将 `version_profile_code` 写入 `params.meta`。完整分段结构见 §4.3。

**MySQL standalone（节选）：**

```json
{
  "meta": {
    "job_type": "mysql_standalone",
    "version_profile_code": "mysql-5.7.44"
  },
  "cmdb": {
    "instance_name": "prod-order-mysql-01",
    "connect_host": "10.1.2.10",
    "port": 3306,
    "charset": "utf8mb4",
    "topology": "standalone",
    "role": "master"
  },
  "config": {
    "enable_binlog": true,
    "enable_gtid": true,
    "innodb_buffer_pool_size": "1G"
  },
  "credentials": {
    "root_password": "***",
    "admin_account": {
      "account_name": "dba_admin",
      "account_pswd": "***",
      "grant_host": "%"
    }
  }
}
```

`install.*` 路径由 `finalize_mysql_deploy_params()` 按端口生成（如 `/data/mysql/db3306`），用户一般无需填写。共享二进制目录固定 `/usr/local/mysql`。

**Oracle standalone（节选）：**

```json
{
  "meta": { "version_profile_code": "oracle-19c" },
  "cmdb": {
    "instance_name": "prod-orcl-01",
    "port": 1521,
    "sid": "ORCL",
    "service_name": "orcl"
  },
  "install": {
    "oracle_base": "/u01/app/oracle",
    "oracle_home": "/u01/app/oracle/product/19c/dbhome_1"
  },
  "credentials": {
    "sys_password": "***"
  }
}
```

### 3.3 单实例执行流程（统一骨架）

| 步骤 | step_code | 说明（MySQL 已实现） |
|------|-----------|----------------------|
| 1 | precheck | Python 解释器 + OS/架构/软件版本/端口归属/介质 HEAD/glibc |
| 2 | prepare | 用户与目录；force_rebuild 时清理实例目录 |
| 3 | install | 下载解压、`package_ref` 软链、PATH |
| 4 | configure | `my.cnf`（site.yml 内联，按 major 分支） |
| 5 | initialize | `mysqld --initialize-insecure` |
| 6 | start | systemd `mysqld{port}` |
| 7 | post_config | root 密码、DBA 账号、bind-address |
| 8 | verify | `SELECT VERSION()`，写入 `result.detected_version` |
| 9 | register_cmdb | `DatabaseInstance` + `DatabaseInstanceHost` + `DatabaseAccount` |

Oracle Playbook 步骤编码相同，实现程度为 MVP（见 §8）。

### 3.4 与现有 CMDB 的映射关系

部署成功后自动创建：

| CMDB 表 | 单实例写入内容 |
|---------|----------------|
| `DatabaseInstance` | `topology=standalone`, `engine=mysql/oracle`, `connect_host/port`, `version`, `sid/service_name` |
| `DatabaseInstanceHost` | 1 条，`host=目标主机`, `listener_port=port`, `is_primary=True` |
| `DatabaseAccount` | root（`user_adm`）+ DBA（`user_dba`，`is_default=True`）；账号类型见 `deploy_constants` |

部署成功后**未**自动调用 `probe_and_save_instance`（待第二期）。

### 3.5 前端页面（已实现）

菜单：**DB实例管理 → 实例部署**（`/db-deploy/`）

1. 单页表单：部署类型、版本档案、目标主机、台账与配置项
2. `GET /db-deploy/profiles/api/` 拉取 Profile 列表与 `default_params`
3. 提交 `POST /db-deploy/api/` → 跳转 `/db-deploy/{id}/` 详情（步骤进度 + 日志）
4. 详情页操作：`retry`（继续执行）、`force_rebuild`（MySQL）、`release_endpoint`（failed）、`cancel`（pending）
5. 成功后详情页链接到实例台账

---

## 四、实例安装参数配置设计

本章描述参数分层与扩展方向。**MVP 已实现**：`profile_loader.resolve_deploy_params` + `deploy_services._validate_create_body` + `finalize_mysql_deploy_params`；**未实现**：`deploy_schemas/` 目录、分步向导表单、DRF Serializer 独立校验层。

### 4.1 核心思路：四套参数，不要混在一个 JSON 里

| 层级 | 存放位置 | 含义 | 生命周期 |
|------|----------|------|----------|
| **Profile 默认参数** | Version Profile 的 `default_params` | 版本 + 场景推荐的默认值 | 档案维护时配置 |
| **Job 用户参数** | `DbDeployJob.params` | 用户表单提交 / 覆盖项 | 创建任务时写入，只读归档 |
| **Resolved 合并参数** | `DbDeployJob.resolved_params`（JSONField 快照） | Profile 默认 + 用户覆盖 + 主机推导 + MySQL 路径/server_id | 创建时写入；续跑失败任务默认不刷新 |
| **Result 实际参数** | `DbDeployJob.result` | 安装完成后探测到的真实值 | verify 步骤写入 |
| **CMDB 台账字段** | `DatabaseInstance` 等 | 运维长期可见的元数据 | register_cmdb 步骤写入 |

**原则：**

1. 用户**不手填** Profile 已能确定的项（如 5.7 默认路径、推荐字符集），除非打开「高级选项」覆盖。
2. **安装过程参数**（datadir、oracle_home）与 **台账参数**（instance_name、connect_host）分开建模，避免 CMDB 字段膨胀。
3. **敏感参数**（密码）单独标记，日志脱敏，CMDB 只落账号表，不回显到 Job 详情。

### 4.2 参数分类（按用途）

#### ① 台账参数（部署成功后写入 CMDB）

与现有 `DatabaseInstance` / `DatabaseAccount` 字段对齐：

| 参数名 | 对应 CMDB | 必填 | 说明 |
|--------|-----------|------|------|
| `instance_name` | `DatabaseInstance.instance_name` | 是 | 全局唯一 |
| `environment_id` | `environment` | 是 | FK |
| `business_id` | `business` | 是 | FK |
| `engine` | `engine` | 是 | 由 job_type 推导 |
| `topology` | `topology` | 是 | standalone / replication / ha_cluster |
| `role` | `role` | 单实例默认 master | 从库场景为 slave |
| `connect_host` | `connect_host` | 是 | 默认可从目标主机业务 IP 推导 |
| `port` | `port` | 是 | MySQL 3306 / Oracle 1521 |
| `db_name` | `db_name` | MySQL 可选 | 默认库 |
| `charset` | `charset` | 可选 | utf8mb4 / AL32UTF8 |
| `sid` | `sid` | Oracle 单实例 | 与 service_name 至少一项 |
| `service_name` | `service_name` | Oracle | 连接用 |
| `version_profile_code` | 不直接入库 | 是 | 写入 Job；`version` 字段用 detected_version |
| `admin_account` | `DatabaseAccount` | 可选 | `account_type=user_dba`；空密码则跳过创建 |

#### ② 安装路径与资源参数（仅部署过程使用，写入 Job.result）

| 参数名 | MySQL 示例 | Oracle 示例 | 说明 |
|--------|------------|-------------|------|
| `basedir` | `/usr/local/mysql`（共享） | — | 二进制软链目标，非实例 datadir |
| `instance_root` / `datadir` | `/data/mysql/db{port}` | Oracle 路径见 Profile | 由 `build_mysql_install_paths(port)` 生成 |
| `log_error` / `binlog_dir` / `tmpdir` / `slow_query_log_file` | `{instance_root}/mysql_err.log`、`{instance_root}/binlog`、`{instance_root}/tmp`、`{instance_root}/slow_query.log` | — | 由 `build_mysql_install_paths` 生成 |
| `oracle_base` | — | `/u01/app/oracle` | Oracle 基目录 |
| `oracle_home` | — | `.../19c/dbhome_1` | 由 Profile 默认 |
| `memory_target_mb` | `innodb_buffer_pool` 等 | DBCA 内存 | 预检查内存是否够 |
| `disk_gb_min` | 预检查用 | 预检查用 | 可与 datadir 所在盘联动 |

这些**不要**长期挂在 `DatabaseInstance` 上；若日后运维需要，可放 `remark` 或单独「实例扩展属性」表，第一版不必做。

**安装介质（MySQL tar.gz / Oracle zip）** 不放在用户表单里，由 Version Profile 提供 `media_base_url`、`media_subdir`（Oracle）、`package_filename`（当前环境见 §9.9.1、§9.9.2），合并进 `resolved_params.media` 供 Ansible 下载。

#### ③ 数据库配置参数（生成配置文件，非 CMDB 主字段）

| 参数名 | 作用 | 落点 |
|--------|------|------|
| `character_set` / `collation` | 库字符集 | my.cnf / DBCA |
| `innodb_buffer_pool_size` | 内存 | my.cnf |
| `max_connections` | 连接数 | my.cnf |
| `sql_mode` | 5.7 常见 | my.cnf |
| `default_authentication_plugin` | 8.0 | my.cnf |
| `processes` / `sessions` | Oracle | init.ora / DBCA |
| `redolog_size_mb` | Oracle | DBCA |

**实现方式：** Resolved 参数 → `build_mysql_cnf_sections()` 生成 `config.cnf_sections` → Ansible `[configure]` 写入 `my.cnf`；**规划**迁为 Jinja2 `my.cnf.j2`。高级用户可通过 **MySQL 运行参数模板**（`DbDeployMysqlParamTemplate`）维护额外 `mysqld`/`client` 行。

#### ④ 账号与凭证参数（敏感）

```json
{
  "credentials": {
    "root_password": "***",
    "sys_password": "***",
    "system_password": "***",
    "admin_account": {
      "account_name": "dba_admin",
      "account_pswd": "***",
      "grant_host": "%",
      "account_type": "user_dba"
    },
    "repl_account": {
      "account_name": "repl",
      "account_pswd": "***",
      "grant_host": "10.1.%"
    }
  }
}
```

- 存入 `Job.params` 时可加密或任务完成后擦除明文，仅保留「已创建账号」标记。
- 步骤日志、Ansible output **必须脱敏**后再写 `DbDeployJobStep.output`。
- 成功后写入 `DatabaseAccount`，与现有实例账号维护一致。
- **`mysql_replica` 特例**：表单不要求 `root_password`；`resolve` 时从主库 CMDB 读取 `user_adm / root@localhost` 写入 `resolved_params.credentials.root_account`，供 `repl_setup` 连接从库 socket（详见 [db-deploy-mysql-replica-guide.md](./db-deploy-mysql-replica-guide.md) §2.2、§7.1）。

#### ⑤ 场景上下文参数（扩展场景）

| job_type | 典型 context 参数 |
|----------|-------------------|
| `mysql_replica` | `replication_cluster_id`, `master_instance_id`, `repl_mode`（gtid/file） |
| `mysql_mgr_member` | `cluster_instance_id`, `group_name`, `group_seeds`, `local_address` |
| `oracle_rac_node` | `cluster_instance_id`, `node_name`, `scan_name`, `grid_home` |

context 参数在表单中通过**选择已有对象**（复制集、主库、集群）带出，减少手填。

### 4.3 Job.params 推荐 JSON 结构

统一结构，便于 Serializer 校验与 Executor 解析：

```json
{
  "meta": {
    "job_type": "mysql_standalone",
    "version_profile_code": "mysql-5.7.44"
  },
  "target": {
    "host_id": 101
  },
  "cmdb": {
    "instance_name": "prod-order-mysql-01",
    "environment_id": 1,
    "business_id": 2,
    "connect_host": "10.1.2.10",
    "port": 3306,
    "db_name": "order_db",
    "charset": "utf8mb4",
    "topology": "standalone",
    "role": "master",
    "remark": ""
  },
  "install": {},
  "config": {
    "character_set": "utf8mb4",
    "collation": "utf8mb4_unicode_ci",
    "innodb_buffer_pool_size": "2G",
    "max_connections": 500,
    "extra_cnf_lines": ""
  },
  "credentials": {
    "root_password": "***",
    "admin_account": {
      "account_name": "dba_admin",
      "account_pswd": "***",
      "grant_host": "%",
      "account_type": "user_dba"
    }
  },
  "context": {},
  "overrides": {}
}
```

Oracle 单实例将 `install` / `config` 换为：

```json
{
  "install": {
    "oracle_base": "/u01/app/oracle",
    "oracle_home": "/u01/app/oracle/product/19c/dbhome_1",
    "ora_inventory": "/u01/app/oraInventory",
    "datafile_dest": "/u01/oradata"
  },
  "config": {
    "sid": "ORCL",
    "service_name": "orcl",
    "character_set": "AL32UTF8",
    "national_character_set": "AL16UTF16",
    "memory_target_mb": 4096,
    "processes": 300
  },
  "credentials": {
    "sys_password": "***",
    "system_password": "***"
  }
}
```

### 4.4 参数合并规则（已实现）

合并入口：`profile_loader.resolve_deploy_params()`；MySQL 额外调用 `finalize_mysql_deploy_params()`（`deploy_constants.py`）。

```
merged = deep_merge(profile.default_params, user_params)
merged["target"] ← 主机 hostname、os_type、os_version
merged["cmdb"].connect_host ← 业务 IP（若用户未填）
merged["profile"] ← major/minor、supported_os_rules
merged["media"] ← build_media_info(profile)
# MySQL only:
merged["install"] ← build_mysql_install_paths(port)  # /data/mysql/db{port}/...
merged["config"]  ← enable_binlog/gtid、server_id=SHA256(host:port)
ensure_deploy_endpoint_available / ensure_mysql_server_id_available
```

创建任务时写入 `resolved_params`；失败续跑默认**不**刷新快照（避免与已初始化实例不一致）。全量重跑 / `force_rebuild` / `cancelled` 后重试会刷新。

| 条件 | 行为 |
|------|------|
| `cmdb.connect_host` 为空 | 取目标主机 `HostIP.ip_type=business` |
| MySQL `install.*` | 按 `cmdb.port` 自动生成，覆盖 Profile 中 install 段 |
| `cmdb.port` 端点冲突 | 创建时 `ensure_deploy_endpoint_available` 失败 |
| 开 binlog | 自动生成并校验 `server_id` 唯一 |
| 同主机并发部署 | `ensure_host_deploy_lock_available` 拒绝 |

### 4.5 参数校验（当前 vs 规划）

**当前（MVP）**：`deploy_services._validate_create_body()` + `resolve_deploy_params()` / `finalize_mysql_deploy_params()` 内校验；无独立 `deploy_schemas/` 目录、无 DRF Serializer。

| 层级 | 时机 | 内容 |
|------|------|------|
| **静态校验** | `POST /db-deploy/api/` | 必填项、端口范围、GTID⇒Binlog、实例名唯一、Oracle sid/service、MySQL connect_host=业务 IP |
| **合并时校验** | `resolve_deploy_params` | Profile 存在且 enabled、job_type 匹配、OS 规则（MySQL） |
| **端点/server_id** | `finalize_mysql_deploy_params` | CMDB 与进行中任务（含 `failed`）端点互斥；server_id 互斥 |
| **动态预检查** | Ansible `precheck` | 目标机端口归属、软件版本、介质 HEAD、glibc 等 |

**规划（第二期）**：`deploy_schemas/` + Schema 驱动动态表单（`GET /deploy/schema/`）。

### 4.6 参数 → Ansible → 配置文件

数据流：

```
表单 → Job.params
     → merge → Job.resolved_params
     → Executor 转为 ansible extra-vars（credentials 走 vault 或 env）
     → Playbook roles 写 my.cnf / systemd / listener.ora / dbca.rsp
     → verify 探测 → Job.result
     → register_cmdb → DatabaseInstance / DatabaseAccount
```

**Ansible 传参（已实现）**：整份 `resolved_params` 作为 extra-var `deploy`，Playbook 内通过 `d: "{{ deploy }}"` 读取：

```yaml
# deploy_ansible.py
json.dump({"deploy": deploy_vars}, ...)
ansible-playbook ... -e @vars.json --tags precheck
```

密码类字段仍在 `deploy.credentials` 明文传递（快照与 `Job.params` 均明文；API 响应用 `mask_sensitive_data` 脱敏）。步骤 `output` **未**系统性脱敏。

### 4.7 前端表单（已实现）

当前为**单页表单**（`templates/dbmgr/deploy_job_list.html`），非分步向导：

- 选 `job_type` 后加载 `GET /db-deploy/profiles/api/?job_type=...`
- Profile 的 `default_params` 用于预填端口等；MySQL 可选 **参数模板**（`meta.mysql_param_template_title`）
- `connect_host` 只读，取自目标主机业务 IP（后端强制校验）
- **实例目录**只读展示 `/data/mysql/db{port}`，随端口联动；**实例参数**区预览路径与 Binlog/GTID 项
- 高级项：binlog/gtid、root/DBA 密码（默认 DBA 名 `dba_admin`）

**规划**：分步向导、`GET /deploy/schema/` 驱动字段元数据。

### 4.8 MySQL 单实例 — 参数字段清单（MVP）

| 字段 | 分组 | 必填 | 默认来源 | CMDB / 用途 |
|------|------|------|----------|-------------|
| version_profile_code | 版本 | 是 | 下拉 | Job.meta |
| host_id | 目标 | 是 | 用户选 | target |
| instance_name | 台账 | 是 | 用户填 | CMDB |
| environment_id / business_id | 台账 | 是 | 用户选 | CMDB |
| connect_host | 连接 | 是 | 主机业务 IP（MySQL 不可改） | CMDB |
| port | 连接 | 是 | Profile 默认 3306 | CMDB + 路径/server_id |
| mysql_param_template_title | 配置 | 否 | 用户选模板标题 | 合并进 my.cnf（`DbDeployMysqlParamTemplate`） |
| enable_binlog / enable_gtid | 配置 | 否 | 表单默认 true | my.cnf |
| datadir 等 install 路径 | 安装 | 自动 | `build_mysql_install_paths`（`/data/mysql/db{port}`） | 表单只读展示，提交不传 |
| innodb_buffer_pool_size | 配置 | 否 | Profile | my.cnf |
| root_password | 账号 | 是 | 用户填 | 初始化 + `user_adm` 台账 |
| admin_account | 账号 | 否 | 用户填 | `user_dba` 台账 |

### 4.8.1 MySQL 从库 — 参数字段清单（MVP）

| 字段 | 分组 | 必填 | 默认来源 | CMDB / 用途 |
|------|------|------|----------|-------------|
| replication_cluster_id | 复制 | 是 | 用户选复制集 | `context`；自动带出主库 |
| repl_account_id | 账号 | 是 | 主库 `user_repl` 列表 | `credentials.repl_account`；`CHANGE MASTER` 的复制用户 |
| dump_account_id | 账号 | 否 | 主库默认 `user_dba` | `credentials.dump_account`；`mysqldump` 连主库 |
| （无）root_password | 账号 | — | **主库台账** `user_adm/root@localhost` | `credentials.root_account`；`repl_setup` 连从库 socket |
| port | 连接 | 是 | 用户填（默认可 3306） | 从库监听端口 |

> 从库不要求表单填写 root / DBA 密码；`repl_setup` 使用主库已登记的 root 密码操作从库本地实例。完整流程见 [db-deploy-mysql-replica-guide.md](./db-deploy-mysql-replica-guide.md)。

### 4.9 Oracle 单实例 — 参数字段清单（MVP）

| 字段 | 分组 | 必填 | 默认来源 | CMDB / 用途 |
|------|------|------|----------|-------------|
| version_profile_code | 版本 | 是 | 下拉 | Job.meta |
| host_id | 目标 | 是 | 用户选 | target |
| instance_name | 台账 | 是 | 用户填 | CMDB |
| connect_host / port | 连接 | 是 | IP / 1521 | CMDB |
| sid | Oracle | 是* | 用户填 | CMDB + DBCA |
| service_name | Oracle | 是* | 用户填 | CMDB + listener |
| oracle_base / oracle_home | 安装 | 是 | Profile | install |
| datafile_dest | 安装 | 是 | Profile | DBCA |
| character_set | 配置 | 是 | AL32UTF8 | DBCA |
| memory_target_mb | 配置 | 是 | Profile / 硬件 | DBCA + precheck |
| sys_password / system_password | 账号 | 是 | 用户填 | 初始化 |
| admin_account | 账号 | 建议 | 用户填 | DatabaseAccount |

\* sid 与 service_name 至少填一项（与现有 `DatabaseInstance` 校验一致）。

### 4.10 MySQL 运行参数模板（已实现）

除 Profile YAML 外，平台提供 **MySQL 运行参数模板**（`DbDeployMysqlParamTemplate` / `DbDeployMysqlParamTemplateItem`），用于维护可复用的 `my.cnf` 片段（`mysqld` / `client` 段）。

| 能力 | 说明 | 代码 |
|------|------|------|
| **模板 CRUD** | 按 major 版本维护参数项；启用/禁用 | `deploy_param_template_services.py`；页面 `mysql_param_template_list.html` |
| **部署选用** | 新建 MySQL 单实例任务时可选模板标题 | `meta.mysql_param_template_title` → `apply_mysql_param_template_to_merged()` |
| **合并顺序** | Profile → 参数模板 → 用户 params → `finalize_mysql_deploy_params()` | `profile_loader.resolve_deploy_params()` |
| **保留项** | `basedir`、`datadir`、`port`、`tmpdir`、`slow_query_log_file` 等由平台派生，**禁止**写入模板 | `MYSQL_PARAM_TEMPLATE_RESERVED_NAMES` |

模板不含密码；`build_mysql_cnf_sections()` 生成平台固定行后再合并模板项，最终由 `site.yml` `[configure]` 写入 `cnf_path`。

**尚未实现**：从已有实例克隆参数、环境级默认模板表、Oracle 参数模板。

### 4.11 常见误区（避免）

| 误区 | 正确做法 |
|------|----------|
| 所有参数都塞进 `DatabaseInstance` | 安装路径进 Job.result，CMDB 只保留连接与运维必要字段 |
| 每个版本一套独立表单 | 同一 job_type 共用 Schema，差异由 Profile 的 default_params 驱动 |
| 用户粘贴完整 my.cnf | 结构化 config 字段 + 可选 extra_lines |
| 密码明文写进 step 日志 | 脱敏 + 可选加密存储 |
| 提交时做端口检测 | 端口检测放 precheck 步骤（需 SSH 到目标机） |
| params 扁平无结构 | 按 meta/target/cmdb/install/config/credentials/context 分段 |

### 4.12 小结（参数配置）

| 问题 | 建议 |
|------|------|
| 参数存在哪？ | Profile 默认 + Job.params + resolved 快照 + result + CMDB 五层 |
| 怎么扩展？ | 新 job_type 增加 Schema 与 context 段，核心 JSON 结构不变 |
| 怎么校验？ | 静态 Schema + 动态 precheck 分工 |
| 怎么驱动安装？ | resolved_params → Ansible extra-vars → 模板生成配置文件 |
| 与 CMDB 关系？ | cmdb 段字段映射 `DatabaseInstance`；credentials 映射 `DatabaseAccount` |
| MVP 做什么？ | ✅ 单页表单 + Profile YAML + `_validate_create_body`；Schema 驱动表单待第二期 |

---

## 五、未来扩展：从库 / MGR 成员 / RAC

### 5.1 与现有模型对齐

`DatabaseInstance` 已具备扩展位：

| 字段 | 用途 |
|------|------|
| `topology` | `standalone` / `ha_cluster` / `replication` |
| `cluster_style` | `rac` / `mgr` / `innodb_cluster` 等 |
| `role` | `master` / `slave` |
| `replication_cluster` | 复制集关联 |

部署层应对齐这些字段，而不是另起一套概念。

### 5.2 扩展矩阵

| 场景 | job_type | 前置依赖 | CMDB 写入 |
|------|----------|----------|-----------|
| MySQL 单实例 | `mysql_standalone` | 无 | standalone + 1 deploy_host |
| Oracle 单实例 | `oracle_standalone` | 无 | standalone + 1 deploy_host |
| MySQL 从库 | `mysql_replica` | 已有主库 + 复制集 | replication / slave + 复制主库账号台账（见 replica 指南） |
| MGR 新成员 | `mysql_mgr_member` | 已有 MGR 集群（至少 1 个 seed） | ha_cluster + cluster_style=mgr + 新 deploy_host |
| Oracle RAC 节点 | `oracle_rac_node` | 已有 RAC 集群 + Grid | ha_cluster + cluster_style=rac + 新 deploy_host |

### 5.3 必须提前考虑的 8 件事

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

#### ⑥ 幂等与重试（MySQL 已实现）

- 端点冲突：创建时静态校验 + precheck 动态校验
- `failed` 任务：`retry` 从失败步续跑（跳过已成功步）；`force_rebuild` 清实例目录全量重装；`release_endpoint` 释放端点
- Ansible task 使用 `creates:` 等保证部分步骤幂等

#### ⑦ 安全（部分实现）

- API 列表/详情：`mask_sensitive_data` 脱敏 `params` / `resolved_params`
- `Job.params`、`DatabaseAccount`、目标机 `.root-client.cnf` **仍为明文**
- 部署权限：沿用 Django 登录，**未**单独限制 DBA 组

#### ⑧ 与探测 / 监控联动（未实现）

部署 verify 通过后 register_cmdb 写 `status=online`，**未**自动调用 `probe_and_save_instance`。

---

## 六、推荐实现路径（分三期）

### 第一期（MVP）— 已完成

1. ✅ `DbDeployJob` + `DbDeployJobStep`（任务落库）
2. ✅ Version Profile YAML（`deploy/profiles/`，未建 `DbDeployVersionProfile` 表）
3. ✅ `profile_loader`：按 `version_profile_code` 加载并合并 `resolved_params`
4. ✅ `mysql_standalone`、`oracle_standalone` 两个 Executor
5. ✅ Ansible playbook 各 1 套（MySQL 生产可用；Oracle 为 MVP playbook）
6. ✅ LayUI：发起部署 + 任务列表 + 任务详情（步骤日志）
7. ✅ 成功后自动注册 CMDB
8. ✅ MySQL 运行参数模板（`DbDeployMysqlParamTemplate`）CRUD + 部署表单选用

**待第二期**：`mysql_replica`、MGR、RAC；Profile **落库**管理页；部署后自动 probe。

### 第二期

- `DbDeployVersionProfile` **落库** + 后台维护页（从 YAML 迁移或双写过渡）
- `mysql_replica`：选已有复制集 + 主库，自动 `CHANGE MASTER` / GTID
- `mysql_mgr_member`：bootstrap / join 两种模式
- 部署前检查页（端口占用、磁盘、内存、已装实例冲突）

### 第三期

- `oracle_rac_node`：Grid 已存在前提下加实例 / 加节点
- Oracle 19c 运行参数模板；从实例克隆部署参数

---

## 七、代码分层（当前目录）

```
apps/dbmgr/
├── models.py                 # DbDeployJob, DbDeployJobStep, CMDB
├── views.py                  # 薄视图，/db-deploy/ 路由
├── deploy_services.py        # 任务 CRUD、续跑、注册台账
├── deploy_constants.py       # 步骤、路径、端点/server_id 校验
├── deploy_ansible.py         # ansible-playbook 单步执行
├── deploy_os_compat.py       # Profile OS 规则校验
├── profile_loader.py         # Profile 加载与 resolve_deploy_params
├── deploy_param_template_services.py  # MySQL 运行参数模板 CRUD / 合并
├── deploy_executors/
│   ├── base.py
│   ├── mysql_standalone.py
│   ├── oracle_standalone.py
│   └── registry.py
├── deploy_tasks.py           # Celery run_db_deploy_job
└── tasks.py                  # import deploy_tasks 供 autodiscover

deploy/
├── profiles/mysql/*.yml      # 5.7.44, 8.0.45, 8.0.45-glibc217
├── profiles/oracle/*.yml     # 19c, 21c
└── playbooks/
    ├── mysql/standalone/site.yml
    └── oracle/standalone/site.yml
```

编排逻辑在 `deploy_services.py` + `deploy_executors/`；Inventory 复用 `common.tasks._build_inventory`。**未**使用 `deploy/roles/` 拆分（步骤均在 `site.yml` 内按 tag 组织）。

---

## 八、关键决策（已拍板 / 现状）

| 决策 | 结论 |
|------|------|
| Oracle 第一版 | MVP Playbook（软件 + listener + DBCA 路径），非生产全量验证 |
| 安装介质 | Profile YAML 配置 `media_base_url` + `package_filename`；可用 `.env` 覆盖前缀 |
| MySQL 第一期版本 | `mysql-5.7.44`、`mysql-8.0.45`（及 `8.0.45-glibc217` 变体） |
| Oracle Profile | `oracle-19c`、`oracle-21c` |
| my.cnf 模板化 | **未做**；configure 仍在 `site.yml` 内联（见 multiversion-tasks） |
| 失败回滚 | 续跑 / force_rebuild / release_endpoint，无逐步 undo |

---

## 九、多版本安装：MySQL / Oracle 版本如何考虑

Oracle 和 MySQL 均存在多个大版本、小版本及不同安装方式。若不在设计阶段单独建模，后期每加一个版本就会复制一套 Playbook，维护成本会迅速失控。

### 9.1 先区分三个概念（不要混为一谈）

| 概念 | 含义 | 示例 |
|------|------|------|
| **版本档案（Version Profile）** | 用户在下拉框里选的「可部署版本」 | `mysql-5.7.44`、`mysql-8.0.45`、`oracle-19c` |
| **安装介质（Package / Media）** | 实际用到的 tar / zip / 内网 URL | MySQL：`.../tgz/mysql-5.7.44-....tar.gz`；Oracle 19c：`.../zip/19c/LINUX.X64_193000_db_home.zip`；21c：`.../zip/21c/LINUX.X64_213000_db_home.zip` |
| **部署配方（Recipe / Playbook 变体）** | 该版本对应的安装步骤与默认参数 | 5.7 用 `--initialize-insecure`；8.0 默认 caching_sha2_password |

**DeployJob.params 里只存 `version_profile_id`（或 profile 编码）+ 用户覆盖项**；介质路径、默认路径、兼容 OS 等从 Profile 解析，不要每次让用户手填。

### 9.2 与现有 CMDB 的关系

`DatabaseInstance.version`（`CharField(max_length=32)`）继续表示**实例实际运行版本**，由部署 verify 步骤探测后写入，例如：

- MySQL：`5.7.44`、`8.0.45`
- Oracle：`19.20.0.0.0`（或台账简写 `19c` + 备注存 RU）

部署任务侧额外记录：

- `version_profile`：发起部署时选择的档案
- `result.detected_version`：安装完成后 `SELECT VERSION()` / `v$instance` 探测结果

两者可能不一致时（如选了 19c profile 实际打了 RU），以 **detected_version 写入 CMDB** 为准。

### 9.3 版本档案（Version Profile）— 已实现 YAML

第一期使用 `deploy/profiles/**/*.yml`；`profile_loader.load_all_profiles()` 扫描加载。后期可落库 `DbDeployVersionProfile`。

**推荐字段：**

```python
# DbDeployVersionProfile（后期）
engine              # mysql / oracle
profile_code        # mysql-5.7.44 / mysql-8.0.45 / oracle-19c（唯一）
display_name        # MySQL 5.7.44
major_version       # 5.7 / 8.0 / 19 / 21
minor_version       # 42 / 36 / 可选
status              # enabled / deprecated / disabled
supported_os_rules  # 如 family: centos, min_major: 7（见 deploy_os_compat）
supported_arch      # 如 ["x86_64"]
supported_job_types # JSON: ["mysql_standalone", "mysql_replica"]
install_method      # yum / rpm / tar / tar_http / zip_http / oracle_runinstaller
package_ref         # 介质标识（逻辑名）
media_base_url      # HTTP 目录前缀（内网软件库根路径）
media_subdir        # 可选：Oracle 版本子目录，如 19c / 21c
package_filename    # tar.gz / zip 文件名（以软件库实际文件名为准）
package_url         # 完整 URL（由 base_url + subdir + filename 拼接）
package_checksum    # 可选：sha256
default_params      # JSON: datadir、字符集、oracle_home 模板等
playbook_variant    # install_5_7 / install_8_0 / install_19c
min_memory_gb       # 预检查用
remark
```

**第一期 MVP 可放在 `deploy/profiles/`：**

```
deploy/profiles/
├── mysql/
│   ├── 5.7.44.yml
│   ├── 8.0.45.yml
│   └── 8.0.45-glibc217.yml
└── oracle/
    ├── 19c.yml
    └── 21c.yml
```

#### 9.3.1 第一版存储策略：Profile 存文件还是存表？

**结论（MVP）：Version Profile 放在 YAML 文件；部署任务放在数据库表。** 二者职责分离，不要第一版就为 Profile 建表、做 CRUD 页面。

**分工一览：**

| 内容 | 第一版存放 | 第二版及以后 |
|------|------------|--------------|
| Version Profile（版本、介质 URL、`default_params`、playbook 变体） | `deploy/profiles/**/*.yml` | 可迁移至 `DbDeployVersionProfile` 表 + 管理页 |
| 部署任务 `DbDeployJob` | 数据库表 | 不变 |
| 任务步骤 `DbDeployJobStep` | 数据库表 | 不变 |
| Job 对 Profile 的引用 | `params.meta.version_profile_code`（字符串） | 可改为 FK `version_profile_id` |
| 执行用合并参数 | `resolved_params`（JSONField 快照） | 不变 |

**第一版 Job 表记什么（不把 Profile 全文塞进 Job）：**

```json
{
  "meta": {
    "job_type": "mysql_standalone",
    "version_profile_code": "mysql-5.7.44"
  },
  "target": { "host_id": 101 },
  "cmdb": { "instance_name": "...", "port": 3306 },
  "install": {},
  "credentials": { "root_password": "***" }
}
```

`install` 段在 MySQL 合并后由 `build_mysql_install_paths` 填充，示例中可省略。

**第一版用文件、不用表的原因：**

1. Profile 数量仍少，变更走 Git；已上线 MySQL 5.7.44 / 8.0.45、Oracle 19c/21c YAML。
2. Profile 与 `deploy/playbooks/`、`deploy/roles/` 同仓，发布版本一致。
3. 减少 MVP 范围：无需 Profile 管理页、迁移、权限与在线编辑。
4. 任务侧仍必须落库，便于审计、重试、步骤日志。

**代码侧最小实现：**

```
apps/dbmgr/
├── profile_loader.py       # load_profile(code) → dict；list_profiles(engine?)
├── deploy_tasks.py
└── ...

deploy/profiles/            # 与代码同仓，不进 Python 包
├── mysql/5.7.44.yml
└── oracle/19c.yml, 21c.yml
```

`GET /db-deploy/profiles/api/` 返回 Profile 列表；字段见 `profile_loader.list_profiles()`。

**环境变量覆盖（可选，减少改 YAML 频率）：**

```bash
# .env
DEPLOY_MYSQL_MEDIA_BASE_URL=http://10.32.14.211/soft/mysql/tgz/
DEPLOY_ORACLE_MEDIA_BASE_URL=http://10.32.14.211/soft/oracle/zip/
```

`profile_loader` 合并 YAML 中的 `media_base_url` 时，若环境变量存在则覆盖前缀；`package_filename`、`media_subdir` 仍以 YAML 为准。

**何时改为存表（第二期触发条件）：**

- DBA 需在页面上改介质地址、默认参数，而不想发版改 YAML
- Profile 数量增多（如 >10）或多人并行维护易冲突
- 需要 `enabled` / `deprecated` 在线切换与变更审计
- 需要按环境（生产/测试）配置不同 Profile 默认值

落库时，`DbDeployVersionProfile` 字段与 §9.3 及 YAML 结构 **一一对应**，便于脚本从 YAML 批量导入；`Job.params.meta.version_profile_code` 可逐步改为 `version_profile_id` FK。

### 9.4 MySQL 多版本差异要点

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
4. 项目当前生产数据库版本为 **MySQL 5.7.42**（见 `CLAUDE.md`）；内网安装介质已为 **5.7.44 tgz**（见 §9.9.1），Profile 以介质为准，部署完成后 CMDB `version` 写探测值（如 `5.7.44`）。

### 9.5 Oracle 多版本差异要点

| 维度 | 说明 |
|------|------|
| 营销版本 vs 补丁 | 用户选 `19c`，实际还有 RU（如 19.20）；Profile 可指向「19c 基线 + 推荐 RU」 |
| ORACLE_HOME | 19c 与 21c 路径、安装包、DBCA 模板不同，必须进 `default_params` |
| 安装方式 | 单实例：`runInstaller` + DBCA silent；RAC 还需 Grid Infrastructure |
| OS 兼容 | Oracle Profile 使用 `supported_os` 列表；MySQL 使用 `supported_os_rules` + `deploy_os_compat` |
| 字符集 / 内存 | DBCA 响应文件随版本略有差异 |

**设计建议：**

1. Profile 用 **`oracle-19c`、`oracle-21c`** 等编码，不要只存模糊字符串 `"19c"`。
2. **RAC / ADG 节点**：新节点 Profile 的 `major_version` 必须与集群一致，precheck 读已有实例的 `version` 或 Grid 版本比对。
3. 第一期若只做「软件 + listener + 手工 DBCA」，Profile 仍要预留 `dbca_response_template` 字段，便于第二期 silent 安装。
4. 台账 `DatabaseInstance.version` 建议存 **探测到的完整版本号**；展示层可再映射为 `19c`。

### 9.6 版本兼容矩阵（预检查 + 扩展场景）

在 `services.py` 部署校验层维护（或 Profile 配置）：

| 场景 | 版本规则 |
|------|----------|
| MySQL 单实例 | Profile.enabled + OS 匹配即可 |
| MySQL 从库 | 从库 major = 主库 major；8.0 主不能挂 5.7 从 |
| MySQL MGR 成员 | 必须 8.0+；与集群已有成员 major 一致 |
| Oracle 单实例 | Profile + OS 匹配 |
| Oracle RAC 新节点 | 与现有 RAC 同 major；Grid 版本已满足 |

预检查失败应 **在 Job 进入 running 前拒绝**，并返回可读原因（如「目标主机 Rocky 8 不支持 oracle-19c profile」）。

### 9.7 前端交互（已实现 / 规划）

**已实现**：

1. 选部署类型 → `GET /db-deploy/profiles/api/` 拉 Profile 列表（`status=disabled` 不返回）。
2. MySQL 单实例：选 Profile 后可选 **参数模板**（`GET /db-deploy/mysql-param-template/api/?scope=options`）；**连接地址**只读（取自目标主机业务 IP）；**实例目录**只读展示（`/data/mysql/db{port}`，随端口联动）；**实例参数**区预览路径与 Binlog/GTID 派生项。
3. 任务详情展示 `version_profile_code`、`result.detected_version`、步骤日志。

**规划**：按 OS 过滤 Profile 下拉、`deprecated` 提示、Resolved 摘要确认页。

### 9.8 Playbook 目录组织（当前 vs 规划）

**当前**：步骤均在 `site.yml` 内按 Ansible `tags` 组织；MySQL 5.7/8.0 差异在 configure 等步骤用 `mysql_profile_major` 分支。**未**使用 `deploy/roles/` 拆分。

**规划**（版本差异增大时）：

```
deploy/playbooks/mysql/standalone/site.yml   # 入口
deploy/roles/mysql/install_8_0/              # 按 playbook_variant include
```

Profile 字段 `playbook_variant`（如 `install_5_7_tgz`）已预留，尚未接线到 `include_tasks`。

### 9.9 介质管理（与版本档案配合）

| 方式 | 适用 | 注意 |
|------|------|------|
| **内网 HTTP 软件库** | MySQL tar.gz、Oracle zip（**当前环境**） | Profile 存 `media_base_url` + 子目录 + `package_filename`；见 §9.9.1 / §9.9.2 |
| 内网 yum / apt 源 | MySQL rpm 等 | Profile 只存 repo 名 + 包名模式 |
| 共享 NFS 目录 | 备用 Oracle 介质 | Profile 存 `media_path` |
| 平台上传 | 非常规版本 | 后期 `DbDeployPackage` 表：checksum、上传人、过期策略 |

同一 Profile 只指向 **一种主安装方式**；换方式则新建 Profile（如 `mysql-5.7.44-tgz` vs `mysql-5.7.44-yum`）。

#### 9.9.1 内网 MySQL 安装包（当前环境）

运维内网已提供 **HTTP 目录**存放 MySQL 二进制 tar.gz，部署平台通过 Version Profile 引用，**不要求用户在部署表单里填写下载地址**。

| 项 | 值 |
|----|-----|
| 介质类型 | tar.gz 二进制包（非 yum rpm） |
| 目录前缀 | `http://10.32.14.211/soft/mysql/tgz/` |
| 当前可用包示例 | `mysql-5.7.44-linux-glibc2.12-x86_64.tar.gz` |
| 完整 URL 示例 | `http://10.32.14.211/soft/mysql/tgz/mysql-5.7.44-linux-glibc2.12-x86_64.tar.gz` |

**Profile 配置示例**（`deploy/profiles/mysql/5.7.44.yml`）：

```yaml
engine: mysql
profile_code: mysql-5.7.44
display_name: MySQL 5.7.44（glibc2.12 tgz）
major_version: "5.7"
minor_version: "44"
status: enabled
supported_os_rules:
  - family: centos
    min_major: 7
supported_arch:
  - x86_64
supported_job_types:
  - mysql_standalone
install_method: tar_http
package_ref: mysql-5.7.44-linux-glibc2.12-x86_64
media_base_url: "http://10.32.14.211/soft/mysql/tgz/"
package_filename: "mysql-5.7.44-linux-glibc2.12-x86_64.tar.gz"
# package_url 可由代码拼接：media_base_url + package_filename
playbook_variant: install_5_7_tgz
default_params:
  cmdb:
    port: 3306
    charset: utf8mb4
  install:
    basedir: "/usr/local/mysql"
  config:
    enable_binlog: true
    enable_gtid: true
    character_set: utf8mb4
    collation: utf8mb4_unicode_ci
    default_authentication_plugin: mysql_native_password
min_memory_gb: 2
```

> 实例路径（`datadir`、`socket` 等）不在 Profile 中写死，由 `finalize_mysql_deploy_params` 按 `cmdb.port` 生成。

**与 Job.params 的关系：**

- 用户在表单里**只选** Profile `mysql-5.7.44`，不填 URL。
- `resolved_params.media` 由 Profile 注入，例如：

```json
{
  "media": {
    "install_method": "tar_http",
    "base_url": "http://10.32.14.211/soft/mysql/tgz/",
    "filename": "mysql-5.7.44-linux-glibc2.12-x86_64.tar.gz",
    "download_url": "http://10.32.14.211/soft/mysql/tgz/mysql-5.7.44-linux-glibc2.12-x86_64.tar.gz"
  }
}
```

**Ansible install 步骤（概要，与 `site.yml` 一致）：**

1. precheck / prepare：目标机对 `media.download_url` 做 HEAD。
2. 若 `package_ref` 目录不存在则 `get_url` + `unarchive` 到 `/usr/local`。
3. 软链 `/usr/local/mysql` → `package_ref` 目录；`chown` 与 `/etc/profile` PATH。
4. configure / initialize / start 等后续 tag 处理实例目录（`/data/mysql/db{port}`）。

**配置与安全建议：**

| 项 | 建议 |
|----|------|
| URL 写死还是可配置 | Profile 写默认值；`settings` / `.env` 可提供 `DEPLOY_MYSQL_MEDIA_BASE_URL` 覆盖前缀，便于换 IP 而不改 Profile |
| 多版本多文件 | 目录下每增加一个 tgz，新增一条 Profile（改 `package_filename` + `minor_version`） |
| 校验 | precheck 目标机 HEAD；glibc 版本比对 `package_glibc_version` |
| min_memory_gb | Profile 声明，**当前 precheck 未强制执行** |
| 日志 | 任务详情展示 `download_url`，不含密码 |
| glibc 要求 | 包名含 `glibc2.12`，precheck 需确认目标 OS glibc ≥ 2.12 |

**说明：** 台账字段 `DatabaseInstance.version` 存部署后探测结果（如 `5.7.44-log`），与 Profile 的 `profile_code`（选型）可并存；历史实例若为 5.7.42，不影响新装选用 5.7.44 介质。

#### 9.9.2 内网 Oracle 安装包（当前环境）

运维内网 **HTTP 软件库**存放 Oracle 安装 zip，按大版本分子目录；部署平台通过 Version Profile 引用，**不要求用户填写下载地址**。

| 项 | 值 |
|----|-----|
| 介质类型 | zip 安装包（runInstaller 用） |
| 根目录前缀 | `http://10.32.14.211/soft/oracle/zip/` |
| 版本子目录 | `19c/`、`21c/`（其下为对应版本安装 zip） |
| 19c 介质 URL | `http://10.32.14.211/soft/oracle/zip/19c/LINUX.X64_193000_db_home.zip` |
| 21c 介质 URL | `http://10.32.14.211/soft/oracle/zip/21c/LINUX.X64_213000_db_home.zip` |
| 19c 安装包 | `LINUX.X64_193000_db_home.zip` |
| 21c 安装包 | `LINUX.X64_213000_db_home.zip` |

Profile 中通过 `package_filename` 维护；新增/更换包时只改 Profile，不改部署表单。

**目录结构（逻辑）：**

```
http://10.32.14.211/soft/oracle/zip/
├── 19c/
│   └── LINUX.X64_193000_db_home.zip
└── 21c/
    └── LINUX.X64_213000_db_home.zip
```

**Profile 配置示例 — Oracle 19c**（`deploy/profiles/oracle/19c.yml`）：

```yaml
engine: oracle
profile_code: oracle-19c
display_name: Oracle Database 19c
major_version: "19"
status: enabled
supported_os:
  - centos7
supported_job_types:
  - oracle_standalone
install_method: zip_http
package_ref: oracle-19c-db-home
media_base_url: "http://10.32.14.211/soft/oracle/zip/"
media_subdir: "19c"
package_filename: "LINUX.X64_193000_db_home.zip"
# download_url: http://10.32.14.211/soft/oracle/zip/19c/LINUX.X64_193000_db_home.zip
playbook_variant: install_19c_zip
default_params:
  cmdb:
    port: 1521
  install:
    oracle_base: "/u01/app/oracle"
    oracle_home: "/u01/app/oracle/product/19c/dbhome_1"
    ora_inventory: "/u01/app/oraInventory"
    datafile_dest: "/u01/oradata"
  config:
    character_set: AL32UTF8
    national_character_set: AL16UTF16
    memory_target_mb: 4096
    processes: 300
min_memory_gb: 8
remark: "介质来自内网 HTTP 软件库 10.32.14.211/oracle/zip/19c"
```

**Profile 配置示例 — Oracle 21c**（`deploy/profiles/oracle/21c.yml`）：

```yaml
engine: oracle
profile_code: oracle-21c
display_name: Oracle Database 21c
major_version: "21"
status: enabled
supported_os:
  - centos7
supported_job_types:
  - oracle_standalone
install_method: zip_http
package_ref: oracle-21c-db-home
media_base_url: "http://10.32.14.211/soft/oracle/zip/"
media_subdir: "21c"
package_filename: "LINUX.X64_213000_db_home.zip"
# download_url: http://10.32.14.211/soft/oracle/zip/21c/LINUX.X64_213000_db_home.zip
playbook_variant: install_21c_zip
default_params:
  cmdb:
    port: 1521
  install:
    oracle_base: "/u01/app/oracle"
    oracle_home: "/u01/app/oracle/product/21c/dbhome_1"
    ora_inventory: "/u01/app/oraInventory"
    datafile_dest: "/u01/oradata"
  config:
    character_set: AL32UTF8
    national_character_set: AL16UTF16
    memory_target_mb: 4096
    processes: 300
min_memory_gb: 8
remark: "介质来自内网 HTTP 软件库 10.32.14.211/oracle/zip/21c"
```

**与 Job.params 的关系：**

- 用户表单只选 `oracle-19c` 或 `oracle-21c`，不填 URL。
- `resolved_params.media` 示例（19c）：

```json
{
  "media": {
    "install_method": "zip_http",
    "base_url": "http://10.32.14.211/soft/oracle/zip/",
    "subdir": "19c",
    "filename": "LINUX.X64_193000_db_home.zip",
    "download_url": "http://10.32.14.211/soft/oracle/zip/19c/LINUX.X64_193000_db_home.zip"
  }
}
```

**URL 拼接规则（代码层）：**

```
download_url = media_base_url.rstrip("/") + "/" + media_subdir + "/" + package_filename
```

MySQL 无 `media_subdir`，为：

```
download_url = media_base_url.rstrip("/") + "/" + package_filename
```

**Ansible install 步骤（概要）：**

1. precheck：目标机 HEAD `download_url`；Oracle 另检查 ORACLE_HOME / 端口（`wait_for`）。
2. 下载 zip 到 staging 目录（如 `/tmp/oracle_deploy_*`）。
3. 解压、`runInstaller` silent、DBCA（MVP Playbook 范围见 §8）。
4. listener、`/etc/oratab`；verify 后 register_cmdb。

**配置建议：**

| 项 | 建议 |
|----|------|
| 环境变量覆盖 | `.env`：`DEPLOY_ORACLE_MEDIA_BASE_URL=http://10.32.14.211/soft/oracle/zip/` |
| 19c / 21c 区分 | 各一条 Profile，`media_subdir` 分别为 `19c`、`21c` |
| RAC | Grid 介质若也在软件库，可另建 `oracle-19c-grid` Profile，指向不同 subdir/文件名 |
| 探测写 CMDB | `DatabaseInstance.version` 写 `v$instance` 探测值，可与 profile_code 并存 |

### 9.10 分阶段落地建议

| 阶段 | 版本能力 |
|------|----------|
| **第一期 MVP** | ✅ MySQL `5.7.44` / `8.0.45` + Oracle `19c`/`21c` Profile |
| **第二期** | `DbDeployVersionProfile` 表 + 后台维护；主从 / MGR 版本校验 |
| **第三期** | 介质库、deprecated 策略、RU 升级 playbook（与「新装」分离） |

**不要第一期就支持「任意版本」**；每增加一个 Profile，需配套：precheck 规则、install role、verify 命令、（可选）CMDB 默认值模板，并在一台测试机跑通。

### 9.11 小结（多版本）

| 问题 | 建议 |
|------|------|
| 用户选的 version 是什么？ | **Version Profile**（可选列表项），不是自由文本 |
| 小版本怎么办？ | 同 major 共用 Playbook，只改 package_ref |
| 大版本怎么办？ | 独立 `install_X_Y` role + 独立 default_params |
| 和 CMDB 怎么同步？ | 部署成功写 `DatabaseInstance.version = detected_version` |
| 从库 / RAC 怎么控版本？ | 预检查：与主库 / 集群 major 一致 |
| 第一期做多少？ | 已上线 4 个 MySQL/Oracle Profile；新 Profile 需配套 precheck/install/verify |

---

## 十、小结

| 问题 | 现状 |
|------|------|
| 怎么设计？ | **DbDeployJob + Executor + Playbook + CMDB** 四层分离（已实现） |
| 单实例怎么实现？ | 9 步流程；MySQL 生产可用；Oracle MVP |
| 参数怎么配？ | Profile YAML + `params` 分段 + `resolved_params` 快照；见第四章 |
| 怎么扩展？ | 新 `job_type` + Executor + Playbook；`mysql_replica` 等未实现 |
| 现有代码怎么用？ | Host + `_build_inventory` + Celery；**不用** BatchTask 部署 |
| 模型要改吗？ | CMDB 基本不变；`DbDeployJob` / `DbDeployJobStep` 已新增 |
| 多版本怎么管？ | `deploy/profiles/*.yml`；`profile_code` 选型，`version` 写探测值 |
| Profile 存哪？ | YAML（第一版）；任务与步骤落库 |

---

## 相关代码路径

| 路径 | 说明 |
|------|------|
| `apps/dbmgr/models.py` | `DbDeployJob`、`DbDeployJobStep`、CMDB 实例/账号 |
| `apps/dbmgr/deploy_services.py` | 创建任务、续跑、force_rebuild、注册台账 |
| `apps/dbmgr/deploy_constants.py` | 步骤定义、路径、端点/server_id/主机锁校验 |
| `apps/dbmgr/deploy_executors/` | 按 `job_type` 的执行器与步骤编排 |
| `apps/dbmgr/deploy_ansible.py` | ansible-playbook 按 tag 执行单步 |
| `apps/dbmgr/deploy_tasks.py` | Celery `run_db_deploy_job` |
| `apps/dbmgr/profile_loader.py` | 加载 Profile、合并 `resolved_params` |
| `deploy/profiles/` | Version Profile YAML（MySQL 5.7.44 / 8.0.45；Oracle 19c / 21c） |
| `deploy/playbooks/` | 按场景划分的 Playbook |
| `apps/common/tasks.py` | `_build_inventory`（部署与 BatchTask 共用） |
| `apps/dbmgr/deploy_os_compat.py` | Profile OS 规则校验 |
| `templates/dbmgr/deploy_job_*.html` | LayUI 部署列表与详情 |
| `templates/dbmgr/mysql_param_template_list.html` | MySQL 运行参数模板维护 |
| `apps/dbmgr/deploy_param_template_services.py` | 参数模板 CRUD / 合并 |
| `docs/db-deploy-mysql-profile-spec.md` | Profile 与 my.cnf 链路 |
| `docs/db-deploy-mysql-install-decision-guide.md` | 现场判断与 force_rebuild |
| `docs/db-deploy-mysql-endpoint-release.md` | 失败任务释放端点 |
| `docs/db-deploy-mysql5.7-step-action.md` | MySQL 单实例逐步说明 |

---

*文档版本：v1.9 · 对照代码校准（实例路径 `/data/mysql/db{port}`、参数模板、post_config semi-sync、dba_admin）· Django 5.2 + LayUI + Celery + Ansible*
