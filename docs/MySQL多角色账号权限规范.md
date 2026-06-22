# MySQL 多角色账号标准化权限配置文档

## 目录

1. 通用规范说明
2. 应急超级 DBA 账号（dba_admin）
3. 高权限运维 DBA 账号（dba_ops）
4. 业务日常运维账号（ops_xxx）
5. 数据库监控只读账号（dbmonitor）
6. 备份专用账号（backup）
7. 主从复制同步账号（repl）
8. 数据导出只读账号（data_export）

------

## 1. 通用规范说明

### 1.1 账号命名规范

1. 全部小写，下划线分隔，禁用中文、大写、特殊符号；
2. 业务运维账号统一格式：`ops_业务标识`，示例：`ops_order`、`ops_user`、`ops_goods`。

### 1.2 访问 IP 管控规范

1. 生产环境不允许使用 `%` 任意地址登录，优先限制内网网段 `10.%`；
2. 应急高权限账号建议仅放行堡垒机固定单 IP，缩小访问范围；
3. 所有账号设置高强度密码，定期轮换。

### 1.3 权限设计原则

1. 最小权限原则，按业务用途拆分独立账号，不混用；
2. 只读类账号不分配任何 DML/DDL 修改权限；
3. SUPER、FILE、CREATE USER 等高风险权限仅分配给应急账号。

------

## 2. 应急超级 DBA 账号

### 2.1 账号信息

账号：`dba_admin`@`10.%`

使用场景：故障应急处理、实例参数在线调整、账号全量管理、删库、数据迁移等高风险操作；仅专人保管，日常业务操作禁止使用。

### 2.2 完整授权 SQL

```
-- 创建账号（MySQL8.0 要求CREATE USER与GRANT分离）
CREATE USER IF NOT EXISTS dba_admin@'10.%' IDENTIFIED BY '强密码';

-- 全局管控权限
GRANT PROCESS, RELOAD, SUPER,
CREATE USER, DROP USER, ALTER USER, CREATE ROLE, DROP ROLE, GRANT OPTION,
REPLICATION CLIENT, REPLICATION SLAVE,
BACKUP ADMIN,
FILE
ON *.* TO dba_admin@'10.%';

FLUSH PRIVILEGES;
```

### 2.3 权限补充说明

1. `BACKUP ADMIN`：MySQL8.0 专属权限，物理热备无需依赖 SUPER；
2. `FILE` 按需开启，仅存在 `LOAD DATA` / `SELECT ... INTO OUTFILE` 文件导出场景时保留，无需求建议移除；
3. 包含 SUPER、RELOAD、账号管理等高危险权限，严格限制登录人群。

------

## 3. 高权限运维 DBA 账号

### 3.1 账号信息

账号：`dba_ops`@`10.%`

使用场景：DBA 日常全量运维、批量建表、触发器 / 存储过程维护、分配普通业务账号、主从集群搭建。

### 3.2 完整授权 SQL

```
CREATE USER IF NOT EXISTS dba_ops@'10.%' IDENTIFIED BY '强密码';

GRANT PROCESS, REPLICATION CLIENT, REPLICATION SLAVE, SHOW DATABASES,
SELECT, INSERT, UPDATE, DELETE, CREATE, DROP, ALTER, INDEX,
CREATE VIEW, SHOW VIEW, CREATE ROUTINE, ALTER ROUTINE, EXECUTE,
RELOAD, REFERENCES, CREATE TEMPORARY TABLES, LOCK TABLES,
CREATE USER, EVENT, TRIGGER 
ON *.* TO 'dba_ops'@'10.%' WITH GRANT OPTION;

FLUSH PRIVILEGES;
```

### 3.3 权限补充说明

1. 携带 `WITH GRANT OPTION`，可自主为业务运维账号分配权限；
2. 全局拥有全量 DDL/DML 操作权限，可操作实例下所有业务库。

------

## 4. 业务日常运维账号

### 4.1 账号信息

模板账号：`ops_xxx`@`10.%`

示例账号：`ops_order`、`ops_user`、`ops_goods`、`ops_wuliu`

使用场景：单一业务库日常维护、新增索引、调整表结构、业务数据增删改查、存储过程维护。

### 4.2 完整授权 SQL

```
CREATE USER IF NOT EXISTS ops_xxx@'10.%' IDENTIFIED BY '强密码';

-- 全局基础状态查看权限
GRANT PROCESS, REPLICATION CLIENT, REPLICATION SLAVE, SHOW DATABASES 
ON *.* TO ops_xxx@'10.%';

-- 指定业务库操作权限
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, DROP, ALTER, INDEX, 
CREATE VIEW, SHOW VIEW, CREATE ROUTINE, ALTER ROUTINE, EXECUTE 
ON app_db.* TO ops_xxx@'10.%';

FLUSH PRIVILEGES;
```

### 4.3 权限补充说明

1. 仅可操作配置的单一业务库，无跨库操作权限；
2. 无账号创建、RELOAD、FILE、SUPER 等高风险管控权限。

------

## 5. 数据库监控只读账号

### 5.1 账号信息

账号：`dbmonitor`@`10.%`

使用场景：Prometheus/Zabbix/Grafana 指标采集、查看实例会话、监控主从延迟，仅只读，无任何数据修改能力。

### 5.2 完整授权 SQL

```
CREATE USER IF NOT EXISTS dbmonitor@'10.%' IDENTIFIED BY '强密码';

-- 全局监控状态权限
GRANT PROCESS, REPLICATION CLIENT, SHOW DATABASES 
ON *.* TO dbmonitor@'10.%';

-- 业务库只读查询权限
GRANT SELECT ON app_db.* TO dbmonitor@'10.%';

FLUSH PRIVILEGES;
```

### 5.3 权限补充说明

无写入、建表、锁表、DDL 权限，安全等级最高，用于自动化监控程序。

------

## 6. 备份专用账号

### 6.1 账号信息

账号：`backup`@`10.%`

使用场景：mysqldump 逻辑备份、定时全量 / 增量备份，完整导出视图、触发器、事件。

### 6.2 完整授权 SQL

```
CREATE USER IF NOT EXISTS backup@'10.%' IDENTIFIED BY '强密码';

-- 业务库读、锁表、对象导出权限
GRANT SELECT,LOCK TABLES,SHOW VIEW,EVENT,TRIGGER 
ON business.* TO backup@'10.%';

-- 全局会话、binlog位点查看权限
GRANT PROCESS,REPLICATION CLIENT 
ON *.* TO backup@'10.%';

FLUSH PRIVILEGES;
```

### 6.3 权限补充说明

包含 `LOCK TABLES`，满足 mysqldump 一致性备份需求；无任何数据修改、删除权限。

------

## 7. 主从复制同步账号

### 7.1 账号信息

账号：`repl`@`10.%`

使用场景：主从 IO 线程拉取 binlog，搭建异步、半同步、多源复制集群。

### 7.2 完整授权 SQL

```
CREATE USER IF NOT EXISTS repl@'10.%' IDENTIFIED BY '强密码';

GRANT REPLICATION SLAVE, REPLICATION CLIENT 
ON *.* TO repl@'10.%';

FLUSH PRIVILEGES;
```

### 7.3 权限补充说明

1. `REPLICATION SLAVE` 核心权限，允许从库拉取二进制日志；
2. `REPLICATION CLIENT` 配套监控权限，用于脚本查看主从位点与延迟；
3. 无任何业务表读写权限。

------

## 8. 数据导出只读账号

### 8.1 账号信息

账号：`data_export`@`10.%`

使用场景：运营报表数据导出、数仓 ETL 只读拉取数据；不含锁表权限，避免阻塞线上业务。

### 8.2 完整授权 SQL

```
CREATE USER IF NOT EXISTS data_export@'10.%' IDENTIFIED BY '强密码';

-- 全局库列表、会话查看权限
GRANT PROCESS, SHOW DATABASES 
ON *.* TO data_export@'10.%';

-- 业务库只读查询、视图访问权限
GRANT SELECT, SHOW VIEW 
ON business_db.* TO data_export@'10.%';

FLUSH PRIVILEGES;
```

### 8.3 权限补充说明

不含 `LOCK TABLES`、DDL、DML 写入权限，杜绝导出操作锁表阻塞业务。

------

### 下载使用说明

复制全部文本，新建文件，命名为 `MySQL多角色账号权限规范.md`，使用 Typora、VS Code、Obsidian、记事本均可直接打开保存下载。