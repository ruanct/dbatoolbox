# MySQL 5.7 单实例部署 — 执行步骤说明

本文档根据后台程序整理，描述 MySQL 单实例部署任务中 9 个执行步骤的具体动作。

## 执行总览

9 个步骤定义于 `apps/dbmgr/deploy_constants.py` 的 `DEPLOY_STEPS`，由 Celery 调用 `MysqlStandaloneExecutor` 顺序执行。

```
创建任务 → resolve_deploy_params() 合并参数（含 CMDB OS 静态校验）
         → Celery run_db_deploy_job
         → MysqlStandaloneExecutor.run() 逐步执行
              precheck      → Python 预检 + Ansible precheck（见 §1 顺序）
              prepare~verify  → ansible-playbook --tags <step_code>
              register_cmdb   → Django ORM 写库
```

步骤 1～8 中除「注册台账」外，均通过 Ansible Playbook `deploy/playbooks/mysql/standalone/site.yml` 按 `--tags` 分步执行；参数以 `resolved_params` 经 extra-vars 传入。

### 路径约定（按端口生成，以 3306 为例）

| 项 | 路径 |
|----|------|
| 实例根目录 | `/data/mysql3306` |
| 参数文件 | `/data/mysql3306/my.cnf` |
| 数据目录 | `/data/mysql3306/data` |
| Socket | `/data/mysql3306/mysql.sock` |
| 错误日志 | `/data/mysql3306/mysql_err.log` |
| Binlog 目录 | `/data/mysql3306/binlog` |
| 程序目录（basedir） | `/usr/local/mysql`（固定） |
| 系统服务名 | `mysqld3306.service` |

路径由 `apps/dbmgr/deploy_constants.py` 中 `build_mysql_install_paths()` 生成，在 `finalize_mysql_deploy_params()` 写入 `resolved_params.install`。

---

## 1. 预检查（precheck）

**执行位置**：

- 创建任务：`apps/dbmgr/profile_loader.py` → `deploy_os_compat.validate_host_os_against_profile`
- 步骤执行：`apps/dbmgr/deploy_executors/base.py` → `_run_precheck()` + Ansible `tags: precheck`（`deploy/playbooks/mysql/standalone/site.yml`）

校验按下列**顺序**执行；任一项失败则任务标记为 `failed`，后续步骤不再执行。

### 1.1 创建任务时（静态，CMDB）

| 顺序 | 子项 | 说明 |
|------|------|------|
| 1 | **操作系统静态校验** | 根据主机台账 `os_type` + `os_version` 与 Profile `supported_os_rules` 比对：CentOS≥7、RHEL≥7、Anolis≥7、阿里云 Linux≥3；不满足则**拒绝创建任务** |

> 实现：`apps/dbmgr/deploy_os_compat.py`。不校验 CPU 架构（架构在 Ansible precheck 动态校验）。

### 1.2 precheck 步骤（Executor + Ansible）

| 顺序 | 子项 | 说明 |
|------|------|------|
| 2 | **Python 预检** | SSH 到目标主机，探测 Python ≥ 3.8，确定 `ansible_python_interpreter`（`base._run_precheck`） |
| 3 | **gather_facts** | Ansible 采集目标机 facts（含 `ansible_distribution`、`ansible_architecture` 等） |
| 4 | **操作系统校验** | 归一化 `ansible_distribution` → family，与 `d.profile.supported_os_rules` 比对 major 版本；要求 CentOS≥7、RHEL≥7、Anolis≥7、阿里云 Linux≥3 |
| 5 | **CPU 架构校验** | `ansible_architecture` 须在 `d.profile.supported_arch` 内（默认 `x86_64`） |
| 6 | **程序目录检查** | `stat` 检查 `{{ basedir }}/bin/mysqld` 是否存在，结果记入 `mysqld_bin` |
| 7 | **major 版本校验** | **仅当 `mysqld` 已存在**：执行 `mysqld --version`，解析 major，与 `d.profile.major_version` 比对；不一致或无法解析 → **失败** |
| 8 | **数据目录冲突** | 若 `{{ datadir }}/mysql` 已存在 → **失败**（实例已初始化） |
| 9 | **端口占用** | `wait_for` 检测 `{{ port }}` 未被监听；已占用 → **失败** |
| 10 | **介质可达** | 对 `d.media.download_url` 发 HTTP HEAD，非 200/302 → **失败** |
| 11 | **glibc 版本校验** | 探测 OS glibc，与 `d.media.package_glibc_version`（如 `2.12`）比对；OS glibc 须 **≥** 软件包要求，否则失败：**软件包glibc版本高于OS内glibc版本** |

> 若目标机尚未安装 MySQL 程序（`mysqld` 不存在），跳过顺序 7（major 版本校验），后续由 install 步骤下载安装。

---

## 2. 环境准备（prepare）

**执行位置**：Ansible `tags: prepare`

| 子项 | 说明 |
|------|------|
| 创建系统组 | `group: mysql` |
| 创建系统用户 | `user: mysql`（系统用户，`/sbin/nologin`） |
| 创建实例目录 | `{{ instance_root }}`、`{{ datadir }}`、`{{ basedir 父目录 }}`，属主 `mysql:mysql`，权限 `0750` |
| 创建 binlog 目录 | `{{ binlog_dir }}`（仅当 `enable_binlog=true` 时） |

---

## 3. 安装软件（install）

**执行位置**：Ansible `tags: install`

| 子项 | 说明 |
|------|------|
| 检查是否已安装 | 若 `{{ basedir }}/bin/mysqld` 已存在 → **跳过下载/解压**（precheck 已校验 major 版本一致） |
| 下载安装包 | `get_url` 从内网 URL 下载到 `/tmp/{{ filename }}` |
| 解压 | `unarchive` 到 basedir 父目录（如 `/usr/local`） |
| 建立软链接 | 将解压出的 `mysql-*` 目录链接到 `{{ basedir }}`（如 `/usr/local/mysql`） |
| 设置权限 | `{{ basedir }}` 递归 `chown mysql:mysql` |

当前 Profile（`deploy/profiles/mysql/5.7.44.yml`）介质：`mysql-5.7.44-linux-glibc2.12-x86_64.tar.gz`。

---

## 4. 配置文件（configure）

**执行位置**：Ansible `tags: configure`

**动作**：生成并写入 `{{ cnf_path }}`（如 `/data/mysql3306/my.cnf`），主要参数如下。

| 配置项 | 来源 |
|--------|------|
| basedir / datadir / port / socket | `resolved_params.install` |
| character-set-server / collation-server | profile 默认 `utf8mb4` / `utf8mb4_unicode_ci` |
| max_connections | 默认 500 |
| innodb_buffer_pool_size | 默认 1G |
| default_authentication_plugin | 默认 `mysql_native_password` |
| log-error / pid-file | 实例路径 |
| **Binlog 开启时** | `server_id`、`log_bin`、`binlog_format`（默认 ROW） |
| **GTID 开启时** | `gtid_mode=ON`、`enforce_gtid_consistency=ON`、`log_slave_updates=ON` |
| **Binlog 关闭时** | `skip-log-bin`，不写 server_id/GTID |
| [client] | socket + 字符集 |

`server_id` 由连接地址末两段 IP + 端口计算（如 `10.32.13.98:3306` → `13983306`），逻辑见 `build_mysql_server_id()`。

---

## 5. 初始化实例（initialize）

**执行位置**：Ansible `tags: initialize`

```bash
{{ basedir }}/bin/mysqld \
  --defaults-file={{ cnf_path }} \
  --initialize-insecure \
  --user=mysql
```

- 初始化数据目录（生成系统库）
- `--initialize-insecure`：root **无密码**（密码在步骤 7 后置配置中设置）
- `creates: {{ datadir }}/mysql`：已初始化则跳过

---

## 6. 启动服务（start）

**执行位置**：Ansible `tags: start`

| 子项 | 说明 |
|------|------|
| 部署 systemd 单元 | `/etc/systemd/system/{{ service_name }}.service`（如 `mysqld3306.service`） |
| ExecStart | `mysqld --defaults-file=... --daemonize` |
| ExecStop | `mysqladmin -uroot -p'root密码' shutdown` |
| 启动并开机自启 | `systemd: state=started, enabled=true, daemon_reload=true` |

---

## 7. 后置配置（post_config）

**执行位置**：Ansible `tags: post_config`

| 子项 | 说明 |
|------|------|
| 等待就绪 | `wait_for` 监听端口，最长 120 秒 |
| 设置 root 密码 | `mysqladmin` 为 `root@localhost` 设置密码 |
| 创建高级 DBA 账号 | 创建运维账号并授权，分三组 GRANT：①`SELECT, INSERT, UPDATE, DELETE, CREATE, DROP, RELOAD, PROCESS, REFERENCES, INDEX, ALTER` ②`SHOW DATABASES, CREATE TEMPORARY TABLES, LOCK TABLES, EXECUTE, REPLICATION SLAVE, REPLICATION CLIENT` ③`CREATE VIEW, SHOW VIEW, CREATE ROUTINE, ALTER ROUTINE, CREATE USER, EVENT, TRIGGER`（MySQL 8.0+ 另含 `CREATE ROLE, DROP ROLE`），最后 `GRANT USAGE ... WITH GRANT OPTION` |

---

## 8. 连通验证（verify）

**执行位置**：Ansible `tags: verify`；成功后 Django 解析版本写入 `job.result`

```bash
mysql -uroot -p'...' -S {{ socket }} -Nse "SELECT VERSION();"
```

- 输出 `MySQL version=x.x.x`
- Executor 解析版本号写入 `job.result.detected_version`
- 任务状态在此步骤期间为 `verifying`

---

## 9. 注册台账（register_cmdb）

**执行位置**：`BaseDeployExecutor._register_cmdb()` → `register_instance_from_job()`（纯 Django，无 Ansible）

| 写入对象 | 内容 |
|----------|------|
| `DatabaseInstance` | 实例名、引擎 mysql、拓扑 standalone、角色 master、状态 online、版本（上步探测）、环境/业务、连接地址/端口、字符集等 |
| `DatabaseInstanceHost` | 关联目标主机、监听端口、主节点标记 |
| `DatabaseAccount` | `root@localhost`（`user_adm`，超级管理员）；运维账号（`user_dba`，高级DBA用户，`is_default=True`） |
| `DbDeployJob.instance` | 回写任务与实例关联 |

若已注册则跳过，返回「实例已注册」。

---

## 步骤与代码对应关系

| # | 步骤 | step_code | 主要代码 |
|---|------|-----------|----------|
| 1 | 预检查 | `precheck` | `deploy_executors/base.py` + `site.yml` precheck |
| 2 | 环境准备 | `prepare` | `site.yml` prepare |
| 3 | 安装软件 | `install` | `site.yml` install |
| 4 | 配置文件 | `configure` | `site.yml` configure |
| 5 | 初始化实例 | `initialize` | `site.yml` initialize |
| 6 | 启动服务 | `start` | `site.yml` start |
| 7 | 后置配置 | `post_config` | `site.yml` post_config |
| 8 | 连通验证 | `verify` | `site.yml` verify |
| 9 | 注册台账 | `register_cmdb` | `deploy_services.register_instance_from_job()` |

任一步失败即终止后续步骤，任务标记为 `failed`，错误信息写入对应 `DbDeployJobStep.output` 和 `DbDeployJob.error_message`。

---

## 相关文件

| 文件 | 职责 |
|------|------|
| `apps/dbmgr/deploy_constants.py` | 步骤定义、路径生成、参数收敛 |
| `apps/dbmgr/deploy_executors/base.py` | 步骤编排与执行入口 |
| `apps/dbmgr/deploy_executors/mysql_standalone.py` | MySQL 单实例 Executor |
| `apps/dbmgr/deploy_ansible.py` | Ansible 按 tag 执行封装 |
| `apps/dbmgr/deploy_services.py` | 任务创建、台账注册 |
| `apps/dbmgr/profile_loader.py` | Profile 合并、`resolved_params` 生成 |
| `apps/dbmgr/deploy_os_compat.py` | 创建任务 OS 静态校验、OS family 归一化 |
| `deploy/playbooks/mysql/standalone/site.yml` | 各步骤 Ansible 任务 |
| `deploy/profiles/mysql/5.7.44.yml` | MySQL 5.7.44 版本档案 |
