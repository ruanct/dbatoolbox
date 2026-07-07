# MySQL 单实例 — 现场判断与重建决策

目标机已有 MySQL 软件/实例、或多版本解压目录并存时，如何判断能否安装、续跑或 force_rebuild。

**关联**：[db-deploy-mysql5.7-step-action.md](./db-deploy-mysql5.7-step-action.md)、[db-deploy-mysql-endpoint-release.md](./db-deploy-mysql-endpoint-release.md)

---

## 1. 三层模型

```text
网络层  → 目标端口是否被监听、占用方是谁
实例层  → /data/mysql/db{port}/、mysqld{port}.service
软件层  → /usr/local/mysql（软链）、package_ref 解压目录、/etc/profile PATH
```

| 结论 | 说明 |
|------|------|
| force_rebuild | **只清实例层**（`instance_root`），不删共享软件 |
| 同机多实例 | 共享软件层，实例层按端口隔离 |
| 同机多 major | **不支持**（单一 `/usr/local/mysql`） |

---

## 2. 网络层：端口占用

**实现**：precheck 用 `ss` + `systemctl` + 进程 cmdline 判断 `mysql_port_owner`：

| 归属 | 含义 | 普通部署 | force_rebuild |
|------|------|:--------:|:-------------:|
| `free` | 端口空闲 | ✅ | ✅ |
| `platform` | 本任务 `mysqld{port}` 或 cmdline 含本任务 `my.cnf` | ❌ | ✅（prepare 会 stop） |
| `foreign` | yum/手工/其它服务 | ❌ | ❌ |

外来占用时须换端口或人工下线，**不能**靠 force_rebuild 抢占。

```bash
ss -lntp | grep :3306
systemctl status mysqld3306
```

---

## 3. 软件层

- 软链 `/usr/local/mysql` → `{{ package_ref }}`（如 `mysql-5.7.44-linux-glibc2.12-x86_64`）
- 版本：解析 `Ver x.y.z`，与 Profile `major.minor` 比较
- **minor 低于 Profile** → install 下载（如需）并重建软链
- **升级软件前**：若同机其它 `mysqld*.service` 仍运行 → install **失败**（须先停其它实例）

---

## 4. API 层门禁（创建任务时）

| 校验 | 函数 |
|------|------|
| 端点 `(connect_host, port)` | `ensure_deploy_endpoint_available` |
| 同主机互斥 | `ensure_host_deploy_lock_available` |
| server_id（开 binlog） | `ensure_mysql_server_id_available` |

**failed 任务占端点**：`failed` 仍在 `DEPLOY_JOB_ACTIVE_STATUSES` 内。不再续跑且需同端口新建 → 详情页 **「释放端点」**（`release_endpoint` → `cancelled`）。见 [db-deploy-mysql-endpoint-release.md](./db-deploy-mysql-endpoint-release.md)。

---

## 5. 快速决策表

| 现场 | 直接安装 | force_rebuild |
|------|:--------:|:-------------:|
| 干净主机 | ✅ | — |
| 端口 free，datadir 空 | ✅ | — |
| 端口 platform（本任务） | ❌ | ✅ |
| 端口 foreign | ❌ | ❌ |
| datadir 有（本任务残留） | ❌ | ✅ |
| 软件版本 ≥ Profile，新端口 | ✅（跳过 install） | — |
| minor < Profile | ✅（install 升级） | — |
| major 不一致 | ❌ | ❌ |
| 已注册台账 | — | ❌ API |
| 同端点有 failed 任务 | ❌ API | 先 release_endpoint |

---

## 6. 续跑 vs 强制重建

| 操作 | 步骤 | resolved_params | 清实例目录 |
|------|------|-----------------|------------|
| 继续执行 | 失败步及之后 | 不刷新 | 否 |
| force_rebuild | 全部 | 刷新 | 是 |
| 取消后重跑 | 全部 | 刷新 | 否（datadir 在则 precheck 失败，需 force_rebuild） |

---

## 7. 仍待改进

| 项 | 说明 |
|----|------|
| 密码加密 | 库内与台账明文 |
| min_memory / 磁盘 | Profile 未执行 |
| user_repl | 未创建 |
| 执行中取消 | 未支持 |

---

## 8. 现场核对

```bash
ls -l /usr/local/mysql
/usr/local/mysql/bin/mysqld --version
ss -lntp | grep :<port>
systemctl status mysqld<port>
ls -la /data/mysql/db<port>/data/mysql 2>/dev/null
grep DBATOOLBOX /etc/profile
```
