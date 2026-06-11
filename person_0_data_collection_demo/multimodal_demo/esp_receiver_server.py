import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, Any


OUTPUT_DIR = "multimodal_outputs"
LATEST_FILE = os.path.join(OUTPUT_DIR, "esp_latest.json")
LOG_FILE = os.path.join(OUTPUT_DIR, "esp_log.jsonl")

latest_readings: Dict[str, Dict[str, Any]] = {}


def now_ms() -> int:
    return int(time.time() * 1000)


def save_latest() -> None:
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    with open(LATEST_FILE, "w", encoding="utf-8") as file:
        json.dump(latest_readings, file, indent=2)


def append_log(reading: Dict[str, Any]) -> None:
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    with open(LOG_FILE, "a", encoding="utf-8") as file:
        file.write(json.dumps(reading) + "\n")


class ESPRequestHandler(BaseHTTPRequestHandler):
    def _send_json(self, status_code: int, data: Dict[str, Any]) -> None:
        response_body = json.dumps(data).encode("utf-8")

        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response_body)))
        self.end_headers()
        self.wfile.write(response_body)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json(200, {"status": "ok", "message": "ESP server is running"})
            return

        if self.path == "/latest":
            self._send_json(200, latest_readings)
            return

        self._send_json(404, {"error": "Unknown path"})

    def do_POST(self) -> None:
        if self.path != "/esp":
            self._send_json(404, {"error": "Unknown path"})
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(content_length).decode("utf-8")
            data = json.loads(raw_body)

            required_fields = ["anchor_id", "device_id", "rssi", "seen", "confidence"]

            for field in required_fields:
                if field not in data:
                    self._send_json(400, {"error": f"Missing field: {field}"})
                    return

            anchor_id = str(data["anchor_id"])
            device_id = str(data["device_id"])

            reading = {
                "anchor_id": anchor_id,
                "device_id": device_id,
                "rssi": int(data["rssi"]),
                "seen": bool(data["seen"]),
                "confidence": float(data["confidence"]),
                "esp_millis": int(data.get("esp_millis", 0)),
                "server_timestamp_ms": now_ms(),
            }

            key = f"{device_id}|{anchor_id}"
            latest_readings[key] = reading

            save_latest()
            append_log(reading)

            print(
                f"Received: device={device_id}, "
                f"anchor={anchor_id}, "
                f"rssi={reading['rssi']}, "
                f"seen={reading['seen']}"
            )

            self._send_json(200, {"status": "ok"})

        except Exception as error:
            self._send_json(500, {"error": str(error)})


def main() -> None:
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    server = ThreadingHTTPServer(("0.0.0.0", 8000), ESPRequestHandler)

    print("ESP receiver server running.")
    print("Listening on: http://0.0.0.0:8000")
    print("Health check: http://127.0.0.1:8000/health")
    print(f"Saving data into: {OUTPUT_DIR}")
    print()
    print("Leave this terminal open.")

    server.serve_forever()


if __name__ == "__main__":
    main()