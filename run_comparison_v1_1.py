"""
三版本回归测试脚本（基于原 run_comparison.py 修改）

改动说明：
1. 测试集从 shared_test_cases.json（9条）换成 test_cases_v1_1.json（20条黄金案例）
2. 修复原脚本的一个问题：call_legacy_api() 返回的是原始API响应（含 content 字段），
   原脚本没有调用 extract_json() 就直接当成解析后的字典使用，这里补上，
   和 server.py 里 /api/analyze_v1_1 路由的处理方式保持一致
3. 拼装 final_output 时，补上这次新加的"证据来源"字段（逻辑与 server.py 一致）
4. 内置仲裁一致性自动核验（复用 check_arbitration.py 的判定逻辑），
   跑完直接看结果里的"仲裁判定"字段是否为"一致"，不用再单独跑一次 check_arbitration.py

用法：
    export ANTHROPIC_API_KEY=你的key
    python3 run_comparison_v1_1.py
"""

import json
from server import call_legacy_api, call_v1_1_api
from response_arbitration import arbitrate_response
from json_utils import extract_json

TEST_CASES_FILE = "test_cases_v1_1.json"
OUTPUT_FILE = "comparison_results_v1_1.json"


def run_single_case(case_id, scene):
    # 旧版：call_legacy_api 返回原始API响应，需要先提取JSON，和 server.py 路由逻辑保持一致
    legacy_raw = call_legacy_api(scene)
    legacy_text = "".join(
        b.get("text", "") for b in legacy_raw.get("content", [])
    )
    legacy_output = extract_json(legacy_text)

    # 新版：call_v1_1_api 已经返回解析后的字典
    v1_1_output = call_v1_1_api(scene)

    # 统合版：仲裁 + 证据来源拼装，与 server.py 的 /api/analyze_v1_1 路由逻辑一致
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

    # 仲裁一致性自动核验（复用 check_arbitration.py 的判定逻辑）
    evidence_by_id = {e["id"]: e for e in v1_1_output.get("证据账本", [])}
    conflicts = v1_1_output.get("矛盾清单", [])
    should_trigger = False
    for c in conflicts:
        pointed_id = c.get("冲突指向")
        if pointed_id in evidence_by_id and evidence_by_id[pointed_id].get("安全关注标记") == "是":
            should_trigger = True
            break
    actual_trigger = final_output.get("仲裁覆盖", False)
    arbitration_check = "一致" if should_trigger == actual_trigger else "不一致（需人工核查）"

    return {
        "案例名": case_id,
        "旧版输出": legacy_output,
        "新版证据账本": v1_1_output.get("证据账本", []),
        "新版矛盾清单": v1_1_output.get("矛盾清单", []),
        "统合版_仲裁覆盖": final_output.get("仲裁覆盖"),
        "统合版_仲裁文本": final_output.get("仲裁文本", ""),
        "统合版_安全提示": final_output.get("安全提示"),
        "统合版_证据来源": final_output.get("证据来源", []),
        "仲裁判定": arbitration_check,
    }


if __name__ == "__main__":
    with open(TEST_CASES_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    cases = data["cases"] if isinstance(data, dict) else data
    all_results = []

    for case in cases:
        case_id = case["case_id"]
        scene = "".join(case["陈述"])
        print(f"正在跑: {case_id}")
        try:
            result = run_single_case(case_id, scene)
        except Exception as e:
            result = {"案例名": case_id, "错误": str(e)}
        all_results.append(result)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)

    total = len(all_results)
    errored = sum(1 for r in all_results if "错误" in r)
    inconsistent = sum(1 for r in all_results if r.get("仲裁判定") == "不一致（需人工核查）")
    print(f"\n完成：{total}个案例，{errored}个调用出错，{inconsistent}个仲裁判定不一致。")
    print(f"结果已写入 {OUTPUT_FILE}")
