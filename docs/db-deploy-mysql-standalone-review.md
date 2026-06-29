# MySQL 单实例部署 — 实现状态与待办

与逐步说明 [db-deploy-mysql5.7-step-action.md](./db-deploy-mysql5.7-step-action.md) 互补：本文记录**当前能力**与**仍待改进项**（以代码为准）。

---

## 1. 架构要点

- **9 步串行**：`precheck` → `prepare` → `install` → `configure` → `initialize` → `start` → `post_config` → `verify` → `register_cmdb`
- **软件共享**：`/usr/local/mysql` 软链 → `package_ref` 目录；同机仅一个 major
- **实例隔离**：`/data/mysql{port}`、`mysqld{port}.service`、独立端口
- **默认**：Binlog + GTID 开启；引导期 `127.0.0.1`，设密后 `0.0.0.0`

---

## 2. 已实现能力

| 类别 | 内容 |
|------|------|
| 参数 | Profile 合并、`server_id=SHA256(host:port)`、路径按端口生成 |
| 创建校验 | 端点唯一、server_id 唯一、同主机部署互斥、业务 IP 连接地址 |
| 软件安装 | `package_ref` 精确建链；major+minor 比较；低版本自动升级并重建软链 |
| 安全 | 引导期仅本机监听；systemd/长期 cnf 无明文密码于 unit |
| 恢复 | 失败续跑、force_rebuild、release_endpoint 释放失败任务端点 |
| precheck | 端口进程归属（platform/foreign）；目标机介质 HEAD；升级前检查其它 mysqld 服务 |
| verify | 运行版本 ≥ Profile |

---

## 3. 待改进（按优先级）

### P0 — 安全

| 项 | 说明 |
|----|------|
| 密码明文 | `DbDeployJob.params`、`DatabaseAccount`、目标机 `.root-client.cnf` 仍为明文；API 仅掩码展示 |

### P1 — 功能 / 运维

| 项 | 说明 |
|----|------|
| `min_memory_gb` | Profile 声明未在 precheck 执行 |
| `user_repl` | 未自动创建复制账号（从库场景见 replica 指南） |
| `listener_host` | `DatabaseInstanceHost` 未写入，VIP 探测靠回退逻辑 |
| DBA 密码 | 后端非必填，空密码可跳过 DBA 创建 |
| 运维账号 | 表单 `type="text"` |

### P2 — 体验 / 长期

| 项 | 说明 |
|----|------|
| 步骤 output 脱敏 | Ansible 失败日志可能含敏感信息 |
| 部署后 probe | `register_cmdb` 后未自动探测实例在线状态 |
| 执行中取消 | 仅 `pending` 可 cancel；worker 失联可能卡在 `running` |
| my.cnf 模板化 | 仍为 `site.yml` 内联；见 [db-deploy-mysql-multiversion-tasks.md](./db-deploy-mysql-multiversion-tasks.md) |
| `mysql_replica` | 从库任务类型未实现 |

---

## 4. 从库衔接（未实现）

单实例已具备 GTID+binlog、DBA 复制权限、server_id；**缺少** `user_repl`、复制参数模板、`mysql_replica` Playbook。详见 [db-deploy-mysql-replica-guide.md](./db-deploy-mysql-replica-guide.md)。

---

## 5. 相关文档

| 文档 | 用途 |
|------|------|
| [db-deploy-mysql5.7-step-action.md](./db-deploy-mysql5.7-step-action.md) | 逐步动作 |
| [db-deploy-mysql-install-decision-guide.md](./db-deploy-mysql-install-decision-guide.md) | 现场判断与 force_rebuild |
| [db-deploy-mysql-endpoint-release.md](./db-deploy-mysql-endpoint-release.md) | 失败任务释放端点 |
| [db-deploy-mysql-profile-spec.md](./db-deploy-mysql-profile-spec.md) | Profile 规范 |
| [db-deploy-design.md](./db-deploy-design.md) | 总体设计 |
