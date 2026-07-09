# MySQL 从库部署技术指南

> **状态**：设计参考 v0.7  
> **适用范围**：dbatoolbox `mysql_replica` 任务类型 — 参数校验、Playbook 编排、CMDB 登记  
> **关联文档**：[db-deploy-design.md](./db-deploy-design.md)、[db-deploy-mysql-profile-spec.md](./db-deploy-mysql-profile-spec.md)、[db-deploy-mysql5.7-step-action.md](./db-deploy-mysql5.7-step-action.md)、[db-deploy-mysql-replica-mycnf.md](./db-deploy-mysql-replica-mycnf.md)、[MySQL8.0_vs_5.7_复制差异.md](./MySQL8.0_vs_5.7_复制差异.md)

---

## 目录

1. [背景与目标](#1-背景与目标)
2. [产品约定](#2-产品约定)（§2.1 端口 · §2.2 全量账号 · §2.3 复制连接地址 · §2.4 GTID · §2.5 参数白名单）
3. [流程总览](#3-流程总览)
4. [阶段一：创建任务（静态校验）](#4-阶段一创建任务静态校验)
5. [阶段二：全量前准备](#5-阶段二全量前准备)
6. [阶段三：全量同步（bootstrap 分支）](#6-阶段三全量同步bootstrap-分支)
7. [阶段四：建立复制与验收](#7-阶段四建立复制与验收)
8. [技术要点速查](#8-技术要点速查)
9. [Job 步骤与代码映射](#9-job-步骤与代码映射)
10. [现有能力与待实现项](#10-现有能力与待实现项)
11. [第一期验收清单](#11-第一期验收清单)

---

## 1. 背景与目标

dbatoolbox 已完成 MySQL 单实例（`mysql_standalone`）部署。扩展从库时，在复用安装、`my.cnf`、systemd、root/DBA 账号等能力之上，增加：

- 复制集 / 主库关联与一致性校验
- 从库主机软件、目录、端口冲突检查
- 从库运行参数与 **主库现场变量** 对齐
- 全量同步（`mysqldump` / XtraBackup / Clone）与复制建链
- 只读管控、幂等重试、CMDB 登记

**与单实例共用的约定：**

| 项 | 约定 |
|----|------|
| 二进制目录 | `/usr/local/mysql`（同机多实例共享） |
| 实例目录 | `/data/mysql/db{从库port}`（如 `/data/mysql/db3306`、`/data/mysql/db3307`） |
| `server_id` | 从库 `connect_host` + **从库 port** 生成，见 `build_mysql_server_id()` |
| Profile 默认 | `enable_binlog=true`、`enable_gtid=true`（从库以主库现场为准覆盖） |

---

## 2. 产品约定

以下为第一期 **明确的产品规则**，实现与表单需一致：

| 必须有复制集 | 复制集已创建且已登记主库（`primary_instance`）；**主库由复制集自动带出**，不可手选其它实例 |
| 主库必须开 Binlog | precheck 读 `@@log_bin=ON` |
| **第一期仅 GTID ON** | 主库 `gtid_mode` 必须为 `ON`；`OFF` 在 precheck 拒绝（不做 `repl_mode=file`） |
| major 必须一致 | 主库 CMDB `version` 与 Profile `major_version` 一致；precheck 再 `SELECT VERSION()` |
| **从库端口用户自定义** | 表单填写从库监听端口（1024～65535）；驱动路径、`server_id`、systemd 服务名 |
| **复制连接地址** | `master_host` / `master_port` 按 §2.3 解析（非简单等于 CMDB `connect_host`） |
| 端点唯一 | 从库 `connect_host` + `port` + `db_name` 不得与已有实例冲突；不得与主库 **完全相同的连接端点** |
| 同机多实例 | 若从库与主库在同一 `Host` 上，**从库端口必须 ≠ 主库端口**；复制与 precheck 用 `127.0.0.1`（§2.3） |
| 复制账号 | 使用主库已有 `user_repl`，任务中选定；`grant_host` 须与连接路径匹配（§5.2） |
| 全量账号（mysqldump） | 主库默认 `user_dba`（`is_default=true`）；dump 前必须 precheck 连通（§2.2） |
| **从库不做 post_config** | mysqldump 路径：`initialize → start（空密码）→ import`；运维账号与主库一致 |
| **从库 CMDB 账号** | `register_cmdb` 时从主库实例 **复制继承** `DatabaseAccount`，不单独创建从库 DBA |
| 只读时机 | 全量完成且 `START SLAVE` 成功后再开启；`configure` 不写 `super_read_only=1` |
| 主从参数白名单 | 含 `lower_case_table_names` 等（§2.5）；precheck 读主库，从库 `configure` 对齐 |
| 残留数据 | 从库实例目录有文件时失败；人工清理（第二期可加 `force_rebuild`） |

### 2.1 主库端口与从库端口

二者职责不同，实现与文档中须始终区分：

| 概念 | 来源 | 用途 |
|------|------|------|
| **主库端口** `master_port` | §2.3 解析结果 | `mysqldump -P`、`CHANGE MASTER` 的 `MASTER_PORT` / `SOURCE_PORT` |
| **从库端口** `slave_port` | 用户表单 `cmdb.port` | 从库 `my.cnf` 的 `port`、`/data/mysql/db{port}` 路径、`mysqld{port}` 服务、`server_id` 计算 |

**典型组合：**

| 部署方式 | 主库端口 | 从库端口 | 是否允许 |
|----------|----------|----------|----------|
| 异机，均用 3306 | 3306 | 3306 | ✅ 常见 |
| 异机，从库用 3307 | 3306 | 3307 | ✅ |
| 同机多实例 | 3306 | 3307（或其它 ≠ 主库的端口） | ✅ |
| 同机同端口 | 3306 | 3306 | ❌ 端口冲突 |

> MySQL 复制 **不要求** 从库与主库使用相同端口号；平台允许从库端口自定义，以便支持异机同端口、异机不同端口及同机多实例等场景。

### 2.2 全量同步账号（mysqldump）

`repl_bootstrap` 在 **从库主机** 上执行 `mysqldump`，通过 `-h<master_host> -P<master_port>` 连接主库拉取数据（`master_host` / `master_port` 见 §2.3）；导入在从库本地完成。

**默认账号规则（第一期）：**

| 项 | 约定 |
|----|------|
| 账号归属 | **主库实例** CMDB 中的账号 |
| 默认选取 | 主库 `is_default=true` 且 `account_type=user_dba` |
| 解析时机 | `resolve_deploy_params()` 根据 `master_instance_id` 自动解析 |
| 可选覆盖 | `credentials.dump_account_id` 指定主库其它 `user_dba` |

**与复制 / 导入 / 本地运维分工：**

| 用途 | 账号 | 连接目标 |
|------|------|----------|
| `mysqldump` 拉取 | 主库默认 `user_dba` | `master_host:master_port`（§2.3） |
| 复制 IO 线程 | 主库 `user_repl` | 同左；同机时 `master_host=127.0.0.1` |
| 导入 dump | 从库本地 root（**空密码**，`initialize-insecure`） | 从库 socket / `{slave_port}` |
| **`repl_setup` 建立复制** | 从库本地 **root**（密码来自主库 CMDB） | 从库 socket / `{slave_port}` |

**`repl_setup` 本地 root 账号规则：**

| 项 | 约定 |
|----|------|
| 账号归属 | **主库实例** CMDB 台账 |
| 台账条件 | `account_type=user_adm`、`account_name=root`、`grant_host=localhost` |
| 解析时机 | `resolve_mysql_replica_deploy_params()` 写入 `credentials.root_account`；执行 `repl_setup` 前 `ensure_mysql_replica_root_credentials()` 补全（兼容历史任务） |
| 运行时密码 | `mysqldump --all-databases` 导入后，从库 `root@localhost` 与主库一致，故使用主库台账密码连接从库 socket |
| 表单 | **不要求**用户填写 `credentials.root_password` |

**DBA 账号 precheck（`mysqldump` 前必须执行）：**

在 **从库主机** 实测（与 §5.2 一致）：

```bash
mysql -h<master_host> -P<master_port> -u<dba> -p -e "SELECT 1"
```

| 场景 | `master_host` 用于测试 | 失败提示要点 |
|------|------------------------|--------------|
| 异机 | §2.3 解析的 IP | 检查主库 DBA 的 `grant_host` 是否包含 **从库主机业务 IP** |
| 同机 | `127.0.0.1` | 检查主库 DBA 的 `grant_host` 是否允许 `127.0.0.1` / `localhost` |

- 未通过 → 任务失败，**不得**执行 `mysqldump`
- 禁止使用 `user_repl` 做 dump；禁止从库 root 连主库拉取（第一期）

### 2.3 复制连接地址（`master_host` / `master_port`）

CMDB 主库 `connect_host` 可能是 VIP、域名或本机 IP；复制、mysqldump、repl/DBA precheck 使用解析后的 **`context.master_host` + `context.master_port`**，逻辑如下。

**判定同机：** `target_host_id` == 主库 `DatabaseInstanceHost.host_id`（`is_primary=true`）→ `same_host_as_master=true`。

**解析规则：**

| 条件 | `master_host` | `master_port` |
|------|---------------|---------------|
| **同机**（`same_host_as_master=true`） | `127.0.0.1` | 主库 `DatabaseInstanceHost.listener_port`；为空则 `DatabaseInstance.port`（CMDB `connect_port`） |
| 异机，且 CMDB `connect_host` **等于** 主库部署主机业务 IP | 该业务 IP | `DatabaseInstance.port` |
| 异机，且 CMDB `connect_host` 为 **域名或 VIP** | 主库 `deploy_hosts`（`is_primary=true`）的 `listener_host`；为空则取该 `host_id` 的 **第一个业务 IP**（`HostIP`，与 `probe_services.resolve_deploy_host_endpoint` 一致） | `listener_port`；为空则 `DatabaseInstance.port` |

**用途：**

- repl / DBA **precheck**、**mysqldump**、**`CHANGE MASTER`** 均使用上述 `master_host` / `master_port`
- 同机时 **`CHANGE MASTER` 的 `MASTER_HOST` / `SOURCE_HOST` 固定为 `127.0.0.1`**（与 repl precheck 一致）
- 保留 `context.master_connect_host` 记录 CMDB 原始 `connect_host`（展示/VIP 入口，不作为复制连接触发地址）

### 2.4 GTID（第一期）

| 规则 | 说明 |
|------|------|
| 第一期仅支持 | 主库 `gtid_mode=ON` 且 `enforce_gtid_consistency=ON` |
| 拒绝 | 主库 `gtid_mode=OFF` → precheck 失败，提示「第一期不支持非 GTID 主库」 |
| 从库 | `configure` 与主库一致；`CHANGE MASTER` 使用 `MASTER_AUTO_POSITION=1` / `SOURCE_AUTO_POSITION=1` |
| 不做 | `repl_mode=file`、binlog file+pos 自动搭建（第二期） |

`mysqldump` 使用 `--set-gtid-purged=ON`，主库无 GTID 时会失败，故必须与 §2.4 联动的 precheck 一并拦截。

### 2.5 主从参数白名单

precheck 从主库读取下列变量，从库 `my.cnf` **与主库保持一致**；若主库现场值与下表「平台要求」冲突，precheck **失败**（避免主库本身不合规仍建从库）。

| 参数 | 平台要求 / 说明 |
|------|-----------------|
| `lower_case_table_names` | **`1`**（必须小写表名） |
| `transaction_write_set_extraction` | **`XXHASH64`**（8.0；5.7 按主库现场，若存在则对齐） |
| `binlog_checksum` | **`CRC32`** |
| `slave_sql_verify_checksum` | **`ON`**（从库 `my.cnf` 必须写入；5.7/8.0 参数名相同） |
| `gtid_mode` / `enforce_gtid_consistency` / `log_slave_updates` | 与主库一致；第一期主库须 GTID ON |
| `log_bin` / `binlog_format` | 主库 `log_bin=ON`，通常 `ROW` |
| `character_set_server` / `collation_server` | 与主库一致 |
| `default_authentication_plugin` | 8.0 与主库一致 |
| `sql_mode` | 与主库一致 |

> 实现时 5.7 无 `transaction_write_set_extraction` 可跳过该项；8.0 必须校验。

---

## 3. 流程总览

```
┌─────────────────────────────────────────────────────────────────┐
│ 阶段一：创建任务（Django services 静态校验）                      │
│  复制集（自动带出主库）+ Profile + 从库主机 + 从库端口 + repl 账号 │
└────────────────────────────┬────────────────────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ 阶段二：全量前准备（Ansible precheck ~ configure）                │
│  2.1 主库在线 / GTID ON / binlog / server_id / 参数白名单              │
│  2.2 repl·DBA 连通（master_host 按 §2.3）/ 从库端口·目录·软件        │
│  → prepare → install? → configure → initialize → start（**无 post_config**） │
└────────────────────────────┬────────────────────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ 阶段三：全量同步 repl_bootstrap（bootstrap_method 三分支）         │
│  mysqldump │ xtrabackup │ clone                                  │
└────────────────────────────┬────────────────────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ 阶段四：repl_setup → repl_readonly → repl_verify → register_cmdb  │
└─────────────────────────────────────────────────────────────────┘
```

**第一期 MVP：** 阶段三仅实现 `mysqldump`；XtraBackup / Clone 为第二期。

---

## 4. 阶段一：创建任务（静态校验）

在 `deploy_services._validate_create_body()` / `resolve_deploy_params()` 中完成。

### 4.1 表单必填

| 字段 | 说明 |
|------|------|
| `job_type` | `mysql_replica` |
| `version_profile_code` | major 与主库一致 |
| `target_host_id` | 从库部署主机 |
| `context.replication_cluster_id` | 已有复制集（**选定后自动带出主库**，见 §4.2） |
| `context.master_instance_id` | **只读**：等于 `replication_cluster.primary_instance_id`，不可手改 |
| `context.bootstrap_method` | `mysqldump`（第一期）/ `xtrabackup` / `clone` |
| `credentials.repl_account_id` | 主库 CMDB 中 `account_type=user_repl` 的账号（复制用，必选） |
| `credentials.dump_account_id` | 全量用账号（可选）；**未指定时**自动取主库 `is_default=true` 的 `user_dba` 账号 |
| `cmdb.instance_name` / `connect_host` | 从库台账 |
| `cmdb.port` | **从库监听端口**（用户填写，默认 3306） |

**第一期从库任务不要求** 表单填写 `credentials.root_password` / `admin_account`（无 `post_config`；运行时账号自主库继承）。  
**`repl_setup` 所需从库 root 密码** 在 resolve 时从主库 CMDB 的 `user_adm / root@localhost` 自动解析为 `credentials.root_account`（§2.2）。

### 4.2 静态校验规则

1. 复制集 `engine=mysql`，`replication_type=mysql_replication`
2. 复制集 `primary_instance_id` 非空；**强制** `context.master_instance_id = replication_cluster.primary_instance_id`（表单不可选其它实例）
3. 主库 `version` 非空；解析 major 与 Profile `major_version` 一致
4. 主库 CMDB 中至少存在一个 `user_repl`；所选 repl 账号挂在主库实例上
5. 主库存在 `user_dba` 且 `is_default=true`（§2.2）
6. 主库 CMDB 存在 `user_adm` / `root@localhost` 台账且密码非空（§2.2，`repl_setup` 使用）
7. 若指定 `dump_account_id`，须为主库实例上的 `user_dba`
8. 从库 `port` 合法（1024～65535）
9. 从库 `connect_host` + `port` + `db_name` 不与 CMDB 已有实例冲突
10. 从库 `connect_host` + `port` ≠ 主库 `connect_host` + `port`
11. **同机**：`target_host_id` == 主库部署 `host_id` 时，从库 `port` ≠ 主库 `port`
12. 从库 `instance_name` 不重名
13. `bootstrap_method=clone` 时：主库 major ≥ 8.0（且建议 ≥ 8.0.17）

> 主库 `gtid_mode` 在 **precheck** 校验（§2.4），创建任务阶段可读 CMDB 但不可替代现场探测。

### 4.3 resolve 时自动填充 context

`profile_loader.resolve_deploy_params()` 应从主库实例合并：

```json
{
  "context": {
    "replication_cluster_id": 12,
    "master_instance_id": 5,
    "master_connect_host": "mysql-vip.example.com",
    "master_host": "10.32.14.210",
    "master_port": 3306,
    "same_host_as_master": false,
    "bootstrap_method": "mysqldump",
    "force_rebuild": false
  },
  "cmdb": {
    "topology": "replication",
    "role": "slave",
    "connect_host": "10.32.14.211",
    "port": 3307
  },
  "credentials": {
    "repl_account": { "account_name", "account_pswd", "grant_host" },
    "dump_account": {
      "account_name": "dba_admin",
      "account_pswd": "***",
      "account_type": "user_dba",
      "is_default": true,
      "source_instance_id": 5
    },
    "root_account": {
      "account_name": "root",
      "account_pswd": "***",
      "grant_host": "localhost",
      "account_type": "user_adm",
      "source_instance_id": 5,
      "source_account_id": 12
    }
  }
}
```

说明：

- `master_instance_id` 由复制集 `primary_instance_id` 自动填入
- `master_host` / `master_port` 按 §2.3 解析；`master_connect_host` 保留 CMDB 原值
- `dump_account` 由主库 `user_dba` + `is_default=true` 解析；密码进 `resolved_params`，日志脱敏
- `root_account` 由主库 `user_adm` / `root@localhost` 解析；供 `repl_setup` 连接从库 socket，**非**用户表单输入
- `same_host_as_master` 由 `target_host_id` 与主库部署主机比对；为 `true` 时 `master_host=127.0.0.1`
- precheck 后主库现场变量写入 `context.master_runtime`，覆盖从库 `config`（§2.5、§5.4）

---

## 5. 阶段二：全量前准备

对应 Playbook tag：`precheck` → `prepare` → `install`（条件）→ `configure` → `initialize` → `start` → `post_config`。

对应 Playbook tag：`precheck` → `prepare` → `install`（条件）→ `configure` → `initialize` → `start`。  
**mysqldump 路径不包含 `post_config`**（见 §2.2、§5.4.5）。

### 5.1 复制集与主库校验（precheck · 主库侧）

从 **从库主机** 连 `master_host:master_port`（§2.3）探测。

| 顺序 | 校验项 | 失败处理 |
|------|--------|----------|
| 1 | `{master_host}:{master_port}` 可达 | 主库不可达 |
| 2 | `SELECT VERSION()` major = Profile major | 版本不一致 |
| 3 | `@@log_bin = ON` | 主库未开 Binlog |
| 4 | `@@gtid_mode = ON` 且 `@@enforce_gtid_consistency = ON` | **第一期拒绝**（§2.4） |
| 5 | §2.5 参数白名单（含 `lower_case_table_names=1` 等） | 主库参数不合规 |
| 6 | 主库 `@@server_id`；从库计划 `server_id` 唯一 | server_id 冲突 |
| 7 | 读取 §5.4.2 其余变量 | 写入 `context.master_runtime` / `config` |

### 5.2 复制账号与 DBA 账号 precheck

**须在 `mysqldump` 之前完成**（`CHANGE MASTER` 使用从库本地 root，见 §7.1）。均在从库主机执行，目标地址为 §2.3 的 `master_host:master_port`。

#### 5.2.1 复制账号（`user_repl`）

| 场景 | 测试命令 | 失败提示 |
|------|----------|----------|
| **异机** | `mysql -h<master_host> -P<master_port> -u<repl> -p -e "SELECT 1"` | `repl` 账号在从库主机无法连接主库；检查 `grant_host` 是否包含 **从库主机业务 IP** |
| **同机** | `mysql -h127.0.0.1 -P<master_port> -u<repl> -p -e "SELECT 1"` | `repl` 账号 `grant_host` 错误，需允许 `127.0.0.1` / `localhost` |

- 权限须含 `REPLICATION SLAVE`；建议含 `REPLICATION CLIENT`
- 同机时 **`CHANGE MASTER` 的 `MASTER_HOST` / `SOURCE_HOST` 使用 `127.0.0.1`**（与测试一致）

#### 5.2.2 全量账号（主库默认 `user_dba`）

| 场景 | 测试命令 | 失败提示 |
|------|----------|----------|
| **异机** | `mysql -h<master_host> -P<master_port> -u<dba> -p -e "SELECT 1"` | 检查主库 DBA `grant_host` 是否包含 **从库主机 IP** |
| **同机** | `mysql -h127.0.0.1 -P<master_port> -u<dba> -p -e "SELECT 1"` | 检查主库 DBA `grant_host` 是否匹配 `127.0.0.1` |

- 任一项失败 → **终止任务**，不执行 `mysqldump`
- MySQL 8.0：确认 DBA / repl 认证插件与从库 `mysql` 客户端兼容

**第一期不自动创建 repl / DBA**；缺失则在创建任务阶段拒绝。

### 5.3 从库主机：软件、端口、目录（precheck + install）

以下路径与检测均使用 **从库端口** `{slave_port}`（用户表单 `cmdb.port`）。示例：主库 `3306`、从库 `3307` 时，实例根目录为 `/data/mysql/db3307`。

#### 5.3.1 端口与运行中实例

检查从库主机 **`{slave_port}`** 是否已有 MySQL 监听：

- **若已监听** → 失败，消息示例：  
  `从库在端口 3307 上运行有实例，任务失败！请先停止并删除实例。`

- **同机额外校验**（`same_host_as_master=true`）：若 `{slave_port}` = `{master_port}` → 失败：  
  `主从在同一主机上，从库端口不能与主库端口相同。`

与单实例 precheck 端口探测一致（`ss` + `systemctl` 归属判断；对象为从库端口）。

#### 5.3.2 数据目录残留

检查 `/data/mysql/db{slave_port}/data`：

- 目录存在且 **目录内有文件** → 失败，消息示例：  
  `从库主机在目录 /data/mysql/db3307/data 上存在实例文件，请先删除实例文件。`  
  提示 DBA 在主机执行：`rm -rf /data/mysql/db3307/*`（或按需清理整个实例根目录）

- 第二期：任务参数 `context.force_rebuild=true` 时，可由 Ansible 在确认后自动清理

建议同时检查 `/data/mysql/db{slave_port}/my.cnf`、`binlog/` 等残留，避免半拉实例。  
**注意：** 同机部署时，切勿清理主库目录 `/data/mysql/db{master_port}/`。

#### 5.3.3 MySQL 软件（`/usr/local/mysql/bin/mysqld`）

| 情况 | 动作 |
|------|------|
| **存在** `mysqld` | 执行 `mysqld --version`，解析 major；与主库 major **不一致** → 失败：  
  `从库主机已安装 MySQL 软件，与主库 major 版本不一致，请先在从库主机 <hostname> 上处理 MySQL 软件。`  
  （注意：同机若有其它端口实例依赖该 basedir，不可盲目删除） |
| **不存在** | 按 Profile `media.download_url` 下载 tgz/xz → 解压到 `/usr/local/` → 软链 `/usr/local/mysql` → `chown mysql:mysql` |

#### 5.3.4 目录准备（prepare）

创建并授权（属主 `mysql:mysql`，权限 `0750`）：

- `/data/mysql/db{slave_port}`
- `/data/mysql/db{slave_port}/data`
- `/data/mysql/db{slave_port}/binlog`

并确保系统用户 `mysql` / 组 `mysql` 存在（复用单实例 `prepare`）。

### 5.4 从库运行参数（configure）

在 **用户选择的全量方案执行之前**，生成 `/data/mysql/db{slave_port}/my.cnf`。

示例：从库 IP `10.32.14.211`、**从库端口** `3307`（主库端口可为 `3306`）：

#### 5.4.1 路径（与 `build_mysql_install_paths(slave_port)` 一致）

| 项 | 路径（示例 slave_port=3307） |
|----|------------------------------|
| 实例目录 | `/data/mysql/db3307` |
| 参数文件 | `/data/mysql/db3307/my.cnf` |
| 数据目录 | `/data/mysql/db3307/data` |
| 临时目录 | `/data/mysql/db3307/tmp` |
| Socket | `/data/mysql/db3307/mysql.sock` |
| 错误日志 | `/data/mysql/db3307/mysql_err.log` |
| 慢日志 | `/data/mysql/db3307/slow_query.log` |
| Binlog | `/data/mysql/db3307/binlog/mysql-bin` |
| systemd | `mysqld3307.service` |

#### 5.4.2 必须与主库一致（precheck 读主库 → 写入从库 `my.cnf`）

> **实现说明**（参数合并顺序、主库探测时机、代码映射）：见 [db-deploy-mysql-replica-mycnf.md](./db-deploy-mysql-replica-mycnf.md)。

完整白名单见 **§2.5**。`configure` 阶段至少写入：

| 参数 | 说明 |
|------|------|
| `lower_case_table_names` | `1` |
| `transaction_write_set_extraction` | `XXHASH64`（8.0） |
| `binlog_checksum` | `CRC32` |
| `slave_sql_verify_checksum` | `ON` |
| `log_bin` / `binlog_format` | 与主库一致，通常 `ON` + `ROW` |
| `gtid_mode` / `enforce_gtid_consistency` / `log_slave_updates` | 与主库一致（第一期主库须 GTID ON） |
| `character_set_server` / `collation_server` | 与主库一致 |
| `default_authentication_plugin` | 8.0 与主库一致 |
| `sql_mode` | 与主库一致 |

> 从库 `port` = `{slave_port}`，不覆盖为主库端口。

#### 5.4.3 从库本地生成

| 参数 | 规则 |
|------|------|
| `port` | 用户表单 `{slave_port}` |
| `server_id` | `build_mysql_server_id(从库connect_host, slave_port)`：`SHA256(host:port)` 映射到 `1..4294967295` |
| `innodb_buffer_pool_size` | 按从库主机内存，**不强制**与主库相同 |
| `max_connections` | Profile 默认或表单覆盖 |

#### 5.4.4 只读（写入时机）

| 阶段 | `read_only` / `super_read_only` |
|------|-----------------------------------|
| `configure` 写 my.cnf | **不写**或显式 `=0` |
| `repl_setup` 成功后 `repl_readonly` | `read_only=1`；MySQL 8.0/8.4 再 `super_read_only=1` |

#### 5.4.5 initialize / start（mysqldump 路径，无 post_config）

全量导入前仅启动 **空实例**（`--initialize-insecure`，root 无密码）：

```bash
mysqld --defaults-file=/data/mysql/db{slave_port}/my.cnf --initialize-insecure --user=mysql
systemctl start mysqld{slave_port}
# 不执行 post_config：不设从库专属 root/DBA
```

- 导入时使用 **socket + root 空密码**（或 `--initialize-insecure` 初始状态）
- `--all-databases` 导入后，从库 **mysql 系统库与主库一致**（含用户表）
- 从库 **不创建** repl 账号；repl 仅在主库使用
- 从库运维账号在 **`register_cmdb` 从主库 CMDB 复制**（§7.4），与实例实况一致

---

## 6. 阶段三：全量同步（bootstrap 分支）

统一 step_code：`repl_bootstrap`，由 `context.bootstrap_method` 分支。

### 6.1 公共前置

- `configure` 已完成，只读未生效
- 从库 mysqld 已 `initialize` 且 **已 start**（root 空密码）
- §5.2 DBA / repl precheck 已通过
- datadir 为空库（仅有 initialize 系统表）

### 6.2 方案 A：`mysqldump`（第一期 MVP）

**执行位置：** 从库主机执行；拉取连 `master_host:master_port` + 主库 `user_dba`；导入连从库 socket（root 空密码）。

**流程：**

```
repl_bootstrap:
  # 前置：§5.2 DBA precheck 已通过
  mysqldump -h<master_host> -P<master_port> \
    -u<dump_account.account_name> -p'...' \
    --single-transaction \
    --set-gtid-purged=ON \
    --master-data=2 \
    --all-databases \
    -r /tmp/mysql_replica_<job_id>.sql
  # 同一 mysql 会话末尾追加本地 DBA 授权 SQL（补 dba_admin@localhost 等，可选）
  cat /tmp/mysql_replica_<job_id>.sql /tmp/dbatoolbox_replica_local_grant_<job_id>.sql \
    | mysql -S /data/mysql/db{slave_port}/mysql.sock -uroot
  校验 dba_admin socket 连通（bootstrap 内自检）
  删除临时文件
```

**要点：**

- **不在此前/此后执行 `post_config`**；导入后从库账号表与主库一致（含 `root@localhost` 密码）
- `repl_setup` **不**使用 `dba_admin` 连从库，而使用 §2.2 的 `root_account`（主库台账密码 + 从库 socket）
- `--set-gtid-purged=ON` 要求主库 GTID ON（§2.4）
- 密码通过 `defaults-extra-file` 传递，避免命令行泄露
- 导入失败 → 清理 `/data/mysql/db{slave_port}/*` 或 `force_rebuild` 后重试

### 6.3 方案 B：`xtrabackup`（第二期）

**与 mysqldump 差异：** 可 **跳过 initialize**；恢复后 datadir 已有数据。

**流程：**

```
repl_bootstrap:
  停从库 mysqld{slave_port}（若已启动；勿停主库 mysqld{master_port}）
  清空 /data/mysql/db{slave_port}/datadir
  xtrabackup --backup --stream=xbstream -h<master_host> -P<master_port> ... | xbstream -x -C /tmp/xb_restore
  xtrabackup --prepare --target-dir=/tmp/xb_restore
  xtrabackup --copy-back --target-dir=/tmp/xb_restore
  chown -R mysql:mysql /data/mysql/db{slave_port}/data
  systemctl start mysqld{slave_port}
  post_config（若备份未带目标密码策略）
```

**参数：**

```json
"context": {
  "bootstrap_method": "xtrabackup",
  "xtrabackup_binary": "/usr/bin/xtrabackup"
}
```

precheck 检测从库是否已安装匹配 major 的 xtrabackup。连接主库备份时，账号规则同 §2.2（默认主库 `user_dba`）。

### 6.4 方案 C：`mysql clone`（第二期，8.0.17+）

**流程：**

```
repl_bootstrap:
  从库已 configure + initialize + start（空实例或 Clone 要求的状态）
  主库：clone 插件、repl 账号需 BACKUP_ADMIN（除 REPLICATION SLAVE 外）
  从库：CLONE INSTANCE FROM 'repl'@'<master_host>':<master_port> IDENTIFIED BY '...';
  （自动覆盖从库 datadir，须为 /data/mysql/db{slave_port}/data）
  必要时重启 / post_config
```

**参数：**

```json
"context": {
  "bootstrap_method": "clone",
  "clone_ssl": false
}
```

表单规则：仅当主库 major ≥ 8.0 且版本 ≥ 8.0.17 时可选。

### 6.5 方案对比与选型

| 方案 | 第一期 | 适用 | initialize | 典型耗时 |
|------|--------|------|------------|----------|
| mysqldump | ✅ | 小中库、5.7/8.0 | 必须先 | 随库大小线性增长 |
| xtrabackup | 第二期 | 大库生产 | 不需要 | 热备，较快 |
| clone | 第二期 | 8.0 同 major | 特殊 | 网络带宽敏感 |

---

## 7. 阶段四：建立复制与验收

### 7.1 repl_setup

**本地连接账号：** 从库 `root@localhost`（socket），密码取 `credentials.root_account`（主库 CMDB `user_adm / root@localhost`）。执行前由 `ensure_mysql_replica_root_credentials()` 确保历史任务亦已写入该字段。

**Playbook 流程（tag `repl_setup`）：**

```
repl_setup:
  部署 /tmp/dbatoolbox_replica_slave_root_<job_id>.cnf（user=root, socket=从库 sock）
  mysql --defaults-extra-file=... -e "SELECT 1"     # 校验 root 可连接
  STOP SLAVE / RESET SLAVE ALL（幂等）
  生成 CHANGE MASTER SQL（MASTER_USER 仍为 repl 账号）
  mysql --defaults-extra-file=... < change_master.sql
```

按主库 major 选择语法；**`MASTER_HOST` / `SOURCE_HOST` = `context.master_host`**（同机为 `127.0.0.1`），**`MASTER_PORT` / `SOURCE_PORT` = `context.master_port`**。**`MASTER_USER` / `SOURCE_USER` 为主库 `user_repl`**，与本地 root 无关。

```sql
-- 5.7（同机时 MASTER_HOST='127.0.0.1'）
CHANGE MASTER TO
  MASTER_HOST='<master_host>', MASTER_PORT=<master_port>,
  MASTER_USER='<repl>', MASTER_PASSWORD='...',
  MASTER_AUTO_POSITION=1;
START SLAVE;

-- 8.0
CHANGE REPLICATION SOURCE TO
  SOURCE_HOST='<master_host>', SOURCE_PORT=<master_port>,
  SOURCE_USER='<repl>', SOURCE_PASSWORD='...',
  SOURCE_AUTO_POSITION=1;
START REPLICA;
```

幂等：若复制已在跑且健康 → skip；异常 → `STOP SLAVE; RESET SLAVE ALL;` 后重建。

主库若强制 SSL，`CHANGE` 语句需带 `SOURCE_SSL=1` 等（第二期）。

### 7.2 repl_readonly

```sql
SET GLOBAL read_only = ON;
-- MySQL 8.0 / 8.4
SET GLOBAL super_read_only = ON;
```

可选：回写 my.cnf 以便重启后保持。

### 7.3 repl_verify

```sql
SHOW SLAVE STATUS\G    -- 5.7
SHOW REPLICA STATUS\G  -- 8.0
```

| 检查项 | 期望 |
|--------|------|
| IO / SQL 线程 | Yes |
| `Seconds_Behind_Master` | 趋近 0 |
| `Last_IO_Error` / `Last_SQL_Error` | 空 |
| GTID | `Retrieved_Gtid_Set` 正常追赶 |

结果写入 `job.result.replication_status`；`verify` 步可保留 `SELECT VERSION()`。

### 7.4 register_cmdb

| 写入 | 内容 |
|------|------|
| `DatabaseInstance` | `topology=replication`、`role=slave`、`replication_cluster_id`、`connect_host`、`port`（从库）、版本 |
| `DatabaseInstanceHost` | 从库部署主机、`listener_port={slave_port}` |
| `DatabaseAccount` | **从主库实例复制** 全部（或全部非仅本地）台账记录到新从库实例：`account_name`、`grant_host`、`account_pswd`、`account_type`、`is_default` 等与主库 CMDB 一致 |
| `DbDeployJob.instance` | 关联从库实例 |

**账号继承说明：**

- `mysqldump --all-databases` 已使从库 **运行时** 用户表与主库一致；CMDB 复制保证 **台账与主库对齐**，便于运维与探测
- **不**为从库单独创建表单 DBA / root 密码
- repl 账号台账仍在 **主库**；从库 CMDB 是否复制 `user_repl` 记录：建议 **复制**（与主库相同 grant，便于查看），但复制连接仍只用主库上的 repl 定义

部署成功后建议触发 `probe_and_save_instance`（待实现）；探测默认账号可取继承后的 `is_default=true` 的 `user_dba`。

---

## 8. 技术要点速查

### 8.1 major 版本

主从 major 必须一致（5.7↔5.7，8.0↔8.0）；跨 major 不做自动化从库部署。

### 8.2 GTID

第一期 **仅支持主库 GTID ON**；从库与主库一致；`MASTER_AUTO_POSITION=1` / `SOURCE_AUTO_POSITION=1`。  
`gtid_mode=OFF` → precheck 拒绝。不做 `repl_mode=file`（第二期）。

### 8.3 server_id

- 算法：`SHA256("{connect_host}:{port}")` 取前 4 字节映射到 `1..4294967295`（`deploy_constants.build_mysql_server_id`）
- 复制拓扑内唯一；从库 ≠ 主库 ≠ 其他从库
- **同 IP 不同从库端口** 会得到不同 `server_id`（利于同机多实例）
- 从库必须 `log_bin=ON`（默认开启时自动有 `server_id`）
- **`master_runtime` 合并禁止覆盖从库 `server_id`**：主库 `@@server_id` 仅写入 `context.master_runtime` 供冲突校验；从库 `my.cnf` 始终使用按**从库** `connect_host:port` 生成的值。若运行中 `@@server_id` 与主库相同，`repl_setup` / `repl_verify` 会校正 my.cnf 并重启实例。

### 8.4 复制账号、DBA 账号与连接地址

**`master_host` / `master_port`：** 见 §2.3；同机复制与 precheck 用 `127.0.0.1`。

**复制账号（`user_repl`）：**

| 场景 | precheck | `CHANGE MASTER` 的 HOST |
|------|----------|-------------------------|
| 异机 | 从库主机 → `master_host:master_port`；`grant_host` 须含从库 IP | `master_host` |
| 同机 | 从库主机 → `127.0.0.1:master_port`；`grant_host` 须允许本机 | **`127.0.0.1`** |

**全量账号（主库 `user_dba`）：** 规则同上（§5.2.2）；**mysqldump 前必测**。

**建立复制（`repl_setup`）本地 root：**

| 项 | 说明 |
|----|------|
| 连接 | 从库 socket；`user=root`，密码 = 主库 CMDB `user_adm/root@localhost` |
| 前提 | 主库单实例部署时已登记 root 台账；`mysqldump` 导入后从库 root 密码与主库一致 |
| 与 DBA 分工 | `dba_admin` 仅用于连主库做 dump；**不**用于 `CHANGE MASTER` |

**8.0 认证：** repl / DBA 插件须与从库客户端兼容；Clone 另需 repl 具备 `BACKUP_ADMIN`。

### 8.5 主从参数（§2.5 摘要）

从库 `configure` 须对齐：`lower_case_table_names=1`、`binlog_checksum=CRC32`、`slave_sql_verify_checksum=ON`、8.0 `transaction_write_set_extraction=XXHASH64`，及其余与主库一致项。

### 8.6 幂等与重试

| 步骤 | 策略 |
|------|------|
| precheck～start | 单实例相同；从库 **无 post_config** |
| repl_bootstrap | datadir 非空且无 `force_rebuild` → 失败 |
| repl_setup | 复制异常 → `RESET SLAVE ALL` 后重建 |
| register_cmdb | 已注册 → 跳过或更新 |

`retry_deploy_job()` 当前重置全部步骤；从库需 `context.force_rebuild` 及 bootstrap 独立 guard。

---

## 9. Job 步骤与代码映射

### 9.1 目标步骤列表

在 `DEPLOY_STEPS` 基础上扩展（或按 `job_type` 分支）：

| # | step_code | 阶段 | 说明 |
|---|-----------|------|------|
| 1 | `precheck` | §5.1～5.3、§5.2 | 含 GTID ON、参数白名单、repl/DBA 连通 |
| 2 | `prepare` | §5.3.4 | |
| 3 | `install` | §5.3.3 | 条件 |
| 4 | `configure` | §5.4 | 含 §2.5 白名单 |
| 5 | `initialize` | §5.4.5 | |
| 6 | `start` | §5.4.5 | 无 post_config |
| 7 | `repl_bootstrap` | §6 | mysqldump |
| 8 | `repl_setup` | §7.1 | 从库 root（主库台账）+ `master_host` 同 §2.3 |
| 9 | `repl_readonly` | §7.2 | |
| 10 | `repl_verify` | §7.3 | |
| 11 | `verify` | 版本 | |
| 12 | `register_cmdb` | §7.4 | **复制主库账号** |

> **从库 job 不含 `post_config` 步骤**（或该步恒 `skipped`）。单实例 `site.yml` 的 `post_config` tag 在 `job_type=mysql_replica` 时不执行。

### 9.2 相关代码

| 路径 | 职责 |
|------|------|
| `apps/dbmgr/deploy_constants.py` | 步骤、`build_mysql_server_id`、路径 |
| `apps/dbmgr/deploy_services.py` | 创建校验、`register_instance_from_job`、`_fetch_mysql_master_root_account`、`ensure_mysql_replica_root_credentials` |
| `apps/dbmgr/profile_loader.py` | `resolve_deploy_params`、主库 context 合并 |
| `apps/dbmgr/deploy_executors/` | `MysqlReplicaExecutor` |
| `apps/dbmgr/deploy_constants.py` | 从库 `my.cnf` 生成：见 [db-deploy-mysql-replica-mycnf.md](./db-deploy-mysql-replica-mycnf.md) |
| `deploy/playbooks/mysql/replica/site.yml` | 从库 Playbook（`repl_master_probe`、`repl_*` tag） |
| `deploy/profiles/mysql/*.yml` | `supported_job_types` 增加 `mysql_replica` |
| `templates/dbmgr/deploy_job_list.html` | 从库表单（待增） |

### 9.3 与单实例差异摘要

| 单实例 | 从库（mysqldump） |
|--------|-------------------|
| `post_config` 设 root/DBA | **跳过**；import 后用户表来自主库 |
| CMDB 登记表单账号 | **从主库 CMDB 复制** `DatabaseAccount` |
| precheck：datadir 存在即失败 | 同上 |
| CMDB `standalone` / `master` | `replication` / `slave` + `replication_cluster_id` |
| configure 无只读 | `repl_readonly` 开启并建议写回 cnf |
| verify 仅 VERSION | + `repl_verify` |

---

## 10. 现有能力与待实现项

### 10.1 可直接复用

- `DatabaseReplicationCluster` + 管理页
- `DatabaseInstance.topology` / `role` / `replication_cluster`
- `DatabaseAccount.account_type = user_repl`
- 单实例 playbook：`prepare` / `install` / `configure` / `initialize` / `start` / `post_config`
- `finalize_mysql_deploy_params()`、`build_mysql_install_paths()`
- `create_replication_cluster()` 关联主库

### 10.2 待实现（按优先级）

**P0**

- [ ] `mysql_replica` 全链路；**主库由复制集 `primary_instance` 自动带出**
- [ ] `resolve`：§2.3 `master_host`/`master_port`、`dump_account`、`root_account`、§2.5 `config` 合并
- [ ] precheck：GTID ON、§2.5 白名单、§5.2 repl/DBA 异机/同机连通
- [ ] 从库路径：**无 post_config**；`initialize → start → mysqldump import`
- [ ] `repl_setup`：从库 socket + 主库台账 `root@localhost`；`CHANGE MASTER` 使用 §2.3 的 `master_host`（同机 `127.0.0.1`）
- [ ] `register_cmdb`：`replication_cluster_id` + **从主库复制账号台账**

**P1**

- [ ] `force_rebuild` 与 bootstrap 幂等 guard
- [ ] 部署后 `probe_and_save_instance`
- [ ] `job.result.replication_status` 详情展示
- [ ] `db-deploy-mysql-replica-step-action.md` 步骤说明

**P2**

- [ ] `xtrabackup` / `clone` bootstrap 分支
- [ ] SSL 复制、`repl_mode=file`
- [ ] 日志脱敏、DBA 权限控制

---

## 11. 第一期验收清单

- [ ] 选定复制集后自动绑定 `primary_instance`，不可选其它主库
- [ ] 主库 `gtid_mode=OFF` → precheck 拒绝
- [ ] 主库 `lower_case_table_names` 等 §2.5 不合规 → precheck 拒绝
- [ ] 异机：repl/DBA 从从库连 `master_host` 成功；失败提示检查 `grant_host` / 从库 IP
- [ ] 同机：repl/DBA 连 `127.0.0.1:master_port` 成功；`CHANGE MASTER` 使用 `127.0.0.1`
- [ ] 从库端口可自定义；同机时从库端口 ≠ 主库端口
- [ ] 主库 CMDB 存在 `user_adm / root@localhost`；缺失时创建任务失败
- [ ] 从库路径：`initialize → start`（无 post_config）→ mysqldump（主库 DBA）→ 本地 root 空密码导入 → `repl_setup`（主库 root 密码连从库 socket）
- [ ] 导入后 `START SLAVE` / `repl_verify` 通过；`repl_readonly` 生效
- [ ] CMDB 从库账号与主库 CMDB 一致（复制继承）；无单独从库 DBA 表单
- [ ] `replication_cluster_id`、`topology=replication`、`role=slave` 登记正确

**推荐落地顺序：** `resolve`（§2.3、主库绑定）→ precheck（§5.1～5.2）→ 从库安装与 `configure` → `initialize/start` → `repl_bootstrap` → `repl_setup` / `repl_verify` → `register_cmdb`（复制账号）。
