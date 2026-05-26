import json
import importlib
import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion
import registry

BROKER = "localhost"
active_handlers = {}  # mac → handler module


def on_connect(client, userdata, connect_flags, reason_code, properties):
    print(f"[INFO] Broker 連線成功 (rc={reason_code})")
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


client = mqtt.Client(CallbackAPIVersion.VERSION2)
client.on_connect = on_connect
client.on_message = on_message
client.connect(BROKER, 1883)
client.loop_forever()
