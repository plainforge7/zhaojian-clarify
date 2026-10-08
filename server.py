import os
import json
import time
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import requests

from json_utils import extract_json
from response_arbitration import arbitrate_response

app = Flask(__name__)
V1_1_ENABLED = True
CORS(app)

API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()

# ---- 输入保护(最小方案) ----
# 拒绝式校验：只做长度门槛判断，不做摘要/截断，避免在用户确认前替其压缩信息
MAX_INPUT_CHARS = 1200
INPUT_TOO_LONG_MESSAGE = "内容较长，建议先说清楚一件具体的事，我们可以分几轮补充细节。请精简到1200字以内再提交。"


def validate_input_length(text):
    """长度校验。返回 (是否通过, 错误信息)。超长时调用方应直接返回错误，不进入任何模型调用。"""
    if not text or not text.strip():
        return False, "请先描述发生了什么。"
    if len(text) > MAX_INPUT_CHARS:
        return False, INPUT_TOO_LONG_MESSAGE
    return True, ""

V1_1_SYSTEM_PROMPT_FILE = "照见 V1 System Prompt V1.1.md"

LEGACY_SYSTEM_PROMPT_FILE = "照见 旧版 System Prompt（zhaojian-v1-legacy）.md"


def load_legacy_system_prompt():
    """旧版的完整 System Prompt 未随公开仓库分发，需要自行提供同名文件才能跑通。"""
    path = Path(LEGACY_SYSTEM_PROMPT_FILE)
    return path.read_text(encoding="utf-8")


def call_api_with_retry(payload, headers, label="unknown", max_retries=4):
    start = time.time()
    for attempt in range(max_retries):
        try:
            response = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers=headers,
                json=payload,
                timeout=60
            )
            result = response.json()
            elapsed_ms = round((time.time() - start) * 1000)
            usage = result.get("usage", {})
            log_entry = {
                "timestamp": datetime.now().isoformat(),
                "label": label,
                "elapsed_ms": elapsed_ms,
                "input_tokens": usage.get("input_tokens"),
                "output_tokens": usage.get("output_tokens"),
                "attempt": attempt + 1
            }
            with open("api_usage_log.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
            return result
        except (requests.exceptions.ProxyError,
                requests.exceptions.SSLError,
                requests.exceptions.ConnectionError,
                requests.exceptions.Timeout) as e:
            if attempt < max_retries - 1:
                time.sleep(1)
            else:
                raise


def call_legacy_api(scene):
    payload = {
        "model": "claude-sonnet-4-6",
        "max_tokens": 1000,
        "system": load_legacy_system_prompt(),
        "messages": [{"role": "user", "content": scene}]
    }
    headers = {
        "Content-Type": "application/json",
        "x-api-key": API_KEY,
        "anthropic-version": "2023-06-01"
    }
    return call_api_with_retry(payload, headers, label="旧版")


def load_v1_1_system_prompt():
    path = Path(V1_1_SYSTEM_PROMPT_FILE)
    return path.read_text(encoding="utf-8")


def call_v1_1_api(scene):
    system_prompt = load_v1_1_system_prompt()

    payload = {
        "model": "claude-sonnet-4-6",
        "max_tokens": 2000,
        "system": system_prompt,
        "messages": [{"role": "user", "content": scene}]
    }
    headers = {
        "Content-Type": "application/json",
        "x-api-key": API_KEY,
        "anthropic-version": "2023-06-01"
    }

    raw_response = call_api_with_retry(payload, headers, label="V1.1")
    raw_text = "".join(
        block.get("text", "") for block in raw_response.get("content", [])
    )
    return extract_json(raw_text)


@app.route("/api/analyze", methods=["POST"])
def analyze():
    data = request.get_json()
    scene = data.get("scene", "")

    ok, err = validate_input_length(scene)
    if not ok:
        return jsonify({"error": "input_too_long", "detail": err}), 400

    try:
        result = call_legacy_api(scene)
        return jsonify(result), 200
    except Exception as e:
        return jsonify({
            "error": "upstream_connection_failed",
            "detail": str(e)
        }), 502


@app.route("/api/analyze_v1_1", methods=["POST"])
def analyze_v1_1():
    data = request.get_json()
    scene = data.get("scene", "")

    ok, err = validate_input_length(scene)
    if not ok:
        return jsonify({"error": "input_too_long", "detail": err}), 400

    if not V1_1_ENABLED:
        try:
            legacy_raw = call_legacy_api(scene)
            legacy_text = "".join(
                b.get("text", "") for b in legacy_raw.get("content", [])
            )
            legacy_output = extract_json(legacy_text)
            legacy_output["仲裁覆盖"] = False
            legacy_output["版本"] = "旧版（开关关闭）"
            return jsonify(legacy_output)
        except Exception as e:
            return jsonify({"error": "legacy_call_failed", "detail": str(e)}), 502

    with ThreadPoolExecutor(max_workers=2) as executor:
        future_legacy = executor.submit(call_legacy_api, scene)
        future_v1_1 = executor.submit(call_v1_1_api, scene)

        try:
            legacy_raw = future_legacy.result()
            legacy_text = "".join(
                b.get("text", "") for b in legacy_raw.get("content", [])
            )
            legacy_output = extract_json(legacy_text)
        except Exception as e:
            # 旧版本身失败，没有任何结果可以兜底，只能报错
            return jsonify({"error": "legacy_call_failed", "detail": str(e)}), 502

        try:
            v1_1_output = future_v1_1.result()
        except Exception as e:
            # V1.1 失败，但旧版已经成功——退回旧版结果，不让用户空手而归
            legacy_output["仲裁覆盖"] = False
            legacy_output["版本"] = "旧版兜底（V1.1调用失败）"
            legacy_output["兜底原因"] = str(e)
            return jsonify(legacy_output)

    print(json.dumps(v1_1_output, ensure_ascii=False, indent=2))

    final_output = arbitrate_response(
        v1_1_output.get("证据账本", []),
        v1_1_output.get("矛盾清单", []),
        legacy_output
    )
    final_output["版本"] = "V1.1+仲裁"
    has_safety_flag = any(
        e.get("安全关注标记") == "是" for e in v1_1_output.get("证据账本", [])
    )
    final_output["安全提示"] = has_safety_flag
    final_output["证据来源"] = [
        {
            "内容": "、".join(e.get("内容", [])),
            "来源": e.get("来源", ""),
            "原始依据": e.get("原始依据", "")
        }
        for e in v1_1_output.get("证据账本", [])
    ]

    return jsonify(final_output)

@app.route('/api/feedback', methods=['POST'])
def feedback():
    data = request.get_json()
    result = data.get('result', {})
    entry = {
        'timestamp': datetime.now().isoformat(),
        'scene_length': len(data.get('scene', '')),
        'pattern_names': [p.get('name', '') for p in result.get('patterns', [])],
        '版本': result.get('版本', ''),
        '仲裁覆盖': result.get('仲裁覆盖', False),
        'vote': data.get('vote', '')
    }
    with open('feedback_log.jsonl', 'a', encoding='utf-8') as f:
        f.write(json.dumps(entry, ensure_ascii=False) + '\n')
    return jsonify({'status': 'ok'})

@app.route("/dashboard")
def dashboard():
    with open("eval_results.json", "r", encoding="utf-8") as f:
        results = json.load(f)
    with open("test_cases.json", "r", encoding="utf-8") as f:
        cases = json.load(f)
    return render_template("dashboard.html", results=results, cases=cases)


@app.route("/dashboard_v1_1")
def dashboard_v1_1():
    """新旧版本整合(V1.1+仲裁)后的三版本回归测试 dashboard，读取 comparison_results_v1_1.json"""
    with open("comparison_results_v1_1.json", "r", encoding="utf-8") as f:
        results = json.load(f)

    valid = [r for r in results if "错误" not in r]
    summary = {
        "total": len(results),
        "errored": len(results) - len(valid),
        "consistent": sum(1 for r in valid if r.get("仲裁判定") == "一致"),
        "inconsistent": sum(1 for r in valid if r.get("仲裁判定") != "一致"),
        "arbitrated": sum(1 for r in valid if r.get("统合版_仲裁覆盖")),
        "safety_flagged": sum(1 for r in valid if r.get("统合版_安全提示")),
    }
    return render_template("dashboard_v1_1.html", results=results, summary=summary)


if __name__ == "__main__":
    app.run(port=5050, debug=True)
