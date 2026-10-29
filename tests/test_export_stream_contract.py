"""Independent streaming boundary checks. Every byte here is synthetic."""
import importlib.util
import io
import json
from pathlib import Path
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "skills/maintain-personal-context/scripts/chatgpt_export.py"
spec = importlib.util.spec_from_file_location("export_stream_contract", SCRIPT)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class BoundedStream(io.BytesIO):
    def __init__(self, data, bound):
        super().__init__(data)
        self.bound = bound

    def read(self, size=-1):
        if not 0 < size <= self.bound:
            raise AssertionError("unbounded or oversized read")
        return super().read(size)


class ExportStreamContractTests(unittest.TestCase):
    def test_utf8_bom_and_escaped_structural_characters_across_every_small_chunk(self):
        expected = [{"text": "合成 🧪 { } [ ], \\\" \\ tail", "nested": [{"k": [1, 2]}]},
                    {"second": "synthetic"}]
        payload = b"\xef\xbb\xbf" + json.dumps(expected, ensure_ascii=False).encode("utf-8")
        for chunk in range(1, 34):
            with self.subTest(chunk=chunk):
                self.assertEqual(list(m.iter_json_array(BoundedStream(payload, chunk),
                                                       chunk_size=chunk)), expected)

    def test_first_record_is_yielded_without_reading_whole_source(self):
        payload = b'[{"id":"first"},' + json.dumps({"later": "s" * 200000}).encode() + b']'
        stream = BoundedStream(payload, 16)
        records = m.iter_json_array(stream, chunk_size=16)
        self.assertEqual(next(records), {"id": "first"})
        self.assertLessEqual(stream.tell(), 32)
        self.assertEqual(next(records), {"later": "s" * 200000})
        with self.assertRaises(StopIteration):
            next(records)

    def test_limit_counts_utf8_source_bytes_and_is_configurable(self):
        record = json.dumps({"text": "合成" * 7}, ensure_ascii=False).encode("utf-8")
        payload = b'[' + record + b']'
        self.assertEqual(len(list(m.iter_json_array(BoundedStream(payload, 7), chunk_size=7,
                                                   max_record_bytes=len(record)))), 1)
        with self.assertRaises(m.ExportError):
            list(m.iter_json_array(BoundedStream(payload, 7), chunk_size=7,
                                   max_record_bytes=len(record) - 1))

    def test_truncated_and_extra_data_never_silently_finish(self):
        for payload in (b'[{}', b'[{},]', b'[{}] trailing', b'[{"a":"unterminated}]',
                        b'[{}][{}]', b'[null]', b'[{"a":1,"a":2}]', b'[{"a":"\xff"}]'):
            with self.subTest(payload=payload):
                with self.assertRaises(m.ExportError):
                    list(m.iter_json_array(BoundedStream(payload, 3), chunk_size=3))

    def test_deep_structure_returns_sanitized_failure(self):
        payload = b'[{"nested":' + b'[' * 3000 + b'0' + b']' * 3000 + b'}]'
        with self.assertRaises(m.ExportError):
            list(m.iter_json_array(BoundedStream(payload, 64), chunk_size=64))


if __name__ == "__main__":
    unittest.main()
