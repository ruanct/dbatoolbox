# MySQL 多版本部署 — 实施任务清单（Issue 列表）

> **里程碑 M1**：`my.cnf` Jinja2 化 + MySQL 8.0 Profile 样板  
> **关联规范**：[db-deploy-mysql-profile-spec.md](./db-deploy-mysql-profile-spec.md)  
> **总体设计**：[db-deploy-design.md](./db-deploy-design.md)

---

## 里程碑概览

```
M1  my.cnf Jinja2 化 + 8.0 Profile 样板     ← 当前
M2  finalize 按 major 收敛 + 创建校验
M3  前端 Profile 驱动表单与预览
M4  8.4 扩展与 playbook_variant 拆分
```

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

### ISSUE-003 新增 MySQL 8.0.36 Profile 样板 YAML

| 项 | 内容 |
|----|------|
| **优先级** | P0 |
| **依赖** | 无（可与 ISSUE-001 并行） |
| **改动文件** | 新增 `deploy/profiles/mysql/8.0.36.yml` |

**任务描述**：

按 [db-deploy-mysql-profile-spec.md](./db-deploy-mysql-profile-spec.md) §6.2 新增 8.0 Profile；`package_filename`、`media_base_url` 与内网软件库核对。

**验收标准**：

- [ ] `profile_loader.list_profiles()` 能列出 `mysql-8.0.36`；
- [ ] `major_version: "8.0"`，`default_params.config` 含 8.0 差异项（`caching_sha2_password`、`utf8mb4_0900_ai_ci`）；
- [ ] 首版可设 `status: disabled`，待介质就绪后改 `enabled`；
- [ ] `playbook_variant: install_8_0_tgz` 已声明（安装逻辑可与 5.7 共用，见 ISSUE-005）。

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

### ISSUE-005 确认 install 步骤对 8.0 包名兼容

| 项 | 内容 |
|----|------|
| **优先级** | P1 |
| **依赖** | ISSUE-003 |
| **改动文件** | `deploy/playbooks/mysql/standalone/site.yml`（可能无需改） |

**任务描述**：

验证 8.0 tgz 解压后目录模式仍为 `mysql-8.0.*`，现有 `find` + 软链接逻辑是否通用；若不通用，按 `playbook_variant` 引入 `include_tasks`。

**验收标准**：

- [ ] 8.0 安装包在测试机可完成 install 步骤（或文档记录差异与后续改动点）；
- [ ] precheck major 版本校验对 8.0 生效。

---

### ISSUE-006 文档同步

| 项 | 内容 |
|----|------|
| **优先级** | P2 |
| **依赖** | ISSUE-001～005 完成后 |
| **改动文件** | `docs/db-deploy-mysql5.7-step-action.md`、本文件勾选状态 |

**任务描述**：

更新步骤 4「配置文件」说明：由「site.yml 内联 copy」改为「Jinja2 模板渲染」；补充 8.0 Profile 引用。

**验收标准**：

- [ ] 文档描述与代码一致；
- [ ] M1 相关 Issue 验收项已勾选。

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

选择 `mysql-8.0.36` 后，预览区与可编辑项反映 8.0 默认参数；保留端口/binlog/gtid 等实例级覆盖。

---

## M4：版本扩展（远期）

### ISSUE-012 MySQL 8.4 Profile + 兼容性验证

依赖 M1～M2；复制 8.0 样板，更新 major、介质、废弃参数清单。

### ISSUE-013 playbook_variant 任务拆分

当 install/initialize 步骤跨版本差异增大时，`site.yml` 使用 `include_tasks: install_{{ variant }}.yml`。

### ISSUE-014 Profile 在线管理（DbDeployVersionProfile 表）

见 [db-deploy-design.md](./db-deploy-design.md) §9.3.1 第二期触发条件。

---

## 建议实施顺序（M1 开工）

```
Week 1
  ISSUE-001  抽取 my.cnf.j2
  ISSUE-003  新增 8.0.36.yml（status: disabled）
  ISSUE-004  模板 8.0 分支
  ISSUE-002  cnf_template 变量

Week 2
  ISSUE-005  8.0 install 兼容性验证（测试机）
  ISSUE-006  文档同步
  ISSUE-007  finalize 收敛（可并入 M1 尾声）
```

---

## Issue 状态板（手动维护）

| ID | 标题 | 状态 |
|----|------|------|
| ISSUE-001 | 抽取 my.cnf Jinja2 模板 | 待办 |
| ISSUE-002 | site.yml 支持 cnf_template | 待办 |
| ISSUE-003 | 新增 8.0.36 Profile 样板 | 待办 |
| ISSUE-004 | my.cnf.j2 8.0 条件分支 | 待办 |
| ISSUE-005 | install 步骤 8.0 兼容 | 待办 |
| ISSUE-006 | 文档同步 | 待办 |
| ISSUE-007 | finalize 按 major 收敛 | 待办 |
| ISSUE-008 | 创建任务 config 校验 | 待办 |
| ISSUE-009 | my.cnf 渲染测试 | 待办 |
| ISSUE-010 | API 返回 default_params | 待办 |
| ISSUE-011 | 前端 Profile 驱动表单 | 待办 |
| ISSUE-012 | 8.4 Profile |  backlog |
| ISSUE-013 | playbook_variant 拆分 |  backlog |
| ISSUE-014 | Profile 在线管理 |  backlog |

---

## 变更记录

| 版本 | 日期 | 说明 |
|------|------|------|
| v0.1 | 2026-06-14 | 初稿：M1～M4 Issue 列表与验收标准 |
| v0.2 | 2026-06-14 | 移除 9.0 相关 Issue 与规划 |
