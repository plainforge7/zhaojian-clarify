import json
import re


def extract_json(raw_text: str) -> dict:
    """模型有时会在JSON外包一层解释或代码块，尽量宽容地摘出JSON对象。"""
    raw_text = raw_text.strip()
    raw_text = re.sub(r"^```json\s*|\s*```$", "", raw_text.strip())
    match = re.search(r"\{.*\}", raw_text, re.DOTALL)
    if not match:
        raise ValueError("响应中没有找到JSON对象")
    return json.loads(match.group(0))
