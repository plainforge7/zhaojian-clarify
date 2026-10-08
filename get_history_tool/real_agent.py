"""
Prompt扩展：把 get_history 工具定义接入真实的 Claude 调用，
替换 scorer.py 里的占位 stub_agent_decide。

用法：
    export ANTHROPIC_API_KEY=你的key
    cd 到这个文件所在目录
    python3 real_agent.py

设计要点（对应之前定的六项前提）：
- system prompt 里把"当前会话的user_id"当成既定上下文直接给模型，不是让模型自己猜
- 不管模型tool_use参数里传了什么user_id，dispatch时都用已知的真实user_id覆盖执行
  （"模型原始输入"字段保留模型自己写的值，方便复核它有没有试图自己编user_id）
- topic由模型从陈述中提取，这个才是真正体现模型判断能力、需要被评分的字段
- 决定调用后，真的执行一次get_history()拿到mock历史，把结果喂回去让模型给出
  最终判断，写进报告里的"最终回复"字段，供人工核对"历史用没用对"
  （这一步scorer.py暂不自动打分，人工复核）
"""

import json
import os
import requests
import time

from get_history import get_history
from scorer import run_all

API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
MODEL = "claude-sonnet-4-6"
API_URL = "https://api.anthropic.com/v1/messages"

TOOL_DEF = {
    "name": "get_history",
    "description": (
        "查询该用户在指定关系对象(topic)下此前记录的历史证据。"
        "仅当矛盾清单中存在因缺少'过去是否有类似行为'而无法解决的矛盾、"
        "或用户陈述中出现'一直如此/不是第一次'这类未展开的历史暗示时调用。"
        "返回为空时不要重复调用，也不要编造历史内容。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "user_id": {"type": "string", "description": "当前用户的user_id，使用系统提示里给定的值"},
            "topic": {"type": "string", "description": "关系对象标识，如'妈妈'，从当前陈述中提取"}
        },
        "required": ["user_id", "topic"]
    }
}

SYSTEM_PROMPT_TEMPLATE = """你是一位帮助用户看清关系场景中证据与模式的分析助手。

当前会话的user_id是：{user_id}（这是系统提供的固定值，不要自己编造或从对话内容里猜测）

你有一个工具 get_history(user_id, topic) 可以调用，用来查询该用户过去在某个
关系对象下记录的历史证据。

调用判断标准：
1. 用户当前陈述中出现"一直都这样""已经说过好几次了""不是第一次了"这类指向历史
   模式、但没有展开具体细节的表达 —— 应该调用
2. 当前场景本身存在态度不明/证据不足以支撑判断，而这类不确定性本质上需要"过去
   是否有类似行为"来佐证 —— 应该调用
3. 场景信息完整、清晰、无历史暗示、这是该关系对象首次出现 —— 不要调用

如果决定调用，topic参数请从陈述中提取该场景涉及的关系对象（比如"妈妈""前男友"），
user_id固定使用上面给定的值。

拿到历史证据后，结合当前陈述给出一句简短判断（不需要输出JSON，用自然语言说清楚
你的结论，以及有没有用到历史证据、怎么用的）。"""


def _call(system_prompt, messages, tools=True, max_retries=4):
    """跟server.py的call_api_with_retry同样的重试逻辑：
    SSL/连接/超时这类瞬时网络问题重试，不是代码bug就直接崩掉。"""
    headers = {
        "Content-Type": "application/json",
        "x-api-key": API_KEY,
        "anthropic-version": "2023-06-01",
    }
    payload = {
        "model": MODEL,
        "max_tokens": 800,
        "system": system_prompt,
        "messages": messages,
    }
    if tools:
        payload["tools"] = [TOOL_DEF]

    for attempt in range(max_retries):
        try:
            response = requests.post(API_URL, headers=headers, json=payload, timeout=60)
            return response.json()
        except (requests.exceptions.ProxyError,
                requests.exceptions.SSLError,
                requests.exceptions.ConnectionError,
                requests.exceptions.Timeout) as e:
            if attempt < max_retries - 1:
                time.sleep(1.5)
            else:
                return {"error": {"type": "network_error", "message": str(e)}}


def real_agent_decide(case: dict) -> dict:
    try:
        return _real_agent_decide_inner(case)
    except Exception as e:
        return {
            "调用了get_history": False,
            "传参_user_id": None,
            "传参_topic": None,
            "错误": f"{type(e).__name__}: {e}",
        }


def _real_agent_decide_inner(case: dict) -> dict:
    scene = "".join(case["陈述"])
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(user_id=case["user_id"])
    messages = [{"role": "user", "content": scene}]

    first = _call(system_prompt, messages)
    content_blocks = first.get("content", [])

    if "error" in first:
        return {
            "调用了get_history": False,
            "传参_user_id": None,
            "传参_topic": None,
            "错误": first["error"],
        }

    tool_use_blocks = [b for b in content_blocks if b.get("type") == "tool_use" and b.get("name") == "get_history"]

    if not tool_use_blocks:
        final_text = "".join(b.get("text", "") for b in content_blocks if b.get("type") == "text")
        return {
            "调用了get_history": False,
            "传参_user_id": None,
            "传参_topic": None,
            "最终回复": final_text,
        }

    block = tool_use_blocks[0]
    model_input = block.get("input", {})
    topic_from_model = model_input.get("topic")

    # user_id过滤规则的落地：不管模型传了什么，dispatch时都用已知的真实user_id覆盖
    real_user_id = case["user_id"]
    history_result = get_history(real_user_id, topic_from_model)

    messages.append({"role": "assistant", "content": content_blocks})
    messages.append({
        "role": "user",
        "content": [{
            "type": "tool_result",
            "tool_use_id": block["id"],
            "content": json.dumps(history_result, ensure_ascii=False),
        }],
    })

    second = _call(system_prompt, messages, tools=False)
    final_text = "".join(b.get("text", "") for b in second.get("content", []) if b.get("type") == "text")

    return {
        "调用了get_history": True,
        "传参_user_id": real_user_id,
        "传参_topic": topic_from_model,
        "模型原始输入": model_input,
        "get_history返回": history_result,
        "最终回复": final_text,
    }


if __name__ == "__main__":
    if not API_KEY:
        print("没检测到 ANTHROPIC_API_KEY，先 export 一下再跑。")
    else:
        run_all(decide_fn=real_agent_decide)
