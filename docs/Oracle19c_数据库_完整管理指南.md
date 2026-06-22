# Oracle 19c 数据库完整管理指南

> 本文档涵盖 Oracle 19c 数据库的体系架构、安装部署、网络配置、多租户管理、存储管理、安全管理、备份恢复、高可用性（RAC/Data Guard）、性能调优、闪回技术、升级迁移等全部核心管理领域，并附带可直接使用的操作命令。

## 目录

1. 体系架构
2. 安装与部署
3. 网络配置管理
4. 多租户架构管理（CDB与PDB）
5. 数据库启动与关闭
6. 存储与文件管理
7. 参数与初始化管理
8. 安全管理
9. 备份与恢复管理
10. 高可用性管理（RAC）
11. 高可用性管理（Data Guard）
12. 性能监控与调优
13. 闪回技术
14. 日志与诊断管理
15. 升级与迁移管理
16. 常用命令速查表

## 1. 体系架构

Oracle 19c 的体系架构由**实例（Instance）** 和**数据库（Database）** 两大部分组成。实例包含内存结构和后台进程，数据库包含物理文件。

### 1.1 内存结构

#### 1.1.1 系统全局区（SGA）

SGA 是一组共享的内存结构，包含以下核心组件：

| 组件                                          | 说明                                                         |
| :-------------------------------------------- | :----------------------------------------------------------- |
| **共享池（Shared Pool）**                     | 包含库缓存（SQL/PLSQL 执行计划）、字典缓存（数据字典信息）、结果集缓存 |
| **数据库缓冲区缓存（Database Buffer Cache）** | 存储从数据文件读取的数据块副本，默认块大小为 8KB             |
| **重做日志缓冲区（Redo Log Buffer）**         | 缓存重做条目，用于实例恢复                                   |
| **大池（Large Pool）**                        | 为 RMAN 备份、并行查询等操作提供大内存分配                   |
| **Java 池（Java Pool）**                      | 为 Java 存储过程提供内存                                     |
| **流池（Streams Pool）**                      | 为 Oracle Streams 和 GoldenGate 提供内存                     |

**SGA 管理命令：**

sql

```
-- 查看 SGA 组件大小
SHOW SGA;

-- 查看 SGA 详细配置
SELECT * FROM v$sga;

-- 设置 SGA 总大小
ALTER SYSTEM SET sga_target = 4G SCOPE=BOTH;

-- 设置 SGA 最大值
ALTER SYSTEM SET sga_max_size = 8G SCOPE=SPFILE;

-- 查看 SGA 当前使用情况
SELECT pool, name, bytes/1024/1024 AS mb FROM v$sgastat ORDER BY pool, name;
```



#### 1.1.2 程序全局区（PGA）

PGA 是为每个服务器进程分配的私有内存区域，包含：

- **栈区（Stack Space）** ：存储会话变量
- **UGA（User Global Area）** ：包含会话信息和游标状态
- **SQL 工作区**：用于排序、哈希连接、位图连接等操作

**PGA 管理命令：**

sql

```
-- 查看 PGA 配置
SHOW PARAMETER pga_aggregate_target;
SHOW PARAMETER pga_aggregate_limit;

-- 设置 PGA 目标大小
ALTER SYSTEM SET pga_aggregate_target = 2G SCOPE=BOTH;

-- 设置 PGA 硬上限（19c 新特性）
ALTER SYSTEM SET pga_aggregate_limit = 4G SCOPE=BOTH;

-- 查看 PGA 使用情况
SELECT * FROM v$pgastat;
```



### 1.2 进程结构

#### 1.2.1 后台进程

Oracle 19c 的核心后台进程：

| 进程     | 全称                        | 功能                                                         |
| :------- | :-------------------------- | :----------------------------------------------------------- |
| **DBWn** | Database Writer             | 将脏数据从缓冲区写入数据文件（最多 36 个进程：DBW0-DBW9, DBWa-DBWz） |
| **LGWR** | Log Writer                  | 将重做日志缓冲区写入联机重做日志文件                         |
| **CKPT** | Checkpoint                  | 更新控制文件和数据文件头的检查点信息                         |
| **SMON** | System Monitor              | 实例恢复、清理临时段、合并空闲空间                           |
| **PMON** | Process Monitor             | 清理异常终止的进程、恢复事务                                 |
| **RECO** | Recovery                    | 分布式事务的恢复                                             |
| **MMON** | Manageability Monitor       | 收集 AWR 统计信息                                            |
| **MMNL** | Manageability Monitor Light | 收集轻量级性能统计信息                                       |
| **ARCn** | Archiver                    | 归档联机重做日志文件                                         |

**查看后台进程命令：**

sql

```
-- 查看所有后台进程
SELECT name, description FROM v$bgprocess WHERE paddr != '00';

-- 查看当前活动的后台进程
SELECT program, pid, tracefile FROM v$process WHERE background = 1;
```



### 1.3 物理存储结构

Oracle 19c 数据库的物理文件包括：

- **数据文件（Data Files）** ：存储实际数据
- **控制文件（Control Files）** ：记录数据库物理结构信息
- **联机重做日志文件（Online Redo Log Files）** ：记录所有变更
- **归档日志文件（Archived Log Files）** ：历史重做日志
- **参数文件（Parameter Files）** ：初始化参数配置
- **密码文件（Password Files）** ：管理特权用户认证

## 2. 安装与部署

### 2.1 系统要求

- **内存**：最低 2GB（推荐 8GB+）
- **磁盘空间**：最低 10GB
- **操作系统**：Linux、Windows、Solaris 等

### 2.2 静默安装数据库软件

bash

```
# 使用响应文件进行静默安装
./runInstaller -silent -ignorePrereqFailure \
  -responseFile /path/to/db_install.rsp
```



Oracle 19c 提供了完善的响应文件模板，支持包括 PDB 配置、内存自动管理等高级特性。

### 2.3 使用 DBCA 创建数据库

**图形化方式：**

bash

```
dbca
```



**静默方式（使用响应文件）：**

bash

```
# 复制响应文件模板
cp $ORACLE_HOME/assistants/dbca/dbca.rsp /u01/scripts/my_dbca.rsp
chmod 600 /u01/scripts/my_dbca.rsp

# 执行静默创建
dbca -silent -responseFile /u01/scripts/my_dbca.rsp
```



响应文件安装的核心优势在于其可重复性和效率，可以避免人工操作带来的错误。

**静默方式（命令行参数）：**

bash

```
dbca -silent -createDatabase \
     -templateName General_Purpose.dbc \
     -gdbName ORCL \
     -sid ORCL \
     -createAsContainerDatabase true \
     -numberOfPDBs 1 \
     -pdbName PDB1 \
     -pdbAdminPassword password \
     -sysPassword password \
     -systemPassword password \
     -storageType FS \
     -datafileDestination '/u01/oradata' \
     -redoLogFileSize 200 \
     -emConfiguration NONE
```



### 2.4 初始化实例的注意事项

在初始化实例过程中，需要关注以下要点：

- **内核参数配置**：正确设置 `shmmax`、`shmall`、`file-max` 等参数
- **HugePages 配置**：启用大页以提高性能
- **用户和组权限**：创建 `oracle` 用户和 `oinstall` 组
- **字符集选择**：推荐使用 `AL32UTF8`
- **存储规划**：合理规划数据文件、日志文件、控制文件的位置
- **安全设置**：为 SYS 和 SYSTEM 设置强密码

## 3. 网络配置管理

### 3.1 监听器配置文件（listener.ora）

监听器配置文件位于 `$ORACLE_HOME/network/admin/listener.ora`。

bash

```
# listener.ora 示例
LISTENER =
  (DESCRIPTION_LIST =
    (DESCRIPTION =
      (ADDRESS = (PROTOCOL = TCP)(HOST = hostname)(PORT = 1521))
    )
  )

# 静态注册配置
SID_LIST_LISTENER =
  (SID_LIST =
    (SID_DESC =
      (GLOBAL_DBNAME = ORCL)
      (ORACLE_HOME = /u01/app/oracle/product/19c/dbhome_1)
      (SID_NAME = ORCL)
    )
  )
```



**监听器管理命令：**

bash

```
# 启动监听器
lsnrctl start

# 停止监听器
lsnrctl stop

# 查看监听器状态
lsnrctl status

# 重新加载监听器配置
lsnrctl reload

# 查看监听器服务
lsnrctl services
```



### 3.2 客户端配置文件（tnsnames.ora）

客户端配置文件位于 `$ORACLE_HOME/network/admin/tnsnames.ora`。

bash

```
# tnsnames.ora 示例
ORCL =
  (DESCRIPTION =
    (ADDRESS = (PROTOCOL = TCP)(HOST = hostname)(PORT = 1521))
    (CONNECT_DATA =
      (SERVER = DEDICATED)
      (SERVICE_NAME = ORCL)
    )
  )

# 连接到 PDB
PDB1 =
  (DESCRIPTION =
    (ADDRESS = (PROTOCOL = TCP)(HOST = hostname)(PORT = 1521))
    (CONNECT_DATA =
      (SERVER = DEDICATED)
      (SERVICE_NAME = PDB1)
    )
  )
```



**直连语法（无需 tnsnames.ora）：**

bash

```
sqlplus username/password@//hostname:1521/service_name
```



### 3.3 SQLNet 配置（sqlnet.ora）

bash

```
# sqlnet.ora 示例
SQLNET.INBOUND_CONNECT_TIMEOUT = 60
SQLNET.AUTHENTICATION_SERVICES = (NTS)
NAMES.DIRECTORY_PATH = (TNSNAMES, EZCONNECT)
```



> **注意**：Oracle 19c 默认入站连接超时为 60 秒。在大并发场景下（如同时 2000 个以上业务连接），建议设置为 0（不限制）。

## 4. 多租户架构管理（CDB与PDB）

多租户架构是 Oracle 19c 的核心特性，将 CDB（容器数据库）与 PDB（可插拔数据库）分离。

### 4.1 查看 CDB 信息

sql

```
-- 确认是否为 CDB 模式
SELECT name, cdb FROM v$database;

-- 查看当前容器
SHOW CON_NAME;

-- 查看所有 PDB
SHOW PDBS;
SELECT name, open_mode FROM v$pdbs;
```



### 4.2 创建 PDB

sql

```
-- 切换到 CDB$ROOT
ALTER SESSION SET CONTAINER=CDB$ROOT;

-- 从 PDB$SEED 创建 PDB
CREATE PLUGGABLE DATABASE pdb1
  ADMIN USER pdb_admin IDENTIFIED BY password
  ROLES = (DBA)
  DEFAULT TABLESPACE users
  STORAGE (MAXSIZE 2G)
  FILE_NAME_CONVERT = ('/u01/oradata/CDB/pdbseed',
                       '/u01/oradata/CDB/pdb1');

-- 打开 PDB
ALTER PLUGGABLE DATABASE pdb1 OPEN;

-- 保存状态（CDB 重启后自动打开）
ALTER PLUGGABLE DATABASE pdb1 SAVE STATE;
```



### 4.3 PDB 管理命令

sql

```
-- 切换到 PDB
ALTER SESSION SET CONTAINER=pdb1;

-- 打开/关闭 PDB
ALTER PLUGGABLE DATABASE pdb1 OPEN;
ALTER PLUGGABLE DATABASE pdb1 CLOSE IMMEDIATE;

-- 打开所有 PDB
ALTER PLUGGABLE DATABASE ALL OPEN;

-- 删除 PDB
DROP PLUGGABLE DATABASE pdb1 INCLUDING DATAFILES;

-- 克隆 PDB
CREATE PLUGGABLE DATABASE pdb2 FROM pdb1
  FILE_NAME_CONVERT = ('/u01/oradata/CDB/pdb1',
                       '/u01/oradata/CDB/pdb2');
```



## 5. 数据库启动与关闭

### 5.1 数据库启动

sql

```
-- 启动到 NOMOUNT（仅实例）
STARTUP NOMOUNT;

-- 启动到 MOUNT（加载控制文件）
STARTUP MOUNT;

-- 正常启动
STARTUP;
-- 或
STARTUP OPEN;

-- 强制启动
STARTUP FORCE;
```



### 5.2 数据库关闭

sql

```
-- 正常关闭（等待所有事务完成）
SHUTDOWN NORMAL;

-- 立即关闭（推荐）
SHUTDOWN IMMEDIATE;

-- 事务性关闭
SHUTDOWN TRANSACTIONAL;

-- 异常关闭（仅紧急情况）
SHUTDOWN ABORT;
```



### 5.3 查看数据库状态

sql

```
-- 查看数据库状态
SELECT status FROM v$instance;

-- 查看数据库打开模式
SELECT open_mode FROM v$database;

-- 查看数据库角色
SELECT database_role FROM v$database;
```



## 6. 存储与文件管理

### 6.1 控制文件管理

控制文件是数据库的核心文件，记录数据库的物理结构信息。

sql

```
-- 查看控制文件位置
SELECT name FROM v$controlfile;

-- 多路复用控制文件（高可用）
-- 1. 关闭数据库
SHUTDOWN IMMEDIATE;

-- 2. 复制控制文件到新位置
-- 3. 修改 CONTROL_FILES 参数
ALTER SYSTEM SET control_files = 
  '/u01/oradata/control01.ctl',
  '/u02/oradata/control02.ctl' SCOPE=SPFILE;

-- 4. 重启数据库
STARTUP;

-- 备份控制文件
ALTER DATABASE BACKUP CONTROLFILE TO '/backup/control.bkp';

-- 备份控制文件为 SQL 脚本
ALTER DATABASE BACKUP CONTROLFILE TO TRACE;
```



### 6.2 联机重做日志管理

sql

```
-- 查看日志组信息
SELECT group#, thread#, sequence#, bytes/1024/1024 AS mb, 
       members, status, archived 
FROM v$log;

-- 查看日志成员
SELECT group#, member FROM v$logfile;

-- 添加日志组
ALTER DATABASE ADD LOGFILE GROUP 4 
  ('/u01/oradata/redo04a.log', '/u02/oradata/redo04b.log') 
  SIZE 200M;

-- 添加日志成员
ALTER DATABASE ADD LOGFILE MEMBER 
  '/u03/oradata/redo01c.log' TO GROUP 1;

-- 删除日志组
ALTER DATABASE DROP LOGFILE GROUP 4;

-- 切换日志
ALTER SYSTEM SWITCH LOGFILE;

-- 强制日志切换（检查点）
ALTER SYSTEM CHECKPOINT;
```



### 6.3 表空间管理

sql

```
-- 创建表空间
CREATE TABLESPACE app_data
  DATAFILE '/u01/oradata/app_data01.dbf' SIZE 100M
  AUTOEXTEND ON NEXT 100M MAXSIZE 2G
  EXTENT MANAGEMENT LOCAL
  SEGMENT SPACE MANAGEMENT AUTO;

-- 创建大文件表空间
CREATE BIGFILE TABLESPACE big_data
  DATAFILE SIZE 100M
  AUTOEXTEND ON NEXT 100M MAXSIZE UNLIMITED;

-- 创建临时表空间
CREATE TEMPORARY TABLESPACE temp_app
  TEMPFILE '/u01/oradata/temp_app01.dbf' SIZE 50M
  AUTOEXTEND ON NEXT 50M MAXSIZE 500M;

-- 扩展表空间
ALTER TABLESPACE app_data
  ADD DATAFILE '/u01/oradata/app_data02.dbf' SIZE 100M;

-- 修改数据文件大小
ALTER DATABASE DATAFILE '/u01/oradata/app_data01.dbf'
  RESIZE 500M;

-- 设置默认表空间
ALTER DATABASE DEFAULT TABLESPACE users;
ALTER DATABASE DEFAULT TEMPORARY TABLESPACE temp;

-- 删除表空间
DROP TABLESPACE app_data INCLUDING CONTENTS AND DATAFILES;

-- 查看表空间使用情况
SELECT tablespace_name,
       ROUND(SUM(bytes)/1024/1024, 2) AS size_mb
FROM dba_data_files
GROUP BY tablespace_name;
```



### 6.4 ASM 管理（Automatic Storage Management）

ASM 是 Oracle 推荐的存储管理解决方案，提供自动化的文件管理和 I/O 负载均衡。

bash

```
# 查看 ASM 磁盘组
asmcmd lsdg

# 查看 ASM 磁盘
asmcmd lsdsk

# 创建磁盘组
CREATE DISKGROUP data EXTERNAL REDUNDANCY
  DISK '/dev/asm-disk1', '/dev/asm-disk2';

# 查看 ASM 实例状态
srvctl status asm
```



## 7. 参数与初始化管理

### 7.1 参数文件类型

Oracle 19c 支持两种参数文件：

| 类型                         | 文件名            | 特点                               |
| :--------------------------- | :---------------- | :--------------------------------- |
| **PFILE**（静态参数文件）    | `init<SID>.ora`   | 文本格式，可手工编辑，修改后需重启 |
| **SPFILE**（服务器参数文件） | `spfile<SID>.ora` | 二进制格式，推荐使用，支持在线修改 |

**SPFILE 的搜索顺序**：`spfile<SID>.ora` → `spfile.ora` → `init<SID>.ora`

### 7.2 参数文件管理命令

sql

```
-- 查看当前使用的参数文件
SHOW PARAMETER spfile;

-- 从 PFILE 创建 SPFILE
CREATE SPFILE FROM PFILE;

-- 从 SPFILE 创建 PFILE
CREATE PFILE='/tmp/init.ora' FROM SPFILE;

-- 从内存创建参数文件
CREATE SPFILE FROM MEMORY;
CREATE PFILE='/tmp/init.ora' FROM MEMORY;

-- 查看所有参数
SHOW PARAMETER;

-- 查看特定参数
SHOW PARAMETER sga;
SHOW PARAMETER db_block_size;

-- 修改参数（仅内存，重启失效）
ALTER SYSTEM SET parameter=value SCOPE=MEMORY;

-- 修改参数（仅 SPFILE，重启生效）
ALTER SYSTEM SET parameter=value SCOPE=SPFILE;

-- 修改参数（内存 + SPFILE）
ALTER SYSTEM SET parameter=value SCOPE=BOTH;

-- 重置参数为默认值
ALTER SYSTEM RESET parameter SCOPE=SPFILE;
```



### 7.3 关键初始化参数

| 参数                   | 说明           | 推荐值             |
| :--------------------- | :------------- | :----------------- |
| `sga_target`           | SGA 总大小     | 物理内存的 40%-60% |
| `pga_aggregate_target` | PGA 总大小     | 物理内存的 20%-30% |
| `db_block_size`        | 数据块大小     | 8KB（不可更改）    |
| `processes`            | 最大进程数     | 根据应用需求       |
| `open_cursors`         | 最大游标数     | 1000+              |
| `job_queue_processes`  | 作业队列进程数 | 1000               |
| `audit_trail`          | 审计跟踪       | DB, EXTENDED       |

## 8. 安全管理

### 8.1 用户管理

**创建用户：**

sql

```
-- 创建用户
CREATE USER app_user IDENTIFIED BY password
  DEFAULT TABLESPACE users
  TEMPORARY TABLESPACE temp
  QUOTA 100M ON users
  PROFILE default;

-- 修改用户密码
ALTER USER app_user IDENTIFIED BY new_password;

-- 锁定/解锁用户
ALTER USER app_user ACCOUNT LOCK;
ALTER USER app_user ACCOUNT UNLOCK;

-- 删除用户
DROP USER app_user CASCADE;

-- 查看用户信息
SELECT username, account_status, created FROM dba_users;
```



### 8.2 权限管理

Oracle 权限分为**系统权限**（如 `CREATE SESSION`）和**对象权限**（如 `SELECT ON table`）。

sql

```
-- 授予系统权限
GRANT CREATE SESSION, CREATE TABLE TO app_user;

-- 授予对象权限
GRANT SELECT, INSERT, UPDATE ON schema.table TO app_user;

-- 授予 DBA 权限
GRANT DBA TO app_user;

-- 撤销权限
REVOKE CREATE TABLE FROM app_user;

-- 查看用户权限
SELECT * FROM dba_sys_privs WHERE grantee = 'APP_USER';
SELECT * FROM dba_tab_privs WHERE grantee = 'APP_USER';
```



### 8.3 角色管理

sql

```
-- 创建角色
CREATE ROLE app_role;

-- 授予角色权限
GRANT CREATE SESSION, SELECT ANY TABLE TO app_role;

-- 将角色授予用户
GRANT app_role TO app_user;

-- 设置默认角色
ALTER USER app_user DEFAULT ROLE app_role;

-- 删除角色
DROP ROLE app_role;

-- 查看角色信息
SELECT * FROM dba_roles;
```



### 8.4 审计配置

sql

```
-- 启用审计
ALTER SYSTEM SET audit_trail = DB, EXTENDED SCOPE=SPFILE;

-- 审计特定操作
AUDIT SELECT TABLE, INSERT TABLE, UPDATE TABLE, DELETE TABLE BY app_user;

-- 审计登录
AUDIT CREATE SESSION;

-- 查看审计记录
SELECT * FROM dba_audit_trail;

-- 取消审计
NOAUDIT SELECT TABLE BY app_user;
```



## 9. 备份与恢复管理

### 9.1 启用归档模式

sql

```
-- 设置归档参数
ALTER SYSTEM SET log_archive_dest_1 = 'location=/u01/archivelog' SCOPE=BOTH;
ALTER SYSTEM SET log_archive_format = 'arch_%t_%s_%r.arc' SCOPE=SPFILE;

-- 设置快速恢复区
ALTER SYSTEM SET db_recovery_file_dest_size = 20G SCOPE=BOTH;
ALTER SYSTEM SET db_recovery_file_dest = '/u01/fast_recovery_area' SCOPE=BOTH;

-- 启用归档模式
SHUTDOWN IMMEDIATE;
STARTUP MOUNT;
ALTER DATABASE ARCHIVELOG;
ALTER DATABASE OPEN;

-- 查看归档状态
ARCHIVE LOG LIST;
```



### 9.2 RMAN 基本配置

RMAN 是 Oracle 内置的备份恢复工具，能够备份整个数据库、表空间、数据文件、控制文件、归档文件和 SPFILE。

bash

```
# 连接 RMAN
rman target /
```



sql

```
-- RMAN 配置
CONFIGURE RETENTION POLICY TO RECOVERY WINDOW OF 7 DAYS;
CONFIGURE BACKUP OPTIMIZATION ON;
CONFIGURE DEFAULT DEVICE TYPE TO DISK;
CONFIGURE DEVICE TYPE DISK PARALLELISM 2;
CONFIGURE CHANNEL DEVICE TYPE DISK FORMAT '/backup/%U';
CONFIGURE CONTROLFILE AUTOBACKUP ON;
CONFIGURE CONTROLFILE AUTOBACKUP FORMAT FOR DEVICE TYPE DISK TO '/backup/cf_%F';
```



### 9.3 RMAN 全量备份

bash

```
RMAN> BACKUP DATABASE PLUS ARCHIVELOG DELETE INPUT;
```



sql

```
-- 带格式的完整备份
RUN {
  ALLOCATE CHANNEL ch1 DEVICE TYPE DISK;
  BACKUP INCREMENTAL LEVEL 0 DATABASE
    FORMAT '/backup/full_%d_%T_%s.bkp'
    PLUS ARCHIVELOG DELETE INPUT;
  RELEASE CHANNEL ch1;
}
```



备份控制文件与参数文件：

bash

```
RMAN> BACKUP CURRENT CONTROLFILE FORMAT '/backup/ctl_%d_%T.bkp';
RMAN> BACKUP SPFILE FORMAT '/backup/spfile_%d_%T.bkp';
```



### 9.4 RMAN 增量备份

sql

```
-- 0 级增量备份（全量）
BACKUP INCREMENTAL LEVEL 0 DATABASE;

-- 1 级增量备份（差异）
BACKUP INCREMENTAL LEVEL 1 DATABASE;
```



增量备份仅备份自上次备份后修改的数据块（0级=全量，1级=增量）。

### 9.5 RMAN 恢复

sql

```
-- 数据库需要处于 MOUNT 模式
STARTUP MOUNT;

-- 查看备份
LIST BACKUP SUMMARY;
LIST BACKUP OF DATABASE;

-- 完整恢复
RESTORE DATABASE;
RECOVER DATABASE;
ALTER DATABASE OPEN;

-- 恢复表空间
RESTORE TABLESPACE users;
RECOVER TABLESPACE users;

-- 恢复数据文件
RESTORE DATAFILE 1;
RECOVER DATAFILE 1;

-- 恢复到指定时间点
RUN {
  SET UNTIL TIME "TO_DATE('2026-06-22 10:00:00','YYYY-MM-DD HH24:MI:SS')";
  RESTORE DATABASE;
  RECOVER DATABASE;
}
ALTER DATABASE OPEN RESETLOGS;
```



### 9.6 RMAN 备份验证

sql

```
-- 验证备份
VALIDATE BACKUPSET <ID>;

-- 交叉检查备份
CROSSCHECK BACKUP;

-- 删除过期备份
DELETE EXPIRED;

-- 删除过期归档
DELETE ARCHIVELOG ALL COMPLETED BEFORE 'SYSDATE-7';

-- 报告需要备份的文件
REPORT NEED BACKUP;
```



### 9.7 CDB/PDB 备份恢复

**备份 PDB：**

bash

```
RMAN> BACKUP AS BACKUPSET INCREMENTAL LEVEL=0 PLUGGABLE DATABASE pdb1;
```



**备份 CDB$ROOT：**

bash

```
RMAN> BACKUP PLUGGABLE DATABASE "CDB$ROOT","PDB$SEED";
RMAN> BACKUP DATABASE ROOT;
```



**恢复 PDB：**

bash

```
RMAN> RESTORE PLUGGABLE DATABASE pdb1;
RMAN> RECOVER PLUGGABLE DATABASE pdb1;
RMAN> ALTER PLUGGABLE DATABASE pdb1 OPEN;
```



## 10. 高可用性管理（RAC）

Oracle Real Application Clusters（RAC）允许多个实例访问同一个数据库，提供高可用性和可扩展性。

### 10.1 RAC 集群管理

bash

```
# 查看集群状态（grid 用户）
crsctl status res -t

# 检查集群健康状态
crsctl check cluster -all

# 启动 CRS
crsctl start crs

# 停止 CRS
crsctl stop crs

# 查看 CRS 版本
crsctl query crs softwareversion
```



### 10.2 RAC 数据库管理

bash

```
# 查看数据库状态
srvctl status database -d db_name

# 启动数据库
srvctl start database -d db_name

# 停止数据库
srvctl stop database -d db_name

# 查看实例状态
srvctl status instance -d db_name -i instance_name

# 启动实例
srvctl start instance -d db_name -i instance_name

# 停止实例
srvctl stop instance -d db_name -i instance_name
```



### 10.3 RAC 集群关机与开机流程

**关机流程：**

bash

```
# 1. 关闭数据库（root 用户）
srvctl stop database -d db_name

# 2. 关闭 CRS（所有节点，root 用户）
crsctl stop crs

# 3. 正常关机
shutdown -h now
```



**开机流程：**

bash

```
# 1. 确保共享存储正常
# 2. 启动各节点服务器
# 3. 查看集群状态（grid 用户）
crsctl check cluster -all

# 4. 启动数据库（如未自动启动）
srvctl start database -d db_name
```



## 11. 高可用性管理（Data Guard）

Data Guard 通过创建备用数据库（Standby Database）提供数据保护和灾难恢复能力。

### 11.1 Data Guard 架构

| 角色                             | 说明                                                        |
| :------------------------------- | :---------------------------------------------------------- |
| **主库（Primary）**              | 提供读写服务的生产库                                        |
| **物理备库（Physical Standby）** | 通过 Redo Apply 同步，可处于 MOUNTED 或 OPEN READ ONLY 状态 |
| **逻辑备库（Logical Standby）**  | 通过 SQL Apply 同步                                         |
| **快照备库（Snapshot Standby）** | 可读写测试的备库                                            |

### 11.2 启用 Data Guard Broker

Data Guard Broker (DGMGRL) 是 Oracle 官方推荐的集中管理框架。

sql

```
-- 启用 Broker（主库和备库均需执行）
ALTER SYSTEM SET DG_BROKER_START=TRUE SCOPE=BOTH;
```



**配置静态监听**（listener.ora）：

bash

```
# 主库 listener.ora
SID_LIST_LISTENER =
  (SID_LIST =
    (SID_DESC =
      (GLOBAL_DBNAME = ORCL_DGMGRL)  -- 格式: <db_unique_name>_DGMGRL
      (ORACLE_HOME = /u01/app/oracle/product/19c/dbhome_1)
      (SID_NAME = ORCL)
    )
  )
```



### 11.3 Data Guard Broker 管理

bash

```
# 启动 DGMGRL
dgmgrl

# 连接到主库
DGMGRL> CONNECT sys@primary;

# 创建 Broker 配置
DGMGRL> CREATE CONFIGURATION dg_config AS PRIMARY DATABASE IS ORCL
  CONNECT IDENTIFIER IS primary;

# 添加备库
DGMGRL> ADD DATABASE ORCL_STDBY AS CONNECT IDENTIFIER IS standby
  MAINTAINED AS PHYSICAL;

# 启用配置
DGMGRL> ENABLE CONFIGURATION;

# 查看配置状态
DGMGRL> SHOW CONFIGURATION;

# 查看数据库状态
DGMGRL> SHOW DATABASE ORCL;
DGMGRL> SHOW DATABASE ORCL_STDBY;

# 切换角色（Switchover）
DGMGRL> SWITCHOVER TO ORCL_STDBY;

# 故障转移（Failover）
DGMGRL> FAILOVER TO ORCL_STDBY;
```



### 11.4 SQL*Plus 手动管理 Data Guard

**切换前状态检查：**

sql

```
-- 主库：检查日志传输状态
SELECT DEST_ID, STATUS, TARGET, ARCHIVER, DESTINATION 
FROM V$ARCHIVE_DEST 
WHERE STATUS='VALID' AND TARGET='STANDBY';

-- 主库：检查是否有日志间隙
SELECT * FROM V$ARCHIVE_GAP;

-- 备库：检查 MRP 应用进度
SELECT PROCESS, STATUS, SEQUENCE# FROM V$MANAGED_STANDBY 
WHERE PROCESS='MRP0';
```



**Switchover（计划内主备切换）：**

sql

```
-- 步骤1：主库执行切换命令
ALTER DATABASE COMMIT TO SWITCHOVER TO PHYSICAL STANDBY;

-- 步骤2：重启进入 Standby 状态
SHUTDOWN IMMEDIATE;
STARTUP NOMOUNT;
ALTER DATABASE MOUNT STANDBY DATABASE;

-- 步骤3：启动 MRP 日志应用
ALTER DATABASE RECOVER MANAGED STANDBY DATABASE DISCONNECT FROM SESSION;
```



**Active Data Guard 模式：**

sql

```
-- OPEN READ ONLY 状态下同时应用日志（需 Active Data Guard License）
ALTER DATABASE OPEN READ ONLY;
ALTER DATABASE RECOVER MANAGED STANDBY DATABASE 
  USING CURRENT LOGFILE DISCONNECT FROM SESSION;
```



## 12. 性能监控与调优

### 12.1 AWR（Automatic Workload Repository）

AWR 自动收集数据库性能统计信息。

sql

```
-- 查看 AWR 快照
SELECT SNAP_ID, BEGIN_INTERVAL_TIME, END_INTERVAL_TIME 
FROM DBA_HIST_SNAPSHOT 
ORDER BY SNAP_ID DESC;

-- 手动创建 AWR 快照
EXEC DBMS_WORKLOAD_REPOSITORY.CREATE_SNAPSHOT;

-- 生成 AWR 报告
@$ORACLE_HOME/rdbms/admin/awrrpt.sql

-- 生成 AWR 差异报告
@$ORACLE_HOME/rdbms/admin/awrddrpt.sql
```



### 12.2 ADDM（Automatic Database Diagnostic Monitor）

ADDM 自动分析 AWR 数据并提供性能建议。

sql

```
-- 使用 ADDM 分析
@$ORACLE_HOME/rdbms/admin/addmrpt.sql

-- 查看 ADDM 任务
SELECT TASK_NAME, STATUS, CREATED FROM DBA_ADDM_TASKS;
```



### 12.3 ASH（Active Session History）

ASH 记录活动会话的采样信息。

sql

```
-- 查看 ASH 数据
SELECT SAMPLE_TIME, SESSION_ID, EVENT, WAIT_TIME, TIME_WAITED
FROM V$ACTIVE_SESSION_HISTORY
WHERE ROWNUM <= 10;

-- 生成 ASH 报告
@$ORACLE_HOME/rdbms/admin/ashrpt.sql
```



### 12.4 SQL 调优

sql

```
-- 查看 SQL 执行计划
EXPLAIN PLAN FOR SELECT * FROM table WHERE condition;
SELECT * FROM TABLE(DBMS_XPLAN.DISPLAY);

-- 查看实际执行计划
SELECT * FROM TABLE(DBMS_XPLAN.DISPLAY_CURSOR('sql_id', 0, 'ALL'));

-- 使用 SQL Tuning Advisor
EXEC DBMS_SQLTUNE.CREATE_TUNING_TASK(
  SQL_ID => 'sql_id',
  SCOPE => 'COMPREHENSIVE',
  TIME_LIMIT => 60,
  TASK_NAME => 'tune_task'
);
EXEC DBMS_SQLTUNE.EXECUTE_TUNING_TASK('tune_task');
SELECT DBMS_SQLTUNE.REPORT_TUNING_TASK('tune_task') FROM DUAL;

-- 使用 SQL Access Advisor
EXEC DBMS_ADVISOR.QUICK_TUNE(
  DBMS_ADVISOR.SQLACCESS_ADVISOR,
  'access_advisor_task',
  'SELECT * FROM table WHERE condition'
);
```



### 12.5 性能监控视图

sql

```
-- 查看当前会话
SELECT sid, serial#, username, status, machine, program 
FROM v$session;

-- 查看活动会话
SELECT sid, serial#, username, event, wait_class, state 
FROM v$session 
WHERE status='ACTIVE' AND username IS NOT NULL;

-- 查看锁
SELECT session_id, object_name, lock_type, mode_held, mode_requested
FROM dba_locks;

-- 查看等待事件
SELECT event, total_waits, time_waited_micro, wait_class
FROM v$system_event
WHERE wait_class != 'Idle'
ORDER BY time_waited_micro DESC;

-- 查看当前等待事件
SELECT sid, event, p1, p2, p3, wait_time, seconds_in_wait
FROM v$session_wait
WHERE wait_class != 'Idle';
```



### 12.6 Oracle Resource Manager

Resource Manager 用于在多个 PDB 或会话之间分配 CPU 资源。

sql

```
-- 创建资源计划
EXEC DBMS_RESOURCE_MANAGER.CREATE_PENDING_AREA;
EXEC DBMS_RESOURCE_MANAGER.CREATE_PLAN(
  PLAN => 'plan_name',
  COMMENT => 'Resource Plan'
);
EXEC DBMS_RESOURCE_MANAGER.CREATE_PLAN_DIRECTIVE(
  PLAN => 'plan_name',
  GROUP_OR_SUBPLAN => 'OTHER_GROUPS',
  COMMENT => 'Other groups',
  CPU_P1 => 100
);
EXEC DBMS_RESOURCE_MANAGER.VALIDATE_PENDING_AREA;
EXEC DBMS_RESOURCE_MANAGER.SUBMIT_PENDING_AREA;

-- 启用资源计划
ALTER SYSTEM SET RESOURCE_MANAGER_PLAN = 'plan_name';

-- 查看资源计划
SELECT * FROM v$rsrc_plan;
```



## 13. 闪回技术

Oracle 闪回技术是一种数据恢复技术，具有恢复时间快、不使用备份文件的优点。

### 13.1 启用闪回

sql

```
-- 设置快速恢复区
ALTER SYSTEM SET db_recovery_file_dest_size = 10G SCOPE=BOTH;
ALTER SYSTEM SET db_recovery_file_dest = '/u01/fast_recovery_area' SCOPE=BOTH;

-- 设置闪回保留时间（分钟）
ALTER SYSTEM SET db_flashback_retention_target = 1440;  -- 24小时

-- 启用闪回
SHUTDOWN IMMEDIATE;
STARTUP MOUNT;
ALTER DATABASE FLASHBACK ON;
ALTER DATABASE OPEN;

-- 查看闪回状态
SELECT flashback_on FROM v$database;
```



### 13.2 闪回查询（Flashback Query）

sql

```
-- 查询过去某个时间点的数据
SELECT * FROM table_name AS OF TIMESTAMP 
  TO_TIMESTAMP('2026-06-22 10:00:00', 'YYYY-MM-DD HH24:MI:SS');

-- 查询过去某个 SCN 的数据
SELECT * FROM table_name AS OF SCN 1234567;

-- 查看表的历史版本
SELECT * FROM table_name VERSIONS BETWEEN TIMESTAMP
  TO_TIMESTAMP('2026-06-22 09:00:00', 'YYYY-MM-DD HH24:MI:SS')
  AND TO_TIMESTAMP('2026-06-22 11:00:00', 'YYYY-MM-DD HH24:MI:SS');
```



### 13.3 闪回表（Flashback Table）

sql

```
-- 闪回表到指定时间
FLASHBACK TABLE table_name TO TIMESTAMP
  TO_TIMESTAMP('2026-06-22 10:00:00', 'YYYY-MM-DD HH24:MI:SS');

-- 闪回表到指定 SCN
FLASHBACK TABLE table_name TO SCN 1234567;

-- 启用表行移动（闪回表前需要）
ALTER TABLE table_name ENABLE ROW MOVEMENT;
```



### 13.4 闪回删除（Flashback Drop）

sql

```
-- 查看回收站
SHOW RECYCLEBIN;
SELECT * FROM dba_recyclebin;

-- 恢复被删除的表
FLASHBACK TABLE table_name TO BEFORE DROP;

-- 恢复时重命名
FLASHBACK TABLE table_name TO BEFORE DROP RENAME TO new_table_name;

-- 清空回收站
PURGE RECYCLEBIN;
PURGE DBA_RECYCLEBIN;
```



### 13.5 闪回数据库（Flashback Database）

sql

```
-- 闪回数据库到指定时间
FLASHBACK DATABASE TO TIMESTAMP
  TO_TIMESTAMP('2026-06-22 10:00:00', 'YYYY-MM-DD HH24:MI:SS');

-- 闪回数据库到指定 SCN
FLASHBACK DATABASE TO SCN 1234567;

-- 闪回后以只读方式确认
ALTER DATABASE OPEN READ ONLY;

-- 确认后以 RESETLOGS 打开
ALTER DATABASE OPEN RESETLOGS;
```



## 14. 日志与诊断管理

### 14.1 告警日志（Alert Log）

告警日志是数据库的重要诊断文件，记录数据库的错误和重要事件。

sql

```
-- 查看告警日志位置
SHOW PARAMETER background_dump_dest;
SHOW PARAMETER diagnostic_dest;

-- 查看告警日志内容（使用 ADRCI）
adrci
ADRCI> show alert -tail 100
ADRCI> show alert -p "message_text like '%ORA-%'"
ADRCI> exit
```



### 14.2 跟踪文件（Trace Files）

sql

```
-- 查看当前会话的跟踪文件
SELECT value FROM v$diag_info WHERE name='Default Trace File';

-- 启用 SQL 跟踪
ALTER SESSION SET SQL_TRACE = TRUE;
-- 执行 SQL
ALTER SESSION SET SQL_TRACE = FALSE;

-- 使用 DBMS_MONITOR 跟踪
EXEC DBMS_MONITOR.SESSION_TRACE_ENABLE(
  session_id => 123,
  serial_num => 456,
  waits => TRUE,
  binds => TRUE
);
```



### 14.3 ADR（Automatic Diagnostic Repository）

ADR 是 Oracle 的统一诊断框架。

bash

```
# 查看 ADR 主目录
adrci
ADRCI> show homes
ADRCI> show problem
ADRCI> show incident
ADRCI> show tracefile
```



### 14.4 日志文件管理

sql

```
-- 查看联机重做日志
SELECT group#, thread#, sequence#, bytes/1024/1024 AS mb,
       members, status, archived 
FROM v$log;

-- 查看归档日志
SELECT name, sequence#, first_time, next_time, applied
FROM v$archived_log
ORDER BY sequence# DESC;

-- 强制日志切换
ALTER SYSTEM SWITCH LOGFILE;

-- 强制检查点
ALTER SYSTEM CHECKPOINT;
```



## 15. 升级与迁移管理

### 15.1 升级到 Oracle 19c

从 Oracle Database 19c 开始，Database Upgrade Assistant (DBUA) 被 AutoUpgrade 工具取代。

**使用 AutoUpgrade 工具：**

bash

```
# 下载 AutoUpgrade 工具
# 创建配置文件
java -jar autoupgrade.jar -create_sample_file config

# 执行升级前分析
java -jar autoupgrade.jar -config config.cfg -mode analyze

# 执行升级（部署模式）
java -jar autoupgrade.jar -config config.cfg -mode deploy

# 查看升级状态
java -jar autoupgrade.jar -config config.cfg -mode status
```



**手动升级（使用 Parallel Upgrade Utility）：**

bash

```
# 运行升级脚本
cd $ORACLE_HOME/rdbms/admin
sqlplus / as sysdba
SQL> STARTUP UPGRADE;
SQL> @catupgrd.sql
SQL> SHUTDOWN IMMEDIATE;
SQL> STARTUP;
SQL> @utlrp.sql  -- 编译无效对象
```



### 15.2 从 11g 迁移到 19c

从 Oracle 11g 迁移到 19c 的推荐方法：

1. **使用 RMAN 备份与恢复**
2. **使用 Data Guard 切换**
3. **使用数据泵（Data Pump）导入导出**
4. **使用 PDB 插拔（非 CDB 转 PDB）**

**非 CDB 转 PDB：**

sql

```
-- 1. 在源库执行
EXEC DBMS_PDB.DESCRIBE('/tmp/noncdb.xml');

-- 2. 关闭源库
SHUTDOWN IMMEDIATE;

-- 3. 在目标 CDB 中插入
CREATE PLUGGABLE DATABASE pdb_name
  USING '/tmp/noncdb.xml'
  COPY
  FILE_NAME_CONVERT = ('/source_path', '/dest_path');

-- 4. 打开 PDB
ALTER PLUGGABLE DATABASE pdb_name OPEN;

-- 5. 运行转换脚本
ALTER SESSION SET CONTAINER=pdb_name;
@$ORACLE_HOME/rdbms/admin/noncdb_to_pdb.sql
```



### 15.3 数据泵（Data Pump）

数据泵是 Oracle 的高性能数据导入导出工具。

bash

```
# 导出整个数据库
expdp system/password FULL=Y DIRECTORY=data_pump_dir DUMPFILE=full_export.dmp
  LOGFILE=full_export.log

# 导出指定 Schema
expdp system/password SCHEMAS=app_user DIRECTORY=data_pump_dir
  DUMPFILE=schema_export.dmp LOGFILE=schema_export.log

# 导出指定表
expdp system/password TABLES=app_user.table1,app_user.table2
  DIRECTORY=data_pump_dir DUMPFILE=table_export.dmp

# 导入整个数据库
impdp system/password FULL=Y DIRECTORY=data_pump_dir
  DUMPFILE=full_export.dmp LOGFILE=full_import.log

# 导入指定 Schema
impdp system/password SCHEMAS=app_user DIRECTORY=data_pump_dir
  DUMPFILE=schema_export.dmp LOGFILE=schema_import.log

# 导入时重命名 Schema
impdp system/password SCHEMAS=app_user REMAP_SCHEMA=app_user:new_user
  DIRECTORY=data_pump_dir DUMPFILE=schema_export.dmp
```



sql

```
-- 创建目录对象
CREATE DIRECTORY data_pump_dir AS '/u01/datapump';
GRANT READ, WRITE ON DIRECTORY data_pump_dir TO system;
```



## 16. 常用命令速查表

| 操作分类            | 命令                                                     |
| :------------------ | :------------------------------------------------------- |
| **数据库连接**      | `sqlplus / as sysdba`                                    |
| **查看实例状态**    | `SELECT status FROM v$instance;`                         |
| **查看数据库**      | `SELECT name, open_mode FROM v$database;`                |
| **查看当前容器**    | `SHOW CON_NAME;`                                         |
| **查看所有 PDB**    | `SHOW PDBS;`                                             |
| **切换容器**        | `ALTER SESSION SET CONTAINER=container_name;`            |
| **查看参数**        | `SHOW PARAMETER parameter_name;`                         |
| **修改参数**        | `ALTER SYSTEM SET parameter=value SCOPE=BOTH;`           |
| **启动数据库**      | `STARTUP;`                                               |
| **关闭数据库**      | `SHUTDOWN IMMEDIATE;`                                    |
| **查看监听状态**    | `lsnrctl status`                                         |
| **启动监听**        | `lsnrctl start`                                          |
| **创建表空间**      | `CREATE TABLESPACE name DATAFILE 'file' SIZE 100M;`      |
| **查看表空间**      | `SELECT * FROM dba_tablespaces;`                         |
| **创建用户**        | `CREATE USER username IDENTIFIED BY password;`           |
| **授予权限**        | `GRANT privilege TO username;`                           |
| **RMAN 备份**       | `RMAN> BACKUP DATABASE;`                                 |
| **RMAN 恢复**       | `RMAN> RESTORE DATABASE; RECOVER DATABASE;`              |
| **查看备份**        | `RMAN> LIST BACKUP SUMMARY;`                             |
| **查看 CRS 状态**   | `crsctl status res -t`                                   |
| **查看 RAC 数据库** | `srvctl status database -d db_name`                      |
| **启动 CRS**        | `crsctl start crs`                                       |
| **停止 CRS**        | `crsctl stop crs`                                        |
| **生成 AWR 报告**   | `@$ORACLE_HOME/rdbms/admin/awrrpt.sql`                   |
| **查看等待事件**    | `SELECT * FROM v$system_event WHERE wait_class!='Idle';` |
| **查看当前会话**    | `SELECT * FROM v$session WHERE username IS NOT NULL;`    |
| **启用闪回**        | `ALTER DATABASE FLASHBACK ON;`                           |
| **闪回查询**        | `SELECT * FROM table AS OF TIMESTAMP TO_TIMESTAMP(...);` |
| **闪回表**          | `FLASHBACK TABLE table TO BEFORE DROP;`                  |
| **查看告警日志**    | `adrci> show alert -tail 100`                            |
| **查看归档状态**    | `ARCHIVE LOG LIST;`                                      |
| **切换日志**        | `ALTER SYSTEM SWITCH LOGFILE;`                           |

**文档结束**