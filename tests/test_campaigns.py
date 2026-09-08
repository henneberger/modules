from __future__ import annotations

import json
import sys

import pytest
from test_assemblies import CALL, interface, publish
from test_workers import wheelhouse as wheelhouse

from module_families.campaigns import CampaignError, read_campaign, run_campaign
from module_families.cli import main
from module_families.contributions import prepare_contribution
from module_families.coordination import CoordinationQueue
from module_families.evidence import EvidenceStore
from module_families.registry import Registry


@pytest.fixture
def campaign(tmp_path):
    base, staging = Registry(tmp_path / "base"), Registry(tmp_path / "staging")
    publish(
        tmp_path, base, "interfaces", interfaces=[interface(CALL, callables=["run"])]
    )
    for name, expected in [("identity", 7), ("increment", 8)]:
        (tmp_path / (name + ".toml")).write_text(f'''schema_version=1
[task]
id="{name}"
summary="{name} a value"
requires={{id="test.call",version="1"}}
capabilities=["{name}"]
[policy]
allowed_effects=[]
[[cases]]
id="seven"
export="run"
args=[7]
expected={expected}
''')
    bundle = prepare_contribution(
        tmp_path / "increment.toml", base, tmp_path / "goal-contract"
    )
    (tmp_path / "goal.toml").write_text(f'''schema_version=1
[goal]
name="composed"
requires={{id="test.call",version="1"}}
capabilities=["increment"]
[policy]
allowed_effects=[]
[evidence]
tasks=["{bundle["sha256"]}"]
evaluators=["campaign-test"]
''')
    (tmp_path / "driver.py").write_text("""import pathlib,json
root=pathlib.Path.cwd()
task=json.loads((root/'task.json').read_text())['document']['task']['id']
feedback=json.loads((root/'feedback.json').read_text())
(root/'src/implementation').mkdir(parents=True)
source = 'def run(value): return value\\n' if task=='identity' else "def run(input): return {'run':lambda value: input.run(value)+1}\\n"
(root/'src/implementation/__init__.py').write_text(source)
kind='operation' if task=='identity' else 'functor'
requirements='{}' if task=='identity' else '{input={id="test.call",version="1"}}'
effects='[]' if task=='identity' else '["dependency-effects"]'
manifest=f\'''schema_version=1
[family]
name="{task}"
version="1.0.0"
description="Independent contribution"
[publisher]
name="agent"
[source]
root="src"
package="implementation"
[[members]]
id="run"
version="1.0.{feedback['attempt']}"
kind="{kind}"
symbol="implementation:run"
summary="{task}"
provides={{id="test.call",version="1"}}
requires={requirements}
capabilities=["{task}"]
effects={effects}
\'''
(root/'family.toml').write_text(manifest)
(root/'proposal.json').write_text(json.dumps({'kind':'family','manifest':'family.toml'}))
""")
    (tmp_path / "driver.toml").write_text(
        "[driver]\ncommand="
        + json.dumps([sys.executable, "{campaign}/driver.py"])
        + "\n"
    )
    manifest = tmp_path / "campaign.toml"
    manifest.write_text("""schema_version=1
[campaign]
id="example"
goal="goal.toml"
driver="driver.toml"
[limits]
rounds=3
workers_per_round=2
[[tasks]]
id="increment"
manifest="increment.toml"
prerequisites=["identity"]
[[tasks]]
id="identity"
manifest="identity.toml"
""")
    return tmp_path, base, staging, manifest


def run(campaign, wheelhouse):
    root, base, staging, path = campaign
    return run_campaign(
        path,
        CoordinationQueue(root / "queue.sqlite"),
        base,
        staging,
        work_root=root / "work",
        evidence_store=EvidenceStore(root / "evidence.sqlite"),
        evaluator_id="campaign-test",
        secret=b"x" * 32,
        find_links=[str(wheelhouse)],
        no_index=True,
    )


def test_real_two_contribution_campaign_and_evidence_goal(campaign, wheelhouse):
    result = run(campaign, wheelhouse)
    assert result["status"] == "complete"
    assert result["rounds"] == 2
    assert all(task["state"] == "accepted" for task in result["tasks"])
    lock = json.loads((campaign[0] / "work/program.lock.json").read_text())
    assert len(lock["bindings"]) == 2
    assert lock["evidence"]["observations"]
    # Accepted tasks are not executed again when the campaign resumes.
    replay = run(campaign, wheelhouse)
    assert replay["status"] == "complete"
    assert all(task["attempts"] == 1 for task in replay["tasks"])


def test_campaign_cli(campaign, wheelhouse, monkeypatch, capsys):
    root, base, staging, path = campaign
    monkeypatch.setenv("MF_EVALUATOR_SECRET", "x" * 32)
    assert (
        main(
            [
                "campaign",
                str(path),
                "--queue",
                str(root / "queue.sqlite"),
                "--registry",
                str(base.root),
                "--staging",
                str(staging.root),
                "--work-dir",
                str(root / "work"),
                "--evidence-store",
                str(root / "evidence.sqlite"),
                "--evaluator",
                "campaign-test",
                "--find-links",
                str(wheelhouse),
                "--no-index",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["status"] == "complete"


@pytest.mark.parametrize(
    "edit",
    [
        lambda text: text.replace(
            'prerequisites=["identity"]', 'prerequisites=["missing"]'
        ),
        lambda text: text + 'prerequisites=["increment"]\n',
        lambda text: text.replace("rounds=3", "rounds=0"),
        lambda text: text.replace(
            'id="identity"\nmanifest', 'id="increment"\nmanifest'
        ),
    ],
)
def test_invalid_campaign_contracts(campaign, edit):
    path = campaign[3]
    path.write_text(edit(path.read_text()))
    with pytest.raises(CampaignError):
        read_campaign(path)


def test_campaign_refuses_unrelated_queue(campaign, wheelhouse):
    CoordinationQueue(campaign[0] / "queue.sqlite").enqueue({"unrelated": True})
    with pytest.raises(CampaignError, match="dedicated queue"):
        run(campaign, wheelhouse)


def test_campaign_driver_failure_budget_and_feedback(campaign, wheelhouse):
    root, _, _, path = campaign
    (root / "driver.py").write_text("import sys;sys.exit(1)\n")
    path.write_text(
        path.read_text().replace(
            'manifest="identity.toml"', 'manifest="identity.toml"\nmax_attempts=2'
        )
    )
    result = run(campaign, wheelhouse)
    assert result["status"] == "blocked"
    failed = next(task for task in result["tasks"] if task["id"].endswith(":identity"))
    assert failed["attempts"] == 2
    feedback = [
        json.loads(p.read_text())
        for p in (root / "work/workers").glob("*/feedback.json")
    ]
    assert any(
        item["attempt"] == 2 and "driver failed" in item["previous_error"]
        for item in feedback
    )
    assert not (root / "work/program.lock.json").exists()


def test_campaign_round_limit_and_immutable_resume(campaign, wheelhouse):
    root, _, _, path = campaign
    path.write_text(path.read_text().replace("rounds=3", "rounds=1"))
    assert run(campaign, wheelhouse)["status"] == "round-limit"
    path.write_text(path.read_text().replace("rounds=1", "rounds=2"))
    with pytest.raises(CampaignError, match="immutable"):
        run(campaign, wheelhouse)
