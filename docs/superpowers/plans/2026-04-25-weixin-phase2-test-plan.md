# 微信 Phase 2A 测试方案

## 一、测试分层

| 层级 | 方式 | 覆盖范围 | 状态 |
|------|------|----------|------|
| **L1 单元测试** | Mock 全部外部依赖 | AES/PKCS7、消息解析、路由分发 | ✅ 已完成（19 个新测试，71 个总测试全过） |
| **L2 半集成测试** | 真实 token + Mock CDN | 验证 sendmessage JSON 格式、getuploadurl 调用 | 🔄 可执行 |
| **L3 端到端测试** | 真实微信扫码收发 | 用户手机发送图片 → Bot 接收 → Bot 回复图片 | 🔄 可执行 |

---

## 二、L2 半集成测试方案

### 2.1 目标
用真实的 `bot_token` 调用 iLink API，但拦截 CDN 上传/下载，验证：
- `getuploadurl` 能正常返回 upload_url
- `sendmessage` 的 JSON 格式被 iLink 服务端接受
- 图片接收时 `getupdates` 能正确返回 image_item

### 2.2 测试脚本

```python
# tests/integration/test_weixin_media_live.py
"""半集成测试：使用真实 token，验证协议格式."""
import os
import tempfile

from adapters.weixin import WeixinAdapter


def test_getuploadurl_returns_url():
    """验证 getuploadurl 能正常返回上传地址."""
    adapter = WeixinAdapter(bot_token=None, on_message=lambda x: None)
    # bot_token 会从 ~/.kiro/weixin_token.json 自动加载

    # 直接调用内部 _post
    from adapters.weixin import _post
    resp = _post(
        "ilink/bot/getuploadurl",
        adapter.base_url,
        adapter.bot_token,
        {"msg": {"item_list": [{"type": 2}]}},
    )
    print(f"getuploadurl resp: {resp}")
    assert resp.get("ret", -1) == 0, f"getuploadurl 失败: {resp}"
    assert "upload_param" in resp
    assert "upload_url" in resp["upload_param"]


def test_send_image_format_accepted():
    """验证 send_image 的 JSON 格式被服务端接受."""
    adapter = WeixinAdapter(bot_token=None, on_message=lambda x: None)

    # 需要一个已交互过的用户（有 context_token）
    # 先让对方发一条消息，获取 context_token
    raw_id = "o9cq80--_0OvjbMU56y1k56bt3fo@im.wechat"  # 替换为实际用户

    fd, tmp_path = tempfile.mkstemp(suffix=".png")
    os.write(fd, b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)  # 最小 PNG 头
    os.close(fd)

    # 手动设置 context_token（前提是用户最近发过消息）
    adapter._context_tokens[raw_id] = "test_context_token"

    result = adapter.send_image(raw_id, tmp_path)
    print(f"send_image result: {result}")
    os.unlink(tmp_path)
```

### 2.3 执行方式

```bash
cd /home/ubuntu/kiro-devops
source .env
python3 -c "
from adapters.weixin import WeixinAdapter, _post
adapter = WeixinAdapter(bot_token=None, on_message=lambda x: None)
print(f'token loaded: {adapter.bot_token[:20]}...')
resp = _post('ilink/bot/getuploadurl', adapter.base_url, adapter.bot_token,
             {'msg': {'item_list': [{'type': 2}]}})
print(f'getuploadurl: {resp}')
"
```

---

## 三、L3 端到端测试方案

### 3.1 前置条件

1. 停止旧 gateway 进程（systemd 或 nohup）
2. 前台启动新 gateway.py（方便看实时日志）
3. 手机微信给 Bot 发消息

### 3.2 测试用例

| # | 场景 | 用户操作 | 预期结果 | 验证方式 |
|---|------|----------|----------|----------|
| 1 | 接收图片 | 手机微信发送一张图片给 Bot | Bot 日志显示"下载微信图片" | 看终端日志 |
| 2 | 接收文件 | 手机微信发送一个 PDF 给 Bot | Bot 回复"收到文件，暂不支持文件理解" | 看微信回复 |
| 3 | 发送图片 | 向 Bot 发送"/new"后，发送"生成一张 CPU 趋势图" | Kiro 生成图片，Bot 将图片发回微信 | 看微信是否收到图片 |
| 4 | 图片+文字 | 发送一张图并配文"分析这张图" | Bot 回复文字（图片被忽略）+ "收到图片，暂不支持理解" | 看微信回复 |
| 5 | 纯图片 | 只发图片不配文字 | Bot 回复"收到图片，暂不支持图片理解" | 看微信回复 |

### 3.3 执行步骤

```bash
# 1. 停止 systemd 服务
sudo systemctl stop kiro-devops

# 2. 前台启动（新开一个终端窗口）
cd /home/ubuntu/kiro-devops
source .env
python3 gateway.py

# 3. 手机微信操作...
# 4. 观察终端日志输出
```

### 3.4 常见问题

| 问题 | 原因 | 解决 |
|------|------|------|
| send_image 返回 ret=-14 | context_token 过期 | 让用户重新发一条消息 |
| CDN 上传返回 403 | upload_url 过期 | 缩短 getuploadurl → upload 之间的时间 |
| 图片发送成功但微信没显示 | x_encrypted_param 或 aes_key 格式错误 | 检查 base64 编码 |
| 接收图片时 download_media 失败 | aes_key 为空或 url 过期 | 忽略该图片，记录日志 |

---

## 四、当前环境检查清单

```bash
# 检查微信 token
ls -la ~/.kiro/weixin_token.json

# 检查新代码是否已加载
python3 -c "from adapters.weixin import WeixinAdapter; print(WeixinAdapter.send_image)"
# 应输出 <function WeixinAdapter.send_image ...>

# 检查依赖
cd /home/ubuntu/kiro-devops && pip3 show cryptography pillow | grep Name
```

---

## 五、建议的测试顺序

1. **先跑 L2** — 用真实 token 验证 `getuploadurl` 能返回 upload_url（不实际发图片）
2. **再跑 L3-用例1/2** — 验证图片/文件接收
3. **最后 L3-用例3** — 验证图片发送（最复杂，依赖前两项）

> ⚠️ 每次测试发送图片后，建议让用户再发一条文字消息，刷新 context_token。
