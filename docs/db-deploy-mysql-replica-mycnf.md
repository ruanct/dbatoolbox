# MySQL 从库 my.cnf 启动参数确定方法

> **状态**：实现说明 v1.0  
> **适用范围**：`mysql_replica` 任务 — 从库 `my.cnf` 参数来源、合并顺序与落盘时机  
> **关联文档**：[db-deploy-mysql-replica-guide.md](./db-deploy-mysql-replica-guide.md) §2.5、§5.4、[db-deploy-mysql-profile-spec.md](./db-deploy-mysql-profile-spec.md)、[db-deploy-mysql5.7-step-action.md](./db-deploy-mysql5.7-step-action.md)

---

## 目录

1. [总览](#1-总览)
2. [阶段一：创建任务时的参数合并](#2-阶段一创建任务时的参数合并)
3. [阶段二：configure 前主库现场探测](#3-阶段二configure-前主库现场探测)
4. [阶段三：Ansible 落盘](#4-阶段三ansible-落盘)
5. [参数来源归类表](#5-参数来源归类表)
6. [与单实例部署的差异](#6-与单实例部署的差异)
7. [表单可影响范围](#7-表单可影响范围)
8. [代码映射](#8-代码映射)

---

## 1. 总览

「添加 MySQL 从库」功能中，从库 `my.cnf` **不是**用户在表单中逐行填写的，而是由平台在 Django 侧完成参数合并，生成 `config.cnf_sections` 行列表，再由 Ansible `configure` 步骤写入目标主机。

与单实例共用同一套生成函数 `build_mysql_cnf_sections()`，但从库在写盘前多了一步 **主库现场探测（`repl_master_probe`）**，将复制相关参数与主库运行值对齐。

### 1.1 两阶段流程

```
创建任务（POST）
    ↓
resolve_deploy_params / resolve_mysql_replica_deploy_params
    ↓ Profile default_params + 可选参数模板 + user_params
    ↓ finalize_mysql_deploy_params → build_mysql_cnf_sections（第 1 次）
    ↓
resolved_params 快照入库

执行到 configure 步骤
    ↓
repl_master_probe（从库主机连主库读 @@变量）
    ↓
merge_master_runtime_into_job → apply_mysql_master_runtime
    ↓ build_mysql_cnf_sections（第 2 次，最终版）
    ↓
Ansible [configure] 将 cnf_sections 写入 /data/mysql/db{port}/my.cnf
```

**要点**：任务创建时会生成一版 `cnf_sections` 并存入 `resolved_params`；真正写到磁盘的是 **configure 步骤中、主库探测完成之后**重新生成的那一版。

---

## 2. 阶段一：创建任务时的参数合并

### 2.1 入口

| 环节 | 代码位置 |
|------|----------|
| 创建任务 | `deploy_services.create_mysql_replica_deploy_job()` |
| 参数解析 | `deploy_services.resolve_mysql_replica_deploy_params()` |
| 通用合并 | `profile_loader.resolve_deploy_params()` |

### 2.2 合并顺序（后者覆盖前者）

| 顺序 | 来源 | 作用 |
|------|------|------|
| 1 | **Version Profile** `default_params` | 如 `enable_binlog`、`character_set`、`innodb_buffer_pool_size` 等 |
| 2 | **MySQL 参数模板**（表单可选） | 写入 `config.*` 与 `config.cnf_template_items` |
| 3 | **user_params**（表单提交） | 从库端口、`connect_host`、复制集 `context` 等 |
| 4 | **从库强制项** | `config.enable_binlog=true`、`config.enable_gtid=true` |

参数模板在 `user_params` **之前**合并（`apply_mysql_param_template_to_merged`），因此用户表单中的 `config` 字段可覆盖模板中映射到 `config` 的项。

### 2.3 路径类参数（由从库端口推导）

`finalize_mysql_deploy_params()` 调用 `build_mysql_install_paths(port)`：

| 项 | 规则 |
|----|------|
| 实例根目录 | `/data/mysql/db{从库port}` |
| 参数文件 | `{instance_root}/my.cnf` |
| 数据目录 | `{instance_root}/data` |
| Socket | `{instance_root}/mysql.sock` |
| Binlog | `{instance_root}/binlog/mysql-bin` |
| systemd 服务名 | `mysqld{port}` |
| basedir | 固定 `/usr/local/mysql`（同机多实例共享） |

端口来自表单 `cmdb.port`（用户填写的**从库监听端口**），与主库端口无关。

### 2.4 从库本地生成参数

| 参数 | 确定方式 |
|------|----------|
| `port` | 表单填写的从库端口 |
| `server_id` | `build_mysql_server_id(从库 connect_host, 从库 port)`：`SHA256(host:port)` 映射到 `1..4294967295` |
| `connect_host` | 从库部署主机业务 IP（自动填入，只读） |
| `bind-address` | 从库固定 `0.0.0.0`（单实例默认为 `127.0.0.1`） |

`server_id` 在 `finalize_mysql_deploy_params()` 中生成，并经 `ensure_mysql_server_id_available()` 校验在 CMDB 与进行中任务内唯一。

### 2.5 Profile 默认运行参数

Profile YAML 示例（`deploy/profiles/mysql/5.7.44.yml`）：

```yaml
default_params:
  config:
    enable_binlog: true
    enable_gtid: true
    binlog_format: ROW
    character_set: utf8mb4
    collation: utf8mb4_unicode_ci
    innodb_buffer_pool_size: "1G"
    max_connections: 500
    default_authentication_plugin: mysql_native_password
```

8.0 Profile 的 `collation`、`default_authentication_plugin` 等按 major 不同。

### 2.6 可选 MySQL 参数模板

若表单选择了参数模板（`meta.mysql_param_template_title`），`deploy_param_template_services.apply_mysql_param_template_to_merged()` 会：

1. 将可映射项写入 `config`（见 `PARAM_NAME_TO_CONFIG_KEY`：`max_connections`、`innodb_buffer_pool_size`、`sql_mode`、`binlog_format` 等）
2. 将全部模板项放入 `config.cnf_template_items.mysqld` / `client`，供 `build_mysql_cnf_sections()` 追加到 `my.cnf`

**模板禁止覆盖的保留项**（`MYSQL_PARAM_TEMPLATE_RESERVED_NAMES`）：

`basedir`、`datadir`、`port`、`socket`、`server_id`、`log-error`、`bind-address`、`log_bin`、`tmpdir`、`slow_query_log_file` 等 — 由平台或 Playbook 派生，不允许在模板中维护。

### 2.7 第一次生成 cnf_sections

`build_mysql_cnf_sections(merged)` 将 `config` + `install` 转为 `[mysqld]` / `[client]` 行列表，写入 `config.cnf_sections`。

从库分支（`meta.job_type == mysql_replica`）在此时还会写入平台默认值（**主库现场值尚未探测**）：

| 参数 | 默认值 |
|------|--------|
| `lower_case_table_names` | `1`（或 config 已有值） |
| `binlog_checksum` | `CRC32` |
| `slave_sql_verify_checksum` | `ON` |
| `transaction_write_set_extraction` | `XXHASH64`（仅 8.0） |
| Binlog/GTID 相关 | `server_id`、`log_bin`、`binlog_format`、`gtid_mode=ON` 等（当 `enable_binlog` / `enable_gtid` 为 true） |

此时尚未包含主库真实的 `character_set_server`、`sql_mode` 等现场值；这些在阶段二覆盖。

---

## 3. 阶段二：configure 前主库现场探测

### 3.1 执行时机

`MysqlReplicaExecutor.execute_step()` 在处理 `configure` 步骤时**分两步**：

1. 先执行 Ansible tag `repl_master_probe`
2. 解析输出、合并主库参数后，再执行 tag `configure` 写 `my.cnf`

代码：`apps/dbmgr/deploy_executors/mysql_replica.py`

### 3.2 repl_master_probe 做什么

在**从库主机**上，使用主库 DBA 账号（`credentials.dump_account`）连接 `context.master_host:context.master_port`，读取并校验主库 `@@` 变量。

Playbook：`deploy/playbooks/mysql/replica/site.yml`，tag `repl_master_probe`。

| 读取变量 | 校验规则 |
|----------|----------|
| `VERSION()` | major 须与 Profile `major_version` 一致 |
| `@@log_bin` | 必须为 ON |
| `@@gtid_mode` / `@@enforce_gtid_consistency` | 必须为 ON（第一期仅支持 GTID 主库） |
| `@@lower_case_table_names` | 必须为 `1` |
| `@@binlog_checksum` | 必须为 `CRC32` |
| `@@transaction_write_set_extraction` | 8.0 须为 `XXHASH64`（若变量存在） |
| `@@binlog_format`、`@@log_slave_updates` | 采集，写入从库 |
| `@@character_set_server`、`@@collation_server` | 采集，写入从库 |
| `@@sql_mode` | 采集，写入从库 |
| `@@default_authentication_plugin` | 8.0 采集，写入从库 |
| `@@server_id` | 采集；须与从库计划 `server_id` 不同 |

探测成功时输出一行：

```
MASTER_RUNTIME_JSON={"version":"5.7.44", "gtid_mode":"ON", ...}
```

### 3.3 合并进从库 config

`merge_master_runtime_into_job()` → `apply_mysql_master_runtime()`：

1. 将 `master_runtime` 存入 `resolved_params.context`
2. 用主库现场值**覆盖**从库 `config`：
   - `character_set` ← `character_set_server`
   - `collation` ← `collation_server`
   - `sql_mode`、`binlog_format`、`default_authentication_plugin`
   - `lower_case_table_names`、`binlog_checksum`、`transaction_write_set_extraction` 等（`MYSQL_REPLICA_MASTER_RUNTIME_KEYS`）
   - 根据主库 `log_bin`、`gtid_mode` 重算 `enable_binlog`、`enable_gtid`
3. 校验从库 `server_id` ≠ 主库 `server_id`，冲突则任务失败
4. 再次调用 `build_mysql_cnf_sections()` — **第二次生成**，为最终写入磁盘的版本
5. 更新 `job.resolved_params` 并落库

---

## 4. 阶段三：Ansible 落盘

从库 Playbook import 单实例 `deploy/playbooks/mysql/standalone/site.yml`，复用 `[configure]` 任务：

```yaml
- name: "[configure] 部署 my.cnf"
  tags: [configure]
  copy:
    dest: "{{ mysql_cnf_path }}"
    content: |
      [mysqld]
      {% for line in d.config.cnf_sections.mysqld | default([]) %}
      {{ line }}
      {% endfor %}
      [client]
      {% for line in d.config.cnf_sections.client | default([]) %}
      {{ line }}
      {% endfor %}
```

- **数据源**：Celery 执行时传入的 `deploy` 变量，即 `job.resolved_params`（configure 前已含主库对齐后的 `cnf_sections`）
- **目标路径**：`/data/mysql/db{从库port}/my.cnf`

后续 `initialize` 使用 `--defaults-file={cnf_path}` 初始化；`start` 启动 `mysqld{port}` 服务。**从库路径不包含 `post_config`**，不在此阶段设置 root/DBA 密码。

---

## 5. 参数来源归类表

| 类别 | 参数示例 | 来源 |
|------|----------|------|
| **路径 / 实例** | `basedir`、`datadir`、`socket`、`log-error`、`log_bin` 路径、`tmpdir`、`slow_query_log_file` | 从库端口 → `build_mysql_install_paths()` |
| **从库本地** | `port`、`server_id`、`bind-address=0.0.0.0` | 表单 + 算法；从库专用规则 |
| **与主库对齐** | `lower_case_table_names`、`binlog_checksum`、`binlog_format`、`gtid_mode`、`enforce_gtid_consistency`、`log_slave_updates`、`character-set-server`、`collation-server`、`sql_mode`、`default_authentication_plugin`、8.0 `transaction_write_set_extraction` | `repl_master_probe` 读主库 → `apply_mysql_master_runtime()` |
| **Profile 默认** | `innodb_buffer_pool_size`、`max_connections` 等 | Profile `default_params`（未被主库覆盖项保留） |
| **参数模板** | 模板中非保留项 | 表单可选；`cnf_template_items` 追加写入 |
| **平台固定（从库）** | `slave_sql_verify_checksum=ON`、`symbolic-links=0`、`explicit_defaults_for_timestamp=1` | `build_mysql_cnf_sections()` 从库分支 |
| **不在 configure 写入** | `read_only`、`super_read_only` | `repl_readonly` 步骤 `SET GLOBAL` 动态设置 |

### 5.1 优先级小结

对同一逻辑参数，有效优先级为：

```
主库现场值（repl_master_probe）
  > user_params.config
  > 参数模板映射到 config 的项
  > Profile default_params
  > build_mysql_cnf_sections 内从库/platform 默认值
```

路径类、`port`、`server_id` 由 `finalize_mysql_deploy_params` 派生，不被模板覆盖。

---

## 6. 与单实例部署的差异

| 项 | 单实例 `mysql_standalone` | 从库 `mysql_replica` |
|----|---------------------------|----------------------|
| `bind-address` | configure 写 `127.0.0.1`；`post_config` 改为 `0.0.0.0` | configure 直接写 `0.0.0.0` |
| Binlog / GTID | 表单开关 | 创建时强制 `true`；configure 前再按主库校验 |
| 字符集 / SQL 模式等 | 以 Profile / 模板为主 | **以主库 `@@` 现场值为准**覆盖 |
| `post_config` | 设置 root / DBA 密码、半同步等 | **跳过**（mysqldump 导入后主库用户表一致） |
| `repl_setup` 本地账号 | 单实例 post_config 创建的 root | **主库 CMDB `root@localhost` 密码** + 从库 socket（见 [replica 指南](./db-deploy-mysql-replica-guide.md) §7.1） |
| 主库探测 | 无 | configure 前必须 `repl_master_probe` |
| `slave_sql_verify_checksum` | 无 | 从库必须 `ON` |
| Job 步骤 | 含 `post_config` | 无 `post_config`；含 `repl_bootstrap` 等 |

---

## 7. 表单可影响范围

「添加 MySQL 从库」页面**不提供 my.cnf 逐行编辑**，用户只能通过以下字段间接影响：

| 表单字段 | 影响的 my.cnf 相关项 |
|----------|----------------------|
| 从库端口 | `port`、全部路径、`server_id`、`mysqld{port}.service` |
| 版本 Profile | major、默认 `innodb_buffer_pool_size`、`max_connections`、认证插件默认值等 |
| 参数模板（可选） | 模板中非保留的运行参数 |
| 复制集 / 主库 | 决定 `repl_master_probe` 连接地址与 major 校验；不直接写字段 |

**不能在从库表单设置**：root 密码、DBA 账号、Binlog/GTID 开关（平台第一期强制 GTID 复制）。  
**`repl_setup` 用 root 密码**：不来自表单，resolve 时从主库 CMDB `user_adm / root@localhost` 解析。

---

## 8. 代码映射

| 职责 | 路径 |
|------|------|
| 路径、`server_id`、第一次 `cnf_sections` | `apps/dbmgr/deploy_constants.py` — `build_mysql_install_paths()`、`finalize_mysql_deploy_params()`、`build_mysql_cnf_sections()` |
| 主库现场合并 | `apps/dbmgr/deploy_constants.py` — `apply_mysql_master_runtime()` |
| Profile + 模板合并 | `apps/dbmgr/profile_loader.py` — `resolve_deploy_params()` |
| 参数模板注入 | `apps/dbmgr/deploy_param_template_services.py` — `apply_mysql_param_template_to_merged()` |
| 从库任务创建 / 解析 | `apps/dbmgr/deploy_services.py` — `resolve_mysql_replica_deploy_params()`、`merge_master_runtime_into_job()` |
| configure 两步编排 | `apps/dbmgr/deploy_executors/mysql_replica.py` |
| 主库探测 Playbook | `deploy/playbooks/mysql/replica/site.yml` — tag `repl_master_probe` |
| my.cnf 写盘 | `deploy/playbooks/mysql/standalone/site.yml` — tag `configure` |

### 8.1 设计文档交叉引用

- 主从参数白名单： [db-deploy-mysql-replica-guide.md](./db-deploy-mysql-replica-guide.md) §2.5
- 从库 configure / 路径约定：同上 §5.4
- Profile 与 `cnf_sections` 关系： [db-deploy-mysql-profile-spec.md](./db-deploy-mysql-profile-spec.md)

---

## 附录：方法一句话总结

> **Profile + 可选参数模板 + 从库端口派生路径/server_id**  
> **+ configure 前连主库探测，把复制必须一致的参数对齐主库现场值**  
> **→ `build_mysql_cnf_sections()` 生成行列表 → Ansible configure 写入 `/data/mysql/db{port}/my.cnf`**
