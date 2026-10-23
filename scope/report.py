"""Export an offline HTML artifact from a run result or matrix summary."""
import argparse
import html
import json
from pathlib import Path


def render(data):
    esc = lambda value: html.escape(str(value), quote=True)
    title = "SCOPE 模型评测" if "models" in data else "SCOPE 执行展示"
    sections = []
    if "models" in data:
        sections.append('<p>离线替身测试，非真实模型性能。</p>' if data.get("demo") else '<p>真实运行记录；错误与成功率请一并查看。</p>')
        columns = ["model", "attempted", "succeeded", "errors", "em_all_attempts", "f1_success_only", "mean_seconds", "p95_seconds", "mean_tokens_reported"]
        sections.append('<div class="scroll"><table><tr>' + ''.join('<th>' + esc(c) + '</th>' for c in columns) + '</tr>')
        for row in data["models"]:
            sections.append('<tr>' + ''.join('<td>' + esc(row.get(c)) + '</td>' for c in columns) + '</tr>')
        sections.append('</table></div>')
    else:
        sections.append('<h2>问题</h2><p>' + esc(data["query"]) + '</p><h2>答案</h2><p>' + esc(data["answer"]) + '</p>')
        sections.append('<h2>任务依赖与状态</h2>')
        for node in data["plan"]:
            capsule = data["memory"].get(node["id"], data.get("partials", {}).get(node["id"]))
            status = capsule["status"] if capsule else "Blocked"
            sections.append('<article><b>' + esc(node["id"]) + ' · ' + esc(status) + '</b><p>' + esc(node["description"]) + '</p><p>依赖：' + esc(', '.join(node["dependencies"]) or '无') + '</p>')
            if capsule:
                sections.append('<p>结果：' + esc(capsule["result"]) + '</p><p>验证摘要：' + esc(capsule["rationale"]) + '</p><details><summary>证据</summary><pre>' + esc(json.dumps(capsule["evidence"], indent=2, ensure_ascii=False)) + '</pre></details>')
            sections.append('</article>')
        sections.append('<h2>事件时间线</h2><ol>')
        for event in data["events"]:
            sections.append('<li>' + esc(json.dumps(event, ensure_ascii=False)) + '</li>')
        sections.append('</ol>')
    return '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>' + title + '</title><style>body{font:16px system-ui;max-width:1100px;margin:40px auto;padding:0 20px;background:#f5f7fa;color:#172538}article{background:white;border:1px solid #ccd5e0;border-radius:10px;padding:18px;margin:12px 0}pre{white-space:pre-wrap;overflow-wrap:anywhere}table{border-collapse:collapse;background:white}td,th{padding:10px;border:1px solid #ccd5e0;text-align:left}.scroll{overflow-x:auto}li{margin:12px 0;overflow-wrap:anywhere}</style><h1>' + title + '</h1>' + ''.join(sections) + '</html>'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render(json.loads(Path(args.input).read_text(encoding="utf-8"))), encoding="utf-8")
    print(target)


if __name__ == "__main__":
    main()
