"""
评分器：跑 test_cases_get_history.json，对比Agent实际决定 vs 预期决定

设计说明：
- score_case() 是纯打分逻辑，跟"Agent怎么做决定"完全解耦——先用占位的
  stub_agent_decide() 跑通全流程；下一步接入真实的Prompt+工具调用时，
  只需要把 decide_fn 换成真实调用Claude的函数，评分逻辑本身不用改。
- 对比维度：
  1）是否调用get_history跟预期是否一致（核心指标，一致/漏调用/误调用）
  2）若两边都判断"该调用"，进一步核对传参(user_id/topic)对不对
  3）"调用后有没有把历史结果用对"（比如该不该因历史而调整判断）目前不做自动核验，
     跟照见现有eval脚本一样，这类需要理解语义的判断先留人工复核
"""
import json
from pathlib import Path

CASES_FILE = Path(__file__).parent / "test_cases_get_history.json"
OUTPUT_FILE = Path(__file__).parent / "get_history_score_report.json"


def stub_agent_decide(case: dict) -> dict:
    """
    占位版"Agent决策"：用简单关键词规则模拟，只是为了让评分器现在就能跑通、
    先验证评分逻辑本身没问题。下一步（Prompt扩展）会把这个函数换成真实调用
    Claude+工具的版本——那时候像H-CALL-4这种"态度不明的矛盾"触发的场景才有
    可能被正确捕捉，关键词匹配天然做不到这个。
    """
    scene = "".join(case["陈述"])
    history_hint_keywords = ["一直都", "已经说过好几次", "不是第一次", "之前", "想起来之前"]
    called = any(kw in scene for kw in history_hint_keywords)
    return {
        "调用了get_history": called,
        "传参_user_id": case["user_id"] if called else None,
        "传参_topic": case["topic"] if called else None,
    }


def score_case(case: dict, actual: dict) -> dict:
    expected_call = case["预期是否调用get_history"]
    actual_call = actual["调用了get_history"]

    result = {
        "case_id": case["case_id"],
        "预期是否调用": expected_call,
        "实际是否调用": actual_call,
        "调用判断": "一致" if expected_call == actual_call else "不一致",
    }

    if result["调用判断"] == "不一致":
        result["失败类型"] = "漏调用（该调用但没调）" if expected_call else "误调用（不该调用却调了）"
        return result

    if expected_call and actual_call:
        param_ok = (
            actual.get("传参_user_id") == case["user_id"]
            and actual.get("传参_topic") == case["topic"]
        )
        result["传参正确"] = param_ok
        if not param_ok:
            result["失败类型"] = "调用了但传参错误"

    return result


def run_all(decide_fn=stub_agent_decide):
    with open(CASES_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    cases = data["cases"]

    results = []
    for case in cases:
        actual = decide_fn(case)
        scored = score_case(case, actual)
        # 把决策函数返回的完整信息也存进去（比如最终回复、模型原始输入、
        # get_history实际返回的内容），不然报告里只有对/错，没法人工核对
        # "调用后有没有把历史结果用对"
        scored["详情"] = actual
        results.append(scored)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    total = len(results)
    call_match = sum(1 for r in results if r["调用判断"] == "一致")
    print(f"调用判断一致率：{call_match}/{total}")
    for r in results:
        if r["调用判断"] != "一致":
            print(f"  ✗ {r['case_id']}：{r['失败类型']}")
        elif r.get("传参正确") is False:
            print(f"  ✗ {r['case_id']}：{r['失败类型']}")
    print(f"结果已写入 {OUTPUT_FILE.name}")

    return results


if __name__ == "__main__":
    run_all()
