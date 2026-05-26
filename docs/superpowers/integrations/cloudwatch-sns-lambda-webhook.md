# CloudWatch Alarm → SNS → Lambda → kiro-devops 告警接入方案

## 1. 架构概述

```
CloudWatch Alarm
        │ 状态变化 (ALARM / OK / INSUFFICIENT_DATA)
        ▼
    SNS Topic
        │ 推送消息
        ▼
  Lambda 函数
        │ 转换格式 + 签名认证
        ▼
kiro-devops Webhook (:8080/event)
        │ 入库 + 触发 Kiro 分析
        ▼
   飞书 / 微信 推送
```

**为什么需要 Lambda 中转？**

- CloudWatch Alarm 原生仅支持 **SNS** 作为通知渠道，无法直接调用 HTTP Webhook
- Lambda 作为轻量"胶水"层，负责格式转换、认证头注入、错误重试
- SNS 天然支持死信队列（DLQ），告警不会丢失

---

## 2. 数据映射

| CloudWatch Alarm 字段 | kiro webhook 字段 | 说明 |
|----------------------|-------------------|------|
| `AlarmName` | `title` | `[CloudWatch] {AlarmName}` |
| `AlarmDescription` | `description` | 拼接状态变化与原因 |
| `NewStateValue` | `event_type` + `severity` | ALARM→指标异常/high, OK→故障处理/medium |
| `Trigger.Dimensions` | `entities` | 如 `InstanceId=i-xxx`, `ClusterName=chris-eks` |
| `StateChangeTime` | `timestamp` | ISO 8601 格式 |
| `AlarmName` + `StateChangeTime` | `id` | 幂等键，防止重复入库 |
| 固定值 | `source` | `cloudwatch` |
| 固定值 | `user_id` | `system` |

---

## 3. 前置条件

- kiro-devops 已部署在 EC2 且 Webhook 服务可访问
- 已知环境变量：
  - `WEBHOOK_TOKEN` = `kiro-alert-secret-2026`
  - `WEBHOOK_PORT` = `8080`
  - EC2 公网 IP = `69.231.143.86`（当前）
- AWS CLI 已配置（或用 AWS Console 手动操作）

---

## 4. 配置步骤

### 4.1 创建 SNS Topic

**AWS Console：**
1. 进入 SNS → Topics → Create topic
2. Type: **Standard**
3. Name: `kiro-cloudwatch-alerts`
4. 其余默认 → Create topic

**AWS CLI：**

```bash
aws sns create-topic --name kiro-cloudwatch-alerts
# 记录返回的 TopicArn，如：
# arn:aws:sns:cn-northwest-1:123456789012:kiro-cloudwatch-alerts
```

### 4.2 创建 Lambda 执行角色 (IAM Role)

**信任策略** (`lambda-trust-policy.json`)：

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": { "Service": "lambda.amazonaws.com" },
      "Action": "sts:AssumeRole"
    }
  ]
}
```

**创建命令：**

```bash
aws iam create-role \
  --role-name kiro-webhook-lambda-role \
  --assume-role-policy-document file://lambda-trust-policy.json

# 附加基础权限（CloudWatch Logs）
aws iam attach-role-policy \
  --role-name kiro-webhook-lambda-role \
  --policy-arn arn:aws-cn:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
```

### 4.3 部署 Lambda 函数

**方法一：AWS Console（推荐首次）**

1. Lambda → Create function
2. Function name: `kiro-cloudwatch-webhook-bridge`
3. Runtime: **Python 3.11**
4. Architecture: **x86_64**
5. Execution role: 选择上一步创建的 `kiro-webhook-lambda-role`
6. 粘贴下方【Lambda 源码】到代码编辑器
7. Deploy

**方法二：AWS CLI**

```bash
# 先保存源码为 lambda_function.py，然后打包
zip function.zip lambda_function.py

aws lambda create-function \
  --function-name kiro-cloudwatch-webhook-bridge \
  --runtime python3.11 \
  --handler lambda_function.lambda_handler \
  --role arn:aws-cn:iam::123456789012:role/kiro-webhook-lambda-role \
  --zip-file fileb://function.zip \
  --timeout 30 \
  --memory-size 256 \
  --environment "Variables={KIRO_WEBHOOK_URL=http://69.231.143.86:8080/event,KIRO_WEBHOOK_TOKEN=kiro-alert-secret-2026}"
```

> **注意**：若 EC2 公网 IP 变动，后续仅需更新 Lambda 环境变量 `KIRO_WEBHOOK_URL`。

### 4.4 配置 Lambda 环境变量

| Key | Value | 说明 |
|-----|-------|------|
| `KIRO_WEBHOOK_URL` | `http://69.231.143.86:8080/event` | kiro webhook 地址 |
| `KIRO_WEBHOOK_TOKEN` | `kiro-alert-secret-2026` | 与 `.env` 中 `WEBHOOK_TOKEN` 一致 |

### 4.5 添加 SNS 触发器

**AWS Console：**
1. 进入 Lambda → `kiro-cloudwatch-webhook-bridge` → Add trigger
2. Source: **SNS**
3. SNS topic: `kiro-cloudwatch-alerts`
4. 勾选 **Enable trigger** → Add

**AWS CLI：**

```bash
aws lambda create-event-source-mapping \
  --function-name kiro-cloudwatch-webhook-bridge \
  --event-source-arn arn:aws:sns:cn-northwest-1:123456789012:kiro-cloudwatch-alerts \
  --enabled

# 或者通过 SNS 订阅方式
aws sns subscribe \
  --topic-arn arn:aws:sns:cn-northwest-1:123456789012:kiro-cloudwatch-alerts \
  --protocol lambda \
  --notification-endpoint arn:aws-cn:lambda:cn-northwest-1:123456789012:function:kiro-cloudwatch-webhook-bridge
```

> **重要**：首次订阅后，需在 Lambda 控制台点击 SNS trigger 的 **Enable trigger**，或在 SNS 侧确认订阅。

### 4.6 配置 CloudWatch Alarm 动作

**AWS Console：**
1. CloudWatch → Alarms → 选择目标 Alarm（如 `chris-eks-node-down`）
2. Actions → Edit
3. 在 **Notification** 区域：
   - Alarm state trigger: **In alarm**（触发告警时）
   - Send notification to: `kiro-cloudwatch-alerts`
   - 如需恢复通知，再添加一个 **OK** 状态的动作指向同一个 SNS Topic
4. Save changes

**AWS CLI：**

```bash
aws cloudwatch put-metric-alarm \
  --alarm-name chris-eks-node-down \
  --alarm-description "EKS 节点下线检测" \
  --metric-name StatusCheckFailed \
  --namespace AWS/EC2 \
  --statistic Average \
  --period 60 \
  --evaluation-periods 2 \
  --threshold 1 \
  --comparison-operator GreaterThanOrEqualToThreshold \
  --dimensions Name=InstanceId,Value=i-0b39e60ab69a7c3f7 \
  --alarm-actions arn:aws:sns:cn-northwest-1:123456789012:kiro-cloudwatch-alerts \
  --ok-actions arn:aws:sns:cn-northwest-1:123456789012:kiro-cloudwatch-alerts
```

### 4.7 配置 EC2 安全组（网络连通性）

Lambda 访问 EC2:8080 需要安全组放行。

**方案 A：公网访问（最简单，当前适用）**

Lambda 不挂载 VPC，通过公网 IP 访问 EC2。安全组入站规则：

| Type | Protocol | Port | Source | 说明 |
|------|----------|------|--------|------|
| Custom TCP | TCP | 8080 | `0.0.0.0/0` 或 Lambda 出口 IP 段 | 生产环境建议限制为 Lambda 所在区域 IP 范围 |

**方案 B：私网访问（生产推荐）**

1. Lambda 配置 VPC Access（选择 EC2 所在 VPC + 子网 + 安全组）
2. EC2 安全组入站规则：

| Type | Protocol | Port | Source |
|------|----------|------|--------|
| Custom TCP | TCP | 8080 | Lambda 安全组 ID（如 `sg-0xxxxx`） |

---

## 5. Lambda 源码

保存为 `lambda_function.py`，直接上传即可（零外部依赖）。

```python
"""CloudWatch Alarm SNS → kiro-devops Webhook 中转 Lambda"""
import json
import os
import urllib.request
import urllib.error
from datetime import datetime

WEBHOOK_URL = os.environ["KIRO_WEBHOOK_URL"]
WEBHOOK_TOKEN = os.environ["KIRO_WEBHOOK_TOKEN"]


def _parse_cloudwatch_alarm(sns_message: dict) -> dict:
    """将 CloudWatch Alarm SNS 消息转换为 kiro webhook payload。"""
    alarm_name = sns_message.get("AlarmName", "UnknownAlarm")
    alarm_desc = sns_message.get("AlarmDescription", "")
    new_state = sns_message.get("NewStateValue", "UNKNOWN")
    old_state = sns_message.get("OldStateValue", "UNKNOWN")
    reason = sns_message.get("NewStateReason", "")
    timestamp_raw = sns_message.get("StateChangeTime", "")

    # 统一时间戳为 ISO 8601
    try:
        ts = datetime.strptime(timestamp_raw, "%Y-%m-%dT%H:%M:%S.%f%z").isoformat()
    except ValueError:
        ts = timestamp_raw

    # 实体提取：从 Dimensions 构建 InstanceId=xxx, ClusterName=xxx
    trigger = sns_message.get("Trigger", {})
    dimensions = trigger.get("Dimensions", [])
    entities = [f"{d['name']}={d['value']}" for d in dimensions]
    if not entities:
        entities = [alarm_name]

    # 状态映射
    is_alarm = new_state == "ALARM"
    is_ok = new_state == "OK"
    event_type = "故障处理" if is_ok else "指标异常"
    severity = "high" if is_alarm else ("medium" if is_ok else "low")

    # 幂等 ID：alarm + timestamp + state
    event_id = f"cw-{alarm_name}-{ts}-{new_state}"

    title = f"[CloudWatch] {alarm_name}"
    if is_ok:
        title = f"[RESOLVED] {title}"

    description_lines = [
        f"状态变化: {old_state} → {new_state}",
        f"原因: {reason}",
    ]
    if alarm_desc:
        description_lines.insert(0, alarm_desc)

    return {
        "id": event_id,
        "event_type": event_type,
        "title": title,
        "description": "\n\n".join(description_lines),
        "entities": entities,
        "source": "cloudwatch",
        "severity": severity,
        "timestamp": ts,
        "user_id": "system",
    }


def _send_to_kiro(payload: dict) -> dict:
    """发送 POST 到 kiro webhook。"""
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        WEBHOOK_URL,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {WEBHOOK_TOKEN}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8")
            print(f"Kiro webhook OK: {resp.status} {body}")
            return {"ok": True, "status": resp.status, "body": body}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        print(f"Kiro webhook HTTPError: {e.code} {body}")
        raise
    except Exception as e:
        print(f"Kiro webhook failed: {e}")
        raise


def lambda_handler(event, context):
    """SNS 触发入口。"""
    records = event.get("Records", [])
    print(f"Received {len(records)} SNS record(s)")

    for record in records:
        sns = record.get("Sns", {})
        subject = sns.get("Subject", "")
        message_raw = sns.get("Message", "")

        # CloudWatch Alarm 的 Message 是 JSON 字符串
        try:
            message = json.loads(message_raw)
        except json.JSONDecodeError:
            print(f"Skip non-JSON message: {message_raw[:200]}")
            continue

        print(f"Processing alarm: {message.get('AlarmName')} state={message.get('NewStateValue')}")

        payload = _parse_cloudwatch_alarm(message)
        _send_to_kiro(payload)

    return {"statusCode": 200, "body": json.dumps({"processed": len(records)})}
```

---

## 6. 测试验证

### 6.1 直接测试 Lambda（绕过 CloudWatch）

在 Lambda Console → Test 中创建测试事件：

```json
{
  "Records": [
    {
      "Sns": {
        "Subject": "ALARM: chris-eks-node-down",
        "Message": "{\"AlarmName\":\"chris-eks-node-down\",\"AlarmDescription\":\"EKS 节点状态检查失败\",\"AWSAccountId\":\"123456789012\",\"NewStateValue\":\"ALARM\",\"OldStateValue\":\"OK\",\"NewStateReason\":\"Threshold Crossed: 1 datapoint was greater than the threshold.\",\"StateChangeTime\":\"2026-04-28T14:00:00.000+0000\",\"Trigger\":{\"MetricName\":\"StatusCheckFailed\",\"Namespace\":\"AWS/EC2\",\"Dimensions\":[{\"name\":\"InstanceId\",\"value\":\"i-0b39e60ab69a7c3f7\"}],\"Statistic\":\"Average\",\"ComparisonOperator\":\"GreaterThanThreshold\",\"Threshold\":1.0}}"
      }
    }
  ]
}
```

预期结果：
- Lambda 执行成功（Status: Succeeded）
- CloudWatch Logs 中可见 `Kiro webhook OK: 200 ...`
- kiro-devops 飞书收到告警分析消息（ severity=high 触发自动分析）

### 6.2 端到端测试（触发真实 Alarm）

临时修改 Alarm Threshold 使其极易触发，例如：

```bash
aws cloudwatch put-metric-alarm \
  --alarm-name chris-eks-node-down \
  --threshold 0 \
  --comparison-operator GreaterThanThreshold
```

等待 Alarm 进入 `ALARM` 状态，观察飞书是否收到推送。

测试完成后恢复原始阈值。

### 6.3 查看 kiro 事件库

```bash
python3 -c "
import sqlite3
conn = sqlite3.connect('/home/ubuntu/kiro-devops/events.db')
conn.row_factory = sqlite3.Row
rows = conn.execute(\"SELECT id, ts, event_type, severity, title FROM events WHERE source='cloudwatch' ORDER BY ts DESC LIMIT 5\").fetchall()
for r in rows:
    print(dict(r))
"
```

---

## 7. 进阶：多区域部署

若需在多个 AWS 区域（如 `cn-north-1` 北京 + `cn-northwest-1` 宁夏）部署：

| 组件 | 方案 |
|------|------|
| SNS Topic | 每个区域独立创建（Alarm 只能订阅同区域 SNS） |
| Lambda | 每个区域独立部署，指向同一个 kiro webhook URL |
| kiro 侧 | `source=cloudwatch` 不变，可通过 `entities` 中的区域维度区分 |

**统一 Endpoint 建议**：

为 kiro-devops 分配一个 Elastic IP 并绑定域名（如 `http://kiro.example.com:8080/event`），多区域 Lambda 统一指向该域名，避免 IP 变动时逐个修改。

---

## 8. 故障排查速查表

| 现象 | 排查方向 |
|------|----------|
| Lambda 执行成功但飞书无消息 | 检查 EC2 安全组 8080 是否放行 Lambda 来源 IP |
| Lambda 返回 401 | `KIRO_WEBHOOK_TOKEN` 与 kiro `.env` 中不一致 |
| Lambda 返回 500 | kiro webhook server 未启动，或 event_store 写入异常 |
| 收到重复告警 | 检查 kiro webhook_server 的 `_is_duplicate_alert` 日志；确认 Alertmanager 未同时推送 |
| 仅入库无分析 | 检查 `ALERT_AUTO_ANALYZE_SEVERITY` 是否包含该告警的 severity（默认 high,critical） |
