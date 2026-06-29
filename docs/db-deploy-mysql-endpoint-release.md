# 失败任务 — 释放端点占用

## 原因

创建任务时 `ensure_deploy_endpoint_available` 会拒绝与以下冲突的端点 `(engine, connect_host, port, db_name)`：

- CMDB 已有实例
- 状态为 `pending` / `prechecking` / `running` / `verifying` / **`failed`** 的其它部署任务

失败任务若不再续跑，会阻塞同 IP:端口 新建任务。

## 操作

1. 任务详情 → **「释放端点」**
2. 或 API：`POST /db-deploy/api/{id}/` body `{"action": "release_endpoint"}`
3. 状态变为 `cancelled`，端点释放，任务记录保留

**限制**：仅 `failed` 且 `instance_id` 为空。

## 同主机互斥（另见）

同一目标主机在 `pending` / `prechecking` / `running` / `verifying` 仅允许一个部署任务（`ensure_host_deploy_lock_available`）。与端点释放无关；失败任务 `cancelled` 后不占主机锁。

## 与删除的区别

| 操作 | 释放端点 | 保留记录 |
|------|:--------:|:--------:|
| release_endpoint | ✅ | ✅ |
| DELETE 任务 | ✅ | ❌ |
