# MySQL 8.0 VS MySQL 5.7 全维度异同对比文档

## 目录

1. 账号、权限、认证、安全体系
2. 字符集、排序规则、SQL 语法特性
3. 数据字典 & mysql 系统库
4. InnoDB 存储引擎
5. Binlog & 主从复制
6. 事务、隔离级别、锁机制
7. 性能、内存、并发调度参数
8. 监控体系：sys /performance_schema
9. 高可用：半同步、组复制 MGR
10. 备份、崩溃恢复、运维特性
11. 5.7 存在、8.0 彻底废弃 / 不兼容功能
12. 5.7 与 8.0 完全无改动的核心功能

------

## 一、账号、权限、认证、安全体系

### 相同点

1. 基础权限体系：`SELECT/INSERT/UPDATE/DELETE/CREATE/DROP/ALTER/REPLICATION SLAVE` 等权限含义不变
2. 支持 `REQUIRE SSL` 加密连接、密码过期策略、密码长度限制
3. 支持 `SUPER` 超级权限、`GRANT OPTION` 授权传递

### 不同点

1. 默认认证插件
   - MySQL 5.7：`mysql_native_password`
   - MySQL 8.0：`caching_sha2_password`，5.7 客户端 / 从库无法直连，创建账号需手动指定 `mysql_native_password`
2. 用户底层存储
   - 5.7：`mysql.user` 表明文字段存储密码哈希
   - 8.0：废弃旧密码字段，基于事务型数据字典统一管理
3. 角色 Role 权限
   - 5.7：无角色管理功能
   - 8.0：原生支持 `CREATE ROLE / SET ROLE`，批量分配回收权限
4. 密码安全策略增强
   - 8.0 新增：密码复用限制、历史密码黑名单、强复杂度校验
5. super_read_only 行为差异
   - 5.7：开启 super_read_only 后，SUPER 权限账号仍可写入从库
   - 8.0：`super_read_only=ON` 时，即使 SUPER 用户也禁止写入从库
6. 新增细分专用权限
   - 8.0：`BACKUP ADMIN`、`GROUP REPLICATION ADMIN`，拆分 SUPER 高危权限
7. 授权语法强制规范
   - 5.7：`GRANT ... TO user IDENTIFIED BY` 可一步创建账号
   - 8.0：禁止 GRANT 语句内嵌创建用户，必须 `CREATE USER` + `GRANT` 两步执行

------

## 二、字符集、排序规则、SQL 语法特性

### 相同点

1. 支持 utf8、utf8mb4、latin1 字符集
2. 四大事务隔离级别、事务回滚 / 提交基础语法通用
3. 基础 DML/DDL 语句 `SELECT/INSERT/ALTER TABLE` 语法兼容

### 不同点

1. 默认字符集
   - 5.7：默认 `latin1`
   - 8.0：默认 `utf8mb4`，默认排序规则 `utf8mb4_0900_ai_ci`
2. utf8 语义定义变更
   - 5.7：`utf8` 等价 utf8mb3（仅 3 字节，不支持 emoji 表情）
   - 8.0：`utf8` 直接等价 `utf8mb4`，彻底移除 utf8mb3
3. 窗口函数
   - 5.7：无窗口函数（ROW_NUMBER/RANK/OVER）
   - 8.0：完整支持标准 SQL 窗口函数
4. CTE 公共表达式
   - 5.7：仅简单临时子查询，不支持递归 WITH
   - 8.0：支持普通 CTE + 递归 CTE
5. JSON 能力大幅增强
   - 5.7：仅基础 JSON 函数，无 JSON 专用表、索引优化弱
   - 8.0：支持 JSON_TABLE、JSON 聚合函数、原生 JSON 索引优化
6. 隐藏列 Invisible Column
   - 5.7：无该特性
   - 8.0：支持建表定义隐藏字段，业务平滑兼容改造
7. 语法简写警告
   - 8.0 不推荐简写 `DESC`，标准写法 `DESCRIBE`

------

## 三、数据字典 & mysql 系统库

### 相同点

- 均存在 `mysql` 系统库，用于存储账号、权限、日志元数据

### 不同点

1. 8.0 全新事务型数据字典（最大改动）
   - 5.7：表元数据存储 `.frm` 文件 + 混合系统表，非事务，DDL 崩溃易损坏表结构
   - 8.0：全部元数据存入 InnoDB 内部系统表，无 frm 文件，DDL 支持原子回滚
2. 系统表存储引擎
   - 5.7：mysql 系统库混合 MyISAM + InnoDB
   - 8.0：mysql 库所有系统表统一改为 InnoDB
3. 废弃文件类型
   - 8.0 彻底移除 `.frm`、`.par`、`.trn`、`.trg` 物理文件
4. 信息架构查询性能
   - 8.0 `INFORMATION_SCHEMA` 查询性能大幅提升，不再扫描磁盘元文件

------

## 四、InnoDB 存储引擎

### 相同点

1. 默认存储引擎为 InnoDB，支持事务、MVCC、行锁、外键约束
2. 缓冲池核心参数 `innodb_buffer_pool_size / instances / chunk_size` 底层计算逻辑完全一致
3. redo/undo 日志、自适应哈希索引 AHI、LRU 冷热淘汰链表机制保留

### 不同点

1. 自增 ID 持久化
   - 5.7：实例重启自增计数器归零，易出现主键冲突
   - 8.0：自增值持久化写入 redo 日志，重启数值不变
2. 原子 DDL
   - 5.7：大表 ALTER/DROP 崩溃极易表损坏，无事务保护
   - 8.0：CREATE/DROP/ALTER TABLE 全部原子化，执行失败自动回滚
3. Undo 表空间
   - 5.7：可关闭独立 undo 表空间
   - 8.0 强制开启独立 undo 永久表空间，不支持关闭
4. 锁与死锁检测优化
   - 8.0 事务锁粒度更精细，高并发读写冲突、死锁概率降低
5. BufferPool 启动预热
   - 8.0 多线程并行加载缓冲池，重启实例预热速度大幅提升
6. AHI 自适应哈希索引
   - 5.7 仅重启才能关闭 AHI
   - 8.0 支持在线动态开启 / 关闭 AHI
7. 完善表空间加密、页面校验加密算法升级

------

## 五、Binlog & 主从复制

### 相同点

1. 默认 binlog_format=ROW，支持 GTID、异步复制、半同步复制、多源复制
2. 复制基础权限 `REPLICATION SLAVE / REPLICATION CLIENT` 含义不变

### 不同点

1. binlog_row_image 默认策略
   - 5.7=FULL（记录行全部字段）
   - 8.0=MINIMAL（仅记录变更字段，binlog 体积更小）
2. Binlog 原生压缩
   - 5.7 无内置压缩能力
   - 8.0 支持 `binlog_transaction_compression`，节省磁盘与网络带宽
3. 并行回放复制模型
   - 5.7：库级并行 `DATABASE`，单库高并发无并行收益
   - 8.0：默认逻辑时钟 `LOGICAL_CLOCK`，事务粒度并行，大幅降低主从延迟
4. 崩溃安全复制
   - 5.7 需手动开启 `relay_log_recovery=ON`，依赖文本 info 文件，宕机位点易错乱
   - 8.0 默认开启崩溃安全复制，废弃[master.info/relay-log.info](https://link.wtturl.cn/?target=https%3A%2F%2Fmaster.info%2Frelay-log.info&scene=im&aid=497858&lang=zh)文本文件，位点持久化 InnoDB 系统表
5. GTID 相关
   - 5.7 默认关闭 GTID，修改 gtid_purged 必须重启实例
   - 8.0 默认开启 GTID，支持在线动态修改 gtid_purged
6. 半同步复制
   - 5.7 外置 so 插件，默认等待策略 `AFTER_COMMIT`，存在丢数据风险
   - 8.0 半同步内置内核，默认安全策略 `AFTER_SYNC`
7. 多源复制通道隔离
   - 5.7 无独立 channel 隔离，全局统一启停
   - 8.0 按复制通道独立管理，单独启停、单独查看状态、独立并行参数
8. Binlog 文件名格式
   - 5.7：主机名 - bin.000001
   - 8.0 统一命名 binlog.000001，不再绑定主机名

------

## 六、事务、隔离级别、锁机制

### 相同点

1. 四大隔离级别：READ UNCOMMITTED / READ COMMITTED / REPEATABLE READ / SERIALIZABLE
2. 默认隔离级别均为 REPEATABLE READ
3. 共享读锁、排他写锁、意向锁底层机制完全一致

### 不同点

1. MVCC Undo 清理优化：8.0 Purge 多线程并行清理 undo 日志，长事务回滚性能提升
2. 事务冲突调度优化：8.0 新增等待调度参数，减少读写阻塞、死锁概率
3. READ COMMITTED 隔离级别下索引扫描一致性逻辑优化

------

## 七、性能、内存、并发调度参数

### 相同点

`innodb_buffer_pool_size`、`sort_buffer_size`、`join_buffer_size`、`max_connections` 等参数概念完全一致

### 不同点

1. 资源分组 Resource Groups
   - 5.7：无资源隔离调度
   - 8.0 支持按 SQL 分组限制 CPU、IO 资源，隔离慢查询消耗
2. 后台线程拆分
   - 8.0 刷脏页、undo 回收、IO 读写多线程拆分，并发吞吐更高
3. 在线动态参数范围扩大
   - 8.0 支持在线调整更多全局参数，无需重启实例（如 buffer_pool_size）
4. 系统内存占用管控优化，降低 OOM 风险

------

## 八、监控体系：sys /performance_schema

### 相同点

两者均自带 performance_schema、sys 系统视图，用于慢 SQL、锁等待、线程监控

### 不同点

1. 默认开关
   - 5.7：performance_schema 默认关闭
   - 8.0：默认开启性能监控
2. 新增监控维度
   - 8.0 补充复制、并行回放、半同步、GTID 执行、资源组专属监控表
3. 内存监控精细化
   - 可精准观测 buffer pool、临时表、排序缓存内存占用明细
4. 多源复制每个通道独立监控指标

------

## 九、高可用：半同步、组复制 MGR

### 相同点

均支持异步复制、半同步复制、多源复制拓扑

### 不同点

1. 组复制 Group Replication (MGR)
   - 5.7 MGR 功能不完善、稳定性差，极少用于生产
   - 8.0 MGR 成熟稳定，支持单主 / 多主模式、内置故障检测、自动选主切换
2. 半同步配套完善度
   - 8.0 内置半同步，配套完整性能监控指标，5.7 依赖外置插件

------

## 十、备份、崩溃恢复、运维特性

### 相同点

`mysqldump`逻辑备份、xtrabackup 物理备份工具均兼容两个版本

### 不同点

1. DDL 崩溃恢复能力
   - 5.7 大表 DDL 崩溃极易损坏表
   - 8.0 原子 DDL + 事务数据字典，DDL 失败自动回滚无损坏
2. 实例重启恢复速度
   - 8.0 BufferPool 多线程预热，启动恢复耗时大幅缩短
3. 备份权限拆分
   - 8.0 新增`BACKUP ADMIN`，备份账号无需授予高危 SUPER 权限
4. 日志标准化
   - 错误日志、慢查询日志输出格式统一标准化，便于日志平台采集解析

------

## 十一、5.7 存在、8.0 彻底废弃 / 不兼容功能

1. 默认认证 `mysql_native_password` 不再作为默认插件
2. 元数据物理文件 `.frm`/`.trg`/`.trn`/`.par` 全部移除
3. 废弃 `GRANT ... IDENTIFIED BY` 一步创建用户语法
4. mysql 系统库 MyISAM 表全部移除，统一 InnoDB
5. 废弃 utf8mb3，`utf8` 仅代表 4 字节 utf8mb4
6. 查询缓存 query_cache（5.7 已标记弃用，8.0 直接删除相关参数与逻辑）
7. 废弃老旧弱 SSL 加密套件、低版本加密算法

------

## 十二、5.7 与 8.0 完全一致、无改动核心功能

1. InnoDB 行锁、MVCC、外键约束底层逻辑
2. 基础 DML、简单单表 DDL 语句语法
3. 异步主从复制基础链路逻辑
4. GTID 核心事务 ID 机制（仅默认开关、运维参数存在差异）
5. 慢查询日志、错误日志、二进制日志基础采集能力
6. innodb_buffer_pool 分片计算公式（chunk/instances 约束不变）
7. mysqldump、xtrabackup 主流备份工具兼容逻辑
8. 事务 ACID 基础特性、四大隔离级别定义
9. 分区表、BTree / 唯一 / 联合索引基础功能
10. 运维账号权限分类逻辑（监控 / 备份 / 复制 / 应急 DBA 账号授权规则通用）

------





# 企业生产环境 MySQL 8.0 标准配置



## 一、企业生产环境 MySQL 8.0 标准配置



### 1）账号认证插件（生产通用方案）

#### 两种主流选择

1. **首选兼容方案（绝大多数企业在用）**

sql

```
mysql_native_password
```

适用场景：Java、Python、Navicat、老旧客户端、中间件（Sharding-JDBC、MyCat），**无兼容性问题**。

创建用户语句：

sql

```
CREATE USER root@'%' IDENTIFIED WITH mysql_native_password BY 'StrongPass@123';
```

1. **纯新项目、全栈新版客户端**

sql

```
caching_sha2_password
```

这是 8.0 默认值，加密等级更高。

缺点：老版本数据库客户端会直接连接失败，运维麻烦，传统业务很少直接用默认值。

> 总结：国内互联网、传统政企数据库，90% 都会把 root 业务账号强制改成 `mysql_native_password`。

------

## 2）企业通用 sql_mode（生产标配）

### 标准版（最常用，兼顾稳定 + 严格，适配 5.7 迁移过来的业务）

ini

```
sql_mode = "ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION"
```

### 参数拆解

表格

|            参数            |                       作用                        |
| :------------------------: | :-----------------------------------------------: |
|     ONLY_FULL_GROUP_BY     |  group by 必须包含所有非聚合字段，杜绝不规范 SQL  |
|    STRICT_TRANS_TABLES     |      严格模式，非法数据拒绝写入，防止脏数据       |
|      NO_ZERO_IN_DATE       |        禁止 `2025-00-01` 这类非法月份日期         |
|        NO_ZERO_DATE        | 禁止写入 `0000-00-00` 零日期（迁移 5.7 最大坑点） |
| ERROR_FOR_DIVISION_BY_ZERO |             除零直接报错，不返回 null             |
|   NO_ENGINE_SUBSTITUTION   |        引擎不存在时报错，不会自动替换引擎         |

### 极简宽松版（老旧历史业务兼容，极少推荐）

去掉日期限制，允许零日期，专门给从 5.7 迁移、大量脏数据老系统：

ini

```
sql_mode = "ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION"
```

### 绝对不要做

不要清空 sql_mode，会造成数据混乱，不符合企业数据库规范。

------

## 二、my.cnf 完整片段（可直接复制）

ini

```
[mysqld]
# 字符集
character-set-server=utf8mb4
collation-server=utf8mb4_general_ci

# SQL严格模式
sql_mode = ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION

# 认证方式（全局默认，新建用户自动生效）
default_authentication_plugin=mysql_native_password
```

------

## 三、补充运维经验

1. 5.7 迁移 8.0 必改两项：

   - 修改默认认证插件为 `mysql_native_password`
   - 锁定上面这套 sql_mode，提前清理库中 `0000-00-00` 日期

2. 云数据库（阿里云 RDS、华为云 MySQL 8.0）：

   云厂商默认依然保留 

   ```
   mysql_native_password
   ```

    作为兼容选项，不会强行锁死 sha2。

3. 如果是多租户等安全等级极高的金融业务：

   可以保留默认 

   ```
   caching_sha2_password
   ```

   ，同时升级所有客户端驱动版本。







# Mysql5.7 迁移到8.0 /etc/my.cnf 完整版 (utf8mb4_general_ci)



（MySQL 8.0.45 + CentOS7 glibc2.17 生产配置）

## 适用场景

CentOS7，MySQL8.0 EL7 安装包，业务从 5.7 迁移而来，兼顾兼容性、严格模式、无字符集乱码、客户端兼容，杜绝 sha2 密码连接失败。

ini 

```
[mysqld]
# -------------------------- 基础通用配置 --------------------------
user=mysql
group=mysql
pid-file=/var/run/mysqld/mysqld.pid
socket=/var/lib/mysql/mysql.sock
port=3306
basedir=/usr/local/mysql
datadir=/data/mysql
tmpdir=/tmp

# -------------------------- 字符集与排序规则（兼容5.7迁移，无索引冲突） --------------------------
character-set-server=utf8mb4
collation-server=utf8mb4_general_ci
init_connect='SET NAMES utf8mb4'

# -------------------------- 账号认证（核心：兼容旧客户端，不使用默认sha2） --------------------------
default_authentication_plugin=mysql_native_password

# -------------------------- SQL_MODE 企业标准版（严格模式，和5.7保持一致） --------------------------
sql_mode = ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION

# -------------------------- 时区配置 --------------------------
default_time_zone = '+8:00'

# -------------------------- 连接数设置 --------------------------
max_connections=800
max_connect_errors=1000
wait_timeout=600
interactive_timeout=600

# -------------------------- InnoDB核心参数（8.0优化版） --------------------------
default_storage_engine=InnoDB
innodb_buffer_pool_size=4G
innodb_log_file_size=2G
innodb_log_buffer_size=64M
innodb_file_per_table=1
innodb_flush_log_at_trx_commit=1
sync_binlog=1

# 开启原子DDL（8.0原生特性，防止改表崩溃损坏）
innodb_ddl_log_crash_recover=ON

# 关闭自动锁升级，减少大事务锁等待
innodb_autoinc_lock_mode=2

# -------------------------- Binlog 主从必备 --------------------------
log_bin=mysql-bin
binlog_format=ROW
expire_logs_days=7
binlog_row_image=FULL

# -------------------------- 慢SQL日志 --------------------------
slow_query_log=ON
slow_query_log_file=/data/mysql/slow.log
long_query_time=1
log_queries_not_using_indexes=ON

# -------------------------- 关闭DNS反向解析，加速连接 --------------------------
skip-name-resolve

# -------------------------- 关闭宽松校验 --------------------------
explicit_defaults_for_timestamp=ON

[mysql]
socket=/var/lib/mysql/mysql.sock
default-character-set=utf8mb4

[mysqld_safe]
log-error=/data/mysql/mysql-error.log
pid-file=/var/run/mysqld/mysqld.pid
```

------

## 关键说明（贴合你当前环境）

1. **为什么不用 utf8mb4_0900_ai_ci？**

   当前是 5.7 迁移环境，使用 `utf8mb4_general_ci`，规避 ß/ss、æ/ae 字符等价造成唯一主键冲突，业务平稳过渡；新项目再切换为 0900 规则。

2. **认证插件固定 mysql_native_password**

   Navicat、JDBC、中间件无需修改客户端，不会出现 caching_sha2_password 连接报错，这是 CentOS7+MySQL8.0 企业最通用配置。

3. **glibc 适配无冲突**

   此配置纯参数，不涉及系统库，完美运行在 glibc2.17 的 CentOS7 + MySQL8.0.45 el7 版本。

4. 内存调优：

- 服务器 16G 内存：`innodb_buffer_pool_size=4G`
- 服务器 32G 内存：改为 8G
- 8G 机器改为 2G

------

## 后续执行操作

1. 创建数据目录并授权

Bash

```
mkdir -p /data/mysql
chown -R mysql:mysql /data/mysql
```

1. 初始化数据库
2. 启动服务，执行用户创建语句：

sql

```
CREATE USER root@'%' IDENTIFIED WITH mysql_native_password BY '你的强密码';
GRANT ALL PRIVILEGES ON *.* TO root@'%' WITH GRANT OPTION;
FLUSH PRIVILEGES;
```

------

## 可选版本（全新业务，纯 8.0 新项目）

如果你后续搭建全新库，我可以再给一版：

排序规则改为 `utf8mb4_0900_ai_ci` + 保留 sha2 加密两套配置文件分开使用。





# 两套配置文件



## 文件 1：my.cnf 正式版（新项目 MySQL8.0.45 + CentOS7）

### 核心设定

1. 字符集：`utf8mb4`
2. 排序规则：`utf8mb4_0900_ai_ci`（8.0 原生 UCA9 标准）
3. 认证方式：默认 `caching_sha2_password`（SHA2 高安全加密）
4. SQL_MODE 企业严格标准版
5. InnoDB、binlog、慢日志生产最优参数

Ini

```
[mysqld]
# 基础运行配置
user=mysql
group=mysql
pid-file=/var/run/mysqld/mysqld.pid
socket=/var/lib/mysql/mysql.sock
port=3306
basedir=/usr/local/mysql
datadir=/data/mysql
tmpdir=/tmp

# 字符集&排序规则（新项目标准配置）
character-set-server=utf8mb4
collation-server=utf8mb4_0900_ai_ci
init_connect='SET NAMES utf8mb4'

# 账号密码：启用8.0默认sha2加密
default_authentication_plugin=caching_sha2_password

# 严格SQL模式（行业通用标准）
sql_mode = ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION

# 时区东八区
default_time_zone = '+8:00'

# 连接管理
max_connections=800
max_connect_errors=1000
wait_timeout=600
interactive_timeout=600
skip-name-resolve

# InnoDB引擎参数
default_storage_engine=InnoDB
innodb_buffer_pool_size=4G
innodb_log_file_size=2G
innodb_log_buffer_size=64M
innodb_file_per_table=1
innodb_flush_log_at_trx_commit=1
sync_binlog=1
innodb_ddl_log_crash_recover=ON
innodb_autoinc_lock_mode=2

# 二进制日志（主从、数据恢复必备）
log_bin=mysql-bin
binlog_format=ROW
expire_logs_days=7
binlog_row_image=FULL

# 慢查询日志
slow_query_log=ON
slow_query_log_file=/data/mysql/slow.log
long_query_time=1
log_queries_not_using_indexes=ON

# 时间字段严格模式
explicit_defaults_for_timestamp=ON

[mysql]
socket=/var/lib/mysql/mysql.sock
default-character-set=utf8mb4

[mysqld_safe]
log-error=/data/mysql/mysql-error.log
pid-file=/var/run/mysqld/mysqld.pid
```

------



## 文件 2：my.cnf.compat 兼容备用配置文件

### 核心设定

1. 排序规则依然：`utf8mb4_0900_ai_ci`（字符规则保持不变，保证表结构一致）
2. 密码改为 `mysql_native_password`，兼容老旧 JDBC、Navicat、中间件
3. 其余所有性能参数完全和上面一摸一样，只改认证插件，业务随时切换

Ini

```
[mysqld]
# 基础运行配置
user=mysql
group=mysql
pid-file=/var/run/mysqld/mysqld.pid
socket=/var/lib/mysql/mysql.sock
port=3306
basedir=/usr/local/mysql
datadir=/data/mysql
tmpdir=/tmp

# 字符集保持不变（最重要：字符规则统一，不会出现索引冲突）
character-set-server=utf8mb4
collation-server=utf8mb4_0900_ai_ci
init_connect='SET NAMES utf8mb4'

# 仅修改：降级为原生密码，兼容旧客户端
default_authentication_plugin=mysql_native_password

sql_mode = ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION
default_time_zone = '+8:00'

max_connections=800
max_connect_errors=1000
wait_timeout=600
interactive_timeout=600
skip-name-resolve

default_storage_engine=InnoDB
innodb_buffer_pool_size=4G
innodb_log_file_size=2G
innodb_log_buffer_size=64M
innodb_file_per_table=1
innodb_flush_log_at_trx_commit=1
sync_binlog=1
innodb_ddl_log_crash_recover=ON
innodb_autoinc_lock_mode=2

log_bin=mysql-bin
binlog_format=ROW
expire_logs_days=7
binlog_row_image=FULL

slow_query_log=ON
slow_query_log_file=/data/mysql/slow.log
long_query_time=1
log_queries_not_using_indexes=ON

explicit_defaults_for_timestamp=ON

[mysql]
socket=/var/lib/mysql/mysql.sock
default-character-set=utf8mb4

[mysqld_safe]
log-error=/data/mysql/mysql-error.log
pid-file=/var/run/mysqld/mysqld.pid
```

------

## 配套 SQL 语句

### 1）sha2 加密账号（主配置文件使用）

sql

```
CREATE USER root@'%' IDENTIFIED BY 'YourStrongPass123!';
GRANT ALL PRIVILEGES ON *.* TO root@'%' WITH GRANT OPTION;
FLUSH PRIVILEGES;
```

### 2）兼容模式账号（备用配置文件使用）

sql

```
CREATE USER root@'%' IDENTIFIED WITH mysql_native_password BY 'YourStrongPass123!';
GRANT ALL PRIVILEGES ON *.* TO root@'%' WITH GRANT OPTION;
FLUSH PRIVILEGES;
```

------

## 关键要点总结

1. 两份配置**只有一行不一样**：`default_authentication_plugin`，字符集、排序规则完全统一，不会出现字段 collation 不一致；
2. 生产优先使用第一套 sha2 版本，安全等级更高；一旦遇到客户端兼容问题，直接替换为第二份兼容配置，不需要改表结构；
3. 内存参数适配 16G 服务器；32G 机器把 `innodb_buffer_pool_size` 改成 8G，8G 服务器改为 2G；
4. 适配 MySQL8.0.45 el7 + CentOS7（glibc2.17），无系统库冲突。

## 运维切换方法

bash 

```
# 使用sha2高安全版
cp /etc/my.cnf /etc/my.cnf.bak
cp /etc/my.cnf.original /etc/my.cnf

# 临时切换为兼容密码版
cp /etc/my.cnf.compat /etc/my.cnf
systemctl restart mysqld
```





# MySQL5.7 vs MySQL8.0 核心差异

**生产最关键内容**



## 一、基础信息 & 生命周期

表格

|    项目    |             MySQL 5.7              |          MySQL 8.0（8.0.45）           |
| :--------: | :--------------------------------: | :------------------------------------: |
|  维护状态  |   2023 年已停止维护，无安全补丁    |   长期 LTS 版本，持续更新到 2030 年    |
| glibc 基线 | el7 包 = glibc2.17（CentOS7 原生） | el7 包 = glibc2.17；通用包 = glibc2.28 |
|  默认引擎  |               InnoDB               |         InnoDB（优化全面升级）         |

------

## 二、字符集（升级第一大坑）

1. **MySQL5.7**

   默认字符集：`latin1`，中文极易乱码；内置`utf8`仅为 3 字节 utf8mb3，不支持 emoji。

   需要手动改 my.cnf 为 utf8mb4。

2. **MySQL8.0**

   默认字符集：`utf8mb4`，排序规则`utf8mb4_0900_ai_ci`，原生支持 emoji、生僻字，开箱无乱码MySQL。

------

## 三、账号安全（最容易踩连接报错）

1. **认证插件**

- 5.7：`mysql_native_password`（旧明文加密，老旧客户端完美兼容）
- 8.0：默认`caching_sha2_password`（SHA256 高强度加密）

> 坑：Navicat 旧版本、PHP 低版本连不上 8.0，解决方案：把用户改回 native 密码：

sql

```
ALTER USER root@'%' IDENTIFIED WITH mysql_native_password BY 'xxx';
```

1. 8.0 新增能力：

   角色 RBAC 权限、密码过期、历史密码防重复、密码强度策略，企业权限管理更强。

------

## 四、SQL 语法（开发差距最大）

### 5.7 不支持，8.0 全面支持：

1. **窗口函数**：ROW_NUMBER ()、RANK ()，分组排名不用写子查询
2. **CTE 公共表表达式 + 递归 CTE**，树形层级查询（组织机构、树形菜单），替代复杂子查询
3. **Hash Join**（8.0.18+），大表关联性能碾压 Nested Loop
4. **JSON 增强**：JSON_TABLE，可以直接把 JSON 字段转为数据表，5.7 只有简单 JSON 函数
5. **函数索引（表达式索引）**，支持`create index on t(cast(col as unsigned))`，5.7 无法实现

------

## 五、InnoDB 引擎核心升级（运维重点）

### 1. 原子 DDL（重中之重）

- 5.7：ALTER 改表一旦中途崩溃，表结构残缺，极易出现 frm 文件损坏；
- 8.0：DDL 事务原子化，要么全部成功，要么完整回滚，不会出现半拉子坏表。

### 2. 索引新特性

- 降序索引：支持`index idx (col DESC)`，ORDER BY DESC 不再额外排序；
- 不可见索引：索引可以临时隐藏，测试删除索引的性能影响，不用物理删除；
- INSTANT 即时 DDL：新增字段秒级完成，不需要复制全表数据，大表改字段不再锁表阻塞业务。

### 3. 优化器大幅增强

8.0 加入统计直方图，解决 5.7 统计信息失真导致索引选错、SQL 突然变慢的问题；

redo log、锁机制、缓冲池优化，高并发写入性能普遍比 5.7 高 15%~30%。

### 4. 元数据改造

5.7 表结构存在独立`.frm`文件，文件丢失直接废表；

8.0 统一事务型数据字典，所有元数据存入 InnoDB 系统表，稳定性更高。

------

## 六、其他关键差异

1. 排序规则严格度

   8.0 字符串对比区分大小写、重音；5.7 默认不区分，升级后部分 where 条件结果会变。

2. 事务隔离

   8.0 默认还是 REPEATABLE READ，保持兼容；死锁检测算法优化，死锁更少。

3. 资源管控

   8.0 支持资源组，限制线程 CPU 资源，多租户数据库更稳定。

------

## 七、兼容性大坑（5.7 迁移 8.0 必看）

1. sql_mode 默认值变严格：8.0 默认开启严格模式，零日期`0000-00-00`直接报错，5.7 可以插入；
2. 不再支持 MYISAM 一些老旧语法；
3. 存储过程、触发器语法校验更严格；
4. 排序规则 utf8mb4_0900_ai_ci 与旧 utf8mb4_general_ci 相等逻辑不一致。

------

## 八、选型建议

1. **新项目**：直接上 MySQL8.0.45，功能全、安全、性能更好，优先选 el7 版本（glibc2.17）适配 CentOS7；
2. **老业务 5.7 稳定运行**：可以继续用，但无安全维护；
3. **迁移升级**：优先导出导入，提前修改 sql_mode、账号认证方式、字符集，规避业务报错。

如果你需要，我可以给你一份：

1）my.cnf 配置对比模板；

2）5.7 迁移 8.0 的前置检查 SQL 脚本；

3）大表 DDL 变更在 8.0 下的最佳实践。







# **utf8mb4_0900_ai_ci  utf8mb4_general_ci 对比**



## 一、两个规则的字面含义

- **ci = Case Insensitive：大小写不敏感**

- ai = Accent Insensitive：重音不敏感

  所以二者都满足：

1. `A = a`，大小写等同
2. `é = e`，忽略音标、重音

------

## 二、字符相等逻辑（核心）

### 1）utf8mb4_general_ci（MySQL5.7 通用旧规则）

#### 相等逻辑

**一对一简单字符权重匹配，没有语言扩展规则，无字符折叠扩展**

1. 只做单个字符与单个字符对比，不支持 “一个字符等于多字符”；
2. 只内置一张简化权重表（Unicode4.0 简化版）；
3. 不会执行语言特殊等价规则。

#### 关键行为

- 拉丁字母：`Ä = A`、`é = e`（忽略重音）
- 德语字符：**ß ≠ ss**（最重要区别！general_ci 不会把 ß 等价成 ss）
- 空白、不可见字符严格区分
- 中文：直接按 Unicode 码点值对比，无拼音优化
- 没有字符折叠规则，算法极简，对比速度更快

#### 相等案例

Sql

```
'Cafe' = 'cafe' = 'CAFE' = 'café' → true
'ß' = 'ß' ，但 'ß' <> 'ss'
'æ' 不等于 'ae'
```

------

### 2）utf8mb4_0900_ai_ci（MySQL8.0 默认，UCA 9.0 Unicode 标准）

#### 相等逻辑

严格遵循 **Unicode UCA 9.0 完整排序算法**，支持 3 种高级规则：

1. **字符折叠（ignore accent）**：自动剥离所有重音、音标；
2. **扩展等价（expansion）**：单个字符等价于两个字符（德语 ß = ss，æ = ae）；
3. **收缩匹配（contraction）**：多字符合并等价为单个字符。

#### 关键行为（和 general_ci 最大分水岭）

1. 大小写、重音依然忽略：`É = e = E`
2. 语言等价生效：
   - `ß = ss`
   - `æ = ae`
3. Unicode 字符库更新到 9.0，生僻字、emoji 对比逻辑更严谨
4. 空白、零宽字符会被当作可忽略字符（general_ci 不会忽略）

#### 相等案例

sql

```
'Café' = 'cafe' → true
'ß' = 'ss' → true（general_ci里false）
'æ' = 'ae' → true
```

------

## 三、最容易踩坑的不等价场景（迁移必看）

表格

|       对比内容        | utf8mb4_general_ci |   utf8mb4_0900_ai_ci    |
| :-------------------: | :----------------: | :---------------------: |
|      `ß` vs `ss`      |       不相等       |          相等           |
|      `æ` vs `ae`      |       不相等       |          相等           |
|   带零宽空格字符串    |       不相等       |          相等           |
| 普通英文大小写 + 重音 |        相等        |          相等           |
|     中文汉字对比      |   按码点粗暴对比   | 按新版 Unicode 码点对比 |

### 重大业务风险：

如果唯一索引字段从`general_ci`改成`0900_ai_ci`，原本两条不一样的数据`ß`和`ss`，会突然触发**唯一键冲突**，直接导致插入报错。

------



## 四、中文对比行为差异

1. **utf8mb4_general_ci**

   汉字直接按原始 Unicode 编码值排序，完全不按拼音；排序杂乱。

2. **utf8mb4_0900_ai_ci**

   依然**不支持中文拼音排序**（想要拼音排序要用专门的 `utf8mb4_zh_0900_as_ci`），只是汉字码点对比更贴合新版 Unicode，少量生僻字相等结果会不同。

------

## 五、性能差异

1. `utf8mb4_general_ci`：一对一查表，算法极简，字符串对比更快；
2. `utf8mb4_0900_ai_ci`：完整 UCA 算法，支持扩展字符匹配，CPU 开销略高；

> 普通业务（百万级数据）几乎感知不到差距，只有超大批量字符串匹配才有区别。

------

## 六、生产环境选型建议

1. **5.7 迁移 8.0 老旧业务**

   保持 `utf8mb4_general_ci`，避免字符等价逻辑变化引发唯一索引冲突、where 查询结果不一致。

2. **MySQL8.0 新项目**

   优先使用默认 `utf8mb4_0900_ai_ci`，字符规则符合现代 Unicode 标准，特殊语种搜索更人性化。

3. **账号、唯一编码字段**

   不要用以上两种 ci 规则，改用 `utf8mb4_0900_as_cs`（区分大小写 + 区分重音）或者`_bin`二进制严格比对，防止不同写法被判定为同一数据。

------

## 七、补充：跨规则对比会发生什么？

如果一条 SQL 两边字段排序规则不一致：

sql

```
col1 COLLATE utf8mb4_general_ci = col2 COLLATE utf8mb4_0900_ai_ci
```

会触发**隐式字符集转换**，索引失效，全表扫描，这是线上慢 SQL 高发坑。



# utf8mb4_0900_ai_ci 隐藏风险冲突检查



## 一、风险检查 SQL（核心：找出会冲突的唯一索引）

### 场景说明

从 `utf8mb4_general_ci` 切换到 `utf8mb4_0900_ai_ci` 时，`ß/ss`、`æ/ae` 这类字符会触发唯一键重复。

### 1. 第一步：找出所有唯一索引、主键字段

Sql

```
SELECT
  TABLE_NAME,
  COLUMN_NAME,
  CONSTRAINT_TYPE,
  COLLATION_NAME
FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE k
JOIN INFORMATION_SCHEMA.COLUMNS c
  ON k.TABLE_SCHEMA = c.TABLE_SCHEMA
  AND k.TABLE_NAME = c.TABLE_NAME
  AND k.COLUMN_NAME = c.COLUMN_NAME
WHERE k.TABLE_SCHEMA = DATABASE()
  AND CONSTRAINT_TYPE IN ('PRIMARY KEY','UNIQUE')
  AND COLLATION_NAME LIKE 'utf8mb4%ci';
```

### 2. 第二步：检测表里存在 “字符折叠冲突” 的数据

只针对包含德文、拉丁扩展字符的冲突，复制执行：

Sql

```
-- 查找 ß 与 ss 共存的重复数据
SELECT your_column,COUNT(*)
FROM your_table
WHERE your_column IN ('ß','ss')
GROUP BY your_column;

-- 查找 æ 与 ae 共存
SELECT your_column,COUNT(*)
FROM your_table
WHERE your_column IN ('æ','ae')
GROUP BY your_column;
```

### 3. 批量检测脚本（整库一键扫描冲突行）

> 把 `db_name` 替换成你的库名

sql

```
SET @schema = 'db_name';

SELECT
  CONCAT(
    'SELECT "',TABLE_NAME,'",',
    COLUMN_NAME,
    ' FROM `',TABLE_NAME,'` WHERE ',
    COLUMN_NAME,' COLLATE utf8mb4_0900_ai_ci = ',
    COLUMN_NAME,' COLLATE utf8mb4_general_ci HAVING COUNT(*)>1;'
  ) AS check_sql
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_SCHEMA=@schema
AND COLLATION_NAME='utf8mb4_general_ci'
AND DATA_TYPE LIKE 'varchar%';
```

执行后会输出一批 SQL，把输出结果全部复制出来批量运行，就能查出所有因为排序规则变更会变成重复值的数据。

------

## 二、批量查看全库字符集与排序规则（防止隐式转换）

sql

```
-- 查看库级
SELECT SCHEMA_NAME,DEFAULT_CHARACTER_SET_NAME,DEFAULT_COLLATION_NAME
FROM INFORMATION_SCHEMA.SCHEMATA
WHERE SCHEMA_NAME NOT IN ('mysql','information_schema','performance_schema','sys');

-- 查看所有表
SELECT TABLE_NAME,TABLE_COLLATION
FROM INFORMATION_SCHEMA.TABLES
WHERE TABLE_SCHEMA=DATABASE();

-- 查看字段级不一致（最容易导致索引失效）
SELECT
  TABLE_NAME,COLUMN_NAME,CHARACTER_SET_NAME,COLLATION_NAME
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_SCHEMA=DATABASE()
AND CHARACTER_SET_NAME='utf8mb4'
AND COLLATION_NAME NOT IN ('utf8mb4_general_ci','utf8mb4_0900_ai_ci');
```

------

## 三、修复方案（分两种业务场景）

### 方案 A：老业务 5.7 升级，保持兼容（推荐，零线上故障）

不改排序规则，继续沿用旧规则：

ini

```
character-set-server=utf8mb4
collation-server=utf8mb4_general_ci
```

新建表默认都是 `utf8mb4_general_ci`，不会出现字符等价逻辑突变。

### 方案 B：强制切换为 utf8mb4_0900_ai_ci（新项目）

1. 先清理上面 SQL 查出来的重复数据；
2. 再执行批量修改语句：

sql

```
ALTER TABLE `表名` MODIFY COLUMN `字段` VARCHAR(255)
CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NOT NULL;
```

------

## 四、迁移避坑补充

1. **不要字段两边排序规则不一样**，一旦混用 `general_ci` 和 `0900_ai_ci`，字符串关联查询必定走全表扫描；
2. 账号、手机号、编码类唯一字段，建议直接使用 `utf8mb4_bin` 二进制比对，彻底杜绝字符等价带来的主键冲突；
3. 全库变更字符集必须先导出结构，在测试库完整回放一遍，验证唯一索引无报错再上线。



# mysql 8.0 的线上生产库'root'@'localhost' 

# 需要改为 mysql_native_password 认证方式吗？



## 一、核心结论（生产通用规范）

1. **root@[localhost](https://link.wtturl.cn/?target=https%3A%2F%2Flocalhost&scene=im&aid=497858&lang=zh)（本地管理账号）：完全不需要改成 mysql_native_password，保留默认 `caching_sha2_password` 即可。**
2. **root@%（远程账号）：才需要酌情改成 mysql_native_password 做兼容。**

------

## 二、为什么本地 root 可以保留 sha2 加密？

### 1. 本地命令行客户端天然兼容

MySQL 自带的 `mysql / mysqladmin / mysqldump` 客户端（8.0 自带 libmysqlclient）原生支持 `caching_sha2_password`，通过 socket 本地登录不会出现任何握手报错MySQL。

只有第三方远程工具（旧 Navicat、老 JDBC、PHP 低版本）才会不识别 sha2 插件。

### 2. 安全层面：本地 root 优先用更强的 SHA256 加密

- `caching_sha2_password`：SHA256 加盐加密，防暴力破解、防抓包；

- ```
  mysql_native_password
  ```

  ：老旧 SHA1 哈希，安全性弱。

  

  运维本地登录只用本机命令行，没有兼容性障碍，理应保留高安全认证。

### 3. 区分两个账号（极易踩坑）

sql

```
-- 1）root@localhost：本机socket登录 → 保持 caching_sha2_password（推荐）
-- 2）root@'%' / root@127.0.0.1：TCP远程连接 → 按需改为mysql_native_password兼容客户端
```

很多运维只改全局默认插件，把本地 root 也强制降级，白白降低安全性。

------

## 三、两种生产环境最佳实践方案

### 方案 A：高安全新项目（推荐，对应你上面 sha2 版 my.cnf）

1. my.cnf 全局默认插件：

ini

```
default_authentication_plugin=caching_sha2_password
```

1. 用户拆分：

- `root@localhost`：维持默认 sha2，不改动；
- 新建业务远程账号（appuser@%），如果中间件老旧，单独把远程账号改为`mysql_native_password`。

SQL 示例：

sql

```
# 本地管理员账号：保持sha2加密，不动
ALTER USER 'root'@'localhost' IDENTIFIED BY 'StrongRoot@123';

# 远程应用账号：单独降级为原生密码，兼容老旧驱动
ALTER USER 'appuser'@'%' IDENTIFIED WITH mysql_native_password BY 'App@Pass456';
```

### 方案 B：5.7 迁移兼容环境（全局插件改为 native）

全局配置：

ini

```
default_authentication_plugin=mysql_native_password
```

此时所有新建账号（包括 root@[localhost](https://link.wtturl.cn/?target=https%3A%2F%2Flocalhost&scene=im&aid=497858&lang=zh)）都会自动使用 native，适合全量老旧客户端的迁移场景，属于兼容妥协。

------

## 四、运维行业通用标准（90% 互联网企业做法）

1. **本地管理员账号 (root@[localhost](https://link.wtturl.cn/?target=https%3A%2F%2Flocalhost&scene=im&aid=497858&lang=zh))：一律使用 caching_sha2_password，不降级；**
2. 所有对外业务账号：分情况
   - 新驱动（JDBC8.1+、新版 Navicat、Python 新版驱动）：继续 sha2；
   - 老旧中间件、PHP7.3 以下：仅把远程业务账号单独改成 mysql_native_password；
3. 绝对不建议为了省事，把本地 root 的加密等级一并降级。

------

## 五、补充避坑点

1. `root@127.0.0.1` 不等于 `root@localhost`：前者走 TCP 网络，会遇到 sha2 兼容问题，如果需要本地 TCP 连接，可以单独把 127.0.0.1 这条账号改为 native；
2. 只要你只在服务器本机执行`mysql -uroot -p`，socket 登录，sha2 不会出现任何连接异常；
3. 不要全局一刀切修改认证插件，最小范围修改账号，兼顾安全 + 兼容。

------

## 精简一句话总结

> 本地 root 管理员账号：维持默认 sha2 加密不动；
>
> 只有远程 TCP 连接的业务账号，遇到客户端报错时，才单独改成 mysql_native_password。