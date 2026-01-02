import requests
import json
import uuid
import os
import threading
import time
import csv
from urllib.parse import urljoin

BASE_URL = "http://localhost:1122/sse"
TOOL_NAME = "generate_line_chart"
CSV_FILENAME = "annual-co2-emissions-per-country.csv"

post_url = None
responses = {}
stop_event = threading.Event()


def sse_listener():
    global post_url
    headers = {'Accept': 'text/event-stream'}
    try:
        # print(f"Connecting to SSE stream at {BASE_URL}...")
        with requests.get(BASE_URL, stream=True, headers=headers) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if stop_event.is_set():
                    break
                if line:
                    decoded_line = line.decode('utf-8')
                    if decoded_line.startswith("event:"):
                        current_event = decoded_line.split(":", 1)[1].strip()
                    elif decoded_line.startswith("data:"):
                        data = decoded_line.split(":", 1)[1].strip()
                        if current_event == "endpoint":
                            endpoint_path = data
                            if not endpoint_path.startswith("http"):
                                post_url = urljoin(BASE_URL, endpoint_path)
                            else:
                                post_url = endpoint_path
                            # print(f"Received POST endpoint: {post_url}")
                        elif current_event == "message":
                            try:
                                json_response = json.loads(data)
                                if "id" in json_response:
                                    responses[json_response["id"]] = json_response
                                # else:
                                # print(f"Received notification: {json_response}")
                            except json.JSONDecodeError:
                                pass
                        current_event = None
    except requests.exceptions.RequestException as e:
        print(f"SSE Connection Error: {e}")
        stop_event.set()


def create_json_rpc_request(method, params=None, req_id=None):
    if req_id is None:
        req_id = str(uuid.uuid4())
    payload = {
        "jsonrpc": "2.0",
        "method": method,
        "id": req_id
    }
    if params is not None:
        payload["params"] = params
    return payload


def send_request(session, payload):
    req_id = payload.get("id")
    timeout_start = time.time()
    while post_url is None:
        if time.time() - timeout_start > 10:
            print("Timeout waiting for server handshake (endpoint).")
            return None
        if stop_event.is_set():
            return None
        time.sleep(0.1)
    try:
        response = session.post(post_url, json=payload)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"Error sending request: {e}")
        return None
    if req_id:
        wait_start = time.time()
        while req_id not in responses:
            if time.time() - wait_start > 30:
                print(f"Timeout waiting for response to {req_id}")
                return None
            if stop_event.is_set():
                return None
            time.sleep(0.1)
        return responses.pop(req_id)
    return None


def main():
    listener_thread = threading.Thread(target=sse_listener, daemon=True)
    listener_thread.start()
    session = requests.Session()
    time.sleep(1)

    if stop_event.is_set():
        print("Could not connect to server. Exiting.")
        return

    # 1. Initialize
    init_payload = create_json_rpc_request("initialize", {
        "protocolVersion": "2024-11-05",
        "capabilities": {
            "roots": {"listChanged": True},
            "sampling": {}
        },
        "clientInfo": {"name": "PythonChartClient", "version": "1.0.0"}
    }, req_id=1)

    init_response = send_request(session, init_payload)
    if not init_response or "error" in init_response:
        print("Initialization failed:", init_response)
        stop_event.set()
        return

    # 2. Notify Initialized
    if post_url:
        try:
            session.post(post_url, json=create_json_rpc_request("notifications/initialized"))
        except:
            pass

    # 3. Check Tool Schema
    print(f"🔍 Checking capabilities of '{TOOL_NAME}'...")
    list_tools_payload = create_json_rpc_request("tools/list", req_id=2)
    tools_response = send_request(session, list_tools_payload)

    tool_schema = None
    if tools_response and "result" in tools_response:
        tools = tools_response["result"].get("tools", [])
        for tool in tools:
            if tool["name"] == TOOL_NAME:
                tool_schema = tool.get("inputSchema", {})
                print(f"   Schema found. Required fields: {tool_schema.get('required', [])}")
                print(f"   Available Props: {list(tool_schema.get('properties', {}).keys())}")
                break

    if not tool_schema:
        print(f"⚠️ Tool '{TOOL_NAME}' not found.")
        stop_event.set()
        return

    # 4. Prepare Data
    chart_data = []
    print(f"📂 Reading data from {CSV_FILENAME}...")
    try:
        with open(CSV_FILENAME, mode='r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("Entity") == "World":
                    try:
                        chart_data.append({
                            "time": row["Year"],
                            "value": float(row["Annual CO₂ emissions"])
                        })
                    except ValueError:
                        continue
    except FileNotFoundError:
        print(f"❌ Error: {CSV_FILENAME} not found.")
        stop_event.set()
        return

    # 5. Call Tool with Styling Attempts
    # SCHEMA UPDATE based on line.ts:
    # The server strictly validates inputs.
    # Supported: theme, style (backgroundColor, palette, texture, startAtZero, lineWidth)
    # NOT Supported: label, point, color, xField, yField (these are ignored or hardcoded)

    tool_args = {
        "data": chart_data,
        "theme": "dark",
        "style": {
            # 'palette' is the schema-compliant way to set colors.
            # It usually expects an array of hex strings.
            "palette": ["#ff0000"],
            "lineWidth": 2
        },
        # NOTE: Hiding labels ('label': False) is NOT in the schema provided.
        # It is likely not possible with this version of the server.
    }

    print(f"📊 Requesting chart (Theme: Dark, Palette: Red)...")
    call_tool_payload = create_json_rpc_request("tools/call", {
        "name": TOOL_NAME,
        "arguments": tool_args
    }, req_id=3)

    tool_response = send_request(session, call_tool_payload)

    # 6. Handle Response
    if tool_response and "result" in tool_response:
        content = tool_response["result"].get("content", [])
        for item in content:
            if item.get("type") == "text":
                text_content = item.get("text", "").strip()
                if text_content.startswith("http"):
                    try:
                        img_response = requests.get(text_content)
                        img_response.raise_for_status()

                        ext = ".png"
                        if "jpeg" in img_response.headers.get("Content-Type", ""):
                            ext = ".jpg"

                        filename = f"chart_output{ext}"
                        with open(filename, "wb") as f:
                            f.write(img_response.content)

                        # OUTPUT: Just the path, as requested
                        print(f"✅ Chart Downloaded: {os.path.abspath(filename)}")
                    except Exception as e:
                        print(f"❌ Failed to download: {e}")
                else:
                    print(f"⚠️ Text content: {text_content}")
    elif tool_response and "error" in tool_response:
        print("❌ Tool Error:", tool_response["error"])

    stop_event.set()


if __name__ == "__main__":
    main()