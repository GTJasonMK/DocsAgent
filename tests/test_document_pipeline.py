from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from docsagent.project.binding import load_binding
from docsagent.project.documentation import iter_drafts, render_document, validate_code_document, write_drafts
from docsagent.project.graph import build_graph
from docsagent.project.parsing import parse_file, symbol_anchor


class DocumentPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "src").mkdir()
        self.binding = load_binding(self.root)

    def test_python_facts_cover_scopes_signatures_parameters_returns_calls_and_exceptions(self):
        source = textwrap.dedent("""
            COUNT: int = 1
            COUNT = 2

            class Store:
                cache: dict[str, int] = {}

                def normalize(self, value: int) -> int:
                    return value

                def get(self, key: str, /, default: int = 0, *, strict: bool = False) -> int:
                    result = self.normalize(default)
                    if strict:
                        raise ValueError(key)
                    def nested(flag: bool = False) -> bool:
                        nested_value = flag
                        return nested_value
                    return result

            def outer(value: int):
                outer_value = value
                def inner(flag: bool):
                    inner_value = flag
                    return inner_value
                return inner(True)
        """)
        source_path = self.root / "src" / "store.py"
        source_path.write_text(source, encoding="utf-8")

        facts = parse_file(source_path, self.root)
        by_qualname = {symbol.qualname: symbol for symbol in facts.symbols}
        get_method = by_qualname["Store.get"]
        nested = by_qualname["Store.get.nested"]
        outer = by_qualname["outer"]
        inner = by_qualname["outer.inner"]

        self.assertEqual(get_method.signature, "get(self, key: str, /, default: int = 0, *, strict: bool = False) -> int")
        self.assertEqual([item["name"] for item in get_method.parameters], ["self", "key", "default", "strict"])
        self.assertEqual(get_method.variables, [{"name": "nested", "kind": "local"}, {"name": "result", "kind": "local"}])
        self.assertEqual(get_method.exceptions, ["ValueError"])
        self.assertEqual(get_method.result, "result")
        self.assertEqual(nested.variables, [{"name": "nested_value", "kind": "local"}])
        self.assertEqual(outer.variables, [{"name": "inner", "kind": "local"}, {"name": "outer_value", "kind": "local"}])
        self.assertEqual(inner.variables, [{"name": "inner_value", "kind": "local"}])
        self.assertEqual(by_qualname["Store.cache"].scope, "Store")
        self.assertEqual(by_qualname["COUNT"].result, "1; then 2")
        self.assertTrue(any(edge.source == get_method.symbol_id and edge.target == by_qualname["Store.normalize"].symbol_id for edge in facts.calls))

    def test_generated_document_has_symbol_anchors_and_validator_catches_omissions(self):
        source_path = self.root / "src" / "module.py"
        source_path.write_text("""VALUE: int = 1\n\ndef compute(x: int = 2) -> int:\n    local = x\n    return local\n""", encoding="utf-8")
        facts = parse_file(source_path, self.root)
        content = render_document(self.binding, facts, [facts])

        self.assertEqual(validate_code_document(self.binding, facts, [facts], content), [])
        function = next(symbol for symbol in facts.symbols if symbol.name == "compute")
        self.assertIn("**参数**", content)
        self.assertIn("`x`", content)
        self.assertIn("`local`", content)
        self.assertIn(f'<a id="{symbol_anchor(function.symbol_id)}"></a>', content)

        incomplete = content.replace(f'<a id="{symbol_anchor(function.symbol_id)}"></a>', "")
        self.assertTrue(any("missing documentation anchor" in error for error in validate_code_document(self.binding, facts, [facts], incomplete)))

    def test_sync_orders_python_dependencies_before_importers(self):
        importer = self.root / "src" / "a_consumer.py"
        dependency = self.root / "src" / "z_base.py"
        importer.write_text("from z_base import base\n\ndef run():\n    return base()\n", encoding="utf-8")
        dependency.write_text("def base():\n    return 1\n", encoding="utf-8")

        drafts = list(iter_drafts(self.binding, sources=[importer, dependency]))
        self.assertEqual([draft.source.name for draft in drafts], ["z_base.py", "a_consumer.py"])

    def test_write_refuses_documents_for_changed_source(self):
        source = self.root / "src" / "module.py"
        source.write_text("VALUE = 1\n", encoding="utf-8")
        drafts = list(iter_drafts(self.binding))
        source.write_text("VALUE = 2\n", encoding="utf-8")

        with self.assertRaisesRegex(RuntimeError, "source changed during generation"):
            write_drafts(self.binding, drafts)
        self.assertFalse(drafts[0].document.exists())

    def test_non_python_parser_marks_heuristic_coverage(self):
        source = self.root / "src" / "app.tsx"
        source.write_text("export function save(value: string) { return value; }\n", encoding="utf-8")
        facts = parse_file(source, self.root)
        self.assertTrue(any("heuristic parser" in diagnostic for diagnostic in facts.diagnostics))

    def test_project_call_graph_resolves_unique_python_calls_and_limits_scope(self):
        caller = self.root / "src" / "caller.py"
        callee = self.root / "src" / "callee.py"
        caller.write_text("from callee import run\n\ndef start():\n    return run()\n", encoding="utf-8")
        callee.write_text("def run():\n    return 1\n", encoding="utf-8")
        left, right = [parse_file(path, self.root) for path in (caller, callee)]

        graph = build_graph(self.binding, [left, right], root_path="src/caller.py", max_depth=3)

        self.assertEqual(graph["summary"]["diagnostic_files"], 0)
        self.assertTrue({"start", "run"} <= {node["label"] for node in graph["nodes"]})
        self.assertTrue(any(edge["status"] == "resolved" and edge["label"] == "run" for edge in graph["edges"]))


if __name__ == "__main__":
    unittest.main()
