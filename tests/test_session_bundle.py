"""Synthetic portable sessions: integrity, state, privacy and offline reply flow."""
import base64
import copy
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/maintain-personal-context/scripts/session_bundle.py"
sys.path.insert(0, str(SCRIPT.parent))
import session_bundle as m


def data():
    return {"schema_version": 1, "title": "Synthetic session", "as_of": "2026-06-01T09:00:00+00:00",
            "sources": [{"id": "fictional-note", "title": "Synthetic note", "locator": "synthetic paragraph 1",
                         "kind": "synthetic", "read_status": "read", "time_range": "event date unknown",
                         "limitations": ["Synthetic excerpt only"],
                         "snapshot": "Synthetic adult designs paper models. Old fair is cancelled; study is paused."}],
            "coverage": {"scope": "Only this synthetic note", "limits": ["No real account access"],
                         "uncovered": ["Other sources not read"]},
            "facts": [{"id": "fictional-identity", "text": "Synthetic adult designer", "status": "current",
                       "category": "identity", "source_ids": ["fictional-note"], "sensitivity": "normal"},
                      {"id": "fictional-skill", "text": "Paper model experience", "status": "current",
                       "category": "capability", "source_ids": ["fictional-note"], "sensitivity": "normal"},
                      {"id": "fictional-fair", "text": "Old fair remains cancelled", "status": "cancelled",
                       "category": "ongoing", "source_ids": ["fictional-note"], "sensitivity": "normal"},
                      {"id": "fictional-study", "text": "Study remains paused", "status": "paused",
                       "category": "ongoing", "source_ids": ["fictional-note"], "sensitivity": "normal"}],
            "advice": [{"id": "fictional-advice", "text": "Could catalogue prior models",
                        "fact_ids": ["fictional-skill"], "status": "conditional",
                        "condition": "Only if the user chooses this focus; no action is authorized"}],
            "questions": [{"id": "fictional-focus", "text": "Which focus is useful?", "reason": "Not in checked note",
                           "fact_ids": [], "options": [{"id": "experience", "label": "Prior experience"},
                                                        {"id": "resources", "label": "Current resources"}], "answer": None}]}


def response(bundle, action="unanswered", answer=None):
    return {"kind": "session_responses", "schema_version": 1, "bundle_digest": bundle["bundle_digest"],
            "responses": [{"question_id": q["id"], "action": action, "answer": answer}
                          for q in bundle["payload"]["questions"]]}


class SessionBundleTests(unittest.TestCase):
    def test_roundtrip_five_views_and_fresh_process_are_deterministic(self):
        original = data(); before = copy.deepcopy(original)
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp).resolve() / "session"
            exported = m.export(original, dest)
            self.assertFalse(exported["knowledge_base_updated"])
            read = m.read_bundle(dest, exported["bundle_digest"])
            self.assertEqual(original, before)
            self.assertEqual(read["interview"], {"stage": "unanswered", "unanswered_ids": ["fictional-focus"]})
            self.assertIsNone(read["payload"]["questions"][0]["answer"])
            markdown = (dest / "result.md").read_text()
            for title in ["你是谁", "你能做什么", "你在做什么", "下一步", "建议"]:
                self.assertIn("## " + title, markdown)
            self.assertIn("fictional", markdown)
            args = [sys.executable, "-B", str(SCRIPT), "read", "--bundle", str(dest), "--expect-digest", exported["bundle_digest"]]
            first = subprocess.run(args, check=True, capture_output=True).stdout
            second = subprocess.run(args, check=True, capture_output=True).stdout
            self.assertEqual(first, second)
            self.assertEqual(json.loads(first), read)

    def test_empty_input_leaves_unknown_views_and_no_pending_interview(self):
        value = data(); value.update(sources=[], facts=[], advice=[], questions=[])
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp).resolve() / "empty"
            m.export(value, dest)
            self.assertEqual(m.read_bundle(dest)["interview"]["stage"], "no_pending_questions")
            self.assertIn("暂无已读依据", (dest / "result.md").read_text())

    def test_pause_and_cancel_preserved_advice_cannot_advance_them(self):
        value = data()
        sealed = m.seal(value)
        self.assertEqual([f["status"] for f in sealed["facts"][2:]], ["cancelled", "paused"])
        rendered = m.render_markdown(sealed, "synthetic-version")
        self.assertIn("已取消", rendered); self.assertIn("已暂停", rendered)
        for fact_id in ("fictional-fair", "fictional-study"):
            attempt = copy.deepcopy(value); attempt["advice"][0]["fact_ids"] = [fact_id]
            with self.assertRaises(m.BundleError): m.validate(attempt)

    def test_missing_or_unread_source_cannot_support_facts(self):
        for change in ("missing", "blocked", "unread"):
            value = data()
            if change == "missing": value["facts"][0]["source_ids"] = ["fictional-missing"]
            else: value["sources"][0].update(read_status=change, snapshot=None)
            with self.assertRaises(m.BundleError): m.validate(value)
        value = data(); value["sources"][0].update(read_status="partial", limitations=[])
        with self.assertRaises(m.BundleError): m.validate(value)

    def test_prefilled_answers_and_invalid_advice_refs_are_rejected(self):
        value = data(); value["questions"][0]["answer"] = "invented answer"
        with self.assertRaises(m.BundleError): m.validate(value)
        value = data(); value["advice"][0]["fact_ids"] = ["fictional-missing"]
        with self.assertRaises(m.BundleError): m.validate(value)

    def test_create_only_permissions_conflicts_and_protected_destinations(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve(); dest = base / "session"
            m.export(data(), dest)
            self.assertEqual(stat.S_IMODE(dest.stat().st_mode), 0o700)
            before = {p.name: p.read_bytes() for p in dest.iterdir()}
            for p in dest.iterdir(): self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
            with self.assertRaises(m.BundleError): m.export(data(), dest)
            self.assertEqual(before, {p.name: p.read_bytes() for p in dest.iterdir()})
            installed = base / "installed-skill"; installed.mkdir(); (installed / "SKILL.md").write_text("Synthetic Skill")
            with self.assertRaises(m.BundleError): m.export(data(), installed / "forbidden")
            with self.assertRaises(m.BundleError): m.export(data(), ROOT / "forbidden-session")
            link = base / "linked"; link.symlink_to(dest, target_is_directory=True)
            with self.assertRaises(m.BundleError): m.export(data(), link / "forbidden")

    def test_source_payload_file_and_expected_digest_tamper_stop_read(self):
        for kind in ("snapshot", "payload", "file", "expected"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                dest = Path(tmp).resolve() / "session"; m.export(data(), dest)
                envelope = json.loads((dest / "session.json").read_text())
                if kind == "snapshot":
                    envelope["payload"]["sources"][0]["snapshot"] += " changed"
                    envelope["bundle_digest"] = m.digest(m.encode(envelope["payload"]))
                    (dest / "session.json").write_bytes(m.encode(envelope))
                if kind == "payload":
                    envelope["payload"]["facts"][0]["text"] += " changed"
                    (dest / "session.json").write_bytes(m.encode(envelope))
                if kind == "file": (dest / "result.md").write_text("Synthetic manual edit")
                with self.assertRaises(m.BundleError): m.read_bundle(dest, "wrong" if kind == "expected" else None)
                if kind == "file": self.assertEqual((dest / "result.md").read_text(), "Synthetic manual edit")

    def test_html_script_termination_and_markup_are_inert_json(self):
        attack = '</script><script>globalThis.syntheticAttack=1</script><img src=x onerror=alert(1)>&\u2028\u2029'
        value = data(); value["title"] = attack; value["sources"][0]["snapshot"] = attack
        html = m.render_html(m.seal(value), "synthetic-version")
        class Scripts(HTMLParser):
            def __init__(self): super().__init__(); self.scripts = []; self.active = False
            def handle_starttag(self, tag, attrs):
                if tag == "script": self.scripts.append(""); self.active = True
            def handle_endtag(self, tag):
                if tag == "script": self.active = False
            def handle_data(self, text):
                if self.active: self.scripts[-1] += text
        parser = Scripts(); parser.feed(html)
        self.assertEqual(len(parser.scripts), 2)
        self.assertEqual(json.loads(parser.scripts[0])["payload"]["title"], attack)
        encoded_hash = base64.b64encode(hashlib.sha256(parser.scripts[1].encode()).digest()).decode()
        self.assertIn("script-src 'sha256-" + encoded_hash + "'", html)
        self.assertNotIn("<img", html)
        self.assertIn("connect-src 'none'", html)

    def test_response_bindings_choices_free_input_and_unanswered(self):
        bundle = {"bundle_digest": "synthetic-version", "payload": m.seal(data())}
        pending = m.validate_responses(bundle, response(bundle))
        self.assertIsNone(pending["responses"][0]["answer"])
        self.assertEqual(pending["responses"][0]["action"], "unanswered")
        for answer in ({"option_id": "experience", "text": None}, {"option_id": None, "text": "Synthetic free reply"}):
            read = m.validate_responses(bundle, response(bundle, "answer", answer))
            self.assertEqual(read["responses"][0]["answer"], answer)
            self.assertTrue(read["needs_agent_review"]); self.assertFalse(read["knowledge_base_updated"])
        wrong = response(bundle); wrong["bundle_digest"] = "other"
        with self.assertRaises(m.BundleError): m.validate_responses(bundle, wrong)
        with self.assertRaises(m.BundleError): m.validate_responses(bundle, response(bundle, "answer", {"option_id": "invented", "text": None}))
        missing = response(bundle); missing["responses"] = []
        with self.assertRaises(m.BundleError): m.validate_responses(bundle, missing)

    def test_no_save_excludes_text_without_mutating_supplied_input(self):
        bundle = {"bundle_digest": "synthetic-version", "payload": m.seal(data())}
        secret = "SYNTHETIC-EXCLUDED-REPLY"
        value = response(bundle, "do_not_save", {"option_id": None, "text": secret})
        normalized = m.validate_responses(bundle, value)
        self.assertNotIn(secret, json.dumps(normalized))
        self.assertIn(secret, json.dumps(value))  # original input is never rewritten
        self.assertIsNone(normalized["responses"][0]["answer"])

    def test_offline_ui_one_question_choice_free_no_save_and_download(self):
        node = os.environ.get("SESSION_BUNDLE_NODE") or shutil.which("node")
        if not node: self.skipTest("Node unavailable; optional DOM execution check not run")
        value = data()
        value["questions"] = [dict(copy.deepcopy(value["questions"][0]), id="fictional-q" + str(i)) for i in range(4)]
        html = m.render_html(m.seal(value), "synthetic-version")
        harness = r'''
const fs=require("fs"),vm=require("vm"),assert=require("assert");
const html=fs.readFileSync(process.argv[1],"utf8");
const payload=html.split('<script type="application/json" id="session-data">')[1].split('</script>')[0];
const code=html.split('<script id="app-code">')[1].split('</script>')[0];
class Element {
  constructor(tag){this.tag=tag;this.children=[];this.events={};this._text="";this.value="";this.checked=false;}
  set textContent(value){this._text=String(value);this.children=[];}
  get textContent(){return this._text+this.children.map(x=>x.textContent).join("");}
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(){this.children=[];this._text="";}
  setAttribute(){} addEventListener(name,fn){this.events[name]=fn;}
  click(){if(this.events.click)this.events.click();} remove(){}
}
const ids={}; for(const id of ["session-data","title","stamp","scope","coverage","sources","views","question","progress","error","download","download-status"])ids[id]=new Element("div");
ids["session-data"].textContent=payload;
const document={getElementById:id=>ids[id],createElement:tag=>new Element(tag),body:new Element("body")};
let downloaded=null;
vm.runInNewContext(code,{document,Blob:class {constructor(parts){this.parts=parts;}},URL:{createObjectURL:blob=>{downloaded=blob.parts.join("");return "blob:synthetic";},revokeObjectURL:()=>{}}});
const all=node=>[node,...node.children.flatMap(all)];
const active=()=>all(ids.question);
const button=text=>active().find(n=>n.tag==="button"&&n.textContent===text);
const inputs=()=>active().filter(n=>n.tag==="input");
assert.equal(ids.views.children.length,5); assert.equal(inputs().length,3); assert(inputs().every(n=>!n.checked));
button("确认本题回答").click(); assert(ids.error.textContent); assert(ids.progress.textContent.includes("1 / 4"));
inputs()[0].events.change(); button("确认本题回答").click(); assert(ids.progress.textContent.includes("2 / 4"));
inputs().at(-1).events.change(); active().find(n=>n.tag==="textarea").value="Synthetic free answer"; button("确认本题回答").click();
inputs().at(-1).events.change(); active().find(n=>n.tag==="textarea").value="SYNTHETIC-DO-NOT-SAVE"; button("这项不保存").click();
assert(ids.progress.textContent.includes("4 / 4")); ids.download.click();
assert(!downloaded.includes("SYNTHETIC-DO-NOT-SAVE"));
const replies=JSON.parse(downloaded); assert.equal(replies.responses[0].answer.option_id,"experience");
assert.equal(replies.responses[1].answer.text,"Synthetic free answer");
assert.deepEqual(replies.responses[2],{question_id:"fictional-q2",action:"do_not_save",answer:null});
assert.deepEqual(replies.responses[3],{question_id:"fictional-q3",action:"unanswered",answer:null});
console.log(downloaded);
'''
        with tempfile.TemporaryDirectory() as tmp:
            page = Path(tmp).resolve() / "session.html"; page.write_text(html)
            result = subprocess.run([node, "-e", harness, str(page)], check=True, capture_output=True, text=True)
            replies = m.validate_responses({"bundle_digest": "synthetic-version", "payload": m.seal(value)}, json.loads(result.stdout))
            self.assertEqual([r["action"] for r in replies["responses"]], ["answer", "answer", "do_not_save", "unanswered"])


if __name__ == "__main__":
    unittest.main()
