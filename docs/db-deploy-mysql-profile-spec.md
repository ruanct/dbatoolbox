# MySQL Version Profile YAML 规范（草案）

> **状态**：草案 v0.1  
> **适用范围**：dbatoolbox 实例部署 — MySQL 单实例（`mysql_standalone`）  
> **关联文档**：[db-deploy-design.md](./db-deploy-design.md)、[db-deploy-mysql5.7-step-action.md](./db-deploy-mysql5.7-step-action.md)、[db-deploy-mysql-multiversion-tasks.md](./db-deploy-mysql-multiversion-tasks.md)

---

## 1. 目标

为 MySQL **5.7 / 8.0 / 8.4** 等多版本部署提供统一的「版本参数模板」规范：

1. 每个版本一份 Profile YAML，维护介质信息与默认运行参数；
2. 部署时由 `profile_loader` 加载模板，与用户表单参数合并，生成 `resolved_params`；
3. Ansible 根据 `resolved_params` 渲染 `my.cnf`（Jinja2 模板），不在 Playbook 内硬编码配置正文。

---

## 2. 文件位置与命名

```
deploy/
├── profiles/
│   └── mysql/
│       ├── 5.7.44.yml          # 已上线
│       ├── 8.0.36.yml          # 样板（待新增）
│       └── 8.4.xx.yml          # 后续
├── templates/
│   └── mysql/
│       ├── my.cnf.j2           # 通用模板（推荐，按 major 条件分支）
│       └── my-8.0.cnf.j2       # 可选：major 专用模板
└── playbooks/
    └── mysql/standalone/site.yml
```

**命名约定**：

| 规则 | 示例 |
|------|------|
| 文件名 | `{minor 简写}.yml` 或 `{major}.{minor}.yml`，与目录引擎一致 |
| `profile_code` | 全局唯一，建议 `mysql-{major}.{minor}`，如 `mysql-8.0.36` |
| `playbook_variant` | `install_{major 下划线}_tgz`，如 `install_8_0_tgz` |

加载逻辑见 `apps/dbmgr/profile_loader.py`：`deploy/profiles/**/*.yml` 递归扫描，`profile_code` 为键。

---

## 3. 顶层字段说明

### 3.1 必填字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `engine` | string | 固定 `mysql` |
| `profile_code` | string | 唯一标识，Job 与 API 引用 |
| `display_name` | string | 下拉展示名 |
| `major_version` | string | major 版本，用于 precheck 二进制校验，如 `"5.7"`、`"8.0"` |
| `minor_version` | string | minor 版本，如 `"44"`、`"36"` |
| `status` | string | `enabled` / `disabled` / `deprecated` |
| `supported_job_types` | list | 如 `[mysql_standalone]` |
| `install_method` | string | 当前支持 `tar_http` |
| `package_ref` | string | 介质逻辑名 |
| `media_base_url` | string | 内网 HTTP 根路径（可被环境变量覆盖） |
| `package_filename` | string | 安装包文件名 |
| `playbook_variant` | string | Playbook 安装变体标识 |
| `default_params` | object | 版本默认参数（见 §4） |

### 3.2 推荐字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `supported_os_rules` | list | OS 白名单规则，如 `{family: centos, min_major: 7}` |
| `supported_arch` | list | CPU 架构，默认 `x86_64` |
| `min_memory_gb` | number | 预检查最低内存（后续实现） |
| `media_subdir` | string | 介质子目录（可选） |
| `package_checksum` | string | sha256（可选，后续校验） |
| `cnf_template` | string | **新增**：相对 `deploy/templates/` 的 Jinja2 路径，默认 `mysql/my.cnf.j2` |
| `remark` | string | 备注 |

### 3.3 环境变量覆盖

| 变量 | 作用 |
|------|------|
| `DEPLOY_MYSQL_MEDIA_BASE_URL` | 覆盖 YAML 中 `media_base_url`（见 `profile_loader._apply_media_env_override`） |

---

## 4. `default_params` 结构

`default_params` 分块与 `resolved_params` 一致，部署时与用户 `params` 做 **深度合并**（`profile_loader._deep_merge`）。

### 4.1 合并优先级

```
Profile.default_params
    < 用户表单 params（Job.params）
    < finalize_mysql_deploy_params() 派生项（路径、server_id、binlog/gtid 收敛）
```

**原则**：

- 用户可覆盖 Profile 默认值；
- 路径类（`datadir`、`cnf_path`、`log_error` 等）由程序按端口派生，**不应**在表单中手填覆盖；
- `enable_gtid=true` 时强制 `enable_binlog=true`（前后端一致）。

### 4.2 各块职责

#### `cmdb` — 写入 CMDB / 台账

| 字段 | 必填 | 说明 |
|------|------|------|
| `port` | 推荐 | 默认监听端口 |
| `charset` | 否 | 实例字符集元数据 |
| `topology` | 否 | 默认 `standalone` |
| `role` | 否 | 默认 `master` |

#### `install` — 安装路径（仅版本无关项）

| 字段 | 说明 |
|------|------|
| `basedir` | 二进制目录，当前固定 `/usr/local/mysql` |

> `instance_root`、`datadir`、`socket`、`cnf_path`、`log_error` 等由 `build_mysql_install_paths(port)` 派生，**不要**写在 Profile 里。

#### `config` — my.cnf 运行参数（版本差异核心）

**全版本通用字段（建议）**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `enable_binlog` | bool | 是否开启 Binlog |
| `enable_gtid` | bool | 是否开启 GTID |
| `binlog_format` | string | 默认 `ROW` |
| `character_set` | string | `character-set-server` |
| `collation` | string | `collation-server` |
| `innodb_buffer_pool_size` | string | 如 `1G` |
| `max_connections` | int | 最大连接数 |
| `extra_cnf_lines` | string | **可选**，高级用户追加原始配置行 |

**按 major 区分字段**：

| 字段 | 5.7 | 8.0+ | 说明 |
|------|-----|------|------|
| `default_authentication_plugin` | `mysql_native_password` | `caching_sha2_password` | 写入 my.cnf |
| `collation` 推荐值 | `utf8mb4_unicode_ci` | `utf8mb4_0900_ai_ci` | 8.0 默认排序规则不同 |
| `sql_mode` | 可显式配置 | 可显式配置 | 按环境需要 |

**禁止放入 `config` 的字段**（由派生或 Playbook 变量提供）：

- `basedir`、`datadir`、`port`、`socket`、`log-error` 路径
- `server_id`、`log_bin` 路径（binlog 开启时由 `finalize_mysql_deploy_params` 生成）

#### `credentials` — 不在 Profile 中写密码

Profile **不得**包含真实密码。仅可文档化推荐账号名；密码、运维账号由部署表单提交。

---

## 5. `cnf_template` 与 Jinja2 渲染

### 5.1 设计目标

将 `site.yml` configure 步骤中的内联 `content: |` 迁出为独立模板，避免每增一个版本就改 Playbook。

### 5.2 模板路径约定

Profile 可选指定：

```yaml
cnf_template: mysql/my.cnf.j2
```

未指定时，Playbook 默认使用 `deploy/templates/mysql/my.cnf.j2`。

### 5.3 模板可用变量

Ansible `template` 模块渲染时，建议传入与当前 `site.yml` vars 一致的上下文：

| 变量 | 来源 |
|------|------|
| `d` | `deploy`（即完整 `resolved_params`） |
| `mysql_basedir`、`mysql_datadir`、`mysql_port`、`mysql_socket` | Playbook vars |
| `mysql_log_error`、`mysql_log_bin`、`mysql_server_id` | Playbook vars |
| `mysql_enable_binlog`、`mysql_enable_gtid` | `d.config` 收敛后 |

模板内通过 `d.config.*` 读取合并后的配置项；条件块（binlog/gtid）使用 `mysql_enable_binlog` / `mysql_enable_gtid`。

### 5.4 major 分支策略

**推荐（MVP）**：单文件 `my.cnf.j2` + Jinja2 `{% if d.profile.major_version == '8.0' %}` 控制差异行。

**可选（后期）**：Profile 指定不同模板文件，如 `mysql/my-5.7.cnf.j2`、`mysql/my-8.0.cnf.j2`。

---

## 6. Profile 样例

### 6.1 现有：MySQL 5.7.44

见 `deploy/profiles/mysql/5.7.44.yml`。

### 6.2 样板：MySQL 8.0.36（草案，实施时落文件）

```yaml
engine: mysql
profile_code: mysql-8.0.36
display_name: MySQL 8.0.36（glibc2.17 tgz）
major_version: "8.0"
minor_version: "36"
status: enabled
supported_os_rules:
  - family: centos
    min_major: 7
  - family: rhel
    min_major: 7
  - family: anolis
    min_major: 7
  - family: alinux
    min_major: 3
supported_arch:
  - x86_64
supported_job_types:
  - mysql_standalone
install_method: tar_http
package_ref: mysql-8.0.36-linux-glibc2.17-x86_64
media_base_url: "http://10.32.14.211/soft/mysql/tgz/"
package_filename: "mysql-8.0.36-linux-glibc2.17-x86_64.tar.gz"
playbook_variant: install_8_0_tgz
cnf_template: mysql/my.cnf.j2
min_memory_gb: 2
default_params:
  cmdb:
    port: 3306
    charset: utf8mb4
    topology: standalone
    role: master
  install:
    basedir: "/usr/local/mysql"
  config:
    enable_binlog: true
    enable_gtid: true
    binlog_format: ROW
    character_set: utf8mb4
    collation: utf8mb4_0900_ai_ci
    innodb_buffer_pool_size: "1G"
    max_connections: 500
    default_authentication_plugin: caching_sha2_password
remark: "8.0 样板 Profile；介质文件名以软件库实际为准"
```

> **说明**：`package_filename`、`media_base_url` 需与内网软件库对齐；首版可将 `status: disabled` 直到介质与测试就绪。

---

## 7. 参数 → 执行链路

```
deploy/profiles/mysql/*.yml
        ↓ load_profile(profile_code)
profile.default_params + user_params
        ↓ _deep_merge()
        ↓ finalize_mysql_deploy_params()   # apps/dbmgr/deploy_constants.py
resolved_params（快照写入 DbDeployJob）
        ↓ extra-vars deploy=@json
ansible-playbook site.yml --tags configure
        ↓ template 模块
目标机 {{ cnf_path }}  # 如 /data/mysql3306/my.cnf
```

Ansible **只读 `resolved_params` 快照**，不重新读 YAML，保证任务执行期间参数一致。

---

## 8. 版本差异速查（配置模板设计参考）

| 维度 | 5.7 | 8.0 | 8.4（待补充） |
|------|-----|-----|---------------|
| 默认认证插件 | mysql_native_password | caching_sha2_password | 查阅发行说明 |
| 推荐 collation | utf8mb4_unicode_ci | utf8mb4_0900_ai_ci | 可能延续 8.0 |
| 初始化命令 | mysqld --initialize-insecure | 同左 | 实施时验证 |
| GTID | 可选，常用 ON | 推荐 ON | 待验证 |
| 废弃参数 | — | 部分 5.7 参数已移除 | 模板中避免写入 |

详细对比见 [MySQL8.0_vs_5.7_版本全对比.md](./MySQL8.0_vs_5.7_版本全对比.md)。

---

## 9. 校验规则（实施清单）

创建部署任务时建议校验：

1. `profile_code` 存在且 `status=enabled`；
2. `job_type` 在 `supported_job_types` 内；
3. `config` 中不含当前 major 不支持的键（后期 `finalize` 或 Schema 过滤）；
4. `enable_gtid` 隐含 `enable_binlog`；
5. binlog 开启时必须有 `connect_host` 以生成 `server_id`。

---

## 10. 后期扩展（本期不做）

| 项 | 说明 |
|----|------|
| `DbDeployVersionProfile` 表 | Profile 在线维护 |
| `GET /deploy/schema/` | 按 Profile 返回动态表单字段 |
| `package_checksum` 校验 | 下载后 sha256 比对 |
| 8.4 Profile | 在 8.0 样板跑通后复制扩展 |

---

## 11. 变更记录

| 版本 | 日期 | 说明 |
|------|------|------|
| v0.1 | 2026-06-14 | 初稿：Profile 规范 + cnf_template + 8.0 样板 |
| v0.2 | 2026-06-14 | 移除 9.0 相关规划 |
