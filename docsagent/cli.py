from __future__ import annotations

import argparse
import json
from pathlib import Path

from .agent.application import App
from .infrastructure.git import GitAdapter
from .project.binding import ensure_inside, load_binding, scan_sources
from .project.documentation import generate_drafts, write_drafts, write_project_markdown
from .project.files import search_text
from .project.models import Draft
from .project.parsing import parse_file
from .providers.model_client import ModelClient
from .web.server import DocsServer


def _print_draft_warnings(root: Path, drafts: list[Draft]) -> None:
    for draft in drafts:
        for warning in draft.warnings:
            print(f"Warning ({draft.document.relative_to(root)}): {warning}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="docsagent")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="start the local web workbench")
    serve.add_argument("--root", default=".")
    serve.add_argument("--source")
    serve.add_argument("--docs-root")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)

    sync = sub.add_parser("sync", help="generate mirrored Markdown drafts")
    sync.add_argument("--root", default=".")
    sync.add_argument("--source")
    sync.add_argument("--docs-root")

    run = sub.add_parser("run", help="run a natural-language project task")
    run.add_argument("--root", default=".")
    run.add_argument("--goal", required=True)
    run.add_argument("--source")
    run.add_argument("--docs-root")
    run.add_argument("--input", action="append", default=[])
    run.add_argument("--output")

    scan = sub.add_parser("scan", help="print parsed source facts")
    scan.add_argument("--root", default=".")
    scan.add_argument("--source")
    scan.add_argument("--docs-root")

    git = sub.add_parser("git", help="inspect documentation Git history")
    git.add_argument("--root", default=".")
    git.add_argument("action", choices=("status", "diff", "log"))

    sub.add_parser("self-check", help="run a small deterministic smoke check")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "self-check":
        return self_check()
    if args.command == "git":
        adapter = GitAdapter(Path(args.root).resolve())
        if args.action == "status":
            binding = load_binding(args.root)
            docs_root = binding.docs_root.relative_to(binding.root).as_posix()
            print(json.dumps(adapter.document_status(docs_root), ensure_ascii=False, indent=2))
        elif args.action == "diff":
            binding = load_binding(args.root)
            docs_root = binding.docs_root.relative_to(binding.root).as_posix()
            print(adapter.diff_documents(docs_root, 50000)[0])
        else:
            binding = load_binding(args.root)
            docs_root = binding.docs_root.relative_to(binding.root).as_posix()
            print(json.dumps(adapter.document_history(docs_root), ensure_ascii=False, indent=2))
        return 0
    binding = load_binding(args.root, getattr(args, "source", None), getattr(args, "docs_root", None))
    if args.command == "run":
        app = App(binding, Path(__file__).parent / "web")
        goal = args.goal
        lowered = goal.lower()
        if any(word in lowered for word in ("搜索", "查找", "search")):
            query = goal.split(maxsplit=1)[1] if len(goal.split(maxsplit=1)) == 2 else goal
            result = search_text(binding.root, query, limit=50)
            print("\n".join(f"{item['path']}:{item['line']} {item['text']}" for item in result["items"]) or "没有找到匹配内容。")
            if result["truncated"]:
                print(f"\n已显示前 {result['shown']} 条，仍有更多匹配结果。")
            return 0
        if any(word in lowered for word in ("同步", "生成文档", "代码文档", "mirror")):
            drafts = generate_drafts(binding, ModelClient())
            print(f"Generated {len(drafts)} Markdown drafts.")
            _print_draft_warnings(binding.root, drafts)
            if input("Write these documents? [y/N] ").strip().lower() == "y":
                print(json.dumps(write_drafts(binding, drafts), ensure_ascii=False, indent=2))
            return 0
        answer = app.answer(goal, context_paths=args.input)
        if args.output:
            target = ensure_inside(binding.root, binding.root / args.output)
            print(answer)
            if input(f"Write answer to {target.relative_to(binding.root)}? [y/N] ").strip().lower() == "y":
                base = target.read_text(encoding="utf-8") if target.is_file() else None
                print(write_project_markdown(binding.root, target, answer.rstrip() + "\n", base))
        else:
            print(answer)
        return 0
    if args.command == "serve":
        app = App(binding, Path(__file__).parent / "web" / "dist")
        server = DocsServer((args.host, args.port), app)
        host, port = server.server_address[:2]
        print(f"DOCSAGENT_READY {host} {port}", flush=True)
        print(f"DocsAgent running at http://{host}:{port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return 0
    if args.command == "scan":
        result = []
        for path in scan_sources(binding):
            facts = parse_file(path, binding.root)
            result.append({"path": str(path.relative_to(binding.root)), "language": facts.language, "symbols": [symbol.name for symbol in facts.symbols], "diagnostics": facts.diagnostics})
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "sync":
        drafts = generate_drafts(binding, ModelClient())
        print(f"Generated {len(drafts)} Markdown drafts:")
        for draft in drafts:
            print(f"  {draft.document.relative_to(binding.root)}")
        _print_draft_warnings(binding.root, drafts)
        answer = input("Write these documents? [y/N] ").strip().lower()
        if answer != "y":
            print("Drafts discarded; no files changed.")
            return 0
        print(json.dumps(write_drafts(binding, drafts), ensure_ascii=False, indent=2))
        return 0
    return 1


def self_check() -> int:
    from tempfile import TemporaryDirectory

    with TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "src").mkdir()
        (root / "src" / "main.py").write_text("VALUE = 1\n\ndef run(x):\n    result = x + VALUE\n    return result\n", encoding="utf-8")
        binding = load_binding(root)
        drafts = generate_drafts(binding)
        assert len(drafts) == 1
        assert "run" in drafts[0].content
        assert (drafts[0].document.relative_to(root)).as_posix() == "docs/code-reference/src/main.py.md"
    print("self-check: ok")
    return 0
