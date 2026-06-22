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

### 使用说明

复制全部内容，新建文件命名为 `MySQL8.0_vs_5.7_版本全对比.md`，使用 Typora / VS Code / Obsidian / 记事本均可打开、保存下载。