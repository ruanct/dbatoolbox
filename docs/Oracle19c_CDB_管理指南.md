

以下是 Oracle 19c CDB（容器数据库）的详细管理指南，每个管理领域均附带可直接使用的操作命令。

------

## 一、架构与基础概念

### 1.1 核心组件

| 组件                      | 说明                               |
| :------------------------ | :--------------------------------- |
| **CDB$ROOT**              | 根容器，存储系统元数据和公共用户   |
| **PDB$SEED**              | 种子模板，只读，用于创建新 PDB     |
| **PDB**                   | 可插拔数据库，独立的数据和应用单元 |
| **Application Container** | 可选的应用容器，用于PDB间共享对象  |

### 1.2 查看CDB基本信息

sql

```
-- 确认是否为CDB模式
SELECT name, cdb FROM v$database;          -- cdb=YES 表示是CDB[reference:5]

-- 查看当前所在容器
SHOW CON_NAME;                              -- 显示当前容器名称[reference:6][reference:7]

-- 查看所有PDB列表
SHOW PDBS;                                  -- 显示所有PDB及其状态[reference:8][reference:9]
SELECT name, open_mode FROM v$pdbs;        -- 更详细的PDB状态查询[reference:10]

-- 查看当前容器ID
SELECT sys_context('userenv', 'con_name') FROM dual;
```



## 二、CDB的创建

### 2.1 使用DBCA图形化创建

直接在图形界面中按向导操作，选择“创建容器数据库”选项。

### 2.2 使用DBCA静默命令行创建

bash

```
dbca -createDatabase \
     -silent \
     -gdbName ORCLCDB \
     -sid ORCLCDB \
     -createAsContainerDatabase true \
     -numberOfPDBs 1 \
     -pdbName ORCLPDB \
     -pdbAdminPassword password \
     -sysPassword password \
     -systemPassword password \
     -storageType FS \
     -datafileDestination '/u01/oradata' \
     -redoLogFileSize 200 \
     -emConfiguration NONE \
     -sampleSchema false
```



### 2.3 手动创建CDB

**步骤1：准备初始化参数文件**

bash

```
# 创建 initORCLCDB.ora 参数文件
DB_NAME=ORCLCDB
CONTROL_FILES='/u01/oradata/ORCLCDB/control01.ctl'
DB_BLOCK_SIZE=8192
```



**步骤2：启动实例并创建CDB**

sql

```
STARTUP NOMOUNT;

CREATE DATABASE ORCLCDB
  USER SYS IDENTIFIED BY password
  USER SYSTEM IDENTIFIED BY password
  LOGFILE GROUP 1 ('/u01/oradata/ORCLCDB/redo01a.log') SIZE 200M,
          GROUP 2 ('/u01/oradata/ORCLCDB/redo02a.log') SIZE 200M,
          GROUP 3 ('/u01/oradata/ORCLCDB/redo03a.log') SIZE 200M
  MAXLOGFILES 5
  MAXLOGMEMBERS 5
  MAXDATAFILES 100
  CHARACTER SET AL32UTF8
  NATIONAL CHARACTER SET AL16UTF16
  EXTENT MANAGEMENT LOCAL
  DATAFILE '/u01/oradata/ORCLCDB/system01.dbf' SIZE 700M REUSE
  SYSAUX DATAFILE '/u01/oradata/ORCLCDB/sysaux01.dbf' SIZE 550M REUSE
  DEFAULT TABLESPACE users
    DATAFILE '/u01/oradata/ORCLCDB/users01.dbf' SIZE 500M REUSE
  DEFAULT TEMPORARY TABLESPACE temp
    TEMPFILE '/u01/oradata/ORCLCDB/temp01.dbf' SIZE 20M REUSE
  UNDO TABLESPACE undotbs1
    DATAFILE '/u01/oradata/ORCLCDB/undotbs01.dbf' SIZE 200M REUSE
  ENABLE PLUGGABLE DATABASE                      -- 关键：启用多租户
    SEED
    FILE_NAME_CONVERT = ('/u01/oradata/ORCLCDB/pdbseed',
                         '/u01/oradata/ORCLCDB/pdbseed/')
    SYSTEM DATAFILES SIZE 250M AUTOEXTEND ON NEXT 10M MAXSIZE UNLIMITED
    SYSAUX DATAFILES SIZE 350M AUTOEXTEND ON NEXT 10M MAXSIZE UNLIMITED;
```



**步骤3：运行脚本完成创建**

sql

```
@$ORACLE_HOME/rdbms/admin/catcdb.sql
```



## 三、PDB的创建与管理

### 3.1 从PDB$SEED创建PDB

sql

```
-- 切换到CDB$ROOT（创建PDB必须在此执行）[reference:11]
ALTER SESSION SET CONTAINER=CDB$ROOT;

-- 创建PDB，指定管理员用户
CREATE PLUGGABLE DATABASE pdb1
  ADMIN USER pdb_admin IDENTIFIED BY password
  ROLES = (DBA)
  DEFAULT TABLESPACE users
  STORAGE (MAXSIZE 2G)
  FILE_NAME_CONVERT = ('/u01/oradata/ORCLCDB/pdbseed',
                       '/u01/oradata/ORCLCDB/pdb1');      -- 数据文件路径映射[reference:12][reference:13]

-- 打开PDB
ALTER PLUGGABLE DATABASE pdb1 OPEN;

-- 保存状态（CDB重启后自动打开）
ALTER PLUGGABLE DATABASE pdb1 SAVE STATE;                [reference:14]
```



### 3.2 克隆现有PDB

**同CDB内克隆：**

sql

```
ALTER SESSION SET CONTAINER=CDB$ROOT;

CREATE PLUGGABLE DATABASE pdb2 FROM pdb1
  FILE_NAME_CONVERT = ('/u01/oradata/ORCLCDB/pdb1',
                       '/u01/oradata/ORCLCDB/pdb2');
```



**跨CDB远程克隆（19c新特性）：**

bash

```
dbca -silent -createPluggableDatabase \
     -sourceDB CDB1 \
     -pdbName pdb2 \
     -createFromRemotePDB true \
     -remotePDBName pdb1 \
     -remoteDBConnectionString host:port/service \
     -remoteDBSYSPwd password                    [reference:15]
```



### 3.3 可刷新克隆（Refreshable Clone）

sql

```
-- 创建可定期从源PDB同步的克隆
CREATE PLUGGABLE DATABASE pdb_refresh FROM pdb1
  REFRESH MODE EVERY 60 MINUTES                   -- 每60分钟刷新一次
  FILE_NAME_CONVERT = ('/u01/oradata/ORCLCDB/pdb1',
                       '/u01/oradata/ORCLCDB/pdb_refresh');
```



### 3.4 删除PDB

sql

```
-- 删除PDB并同时删除数据文件
DROP PLUGGABLE DATABASE pdb1 INCLUDING DATAFILES;   [reference:16]
```



### 3.5 拔出与插入PDB

**拔出PDB：**

sql

```
-- 关闭PDB
ALTER PLUGGABLE DATABASE pdb1 CLOSE IMMEDIATE;

-- 拔出，生成XML清单文件
ALTER PLUGGABLE DATABASE pdb1 UNPLUG INTO '/home/oracle/pdb1.xml';   [reference:17]
```



**插入PDB：**

sql

```
-- 使用XML文件插入（数据文件不复制）
CREATE PLUGGABLE DATABASE pdb1 USING '/home/oracle/pdb1.xml' NOCOPY; [reference:18]

-- 使用XML文件插入并复制数据文件
CREATE PLUGGABLE DATABASE pdb1 USING '/home/oracle/pdb1.xml'
  COPY
  FILE_NAME_CONVERT = ('/source_path', '/dest_path');
```



## 四、启动与关闭管理

### 4.1 CDB实例的启动与关闭

**关闭CDB：**

sql

```
SHUTDOWN IMMEDIATE;     -- 立即关闭（推荐）
SHUTDOWN NORMAL;        -- 正常关闭（等待所有会话断开）
SHUTDOWN ABORT;         -- 异常关闭（仅紧急情况）
```



**启动CDB：**

sql

```
-- 启动到NOMOUNT状态（仅实例，不加载数据库）
STARTUP NOMOUNT;

-- 装载数据库到MOUNT状态
ALTER DATABASE MOUNT;

-- 打开数据库
ALTER DATABASE OPEN;
```



**一步到位的启动：**

sql

```
STARTUP;                -- 等同于 STARTUP OPEN
```



### 4.2 PDB的启动与关闭

**在CDB$ROOT中操作：**

sql

```
-- 打开指定PDB
ALTER PLUGGABLE DATABASE pdb1 OPEN;

-- 以只读方式打开
ALTER PLUGGABLE DATABASE pdb1 OPEN READ ONLY;      [reference:22]

-- 以受限模式打开（仅管理员可访问）
ALTER PLUGGABLE DATABASE pdb1 OPEN RESTRICTED;     [reference:23]

-- 强制打开（忽略某些错误）
ALTER PLUGGABLE DATABASE pdb1 OPEN READ WRITE FORCE;

-- 关闭指定PDB
ALTER PLUGGABLE DATABASE pdb1 CLOSE;

-- 立即关闭PDB
ALTER PLUGGABLE DATABASE pdb1 CLOSE IMMEDIATE;
```



**批量操作：**

sql

```
-- 打开所有PDB
ALTER PLUGGABLE DATABASE ALL OPEN;                 [reference:24]

-- 关闭所有PDB
ALTER PLUGGABLE DATABASE ALL CLOSE;                [reference:25]

-- 排除特定PDB
ALTER PLUGGABLE DATABASE ALL EXCEPT pdb1 OPEN;     [reference:26]
```



**在PDB内部操作：**

sql

```
-- 切换到PDB
ALTER SESSION SET CONTAINER=pdb1;

-- 在PDB内使用传统命令
STARTUP;        -- 打开当前PDB
SHUTDOWN;       -- 关闭当前PDB                     [reference:27]
```



### 4.3 设置PDB开机自启

**方法1：保存状态**

sql

```
ALTER PLUGGABLE DATABASE pdb1 OPEN;
ALTER PLUGGABLE DATABASE pdb1 SAVE STATE;          [reference:28]

-- 取消保存状态
ALTER PLUGGABLE DATABASE pdb1 DISCARD STATE;       [reference:29]
```



**方法2：创建触发器**

sql

```
CREATE TRIGGER open_all_pdbs
AFTER STARTUP ON DATABASE
BEGIN
  EXECUTE IMMEDIATE 'ALTER PLUGGABLE DATABASE ALL OPEN';
END open_all_pdbs;
/                                                [reference:30]
```



## 五、存储管理

### 5.1 OMF（Oracle Managed Files）配置

OMF让Oracle自动管理数据文件的命名和位置，避免手动指定路径。

sql

```
-- 查看当前OMF设置
SHOW PARAMETER db_create_file_dest;

-- 设置OMF目标目录（文件系统）
ALTER SYSTEM SET db_create_file_dest = '/u02/oradata' SCOPE=BOTH;

-- 设置OMF目标目录（ASM）
ALTER SYSTEM SET db_create_file_dest = '+DATA' SCOPE=BOTH;    [reference:32]
```



### 5.2 表空间管理

**在PDB中创建永久表空间：**

sql

```
-- 先切换到目标PDB
ALTER SESSION SET CONTAINER=pdb1;

-- 创建表空间（指定数据文件）
CREATE TABLESPACE app_data
  DATAFILE '/u01/oradata/ORCLCDB/pdb1/app_data01.dbf' SIZE 100M
  AUTOEXTEND ON NEXT 100M MAXSIZE 2G
  EXTENT MANAGEMENT LOCAL
  SEGMENT SPACE MANAGEMENT AUTO;                             [reference:34]

-- 创建大文件表空间（推荐）
CREATE BIGFILE TABLESPACE app_data
  DATAFILE SIZE 100M
  AUTOEXTEND ON NEXT 100M MAXSIZE UNLIMITED;                 [reference:35]
```



**创建临时表空间：**

sql

```
CREATE TEMPORARY TABLESPACE temp_app
  TEMPFILE '/u01/oradata/ORCLCDB/pdb1/temp_app01.dbf' SIZE 50M
  AUTOEXTEND ON NEXT 50M MAXSIZE 500M;
```



**管理表空间：**

sql

```
-- 查看表空间
SELECT tablespace_name, status FROM dba_tablespaces;

-- 扩展表空间（添加数据文件）
ALTER TABLESPACE app_data
  ADD DATAFILE '/u01/oradata/ORCLCDB/pdb1/app_data02.dbf' SIZE 100M;   [reference:36]

-- 扩展表空间（使用OMF自动命名）
ALTER TABLESPACE app_data
  ADD DATAFILE SIZE 100M AUTOEXTEND ON NEXT 100M;             [reference:37]

-- 修改数据文件大小
ALTER DATABASE DATAFILE '/u01/oradata/ORCLCDB/pdb1/app_data01.dbf'
  RESIZE 500M;

-- 设置表空间离线
ALTER TABLESPACE app_data OFFLINE NORMAL;                    [reference:38]

-- 设置表空间在线
ALTER TABLESPACE app_data ONLINE;

-- 删除表空间
DROP TABLESPACE app_data INCLUDING CONTENTS AND DATAFILES;

-- 在线移动数据文件位置[reference:39]
ALTER DATABASE MOVE DATAFILE '/old_path/app_data01.dbf'
  TO '/new_path/app_data01.dbf';
```



### 5.3 查看存储信息

sql

```
-- 查看PDB的数据文件
SELECT name, con_id FROM v$datafile ORDER BY con_id;         [reference:40]

-- 查看PDB的临时文件
SELECT name, con_id FROM v$tempfile ORDER BY con_id;         [reference:41]

-- 查看表空间使用情况
SELECT tablespace_name,
       ROUND(SUM(bytes)/1024/1024, 2) AS size_mb
FROM dba_data_files
GROUP BY tablespace_name;
```



## 六、用户与安全管理

### 6.1 公共用户与本地用户

**公共用户（Common User）** ：在CDB$ROOT中创建，名称必须以 `C##` 或 `c##` 开头，存在于所有PDB中。

**本地用户（Local User）** ：在特定PDB中创建，仅存在于该PDB中，名称不能以 `C##` 开头。

**创建公共用户：**

sql

```
-- 切换到CDB$ROOT
ALTER SESSION SET CONTAINER=CDB$ROOT;

-- 创建公共用户
CREATE USER C##admin IDENTIFIED BY password
  DEFAULT TABLESPACE users
  TEMPORARY TABLESPACE temp
  CONTAINER = ALL;                                          [reference:46]

-- 赋予公共用户权限（作用于所有PDB）
GRANT CREATE SESSION, DBA TO C##admin CONTAINER=ALL;
```



**创建本地用户：**

sql

```
-- 切换到目标PDB
ALTER SESSION SET CONTAINER=pdb1;

-- 创建本地用户（无需C##前缀）
CREATE USER app_user IDENTIFIED BY password
  DEFAULT TABLESPACE app_data
  TEMPORARY TABLESPACE temp_app;                            [reference:47]

-- 赋予本地用户权限
GRANT CONNECT, RESOURCE, DBA TO app_user;
```



**查看用户类型：**

sql

```
-- 查看公共用户（COMMON列=YES）
SELECT username, common, con_id FROM cdb_users WHERE common='YES';

-- 查看特定PDB中的用户
ALTER SESSION SET CONTAINER=pdb1;
SELECT username, account_status FROM dba_users;
```



### 6.2 PDB锁定配置文件（Lockdown Profile）

Lockdown Profile用于限制PDB中可执行的操作，增强安全性。

sql

```
-- 切换到CDB$ROOT
ALTER SESSION SET CONTAINER=CDB$ROOT;

-- 创建锁定配置文件
CREATE LOCKDOWN PROFILE pdb_sec_profile;                    [reference:50]

-- 禁用特定功能（例如禁用ALTER SYSTEM）
ALTER LOCKDOWN PROFILE pdb_sec_profile
  DISABLE FEATURE = 'ALTER SYSTEM';

-- 将配置文件应用于PDB
ALTER SYSTEM SET PDB_LOCKDOWN = pdb_sec_profile;            [reference:51]

-- 查看锁定配置文件
SELECT * FROM dba_lockdown_profiles;                        [reference:52]

-- 删除锁定配置文件
DROP LOCKDOWN PROFILE pdb_sec_profile;
```



### 6.3 审计配置

sql

```
-- 在CDB$ROOT中配置公共审计（应用于所有PDB）
ALTER SYSTEM SET audit_trail = DB, EXTENDED SCOPE=SPFILE;

-- 在特定PDB中配置本地审计
ALTER SESSION SET CONTAINER=pdb1;
AUDIT SELECT TABLE, INSERT TABLE, UPDATE TABLE, DELETE TABLE BY app_user;
```



## 七、备份与恢复管理

### 7.1 启用归档模式

sql

```
-- 设置归档日志位置
ALTER SYSTEM SET log_archive_dest_1 = 'location=/u01/archivelog' SCOPE=BOTH;
ALTER SYSTEM SET log_archive_format = 'arch_%t_%s_%r.arc' SCOPE=SPFILE;

-- 启用归档模式（需要重启）
SHUTDOWN IMMEDIATE;
STARTUP MOUNT;
ALTER DATABASE ARCHIVELOG;
ALTER DATABASE OPEN;

-- 确认归档状态
ARCHIVE LOG LIST;
```



### 7.2 RMAN备份

**备份整个CDB（所有PDB）：**

bash

```
rman target /
RMAN> BACKUP DATABASE PLUS ARCHIVELOG DELETE INPUT;         [reference:55]
```



**仅备份CDB$ROOT：**

bash

```
RMAN> BACKUP DATABASE ROOT;                                 [reference:56]
RMAN> BACKUP PLUGGABLE DATABASE "CDB$ROOT", "PDB$SEED";     [reference:57]
```



**备份指定PDB：**

bash

```
RMAN> BACKUP PLUGGABLE DATABASE pdb1, pdb2;                 [reference:58]
```



**增量备份PDB：**

bash

```
RMAN> BACKUP AS BACKUPSET INCREMENTAL LEVEL 0
      PLUGGABLE DATABASE pdb1;                               [reference:59]
```



**查看备份信息：**

bash

```
RMAN> LIST BACKUP OF DATABASE ROOT;                          [reference:60]
RMAN> LIST BACKUP OF PLUGGABLE DATABASE pdb1;               [reference:61]
RMAN> LIST BACKUP SUMMARY;                                  [reference:62]
```



### 7.3 RMAN恢复

**全库恢复（数据库需在MOUNT模式）：**

bash

```
RMAN> STARTUP MOUNT;
RMAN> RESTORE DATABASE;
RMAN> RECOVER DATABASE;
RMAN> ALTER DATABASE OPEN;
```



**恢复PDB：**

bash

```
RMAN> RESTORE PLUGGABLE DATABASE pdb1;
RMAN> RECOVER PLUGGABLE DATABASE pdb1;
RMAN> ALTER PLUGGABLE DATABASE pdb1 OPEN;
```



**恢复到指定时间点：**

bash

```
RMAN> RUN {
  SET UNTIL TIME "TO_DATE('2026-06-22 10:00:00','YYYY-MM-DD HH24:MI:SS')";
  RESTORE PLUGGABLE DATABASE pdb1;
  RECOVER PLUGGABLE DATABASE pdb1;
}
```



### 7.4 Flashback PDB（闪回）

sql

```
-- 将PDB闪回到指定时间
ALTER PLUGGABLE DATABASE pdb1 FLASHBACK TO TIMESTAMP
  TO_TIMESTAMP('2026-06-22 10:00:00', 'YYYY-MM-DD HH24:MI:SS');

-- 将PDB闪回到SCN
ALTER PLUGGABLE DATABASE pdb1 FLASHBACK TO SCN 1234567;
```



## 八、性能与资源管理

### 8.1 Oracle Resource Manager

Resource Manager用于在多个PDB之间分配CPU等资源。

**创建CDB资源计划：**

sql

```
-- 切换到CDB$ROOT
ALTER SESSION SET CONTAINER=CDB$ROOT;

-- 创建待定区域
EXEC DBMS_RESOURCE_MANAGER.CREATE_PENDING_AREA();           [reference:66]

-- 创建CDB资源计划
EXEC DBMS_RESOURCE_MANAGER.CREATE_PLAN(
  PLAN => 'cdb_plan',
  COMMENT => 'CDB Resource Plan for PDBs'
);

-- 为PDB创建资源指令（分配CPU份额）
EXEC DBMS_RESOURCE_MANAGER.CREATE_CDB_PLAN_DIRECTIVE(
  PLAN => 'cdb_plan',
  PLUGGABLE_DATABASE => 'pdb1',
  SHARES => 3,                        -- CPU份额（权重）
  UTILIZATION_LIMIT => 80,            -- CPU使用上限百分比
  PARALLEL_SERVER_LIMIT => 10
);

EXEC DBMS_RESOURCE_MANAGER.CREATE_CDB_PLAN_DIRECTIVE(
  PLAN => 'cdb_plan',
  PLUGGABLE_DATABASE => 'pdb2',
  SHARES => 1,
  UTILIZATION_LIMIT => 40,
  PARALLEL_SERVER_LIMIT => 5
);

-- 验证并提交
EXEC DBMS_RESOURCE_MANAGER.VALIDATE_PENDING_AREA;
EXEC DBMS_RESOURCE_MANAGER.SUBMIT_PENDING_AREA;

-- 启用资源计划
ALTER SYSTEM SET RESOURCE_MANAGER_PLAN = 'cdb_plan';        [reference:67]
```



**查看资源计划状态：**

sql

```
SELECT name, is_enabled FROM v$rsrc_plan;
SELECT pdb_name, shares, utilization_limit
FROM dba_cdb_rsrc_plan_directives;
```



### 8.2 PDB级别参数设置

sql

```
-- 在PDB中设置特定参数
ALTER SESSION SET CONTAINER=pdb1;
ALTER SYSTEM SET optimizer_mode = FIRST_ROWS SCOPE=BOTH;

-- 设置PDB的CPU限制
ALTER SYSTEM SET cpu_count = 4 SCOPE=BOTH;                  [reference:68]

-- 查看PDB参数
SHOW PARAMETER;
```



### 8.3 性能监控视图

sql

```
-- 查看所有PDB状态
SELECT con_id, name, open_mode, restricted
FROM v$pdbs;

-- 查看PDB资源使用统计
SELECT pdb_name, cpu_usage, io_requests
FROM v$rsrc_pdb_metrics;

-- 查看当前会话所在容器
SELECT sys_context('userenv', 'con_name') AS container_name FROM dual;

-- 查看PDB的等待事件
SELECT event, total_waits, time_waited
FROM v$system_event
WHERE con_id = (SELECT con_id FROM v$pdbs WHERE name = 'pdb1');
```



## 九、应用容器管理（Application Container）

应用容器允许在多个PDB之间共享应用对象（表结构、数据等）。

### 9.1 创建应用容器

sql

```
-- 切换到CDB$ROOT
ALTER SESSION SET CONTAINER=CDB$ROOT;

-- 创建应用根容器
CREATE PLUGGABLE DATABASE app_root
  AS APPLICATION CONTAINER                                   -- 关键：指定为应用容器
  ADMIN USER app_admin IDENTIFIED BY password
  DEFAULT TABLESPACE users
  FILE_NAME_CONVERT = ('/u01/oradata/ORCLCDB/pdbseed',
                       '/u01/oradata/ORCLCDB/app_root');    [reference:71]

-- 打开应用根容器
ALTER PLUGGABLE DATABASE app_root OPEN;
```



### 9.2 在应用容器中创建应用PDB

sql

```
-- 切换到应用根容器
ALTER SESSION SET CONTAINER=app_root;

-- 创建应用PDB
CREATE PLUGGABLE DATABASE app_pdb1
  ADMIN USER app_pdb_admin IDENTIFIED BY password
  FILE_NAME_CONVERT = ('/u01/oradata/ORCLCDB/pdbseed',
                       '/u01/oradata/ORCLCDB/app_pdb1');

-- 打开应用PDB
ALTER PLUGGABLE DATABASE app_pdb1 OPEN;
```



### 9.3 在应用根中创建共享对象

sql

```
-- 切换到应用根容器
ALTER SESSION SET CONTAINER=app_root;

-- 创建共享表（元数据链接 - 结构共享，数据独立）
CREATE TABLE shared_products
  (product_id NUMBER PRIMARY KEY,
   product_name VARCHAR2(100))
  CONTAINER = ALL;                         -- 所有应用PDB共享表结构

-- 创建共享表（数据链接 - 结构和数据完全共享）
CREATE TABLE shared_codes
  (code_type VARCHAR2(20),
   code_value VARCHAR2(20))
  CONTAINER = DATA;                        -- 数据在所有PDB中共享
```



## 十、数据库链接（Database Link）

PDB之间可以通过数据库链接进行跨容器访问。

sql

```
-- 在PDB1中创建指向PDB2的数据库链接
ALTER SESSION SET CONTAINER=pdb1;

CREATE DATABASE LINK pdb2_link
  CONNECT TO app_user IDENTIFIED BY password
  USING 'pdb2_service';                    -- 使用PDB2的服务名

-- 通过链接查询PDB2中的数据
SELECT * FROM remote_table@pdb2_link;
```



## 十一、常用管理命令速查表

| 操作           | 命令                                                         |
| :------------- | :----------------------------------------------------------- |
| 查看当前容器   | `SHOW CON_NAME;`                                             |
| 查看所有PDB    | `SHOW PDBS;` 或 `SELECT * FROM v$pdbs;`                      |
| 切换到CDB$ROOT | `ALTER SESSION SET CONTAINER=CDB$ROOT;`                      |
| 切换到PDB      | `ALTER SESSION SET CONTAINER=pdb_name;`                      |
| 创建PDB        | `CREATE PLUGGABLE DATABASE pdb_name ...;`                    |
| 删除PDB        | `DROP PLUGGABLE DATABASE pdb_name INCLUDING DATAFILES;`      |
| 打开PDB        | `ALTER PLUGGABLE DATABASE pdb_name OPEN;`                    |
| 关闭PDB        | `ALTER PLUGGABLE DATABASE pdb_name CLOSE;`                   |
| 打开所有PDB    | `ALTER PLUGGABLE DATABASE ALL OPEN;`                         |
| 保存PDB状态    | `ALTER PLUGGABLE DATABASE pdb_name SAVE STATE;`              |
| 创建公共用户   | `CREATE USER C##username IDENTIFIED BY password CONTAINER=ALL;` |
| 创建本地用户   | `CREATE USER username IDENTIFIED BY password;`               |
| 查看备份       | `RMAN> LIST BACKUP SUMMARY;`                                 |
| 备份PDB        | `RMAN> BACKUP PLUGGABLE DATABASE pdb_name;`                  |
| 启用资源计划   | `ALTER SYSTEM SET RESOURCE_MANAGER_PLAN = 'plan_name';`      |

## 十二、总结

Oracle 19c CDB管理的核心原则是：

1. **创建和删除PDB** 必须在 **CDB$ROOT** 中执行
2. **业务数据、表空间、本地用户** 必须在 **PDB内部** 创建
3. **公共用户**（`C##`开头）存在于所有PDB，**本地用户**仅存在于所属PDB
4. 备份恢复既可以在**CDB级别**统一执行，也可以在**PDB级别**独立执行
5. 资源管理通过 **Resource Manager** 在CDB层面统一调控
6. 应用容器实现了PDB之间**可控的数据和元数据共享**