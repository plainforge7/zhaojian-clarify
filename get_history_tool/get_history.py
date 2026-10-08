"""
Mock 历史事件读取工具：get_history(user_id, topic)

设计依据（对话中已确认的六项前提）：
1. 工具描述：见下方 get_history 的 docstring，写清楚返回什么、什么情况下该调用
2. user_id 的来源：必须由调用方（后端/会话层）传入，本函数不做任何"从对话里猜身份"的逻辑，
   也不信任调用方以外的任何来源
3. topic 分类标准：topic 是"关系对象标识"（用户自己起的代号，如"妈妈"），精确字符串，不做语义匹配
4. 返回结构：对齐现有"证据账本"字段（id/内容/来源/证据状态/安全关注标记/原始依据），
   额外补充 发生时间、topic标签 两个历史场景特有字段
5. 存储/检索方式：Mock 阶段用 JSON 文件模拟 {user_id: {topic: [历史证据...]}} 的两层精确匹配，
   不引入向量库/语义检索
6. "信息不足"的判断标准：不在本文件里实现（那是 Agent/Prompt 层的判断逻辑），
   本文件只负责"被调用后如何准确返回数据"
"""

import json
from pathlib import Path

MOCK_DATA_FILE = Path(__file__).parent / "history_mock_data.json"


def _load_store() -> dict:
    if not MOCK_DATA_FILE.exists():
        return {}
    with open(MOCK_DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def get_history(user_id: str, topic: str) -> dict:
    """
    查询该用户在指定关系对象(topic)下此前记录的历史证据。

    调用时机（由 Agent/Prompt 层判断，不在本函数内做判断）：
    - 矛盾清单中存在因缺少"过去是否有类似行为"这一维度而无法解决的矛盾项
    - 用户陈述中出现"一直都这样""不是第一次了"这类未展开细节的历史暗示

    参数：
        user_id: 必须由调用方在会话层确定，不接受从对话文本中提取或模型自行生成的值
        topic: 关系对象的精确标识符（如"妈妈"），不做模糊/语义匹配

    返回：
        {
          "user_id": str,
          "topic": str,
          "count": int,
          "history_evidence": [
            {
              "id": "h1",
              "内容": [...],
              "来源": "历史记录",
              "证据状态": "既往仅用户报告",
              "安全关注标记": "是"/"否",
              "原始依据": "...",
              "发生时间": "YYYY-MM-DD",
              "topic标签": "..."
            },
            ...
          ]
        }
        查无历史时 history_evidence 为空列表，不抛异常、不报错。
    """
    store = _load_store()
    user_records = store.get(user_id, {})
    history_evidence = user_records.get(topic, [])

    return {
        "user_id": user_id,
        "topic": topic,
        "count": len(history_evidence),
        "history_evidence": history_evidence,
    }


if __name__ == "__main__":
    # 简单自测：三种场景——有历史 / user_id隔离 / 查无历史
    print("场景1：u_demo_01 查 妈妈（应有2条，含1条安全关注）")
    print(json.dumps(get_history("u_demo_01", "妈妈"), ensure_ascii=False, indent=2))

    print("\n场景2：u_demo_02 查 妈妈（应只有自己的1条，验证user_id隔离不串号）")
    print(json.dumps(get_history("u_demo_02", "妈妈"), ensure_ascii=False, indent=2))

    print("\n场景3：u_demo_01 查 一个不存在的topic（应返回空列表，不报错）")
    print(json.dumps(get_history("u_demo_01", "不存在的关系对象"), ensure_ascii=False, indent=2))
