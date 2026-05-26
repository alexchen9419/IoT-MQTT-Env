# MQTT IoT Device Server — Claude Code 開發手冊

## 專案概述

在 Debian 12 home server（iota）上建立一個 MQTT IoT 裝置管理後端。
新裝置（ESP32）上線時只需提供型號，server 自動查詢型號定義並下發設定。
首個裝置：智慧門鎖（SMART-LOCK-V1）。

---

## 技術棧

- **OS**: linux
- **Broker**: Mosquitto（已安裝，port 1883）
- **Backend**: Python 3.11+
- **套件**: `paho-mqtt`, `pyyaml`
- **設定格式**: YAML（型號定義）、JSON（裝置記錄）

---

## 目錄結構

```
~/mqtt-server/
├── models.yaml          # 型號功能定義
├── devices.json         # 已註冊裝置記錄（runtime 自動生成）
├── main.py              # 入口，MQTT 連線與訊息路由
├── registry.py          # 裝置註冊邏輯
├── handlers/
│   ├── __init__.py
│   └── lock.py          # 門鎖專屬事件處理
└── requirements.txt
```

---

## MQTT Topic 規範

| Topic | 方向 | 說明 |
|-------|------|------|
| `home/register` | ESP32 → Server | 裝置上線註冊 |
| `home/device/{mac}/config` | Server → ESP32 | 型號設定下發（retained） |
| `home/device/{mac}/cmd` | Server → ESP32 | 控制指令 |
| `home/device/{mac}/state` | ESP32 → Server | 裝置狀態回報 |
| `home/device/{mac}/event` | ESP32 → Server | 事件通知（門鈴、防拆等） |

---

## Payload 格式

### 註冊（ESP32 → Server）
```json
{
  "model": "SMART-LOCK-V1",
  "mac": "AA:BB:CC:DD:EE:FF"
}
```

### 設定下發（Server → ESP32，retained）
```json
{
  "features": ["lock", "doorbell", "tamper_detect"],
  "pins": { "relay": 26, "doorbell": 27, "tamper": 25, "status_led": 2 },
  "default_state": "locked",
  "auto_lock_sec": 5,
  "handler": "lock"
}
```

### 控制指令（Server → ESP32）
```json
{ "action": "unlock" }
{ "action": "lock" }
```

### 狀態回報（ESP32 → Server）
```json
{ "locked": true }
```

### 事件（ESP32 → Server）
```json
{ "type": "doorbell" }
{ "type": "tamper_detected" }
```

---

## 核心檔案內容

### models.yaml
```yaml
SMART-LOCK-V1:
  features: [lock, doorbell, tamper_detect]
  pins:
    relay: 26
    doorbell: 27
    tamper: 25
    status_led: 2
  default_state: locked
  auto_lock_sec: 5
  handler: lock
```

### requirements.txt
```
paho-mqtt>=1.6.1
pyyaml>=6.0
```

### registry.py
```python
import json, yaml, time
from pathlib import Path

MODELS = yaml.safe_load(open("models.yaml"))
DEVICES_FILE = Path("devices.json")

def load_devices():
    if DEVICES_FILE.exists():
        return json.loads(DEVICES_FILE.read_text())
    return {}

def save_devices(devices):
    DEVICES_FILE.write_text(json.dumps(devices, indent=2))

def register(client, payload):
    mac   = payload["mac"]
    model = payload["model"]

    if model not in MODELS:
        print(f"[WARN] 未知型號: {model}")
        return None

    devices = load_devices()
    devices[mac] = {
        "model": model,
        "last_seen": time.time(),
        "online": True
    }
    save_devices(devices)

    config = MODELS[model]
    topic  = f"home/device/{mac}/config"
    client.publish(topic, json.dumps(config), retain=True)
    print(f"[INFO] 註冊成功: {mac} ({model})")

    return MODELS[model]
```

### handlers/lock.py
```python
import json

def on_state(mac, payload):
    locked = payload.get("locked")
    print(f"[LOCK {mac}] 狀態: {'鎖上' if locked else '開啟'}")

def on_event(mac, payload):
    event_type = payload.get("type")
    if event_type == "doorbell":
        print(f"[LOCK {mac}] 🔔 有人按門鈴")
        # TODO: 推播通知（Telegram / ntfy）
    elif event_type == "tamper_detected":
        print(f"[LOCK {mac}] ⚠️  防拆警報！")
        # TODO: 緊急警報

def send_unlock(client, mac):
    topic = f"home/device/{mac}/cmd"
    client.publish(topic, json.dumps({"action": "unlock"}))

def send_lock(client, mac):
    topic = f"home/device/{mac}/cmd"
    client.publish(topic, json.dumps({"action": "lock"}))
```

### main.py
```python
import json, importlib
import paho.mqtt.client as mqtt
import registry

BROKER = "localhost"
active_handlers = {}  # mac → handler module

def on_connect(client, userdata, flags, rc):
    print(f"[INFO] Broker 連線成功 (rc={rc})")
    client.subscribe("home/register")
    client.subscribe("home/device/+/state")
    client.subscribe("home/device/+/event")

def on_message(client, userdata, msg):
    topic = msg.topic
    try:
        payload = json.loads(msg.payload.decode())
    except json.JSONDecodeError:
        print(f"[WARN] 無效 JSON: {msg.payload}")
        return

    parts = topic.split("/")

    if topic == "home/register":
        model_def = registry.register(client, payload)
        if model_def:
            mac = payload["mac"]
            handler_name = model_def.get("handler", "default")
            active_handlers[mac] = importlib.import_module(f"handlers.{handler_name}")
        return

    if len(parts) == 4 and parts[3] == "state":
        mac = parts[2]
        handler = active_handlers.get(mac)
        if handler:
            handler.on_state(mac, payload)

    if len(parts) == 4 and parts[3] == "event":
        mac = parts[2]
        handler = active_handlers.get(mac)
        if handler:
            handler.on_event(mac, payload)

client = mqtt.Client()
client.on_connect = on_connect
client.on_message = on_message
client.connect(BROKER, 1883)
client.loop_forever()
```

---

## systemd 服務設定

`/etc/systemd/system/mqtt-server.service`

```ini
[Unit]
Description=MQTT IoT Device Server
After=mosquitto.service
Requires=mosquitto.service

[Service]
User=iota
WorkingDirectory=/home/iota/mqtt-server
ExecStart=/usr/bin/python3 main.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now mqtt-server
sudo systemctl status mqtt-server
```

---

## 開發任務清單

### Phase 1 — 基礎建設
- [ ] 建立專案目錄與所有檔案
- [ ] 安裝依賴：`pip install -r requirements.txt`
- [ ] 驗證 Mosquitto 運行：`systemctl status mosquitto`
- [ ] 啟動 main.py 並測試註冊流程

### Phase 2 — 測試工具
- [ ] 寫 `test_register.py`：模擬 ESP32 上線
- [ ] 寫 `test_cmd.py`：模擬發送 unlock 指令
- [ ] 寫 `test_event.py`：模擬門鈴/防拆事件

### Phase 3 — 擴充功能
- [ ] 新增 `/home/device/{mac}/offline` will message 支援
- [ ] 裝置離線偵測（LWT - Last Will and Testament）
- [ ] 推播通知（建議：Telegram bot 或 ntfy）
- [ ] REST API（FastAPI）供手機 app 呼叫 send_unlock

### Phase 4 — 新裝置型號
- [ ] 在 models.yaml 新增第二種型號（例如溫濕度感測器）
- [ ] 建立對應 handlers/sensor.py

---

## 測試指令（Mosquitto CLI）

```bash
# 模擬 ESP32 上線註冊
mosquitto_pub -h localhost -t "home/register" \
  -m '{"model":"SMART-LOCK-V1","mac":"AA:BB:CC:DD:EE:FF"}'

# 監聽所有訊息
mosquitto_sub -h localhost -t "home/#" -v

# 手動發送開鎖指令
mosquitto_pub -h localhost -t "home/device/AA:BB:CC:DD:EE:FF/cmd" \
  -m '{"action":"unlock"}'

# 模擬門鎖回報狀態
mosquitto_pub -h localhost -t "home/device/AA:BB:CC:DD:EE:FF/state" \
  -m '{"locked":false}'

# 模擬門鈴事件
mosquitto_pub -h localhost -t "home/device/AA:BB:CC:DD:EE:FF/event" \
  -m '{"type":"doorbell"}'
```

---

## 未來擴充方向（供參考）

| 功能 | 建議方案 |
|------|---------|
| 手機遠端控制 | FastAPI + WebSocket |
| 推播通知 | Telegram Bot API |
| 認證安全 | Mosquitto ACL + TLS |
| 多棟管理 | topic prefix 改為 `{building}/device/...` |
| 資料記錄 | SQLite 儲存事件 log |
