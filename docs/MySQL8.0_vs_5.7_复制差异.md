# MySQL 8.0 vs MySQL 5.7 主从复制差异对比文档

## 目录

1. Binlog 基础与事务写入机制
2. 复制账号认证 & SSL 安全
3. 复制线程 & 并行回放模型
4. read_only /super_read_only 从库写入管控
5. GTID 复制能力差异
6. 多源复制 Multi-Source Replication
7. 半同步 Semi-Sync Replication
8. 崩溃安全 Crash-Safe 复制
9. DDL / 过滤 / 权限对象同步逻辑
10. 性能监控与 Performance Schema
11. 高可用：组复制 Group Replication
12. 升级迁移风险点汇总

------

## 1. Binlog 基础与事务写入机制

表格







|         对比项          |           MySQL 5.7            |                          MySQL 8.0                           |
| :---------------------: | :----------------------------: | :----------------------------------------------------------: |
|   默认 binlog_format    |              ROW               |                       ROW（保持一致）                        |
| binlog_row_image 默认值 |     FULL（记录行所有字段）     |        MINIMAL（仅记录变更字段，binlog 体积大幅降低）        |
|     binlog 文件命名     |      主机名 - bin.000001       |              统一 binlog.000001，不再绑定主机名              |
|     自增 ID 持久化      | 崩溃重启自增计数器重置，易重复 |            自增 ID 持久化到 redo，重启无重复风险             |
|      binlog 组提交      |    基础组提交，fsync 次数多    | 组提交深度优化，新增 `binlog_group_commit_sync_delay` / `binlog_group_commit_sync_no_delay_count`，高并发 TPS 提升 |
|    原生 binlog 压缩     |           无内置压缩           | 支持 `binlog_transaction_compression=ON`，节省磁盘与网络带宽 50%+ |
|      binlog 校验和      |         CRC32 默认开启         |               CRC32 默认开启，增加校验容错逻辑               |

------

## 2. 复制账号认证 & SSL 安全

1. 认证插件（异构复制最常见坑）

   - MySQL 5.7 默认：`mysql_native_password`
   - MySQL 8.0 默认：`caching_sha2_password`
   - 兼容方案：创建复制账号时指定兼容插件

   sql

   

   

   

   

   ```
   CREATE USER repl@'10.%' IDENTIFIED WITH mysql_native_password BY 'xxx';
   GRANT REPLICATION SLAVE ON *.* TO repl@'10.%';
   ```

2. SSL/TLS 复制加密

   - 5.7：支持 SSL，但初始化不自动生成证书，配置繁琐，无强制加密语法增强
   - 8.0：初始化自动生成 SSL 证书；支持 `REQUIRE SSL` 强制复制加密；兼容 TLSv1.2/TLSv1.3，废弃弱加密套件

3. 权限体系

   - 两者基础复制权限一致：`REPLICATION SLAVE`（拉 binlog）、`REPLICATION CLIENT`（查看主从状态）
   - 8.0 新增 `BACKUP ADMIN`，物理备份不再依赖 SUPER，与复制权限解耦

------

## 3. 复制线程 & 并行回放模型

### 3.1 并行复制核心差异

- **MySQL 5.7**

  并行策略：`slave_parallel_type=DATABASE`（库级并行）

  限制：同一库事务串行回放，单库高并发场景几乎无并行收益，主从延迟严重。

- **MySQL 8.0**

  默认：`LOGICAL_CLOCK` 逻辑时钟并行（事务粒度并行）

  优势：同一批提交的事务，不分库均可并行回放，大幅降低单库写入延迟；

  配套参数：`slave_parallel_workers`、`slave_parallel_max_queued`

### 3.2 线程架构拆分

- 5.7：IO 线程 + 单 SQL 分发线程 + worker 工作线程，分发逻辑易阻塞
- 8.0：新增独立**协调线程（Coordination Thread）**，IO、协调、worker 完全解耦，锁竞争大幅减少

------

## 4. read_only /super_read_only 从库写入管控

- MySQL 5.7：开启 `super_read_only=OFF` 时，拥有 SUPER 权限账号可直接写入从库，易人为误操作破坏主从一致性
- MySQL 8.0：`super_read_only=ON` 强约束，**即使带 SUPER 权限账号也无法写入从库**，切换主从场景安全性更高

------

## 5. GTID 复制能力差异

表格







|       对比项       |                     MySQL 5.7                     |                     MySQL 8.0                     |
| :----------------: | :-----------------------------------------------: | :-----------------------------------------------: |
|   GTID 默认状态    |        gtid_mode=OFF，需手动开启并重启实例        |              gtid_mode=ON，开箱即用               |
|   GTID 崩溃安全    | GTID 集合存在丢失风险，gtid_executed 时序存在漏洞 | GTID 持久化至 InnoDB 系统表，宕机自动恢复，无断层 |
|  gtid_purged 修改  |           必须重启数据库，无法在线调整            |            支持动态在线修改，无需重启             |
| 多源复制 GTID 隔离 |            隔离简陋，易产生 GTID 冲突             |    按复制通道隔离 GTID 集合，冲突概率大幅降低     |

------

## 6. 多源复制 Multi-Source Replication

1. MySQL 5.7

   

   支持多源复制，但无完整通道隔离，监控、启停均全局生效，多业务同步易互相干扰。

2. MySQL 8.0 重大增强

   - 独立复制通道 Channel 完全隔离 relaylog、IO/SQL 线程、同步位点
   - 按通道单独启停：`START SLAVE FOR CHANNEL 'xxx'`
   - 按通道单独查看状态：`SHOW SLAVE STATUS FOR CHANNEL 'xxx'`
   - 每个通道可独立配置并行复制参数，互不影响

------

## 7. 半同步 Semi-Sync Replication

1. 插件加载方式
   - 5.7：`semisync_master.so` / `semisync_slave.so` 外置插件，需要手动 INSTALL
   - 8.0：半同步内置内核，无需安装插件，直接启用
2. 等待策略（数据安全核心）
   - 5.7 默认：`AFTER_COMMIT`（主库先提交事务，再等待从库 ACK，极端场景存在数据丢失风险）
   - 8.0 默认：`AFTER_SYNC`（主库落盘 binlog 后、事务提交前等待从库应答，数据零丢失）
3. 监控指标：8.0 在 performance_schema 提供完整半同步等待耗时、应答计数指标

------

## 8. 崩溃安全 Crash-Safe 复制

- MySQL 5.7
  - 默认关闭 `relay_log_recovery=ON`，需手动开启才支持中继日志崩溃恢复
  - [master.info](https://link.wtturl.cn/?target=https%3A%2F%2Fmaster.info&scene=im&aid=497858&lang=zh) / [relay-log.info](https://link.wtturl.cn/?target=https%3A%2F%2Frelay-log.info&scene=im&aid=497858&lang=zh) 存储为文本文件，宕机易损坏，导致位点错乱、重复回放
- MySQL 8.0
  - 默认开启 Crash-Safe 复制，无需额外参数配置
  - 废弃文本 info 文件，同步位点持久化至 InnoDB 系统表；宕机重启自动恢复准确复制位点，不会丢 / 重放事务

------

## 9. DDL / 过滤 / 权限对象同步逻辑

1. DDL binlog 记录
   - 5.7：ROW 模式下部分 DDL 记录为 STATEMENT 格式，异构主从易出现数据不一致
   - 8.0：统一标准化 DDL binlog 记录逻辑，降低主从结构差异风险
2. 复制过滤规则
   - 5.7：`REPLICATE_WILD_DO_DB` 库名大小写匹配存在 bug
   - 8.0：修复大小写匹配问题，通配符过滤逻辑更严谨
3. 用户 / 权限同步
   - 5.7：`CREATE USER` / `GRANT` 以语句模式记录，跨字符集、大小写环境易同步异常
   - 8.0：基于统一数据字典记录账号变更，权限同步一致性大幅提升

------

## 10. 性能监控与 Performance Schema

- MySQL 5.7：复制监控指标匮乏，仅依赖 `SHOW SLAVE STATUS`，无法观测并行队列、GTID 耗时等细节
- MySQL 8.0 新增大量复制专属性能视图：
  - 并行复制 worker 队列堆积延迟
  - GTID 执行、提交耗时统计
  - 半同步等待时长、ACK 延迟指标
  - binlog 压缩率、事务 binlog 写入耗时
  - 多源复制每个通道独立监控指标

------

## 11. 高可用：组复制 Group Replication

- MySQL 5.7：组复制功能不完善、稳定性差，线上极少生产使用
- MySQL 8.0：组复制大规模完善，可用于生产高可用集群：
  - 内置故障自动检测、自动选主切换
  - 原生兼容并行复制、半同步、binlog 压缩
  - 多节点数据强一致，替代传统 MGR / 主从切换脚本方案

------

## 12. 升级迁移风险点汇总（8.0 ↔ 5.7 异构复制必看）

1. 8.0 主库对接 5.7 从库：默认 `caching_sha2_password` 认证不兼容，从库 IO 线程连接失败，复制账号必须指定 `mysql_native_password`；
2. `binlog_row_image` 默认为 MINIMAL，5.7 从库可正常回放，但异构数据校验逻辑存在细微差异；
3. 5.7 升级至 8.0 后，并行模式自动切换 LOGICAL_CLOCK，单库延迟下降，建议同步调大 `slave_parallel_workers`；
4. 8.0 默认开启 GTID+Crash-Safe，5.7 备份搭建 8.0 从库需手动处理 `gtid_purged`；
5. 半同步默认等待策略为 `AFTER_SYNC`，降级回 5.7 环境需手动修改 `rpl_semi_sync_master_wait_point`；
6. 8.0 `super_read_only` 强限制，原有 SUPER 账号写入从库的运维脚本会直接报错，需要改造流程。

------

### 使用说明

将全文复制保存为 `MySQL8.0_vs_5.7_复制差异.md`，即可直接用 Typora、VS Code、Obsidian 等工具打开查看 / 下载。