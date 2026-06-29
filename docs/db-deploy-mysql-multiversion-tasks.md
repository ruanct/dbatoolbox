# MySQL 多版本部署 — 实施任务清单

> **关联**：[db-deploy-mysql-profile-spec.md](./db-deploy-mysql-profile-spec.md)、[db-deploy-design.md](./db-deploy-design.md)

## 当前实现状态（2026-06）

| 项 | 状态 |
|----|------|
| Profile `5.7.44`、`8.0.45` | ✅ 已上线 |
| `site.yml` 8.0 install（tar.xz）、OS precheck、major 分支 configure | ✅ |
| `package_ref` 精确软链、minor 升级 | ✅ |
| `my.cnf.j2` 模板化 | ❌ configure 仍内联 |
| `cnf_template` Profile 字段 | ❌ 未接线 |
| finalize 按 major 收敛 | 部分（playbook 分支） |

下文 Issue 列表保留为 **未完成工作**；已落地项在状态板标为「已完成」。

---

## M1：my.cnf Jinja2 化 + 8.0 Profile 样板

### ISSUE-001 抽取 my.cnf Jinja2 模板

| 项 | 内容 |
|----|------|
| **优先级** | P0 |
| **依赖** | 无 |
| **改动文件** | 新增 `deploy/templates/mysql/my.cnf.j2`；修改 `deploy/playbooks/mysql/standalone/site.yml` |

**任务描述**：

将 `site.yml` configure 步骤中 `copy.content` 内联多行配置迁移为 Jinja2 模板文件。

**验收标准**：

- [ ] `deploy/templates/mysql/my.cnf.j2` 包含现有 5.7 所需的全部配置项（basedir、datadir、port、socket、字符集、内存、binlog/gtid 条件块、client 段等）；
- [ ] configure 步骤改为 `template` 模块，`dest` 仍为 `{{ mysql_cnf_path }}`；
- [ ] 模板文件随仓库部署，Playbook 通过相对路径或 `playbook_dir` 引用；
- [ ] 5.7.44 现有部署任务渲染结果与迁移前 **语义一致**（可做 diff 对比）。

**参考**：当前内联内容见 `site.yml` `[configure] 部署 my.cnf` 任务。

---

### ISSUE-002 site.yml 支持 cnf_template 变量

| 项 | 内容 |
|----|------|
| **优先级** | P1 |
| **依赖** | ISSUE-001 |
| **改动文件** | `deploy/playbooks/mysql/standalone/site.yml` |

**任务描述**：

Playbook vars 增加 `mysql_cnf_template`，默认 `mysql/my.cnf.j2`；若 `d.cnf_template` 或 `d.profile` 中指定则使用 Profile 值（规范见 Profile spec §5.2）。

**验收标准**：

- [ ] 未指定 Profile `cnf_template` 时行为与 ISSUE-001 默认一致；
- [ ] Profile 指定 `cnf_template` 时可指向备用模板（为 major 专用模板预留）。

---

### ISSUE-003 新增 MySQL 8.0 Profile

| 项 | 内容 |
|----|------|
| **状态** | **已完成**（`deploy/profiles/mysql/8.0.45.yml`） |
| **改动文件** | `deploy/profiles/mysql/8.0.45.yml` 等 |

---

### ISSUE-004 my.cnf.j2 增加 8.0 major 条件分支

| 项 | 内容 |
|----|------|
| **优先级** | P0 |
| **依赖** | ISSUE-001、ISSUE-003 |
| **改动文件** | `deploy/templates/mysql/my.cnf.j2` |

**任务描述**：

模板内对 8.0 与 5.7 差异项做条件渲染；优先使用 `d.config` 合并后的值，而非在模板内写死版本默认值。

**验收标准**：

- [ ] 5.7 Profile 合并后渲染出 `default_authentication_plugin=mysql_native_password`（或等价配置）；
- [ ] 8.0 Profile 合并后渲染出 `caching_sha2_password` 与 `utf8mb4_0900_ai_ci`；
- [ ] binlog/gtid 开关逻辑与现网一致；
- [ ] 可选：支持 `d.config.extra_cnf_lines` 追加到 `[mysqld]` 末尾。

---

### ISSUE-005 install 步骤对 8.0 包兼容

| 项 | 内容 |
|----|------|
| **状态** | **已完成**（`package_ref` 建链、tar.xz、`mysql_profile_major` 分支） |

---

### ISSUE-006 文档同步

| 项 | 内容 |
|----|------|
| **状态** | **部分完成**（步骤说明、决策指南、review 已与代码对齐；configure 仍内联需在 ISSUE-001 后更新） |

---

## M2：参数管线加强（M1 之后）

### ISSUE-007 finalize_mysql_deploy_params 按 major 收敛

| 项 | 内容 |
|----|------|
| **优先级** | P1 |
| **依赖** | M1 |
| **改动文件** | `apps/dbmgr/deploy_constants.py` |

**任务描述**：

根据 `merged.profile.major_version` 剔除无效 config 键、补充版本默认；例如 5.7 不传 8.0 专属项。

**验收标准**：

- [ ] 5.7 任务 `resolved_params.config` 不含 8.0 无效项；
- [ ] 8.0 任务默认认证插件与 collation 与 Profile 一致；
- [ ] 单元测试覆盖 5.7 / 8.0 合并样例。

---

### ISSUE-008 创建任务 config 字段校验

| 项 | 内容 |
|----|------|
| **优先级** | P2 |
| **依赖** | ISSUE-007 |
| **改动文件** | `apps/dbmgr/deploy_services.py` |

**任务描述**：

在 `_validate_create_body` 或独立校验函数中，按 profile 校验 config 合法性。

**验收标准**：

- [ ] 非法组合（gtid on + binlog off）创建失败；
- [ ] 错误信息可读。

---

### ISSUE-009 my.cnf 渲染快照测试

| 项 | 内容 |
|----|------|
| **优先级** | P2 |
| **依赖** | ISSUE-001、ISSUE-004 |
| **改动文件** | `tests/dbmgr/test_mysql_cnf_template.py`（新建） |

**任务描述**：

用固定 `resolved_params` fixture，对 Jinja2 模板做渲染快照或关键行断言，防止回归。

**验收标准**：

- [ ] 5.7 / 8.0 各至少 2 个场景（binlog on/off）通过测试。

---

## M3：前端 Profile 驱动（可选，M2 之后）

### ISSUE-010 API 返回 Profile default_params 详情

| 项 | 内容 |
|----|------|
| **优先级** | P2 |
| **依赖** | ISSUE-003 |
| **改动文件** | `apps/dbmgr/views.py` 或 deploy API |

**任务描述**：

版本档案下拉变更时，前端可获取完整 `default_params.config` 用于填表与预览。

---

### ISSUE-011 部署表单按 Profile 加载默认值

| 项 | 内容 |
|----|------|
| **优先级** | P2 |
| **依赖** | ISSUE-010 |
| **改动文件** | `templates/dbmgr/deploy_job_list.html` |

**任务描述**：

选择 `mysql-8.0.45` 后，预览区与可编辑项反映 8.0 默认参数；保留端口/binlog/gtid 等实例级覆盖。

---

## M4：版本扩展（远期）

### ISSUE-012 MySQL 8.4 Profile + 兼容性验证

依赖 M1～M2；复制 8.0 样板，更新 major、介质、废弃参数清单。

### ISSUE-013 playbook_variant 任务拆分

当 install/initialize 步骤跨版本差异增大时，`site.yml` 使用 `include_tasks: install_{{ variant }}.yml`。

### ISSUE-014 Profile 在线管理（DbDeployVersionProfile 表）

见 [db-deploy-design.md](./db-deploy-design.md) §9.3.1 第二期触发条件。

---

## Issue 状态板

| ID | 标题 | 状态 |
|----|------|------|
| ISSUE-001 | 抽取 my.cnf Jinja2 模板 | 待办 |
| ISSUE-002 | site.yml 支持 cnf_template | 待办 |
| ISSUE-003 | 8.0 Profile | **已完成** |
| ISSUE-004 | my.cnf.j2 8.0 条件分支 | 待办（暂由 site.yml 内联分支） |
| ISSUE-005 | install 8.0 兼容 | **已完成** |
| ISSUE-006 | 文档同步 | 部分完成 |
| ISSUE-007 | finalize 按 major 收敛 | 待办 |
| ISSUE-008～014 | 见上文 | backlog / 待办 |

---

## 变更记录

| 版本 | 日期 | 说明 |
|------|------|------|
| v0.1 | 2026-06-14 | 初稿：M1～M4 Issue 列表与验收标准 |
| v0.3 | 2026-06-24 | 对齐 8.0.45 已上线；更新 Issue 状态板 |
