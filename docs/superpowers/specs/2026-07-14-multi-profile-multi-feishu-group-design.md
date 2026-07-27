# 多飛書 App、多群與多 AWS Profile 設計規格

- **日期：** 2026-07-14
- **狀態：** 已完成設計評審，待書面規格確認
- **適用專案：** `kiro-devops`

## 1. 背景

目前 `kiro-devops` 以單一程序同時提供飛書、微信與 Webhook 通道，但飛書與 Kiro 執行設定仍是全域單例：

- `gateway.py` 只建立一個 `FeishuAdapter` 與一個 `MessageHandler`。
- `PlatformDispatcher` 以 `platform` 作為唯一鍵，無法同時註冊多個飛書 App。
- `FEISHU_POLL_CHAT_IDS` 可讓單一 App 輪詢多群，但所有群共用同一組 Kiro 與 AWS 執行環境。
- AWS 身分沒有顯式路由，Kiro 子程序直接繼承 gateway 程序的環境。
- `IncomingMessage` 已有 `group_id`，但 Session、背景任務與記憶仍以 `feishu:<open_id>` 為主要鍵；同一使用者跨群會共享上下文。
- `KiroExecutor` 接收 Session ID，實際卻只傳入 `--resume`，會恢復工作目錄中的最新 Session，而非指定 Session。
- `SessionRouter` 透過全域 `user_sessions.json` 與 `--list-sessions` 捕捉最新 Session；多群並行時可能誤綁。
- 群告警分析由 `alert_analysis.py` 執行，但使用全域 AWS、模型與逾時設定。

本設計讓多個飛書 App、飛書群與 AWS CLI profile 能在同一 Python 程序及同一 systemd 服務中安全運行。

## 2. 目標

1. 支援單一飛書 App 加入多群，也支援多個飛書 App 各自加入多群。
2. 使用 `(app_key, chat_id)` 將群固定映射到一個執行 profile。
3. 允許多個群映射到同一 profile。
4. 每個 profile 可設定 AWS CLI profile、Region、Kiro Agent、模型、工作目錄及逾時。
5. 私聊使用該 App 的預設 profile。
6. 未映射群收到 @Bot 普通訊息或可辨識告警時，在原群明確拒絕；未 @Bot 且非告警的普通輪詢訊息保持靜默。所有情況都不得使用全域或其他 profile 作為 fallback。
7. Session、背景任務、取消操作與記憶按 App、群／私聊及使用者隔離。
8. 相同上下文同時最多一個任務；不同上下文可並行。
9. 群告警分析使用群映射的 AWS profile，並回覆原 App、原群。
10. 透過 Dashboard 管理非敏感設定、STS 驗證、熱載入、修訂版本與設定回滾。
11. 保留現有單 App `.env` 模式，並提供經演練的應用版本回滾能力。

預期規模為 10 多個群與 10 多個 profile。設計需合理支援最多 10 個 App、20 個 profile 與 100 條群映射，不要求為大型分散式系統設計。

## 3. 非目標

本次不包含：

- Scheduler 的 profile 路由。
- Webhook 告警的 profile 路由。
- Dashboard AWS 資源查詢的 profile 路由。
- 微信的多 App 或 AWS profile 路由。
- 群內透過命令動態切換 profile。
- 每個 profile 一個常駐 worker 程序或 systemd 實例。
- 在 `multi_profile_config.yaml` 中保存飛書 Secret、AWS Access Key 或 Session Token。

上述功能不得因本次重構而改變既有行為。

## 4. 核心決策

採用**單一 Python 程序內的顯式上下文路由**：

- 多個飛書 Adapter 共存於同一 gateway。
- 所有租戶差異由不可變 `ExecutionContext` 傳遞。
- 每個 Kiro 子程序取得獨立環境副本。
- 不修改程序全域 `os.environ` 來切換 AWS 身分。
- 不使用模糊的 `--resume` 恢復 Session。
- 執行中的任務固定使用啟動時取得的設定 generation。

此方案比 profile worker 程序節省資源，也比多 systemd 實例容易集中管理。代價是必須移除目前 MessageHandler、KiroExecutor、SessionRouter 與告警分析中的全域 profile 假設。

## 5. 整體架構

### 5.1 元件與責任

| 元件 | 責任 | 不負責 |
|------|------|--------|
| `AppManager` | 依設定建立及監督多個 `FeishuAdapter`；記錄連線狀態與重啟需求 | 群路由、AWS 執行 |
| `PlatformDispatcher` | 以 `(platform, app_key)` 註冊與查找 Adapter；確保由原 App 回覆 | profile 選擇 |
| `ConfigRegistry` | 載入、驗證並發布不可變設定 snapshot；保存目前 generation | 執行 Kiro |
| `TenantRouter` | 群聊以 `(app_key, chat_id)` 選 profile；私聊以 `app_key` 選預設 profile | 修改設定 |
| `ExecutionContext` | 攜帶固定的路由、profile、AWS、Kiro、逾時與 generation | 執行邏輯 |
| `MessageHandler` | 處理命令、普通對話、群告警及回覆流程 | 直接讀取全域 profile |
| `RuntimeManager` | 建立 Kiro argv、隔離環境、程序生命週期與 per-context 任務鎖 | 決定群映射 |
| `SessionStore` | 保存 context-scoped Session、fingerprint 與活動時間 | 選擇 AWS 身分 |
| `SessionCaptureCoordinator` | 並行安全地配置新 Kiro Session UUID | 保存聊天內容 |
| `ProfileHealthMonitor` | 執行 STS 健康檢查並管理 profile 狀態 | 自動切換 profile |
| Dashboard API | Draft 驗證、發布、狀態、revision 與回滾 | 保存 Secret 值 |

### 5.2 訊息模型

`IncomingMessage` 新增必填或可正規化取得的 `app_key`。飛書訊息至少包含：

- `platform = "feishu"`
- `app_key`
- `chat_type`
- `group_id`（群聊時）
- `raw_user_id`／`open_id`
- `message_id`

`PlatformDispatcher` 的 Adapter Registry 鍵由目前的 `platform` 改為 `(platform, app_key)`。微信以固定 `app_key = "default"` 維持既有行為。

## 6. 設定模型

### 6.1 檔案位置

- 啟用開關：`.env` 中的 `MULTI_PROFILE_ENABLED`，預設 `false`。
- 主設定路徑：`.env` 中的 `MULTI_PROFILE_CONFIG`，預設 `<project>/multi_profile_config.yaml`。
- Revision 目錄：`MULTI_PROFILE_REVISION_DIR`，預設 `<project>/runtime/config-revisions/multi-profile/`。
- Last-known-good：Revision 目錄中的 `last-known-good.yaml`。
- 新 Session DB：`<project>/runtime/tenant_sessions.db`。

`runtime/` 必須加入 `.gitignore`。主設定是否納入版本控制由部署方決定；Dashboard 只會編輯 `MULTI_PROFILE_CONFIG` 指向的檔案。

### 6.2 YAML Schema

```yaml
version: 1

apps:
  ops-bot:
    enabled: true
    app_id_env: FEISHU_OPS_APP_ID
    app_secret_env: FEISHU_OPS_APP_SECRET
    default_profile: prod-cn

profiles:
  prod-cn:
    enabled: true
    aws_profile: production
    aws_region: cn-northwest-1
    expected_account_id: "123456789012"
    kiro_agent: my-dev-bot
    model: claude-sonnet
    alert_agent: ec2-alert-analyzer
    alert_model: claude-sonnet
    working_dir: /home/ubuntu/kiro-devops
    sync_timeout: 120
    async_timeout: 1800
    alert_timeout: 300

routes:
  - app: ops-bot
    chat_id: oc_xxxxxxxxxx
    profile: prod-cn
    poll_alerts: true
```

### 6.3 Schema 欄位與預設值

根層級的 `version`、`apps`、`profiles`、`routes` 均為必填；`apps` 與 `profiles` 必須是非空 mapping，`routes` 必須是 list 且允許為空。所有層級均拒絕未定義欄位，避免拼字錯誤被靜默忽略。

`apps.<app_key>`：

| 欄位 | 必填 | 預設值 | 規則 |
|------|------|--------|------|
| `enabled` | 否 | `true` | `false` 時不建立連線，所有引用路由無效 |
| `app_id_env` | 是 | 無 | 合法環境變數名稱；啟用時必須有非空值 |
| `app_secret_env` | 是 | 無 | 合法環境變數名稱；啟用時必須有非空值 |
| `default_profile` | 是 | 無 | 必須引用已啟用 profile |

`profiles.<profile_id>`：

| 欄位 | 必填 | 預設值 | 規則 |
|------|------|--------|------|
| `enabled` | 否 | `true` | `false` 時不可被 App 或路由引用 |
| `aws_profile` | 是 | 無 | 必須是存在且可非互動使用的 AWS CLI profile |
| `aws_region` | 否 | `null` | 省略時由 AWS CLI profile／SDK 正常解析；`/profile` 顯示「profile default」 |
| `expected_account_id` | 是 | 無 | 必須是 12 位數字且與 STS 相符 |
| `kiro_agent` | 否 | `null` | 省略時使用 Kiro CLI 預設 Agent |
| `model` | 否 | `null` | 省略時使用 Kiro CLI 預設模型 |
| `alert_agent` | 否 | `ec2-alert-analyzer` | 必須是已存在 Agent |
| `alert_model` | 否 | `null` | 省略時依第 12 節模型優先順序 |
| `working_dir` | 是 | 無 | 必須是服務使用者可讀取與進入的絕對目錄 |
| `sync_timeout` | 否 | `120` | 10–600 秒 |
| `async_timeout` | 否 | `1800` | 不小於 `sync_timeout`，最大 86400 秒 |
| `alert_timeout` | 否 | `300` | 10–3600 秒 |

每個 `routes[]` 項目：

| 欄位 | 必填 | 預設值 | 規則 |
|------|------|--------|------|
| `app` | 是 | 無 | 必須引用已啟用 App |
| `chat_id` | 是 | 無 | 非空飛書群 ID |
| `profile` | 是 | 無 | 必須引用已啟用 profile |
| `poll_alerts` | 否 | `false` | 只控制群歷史輪詢 |

其他跨欄位規則：

- `version` 必須等於 `1`。
- `app_key` 與 `profile_id` 必須符合 `[a-z0-9][a-z0-9_-]{0,62}`。
- 路由唯一鍵是 `(app, chat_id)`；重複鍵使整份 Draft 驗證失敗。
- 多條路由可以引用同一 profile。
- `poll_alerts` 不影響 WebSocket 的 @Bot 普通訊息。

AWS AssumeRole 由 `~/.aws/config` 的 `role_arn`、`source_profile` 或 `credential_source` 管理。此設定不重複保存 Role 憑證；`aws_profile` 直接引用可非互動執行的 AWS CLI profile。

## 7. 設定載入與路由

### 7.1 Snapshot

`ConfigRegistry` 將有效設定轉成不可變 snapshot，並配置單調遞增的 generation。每則訊息只取得一次 snapshot；路由、Session、執行與回覆都使用同一份 snapshot。

熱載入成功後：

- 新任務使用新 generation。
- 執行中任務繼續使用舊 generation。
- 不修改舊 `ExecutionContext`。

### 7.2 群聊

1. 以 `(app_key, chat_id)` 查找路由。
2. 找到啟用 profile 後建立 `ExecutionContext`。
3. 未找到路由時：
   - @Bot 的普通訊息在原群回覆未配置提示。
   - 可辨識的群告警在原群回覆未配置提示。
   - 未 @Bot 且不是告警的普通輪詢訊息保持靜默，避免刷屏。
4. 不得使用 App 預設 profile、legacy profile 或任意第一個 profile 作為群聊 fallback。

### 7.3 私聊

私聊以 `app_key` 對應 App 的 `default_profile` 建立 `ExecutionContext`。預設 profile 無效時明確拒絕，不使用其他 profile。

### 7.4 `/profile`

`/profile` 不執行 Kiro，直接回覆：

- profile 別名。
- 已驗證 AWS Account ID 的遮罩值，固定格式為 `********9012`（只顯示最後 4 位）。
- Region。
- 普通聊天 Agent／模型。
- profile 健康狀態。
- 最近 STS 驗證時間。

不得顯示環境變數值、App Secret、AWS credential 或完整 ARN 中不需要公開的部分。

## 8. ExecutionContext 與隔離鍵

`ExecutionContext` 至少包含：

- `config_generation`
- `platform`
- `app_key`
- `chat_type`
- `chat_id`
- `user_id`
- `principal_key`
- `group_scope_key`（群聊時）
- `profile_id`
- AWS 與 Kiro 執行設定
- `profile_fingerprint`

隔離鍵格式：

- 群聊 principal：`feishu/{app}/group/{chat}/user/{open_id}`
- 私聊 principal：`feishu/{app}/private/{open_id}`
- 群事件 scope：`feishu/{app}/group/{chat}`

Session、忙碌狀態、取消操作及語義記憶使用 `principal_key`。群告警去重與事件記錄使用 `group_scope_key`。即使多個群映射同一 profile，也不得共享上述狀態。

`profile_fingerprint` 由會影響安全或 Session 行為的欄位穩定計算，至少包含：

- `profile_id`
- `aws_profile`
- `aws_region`
- `kiro_agent`
- `model`
- `working_dir`

逾時變更不使既有 Session 失效；AWS 身分、Agent、模型或工作目錄變更會使 fingerprint 改變。

## 9. Kiro 執行與 AWS 環境

### 9.1 子程序環境

每次執行建立獨立環境副本。建立後先移除：

- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`
- `AWS_SESSION_TOKEN`
- `AWS_PROFILE`
- `AWS_DEFAULT_PROFILE`
- `AWS_REGION`
- `AWS_DEFAULT_REGION`

再設定：

- `AWS_PROFILE = profile.aws_profile`
- `AWS_DEFAULT_PROFILE = profile.aws_profile`
- `AWS_REGION` 與 `AWS_DEFAULT_REGION`（有設定時）
- `AWS_SDK_LOAD_CONFIG = 1`
- `NO_COLOR = 1`

此規則避免 systemd、shell 或父程序中的靜態 credential 優先於選定 profile。不得將完整環境寫入日誌。

### 9.2 Kiro argv

- 普通聊天使用 profile 的 `kiro_agent` 與 `model`。
- 恢復 Session 必須使用 `--resume-id <UUID>`。
- 禁止使用 `--resume`。
- `working_dir` 作為 Kiro 子程序 `cwd`。
- 所有命令使用 argv 陣列，不透過 shell 字串拼接。

### 9.3 並發

`RuntimeManager` 以 `principal_key` 管理任務：

- 相同 principal 同時最多一個任務。
- `/status` 與 `/cancel` 只影響目前 principal。
- 不同群、App 或使用者可以並行，即使映射同一 AWS profile。
- 本次不增加跨 principal 的全域序列化或任務佇列。

## 10. Session 管理

### 10.1 儲存

多 profile 模式使用獨立 SQLite `tenant_sessions.db`，每筆 Session 至少保存：

- `principal_key`
- `kiro_session_id`
- `profile_id`
- `profile_fingerprint`
- `short_id`
- `topic`
- `created_at`
- `last_active`
- `message_count`

唯一性與查詢都以 `principal_key` 為邊界。

### 10.2 新 Session ID 配置（capture-at-exit）

實測 kiro-cli 2.4.1：conversation row 只在 chat 程序**退出時**才寫入 sqlite（`~/.local/share/kiro-cli/data.sqlite3` 的 `conversations_v2` 表），且落盤可能再延遲數秒；運行中輪詢永遠看不到新 session。因此採用 `SessionCaptureCoordinator` 的 capture-at-exit 協定：

1. 啟動前 `begin()`：在 canonical `working_dir`（`realpath` 後的絕對路徑）短期鎖內拍攝既有 Session UUID baseline。Kiro `--list-sessions` 按工作目錄共享，鎖鍵不得包含 Agent。
2. 啟動新的 Kiro 子程序（鎖立即釋放，不涵蓋 chat 執行期間）。
3. 程序退出後 `capture()`：短暫輪詢 `--list-sessions`（預設 30s、0.5s 間隔），取 `new = current − baseline − claimed[working_dir]`。
4. 恰好 1 個新 UUID → claim 並綁定 principal。0 個（逾時）→ 視為未落盤；>1 個 → 歧義。兩者皆 fail closed，絕不猜測或綁定「最新」UUID。
5. per-working-dir 的 claimed 集合讓同目錄並行的新 chat 各自綁定正確 session（A、B 同時啟動，A 退出先 claim A，B 退出比對時排除已認領的 A）；殘餘競態（同一輪詢窗口同時落盤且皆未 claim）落入歧義分支，同樣正確。
6. 捕捉失敗不影響任務結果交付：本次只是不綁定 session（下則訊息開新 session），記 warning 含 trace context。聊天失敗路徑仍 best-effort 嘗試捕捉。

### 10.3 恢復與 profile 變更

- 自動恢復、`/resume` 與 `/sessions` 只查目前 `principal_key`。
- 恢復前必須確認 Session 的 `profile_fingerprint` 與目前 ExecutionContext 完全相同。
- 群改綁其他 profile，或 AWS profile、Region、Agent、模型、工作目錄改變時，下一則訊息建立新 Session。
- fingerprint 不符的舊 Session 保留供稽核，但不得在新 profile 下恢復。

舊 `user_sessions.json` 缺少 App、群與 profile 維度，不能安全匯入多 profile Session DB。多 profile 模式從新 Session 開始；舊檔保持不變，僅供 legacy 模式或版本回滾使用。

## 11. 記憶與事件隔離

- 新模式對 MemoryLayer 傳入 `principal_key`，不再傳入單純 `feishu:<open_id>`。
- 群事件與告警使用 `group_scope_key`，避免其他群查到該群事件。
- 舊記憶記錄不自動複製到新 context key，因為無法可靠判斷其原始 App 與群。
- 舊記憶資料保持不變；回到 legacy 模式後仍可使用。
- 多 profile 模式建立的新記憶在舊版本中暫不可見，重新啟用多 profile 模式後恢復可見。

## 12. 群告警分析

群告警流程沿用既有解析、去重與 Alert Mapping，但新增 `ExecutionContext`：

1. 先解析群路由；未映射群不執行分析。
2. 使用 `group_scope_key` 寫入事件與去重。
3. Alert Mapping 可覆蓋 Agent、工具與 alert timeout。
4. Agent 優先順序：Alert Mapping action → profile `alert_agent` →既有全域預設。
5. 模型優先順序：profile `alert_model` → profile `model` →既有 `BACKGROUND_MODEL`。
6. AWS profile 與 Region只能來自群的 ExecutionContext；Alert Mapping 不得覆蓋。
7. 分析結果透過原始 `IncomingMessage` 由原 App 回覆原群。
8. 告警分析不加入普通聊天 Session。

群告警執行錯誤時在原群回覆錯誤摘要與 trace ID，不改用其他 profile 重試。

## 13. Dashboard 與熱載入

### 13.1 頁面

Dashboard 新增 `Multi Profile Config`，包含：

- **Apps**：別名、env 引用、預設 profile、執行狀態。
- **Profiles**：AWS、Kiro、工作目錄與逾時。
- **Group Routes**：App、chat ID、profile、告警輪詢。
- **Revisions**：時間、checksum、驗證結果、diff 與回滾。

Dashboard 不提供 Secret 值編輯欄位，只編輯 Secret 所在的環境變數名稱。

### 13.2 API 邊界

至少提供：

- 取得目前設定與執行狀態。
- 驗證 Draft。
- 發布 Draft。
- 列出 revision 與差異。
- 回滾至指定 revision。
- 取得 App、profile、generation 與 pending-restart 狀態。

發布 API 必須在伺服器端重新驗證，不得信任瀏覽器先前的驗證結果。

### 13.3 驗證順序

1. YAML 與 schema。
2. env 引用存在。
3. App、profile 與 route 關聯完整。
4. 工作目錄與逾時。
5. Kiro Agent 存在；有指定模型時確認模型可用。
6. AWS CLI profile 存在。
7. 以隔離環境執行 `aws sts get-caller-identity --profile <name>`。
8. 核對 `expected_account_id`。

任何一步失敗都不得發布。

### 13.4 原子發布

發布流程：

1. 產生目前設定的 revision 與 checksum。
2. 將新設定寫入同目錄暫存檔。
3. flush 並 `fsync`。
4. 使用 `os.replace` 原子替換主設定。
5. ConfigRegistry 重新讀取並建立新 snapshot。
6. 只有新 snapshot 建立成功才切換 generation。
7. 更新 last-known-good。

若檔案替換後 snapshot 建立失敗，立即原子恢復上一 revision；執行中 Registry 仍保留舊 snapshot。

### 13.5 熱載入範圍

可熱載入：

- profile 執行欄位。
- 群路由。
- `poll_alerts`。
- 既有 App 的 `default_profile`。

需要重啟：

- 新增或刪除 App。
- `app_id_env` 或 `app_secret_env` 變更。
- App 啟用／停用導致連線生命週期改變。

需要重啟的變更可以保存，但狀態必須顯示 `pending-restart`；尚未運行的 App 不得接收有效路由。

## 14. Profile 健康狀態

Profile 狀態包括：

- `active`：最近一次 STS 成功且 Account ID 相符。
- `degraded`：STS 暫時性失敗，但仍在 grace period 內。
- `blocked`：Account ID 不符、profile 不存在、持續超過 grace period，或必要設定無效。
- `disabled`：管理員停用。

STS 健康檢查定期執行並加入 jitter，避免所有 profile 同時呼叫。Account ID 不符立即 `blocked`，不適用 grace period。`blocked` profile 的新普通聊天與群告警任務都必須拒絕。

Health Monitor 不得自動改用其他 profile。

### 14.1 操作預設值

以下數值可透過 `.env` 調整，但必須有界限並由 Dashboard 顯示實際值：

- `AWS_STS_TIMEOUT_SEC`：預設 10 秒，允許 3–60 秒。
- `PROFILE_HEALTH_CHECK_INTERVAL_SEC`：預設 600 秒，允許 60–3600 秒；每次檢查加入 0–60 秒 jitter。
- `PROFILE_HEALTH_GRACE_SEC`：預設 1800 秒，允許 0–86400 秒；只適用暫時性 STS 連線錯誤。
- `SESSION_ID_CAPTURE_TIMEOUT_SEC`：預設 30 秒，允許 5–120 秒；每 500 ms 檢查一次新 Session UUID。
- App 重連延遲依序為 1、2、4、8、16、32、60 秒，之後維持 60 秒上限，連線成功後重設為 1 秒。

Account ID 不符、profile 不存在或 schema 無效不適用 grace period，必須立即 `blocked`。

## 15. 錯誤處理

### 15.1 Fail-closed

- 未映射群：@Bot 普通訊息或可辨識告警在原群明確拒絕；未 @Bot 且非告警的普通輪詢訊息保持靜默；兩者都不得啟動 Kiro／AWS 子程序。
- 無效私聊預設：原私聊明確拒絕。
- profile disabled／blocked：明確拒絕。
- Kiro 或 AWS 執行失敗：回覆摘要與 trace ID。
- Session UUID 配置不明確：終止任務，不猜測。
- 群告警分析失敗：原群回覆，不 fallback。
- 熱載入失敗：保留舊 snapshot。

### 15.2 App 故障隔離

- 每個 App 的 WebSocket 與輪詢生命週期獨立。
- 單一 App 中斷不得終止其他 App。
- AppManager 對每個 App 採有上限的指數退避重連。
- 單群輪詢錯誤只標記該群，不中止同 App 其他群。
- 發送失敗需記錄 App、chat/message 與 trace ID，但不得改由另一 App 發送。

### 15.3 啟動失敗

啟用多 profile 時若沒有有效主設定或 last-known-good，gateway 仍可啟動 Dashboard 與健康端點，但多 profile 訊息執行保持停用。系統不得因設定錯誤自動進入 legacy profile；legacy 模式只能透過明確設定 `MULTI_PROFILE_ENABLED=false` 啟用。

## 16. 安全與日誌

- 不在 YAML、revision、API response 或日誌中保存 Secret 值。
- 不輸出完整子程序環境。
- 不記錄完整使用者 prompt 或包含 prompt 的完整 Kiro 命令。
- 使用 argv 呼叫外部程序，不使用 `shell=True`。
- Session 恢復前同時核對 principal 與 profile fingerprint。
- Dashboard 沿用既有 Token 驗證；設定發布與回滾都需要已驗證 Dashboard Session。
- Revision 保存非敏感設定、checksum、時間及驗證摘要。
- 任務日誌包含 trace ID、app key、chat ID、profile 別名、Session ID、generation、耗時與結果狀態。

## 17. 可觀測性

健康資訊至少包含：

- 目前 config generation 與 checksum。
- 目前是否為 multi-profile 或 legacy 模式。
- 各 App 的 `connected/disconnected/reconnecting/pending-restart`。
- 各 profile 的健康狀態、已驗證 Account ID 與最近 STS 時間。
- 各 profile 與全系統執行中任務數。
- 最近一次設定載入／發布／回滾結果。

Account ID 可在受 Dashboard 驗證保護的頁面完整顯示；一般群 `/profile` 固定只顯示最後 4 位，格式為 `********9012`。

## 18. 向下相容

當 `MULTI_PROFILE_ENABLED` 不存在或為 `false`：

- 使用現有 `FEISHU_APP_ID`、`FEISHU_APP_SECRET` 與 `FEISHU_POLL_CHAT_IDS`。
- 使用現有 `KIRO_AGENT`、`DEFAULT_MODEL`、`BACKGROUND_MODEL` 與逾時設定。
- 使用現有 MessageHandler、Session 與記憶識別方式。
- 不要求 `multi_profile_config.yaml` 存在。

此路徑稱為 `legacy-default`。它保留目前行為，包括單 App 全域設定；不得與 multi-profile 路徑在同一 App 上同時啟動，以免建立重複 WebSocket 連線。

## 19. 遷移與漸進上線

### 19.1 升級快照

部署前建立 release manifest，記錄：

- Git commit／release。
- Python 依賴鎖定資訊。
- systemd unit checksum。
- `.env`、`dashboard_config.json`、`user_sessions.json` 與既有 SQLite 備份。
- 備份檔 checksum。

### 19.2 Dark Deployment

1. 部署新程式，保持 `MULTI_PROFILE_ENABLED=false`。
2. 執行完整測試與 legacy smoke test。
3. 驗證普通聊天與群告警沒有回歸。

### 19.3 離線設定

1. 建立 Draft。
2. 將現有 App 使用原 env key 加入 `apps`。
3. 建立與目前全域設定等價的 `legacy-default` profile。
4. 將所有已知既有群映射到 `legacy-default`。
5. 完成完整驗證與 STS，不切換流量。

### 19.4 相容切換與擴展

1. 設定 `MULTI_PROFILE_ENABLED=true` 並重啟。
2. 先確認所有既有群仍使用 `legacy-default`。
3. 驗證 App 回覆、Account ID、Session 與記憶隔離。
4. 逐群改綁實際 profile。
5. 新 App 分批加入，每批安排重啟並完成 smoke test。
6. 每批通過觀察後才擴展下一批。

立即停止擴展並回滾的條件：

- 錯誤 AWS Account。
- 跨群或跨 App Session。
- 跨群或跨 App 記憶。
- 由錯誤 App 回覆。
- 有效設定無法載入且不能維持 last-known-good。

## 20. 回滾策略

### 20.1 設定回滾

- 保留最近 20 個 revision。
- 管理員先查看 diff，再選擇歷史 revision。
- 歷史內容需重新執行 schema、env、路徑、Agent、模型與 STS 驗證。
- 驗證成功後將歷史內容發布為一個新的 revision。
- 驗證或發布失敗時不切換目前 snapshot。

### 20.2 同版本緊急回滾

1. 設定 `MULTI_PROFILE_ENABLED=false`。
2. 重啟服務。
3. 服務使用原 `.env`、單 App 與舊 Session／記憶路徑。
4. 執行 legacy health、訊息接收、原 App 回覆與 Kiro smoke test。

### 20.3 應用版本回滾

回滾工具依 release manifest：

1. 停止服務或進入維護狀態。
2. 恢復上一 release／commit 與相符依賴。
3. 恢復升級前 `.env`、Dashboard 設定與 systemd unit；例行版本回滾不得覆寫任何目前的 SQLite 資料檔。
4. 確認 `MULTI_PROFILE_ENABLED=false`。
5. 啟動舊版本。
6. 執行 legacy smoke test。

新 YAML、revision 及 `tenant_sessions.db` 都是 additive；舊版會忽略它們。既有 semantic memory 與 event SQLite 不做破壞性 schema 變更，舊版只會因 key 不同而看不到新 context 資料。例行版本回滾保留所有目前 SQLite 檔與 `tenant_sessions.db`，因此不刪除多 profile 期間的新資料；這些 Session 與記憶在舊版中暫不可見，重新啟用新版本後恢復。

升級前的 SQLite 備份只用於資料庫損壞等災難復原，不屬於例行程式版本回滾。若必須進行災難復原，操作前需先另行保存目前 SQLite 與 `tenant_sessions.db`，避免覆蓋升級後資料。

正式發布前必須完成一次應用版本回滾演練，目標在 5 分鐘內恢復舊單 App 服務。

## 21. 測試策略

### 21.1 單元測試

- YAML schema、缺少 env、無效 ID、時間範圍、重複路由。
- 群路由與私聊 default profile。
- 未映射群 fail-closed。
- principal key、group scope 與 profile fingerprint。
- AWS 父環境清除及子程序環境注入。
- Kiro argv 精確使用 `--resume-id`。
- fingerprint 改變後拒絕恢復。
- Alert Mapping 不可覆蓋 AWS 身分。
- profile 狀態轉換與 Account ID mismatch 立即阻擋。

### 21.2 整合與並行測試

- 多個 FeishuAdapter 同時註冊，由正確 App 回覆。
- 不同 App 的相同 `chat_id` 不互相覆蓋。
- 多群映射同一 profile，但 Session／記憶仍隔離。
- 相同使用者跨群可並行。
- 相同 principal 的第二個任務被拒絕。
- 多個 principal 同時建立 Session，不發生 UUID 誤綁。
- Session 列表出現多個新 UUID 時 fail-closed。
- 熱載入時，執行中任務保留舊 generation。
- 群改綁 profile 後強制新 Session。
- 群告警使用正確 AWS profile 及原 App 回覆。
- 單 App 中斷不影響其他 App。

### 21.3 Dashboard 與回滾測試

- STS 成功且 Account ID 相符才可發布。
- STS timeout、profile 不存在與 Account ID 不符。
- 寫檔、`fsync`、`os.replace`、解析或 snapshot 建立失敗時保留舊設定。
- revision diff、重新驗證與回滾。
- App 連線欄位變更顯示 pending-restart。
- `MULTI_PROFILE_ENABLED=false` 保持現有行為。
- 暫存環境執行升級、切換及程式版本回滾。
- 日誌掃描確認無 Secret、AWS credential、完整環境或完整 prompt。

### 21.4 規模測試

使用 Fake Adapter 與 Fake Runtime 模擬：

- 10 個 App。
- 20 個 profile。
- 100 條群映射。
- 50 個並行訊息。

測試需確認路由正確、沒有共享狀態污染，且 Registry 熱載入不阻塞既有任務。

## 22. 發布驗收標準

發布前必須全部滿足：

1. 完整 `pytest` 套件零失敗。
2. Python 編譯檢查通過。
3. Legacy 模式普通聊天與群告警 smoke test 通過。
4. 至少使用 2 個唯讀或非生產 AWS profile 完成真實 STS 與 Kiro 端到端驗證。
5. 多個群共用同一 profile 時，Session、記憶與任務仍完全隔離。
6. 不同 profile 的並行任務取得正確 Account ID。
7. 未映射群的 @Bot 訊息與可辨識告警在原群拒絕；未 @Bot 且非告警的輪詢訊息保持靜默；兩條路徑都沒有 Kiro／AWS 子程序啟動。
8. 群告警由原 App 回覆，且使用群綁定的 AWS profile。
9. 無效熱載入不影響目前有效 snapshot。
10. 設定 revision 回滾成功。
11. 應用版本回滾演練在 5 分鐘內恢復舊單 App 服務。
12. 日誌與 Dashboard response 不包含 Secret 或 AWS credential。

錯誤 AWS Account、跨群／跨 App Session、跨群／跨 App 記憶及錯誤 App 回覆均屬於阻擋發布的 Critical 缺陷。

## 23. 已知限制

- 新模式不匯入缺少群維度的舊 Session 與記憶；切換後使用者需開始新對話。
- Kiro CLI 沒有直接輸出新 Session UUID，因此 SessionCaptureCoordinator 必須處理外部程序同時在相同工作目錄建立 Session 的不確定性，遇到歧義時拒絕綁定。
- App 連線生命週期不熱載入；新增、刪除或修改 App 憑證引用需要重啟。
- 本次不提供跨來源任務的全域佇列；總並發量仍取決於同時活躍的 principal 數量。

## 24. 實作計畫拆分

本文件是同一功能的總體規格，但不得強行塞入單一大型實作計畫。後續使用 `writing-plans` 依下列順序產生 5 份計畫；每份計畫都必須讓主分支保持可運行、具備獨立測試與明確回滾點：

1. **設定模型與路由核心**：Schema、ConfigRegistry、ExecutionContext、TenantRouter、legacy feature flag；功能保持關閉，不改變生產行為。
2. **Runtime、Session 與記憶隔離**：子程序環境、`--resume-id`、SessionCaptureCoordinator、SQLite SessionStore、principal／group scope。
3. **多 App 與群告警整合**：AppManager、多 App Dispatcher、動態輪詢集合、原 App 回覆與告警 ExecutionContext。
4. **Profile 健康與 Dashboard**：STS Health Monitor、Draft 驗證、發布、revision、熱載入、pending-restart 與設定回滾。
5. **遷移、版本回滾與端到端驗收**：release manifest、dark deployment、legacy-default 切換、回滾工具、規模測試與雙 AWS profile 驗收。

計畫 2 依賴計畫 1；計畫 3 依賴計畫 1–2；計畫 4 依賴計畫 1–3；計畫 5 在前四份計畫全部通過後執行。正式切換 `MULTI_PROFILE_ENABLED=true` 只能出現在計畫 5。
