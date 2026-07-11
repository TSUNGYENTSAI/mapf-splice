from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from mapf_splice.lifelong import LifelongRunConfig, run_lifelong_validation


ROOT = Path(__file__).parents[1]
PLAYBACK = ROOT / "src/mapf_splice/web_inspector/playback.js"
CASES = ROOT / "validation/lifelong"


def _missing_dependency(reason: str) -> None:
    """Skip locally, but fail in CI so the JS contract is never silently unverified."""
    if os.environ.get("CI"):
        pytest.fail(reason, pytrace=False)
    pytest.skip(reason)


def _run_node(script: str) -> dict:
    node = shutil.which("node")
    if node is None:
        _missing_dependency("Node.js is required for Web playback tests")
    result = subprocess.run(
        [node, "--input-type=module", "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_story_sequence_exact_resolution_tick_coverage_and_same_frame_explain() -> None:
    result = _run_node(
        f"""
        import {{buildStorySequence}} from {json.dumps(PLAYBACK.as_uri())};
        const checkpoints=['after-completions','after-action-start','after-preview','after-confirmation'];
        const frames=[];
        for(let tick=14;tick<=18;tick++){{
          for(const checkpoint of checkpoints){{
            frames.push({{index:frames.length,tick,checkpoint,robots:[],plans:[],events:[],preview:{{dependencies:[],cyclic_sccs:[]}},deadlock:{{newly_stable:[],containment:null}},confirmed_wait_for:null}});
          }}
        }}
        frames.find(frame=>frame.tick===16&&frame.checkpoint==='after-preview').events=[{{kind:'stable-scc-detected'}},{{kind:'containment-started'}}];
        frames.find(frame=>frame.tick===18&&frame.checkpoint==='after-preview').events=[{{kind:'quiescence-reached'}}];
        frames.find(frame=>frame.tick===18&&frame.checkpoint==='after-confirmation').events=[{{kind:'confirmed-wait-for-built'}}];
        const replay={{checkpoint_names:checkpoints,frames}};
        const story={{terminalFrame:{{tick:18,checkpoint:'after-confirmation'}},segments:[{{
          id:'c',interval:{{from:{{tick:14,checkpoint:'after-action-start'}},to:{{tick:18,checkpoint:'after-confirmation'}}}},canonicalCheckpoint:'after-action-start',
          selection:{{visibleFields:['preview.dependencies','deadlock.containment','confirmed_wait_for'],eventKinds:['stable-scc-detected','containment-started','quiescence-reached','confirmed-wait-for-built'],mandatoryCheckpoints:checkpoints}},
          stages:[
            {{id:'future',entry:{{tick:14,checkpoint:'after-action-start'}},through:{{tick:15,checkpoint:'after-confirmation'}},logicalView:'prospective'}},
            {{id:'stable',entry:{{tick:16,checkpoint:'after-completions'}},through:{{tick:16,checkpoint:'after-confirmation'}},logicalView:'prospective'}},
            {{id:'contained',kind:'explain',entry:{{tick:16,checkpoint:'after-preview'}},through:{{tick:16,checkpoint:'after-preview'}},logicalView:'prospective'}},
            {{id:'drain',entry:{{tick:17,checkpoint:'after-completions'}},through:{{tick:17,checkpoint:'after-confirmation'}},logicalView:'prospective'}},
            {{id:'quiet',entry:{{tick:18,checkpoint:'after-completions'}},through:{{tick:18,checkpoint:'after-preview'}},logicalView:'prospective'}},
            {{id:'confirmed',entry:{{tick:18,checkpoint:'after-confirmation'}},through:{{tick:18,checkpoint:'after-confirmation'}},logicalView:'confirmed'}}
          ]
        }}]}};
        const sequence=buildStorySequence(replay,story);
        let invalid='';
        try{{buildStorySequence(replay,{{...story,terminalFrame:{{tick:18,checkpoint:'after-containment'}}}});}}catch(error){{invalid=error.message;}}
        console.log(JSON.stringify({{
          ticks:[...new Set(sequence.filter(item=>item.kind==='runtime').map(item=>item.sourceFrames[0].tick))],
          terminal:sequence.at(-1).sourceFrames[0].checkpoint,
          explain:sequence.filter(item=>item.kind==='explain').map(item=>item.presentation.stageId),
          monotonic:sequence.filter(item=>item.replayIndex!==null).every((item,index,items)=>index===0||item.replayIndex>=items[index-1].replayIndex),
          invalid
        }}));
        """
    )
    assert result == {
        "ticks": [14, 15, 16, 17, 18],
        "terminal": "after-confirmation",
        "explain": ["contained"],
        "monotonic": True,
        "invalid": "Invalid story playback: terminal frame uses unknown checkpoint after-containment",
    }


def test_playback_controller_pause_resume_completion_and_explicit_restart() -> None:
    result = _run_node(
        f"""
        import {{createPlaybackController}} from {json.dumps(PLAYBACK.as_uri())};
        let clock=0,nextId=1;const jobs=new Map(),changes=[];
        const schedule=(callback,delay)=>{{const id=nextId++;jobs.set(id,{{callback,at:clock+delay}});return id;}};
        const cancel=id=>jobs.delete(id);
        const advance=ms=>{{clock+=ms;for(const [id,job] of [...jobs])if(job.at<=clock){{jobs.delete(id);job.callback();}}}};
        const sequence=[100,200,300].map((durationMs,index)=>({{kind:'runtime',replayIndex:index,sourceFrames:[{{replayIndex:index}}],presentation:{{durationMs,stageId:String(index)}}}}));
        const controller=createPlaybackController({{sequence,onChange:state=>changes.push([state.frameCursor,state.status,state.remainingMs]),schedule,cancel,now:()=>clock}});
        controller.play();advance(40);controller.pause();const paused=controller.getState();controller.play();advance(60);const second=controller.getState();advance(200);advance(300);const complete=controller.getState();controller.play();const stillComplete=controller.getState();controller.restart();const restarted=controller.getState();
        console.log(JSON.stringify({{paused,second,complete,stillComplete,restarted,changes}}));
        """
    )
    assert result["paused"]["frameCursor"] == 0
    assert result["paused"]["remainingMs"] == 60
    assert result["second"]["frameCursor"] == 1
    assert result["complete"]["status"] == "complete"
    assert result["complete"]["frameCursor"] == 2
    assert result["stillComplete"]["status"] == "complete"
    assert result["stillComplete"]["frameCursor"] == 2
    assert result["restarted"]["status"] == "idle"
    assert result["restarted"]["frameCursor"] == 0


def test_story_c_manifest_builds_from_production_replay(tmp_path: Path) -> None:
    config = LifelongRunConfig.from_json(CASES / "three-robot-k3.json")
    replay = run_lifelong_validation(config).replay
    replay_path = tmp_path / "three-robot-k3.json"
    replay_path.write_text(json.dumps(replay), encoding="utf-8")
    result = _run_node(
        f"""
        import fs from 'node:fs';
        import {{STORY_C_PLAYBACK,buildStorySequence}} from {json.dumps(PLAYBACK.as_uri())};
        const replay=JSON.parse(fs.readFileSync({json.dumps(str(replay_path))},'utf8'));
        const sequence=buildStorySequence(replay,STORY_C_PLAYBACK);
        const runtime=sequence.filter(item=>item.kind==='runtime');
        console.log(JSON.stringify({{
          ticks:[...new Set(runtime.map(item=>item.sourceFrames[0].tick))],
          terminal:[runtime.at(-1).sourceFrames[0].tick,runtime.at(-1).sourceFrames[0].checkpoint],
          stages:[...new Set(sequence.map(item=>item.presentation.stageId))],
          explain:sequence.filter(item=>item.kind==='explain').map(item=>item.presentation.stageId),
          monotonic:runtime.every((item,index)=>index===0||item.replayIndex>=runtime[index-1].replayIndex)
        }}));
        """
    )
    assert result == {
        "ticks": [14, 15, 16, 17, 18],
        "terminal": [18, "after-confirmation"],
        "stages": [
            "future-dependencies",
            "stable-cyclic-risk",
            "containment-emphasis",
            "authority-drains",
            "quiescence",
            "confirmed-cycle",
        ],
        "explain": ["containment-emphasis"],
        "monotonic": True,
    }


@pytest.fixture(scope="module")
def production_replays(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    directory = tmp_path_factory.mktemp("story-replays")
    names = {
        "A": "four-robot-nonparticipant.json",
        "B": "four-robot-nonparticipant.json",
        "C": "three-robot-k3.json",
        "D": "four-robot-nonparticipant.json",
        "E": "three-robot-delayed.json",
        "F": "random-k3-two-recoveries-seed615.json",
    }
    paths = {}
    generated = {}
    for story_id, name in names.items():
        if name not in generated:
            config = LifelongRunConfig.from_json(CASES / name)
            replay = run_lifelong_validation(config).replay
            path = directory / name
            path.write_text(json.dumps(replay), encoding="utf-8")
            generated[name] = path
        paths[story_id] = generated[name]
    return paths


def _story_result(story_id: str, replay_path: Path, expression: str) -> dict:
    return _run_node(
        f"""
        import fs from 'node:fs';
        import {{STORIES,buildStorySequence}} from {json.dumps(PLAYBACK.as_uri())};
        const replay=JSON.parse(fs.readFileSync({json.dumps(str(replay_path))},'utf8'));
        const sequence=buildStorySequence(replay,STORIES[{json.dumps(story_id)}]);
        const runtime=sequence.filter(item=>item.kind==='runtime');
        const result=({expression});
        console.log(JSON.stringify(result));
        """
    )


def test_all_production_story_manifests_activate_and_end_exactly(
    production_replays: dict[str, Path],
) -> None:
    expected = {
        "A": [19, "after-action-start"],
        "B": [34, "after-recovery-completion"],
        "C": [18, "after-confirmation"],
        "D": [18, "after-recovery-install"],
        "E": [36, "after-action-start"],
        "F": [147, "after-task-advance"],
    }
    for story_id, terminal in expected.items():
        result = _story_result(
            story_id,
            production_replays[story_id],
            "({count:sequence.length,terminal:[sequence.at(-1).sourceFrames.at(-1).tick,sequence.at(-1).sourceFrames.at(-1).checkpoint],kinds:[...new Set(sequence.map(item=>item.kind))],monotonic:sequence.flatMap(item=>item.sourceFrames.map(frame=>frame.replayIndex)).every((value,index,values)=>index===0||value>=values[index-1])})",
        )
        assert result["count"] > 0
        assert result["terminal"] == terminal
        assert result["monotonic"] is True


def test_story_a_compiler_evidence_is_complete(
    production_replays: dict[str, Path],
) -> None:
    result = _story_result(
        "A",
        production_replays["A"],
        "(()=>{const install=sequence.find(item=>item.presentation.step==='actions').sourceFrames[0],participants=new Set(install.recovery.participants),actions=install.plans.filter(plan=>participants.has(plan.robot_id)).flatMap(plan=>plan.actions),deps=actions.flatMap(action=>action.dependencies.map(dependency=>({action,dependency})));return {paths:sequence[0].sourceFrames[0].recovery.paths.map(path=>path.cells),actions:actions.length,same:deps.filter(pair=>pair.action.action_ref.robot_id===pair.dependency.robot_id).length,cross:deps.filter(pair=>pair.action.action_ref.robot_id!==pair.dependency.robot_id).length,claims:actions.every(action=>action.claims.length>0),robots:[...participants]};})()",
    )
    assert len(result["paths"]) == 3
    assert result["actions"] == 33
    assert result["same"] == 30
    assert result["cross"] == 10
    assert result["claims"] is True
    assert "R4" not in result["robots"]


def test_story_b_continuity_execution_and_r4_evidence(
    production_replays: dict[str, Path],
) -> None:
    result = _story_result(
        "B",
        production_replays["B"],
        "(()=>{const ticks=[...new Set(runtime.map(item=>item.sourceFrames[0].tick))],before=replay.frames.find(frame=>frame.tick===18&&frame.checkpoint==='after-confirmation'),after=replay.frames.find(frame=>frame.tick===18&&frame.checkpoint==='after-recovery-install'),r4Completions=runtime.filter(item=>item.sourceFrames[0].tick>=15).flatMap(item=>item.sourceFrames[0].events).filter(event=>event.kind==='action-completed'&&event.robot_id==='R4').map(event=>event.action_ref.label);return {ticks,execution:[...Array(15)].every((_,offset)=>runtime.some(item=>item.sourceFrames[0].tick===19+offset&&(item.sourceFrames[0].robots.some(robot=>robot.active_action_ref)||item.sourceFrames[0].events.some(event=>['action-completed','recovery-admission-evaluated','recovery-prefix-granted'].includes(event.kind))))),r4Completions:[...new Set(r4Completions)],r4Versions:[before.robots.find(robot=>robot.robot_id==='R4').plan_version,after.robots.find(robot=>robot.robot_id==='R4').plan_version],confirmationIndex:runtime.findIndex(item=>item.sourceFrames[0].events.some(event=>event.kind==='confirmed-wait-for-built')),quiescenceIndex:runtime.findIndex(item=>item.sourceFrames[0].events.some(event=>event.kind==='quiescence-reached'))};})()",
    )
    assert result["ticks"] == list(range(12, 35))
    assert result["execution"] is True
    assert len(result["r4Completions"]) == 5
    assert result["r4Versions"] == [2, 2]
    assert 0 <= result["quiescenceIndex"] < result["confirmationIndex"]


def test_story_e_handoff_uses_recorded_action_state(
    production_replays: dict[str, Path],
) -> None:
    result = _story_result(
        "E",
        production_replays["E"],
        "(()=>{const rows=runtime.map(item=>{const frame=item.sourceFrames[0],actions=frame.plans.flatMap(plan=>plan.actions),pred=actions.find(action=>action.action_ref.label==='R2@3:2'),succ=actions.find(action=>action.action_ref.label==='R1@3:2');return [frame.tick,frame.checkpoint,pred.status,succ.status,frame.robots.find(robot=>robot.robot_id==='R2').remaining_ticks,frame.robots.find(robot=>robot.robot_id==='R1').active_action_ref?.label||null];});return {ticks:[...new Set(rows.map(row=>row[0]))],rows};})()",
    )
    assert result["ticks"] == [33, 34, 35, 36]
    completion = result["rows"].index([36, "after-completions", "completed", "planned", 0, None])
    started = next(
        index
        for index, row in enumerate(result["rows"])
        if row[0:2] == [36, "after-action-start"] and row[5] == "R1@3:2"
    )
    assert completion < started
    assert [row[4] for row in result["rows"] if row[0:2] == [33, "after-action-start"]] == [3]


def test_story_f_montage_windows_gaps_and_terminal_state(
    production_replays: dict[str, Path],
) -> None:
    result = _story_result(
        "F",
        production_replays["F"],
        "(()=>{const gaps=sequence.filter(item=>item.kind==='montage-gap').map(item=>item.presentation.gap),windows=STORIES.F.segments.map(segment=>{const items=runtime.filter(item=>item.presentation.stageId===segment.id),ticks=[...new Set(items.map(item=>item.sourceFrames[0].tick))];return [segment.id,ticks];}),t50=runtime.filter(item=>item.sourceFrames[0].tick===50).flatMap(item=>item.sourceFrames[0].events).map(event=>event.kind),terminal=sequence.at(-1).sourceFrames.at(-1);return {gaps,windows,t50:[...new Set(t50)],terminal:{tasks:terminal.tasks.every(task=>task.status==='completed'),running:terminal.robots.some(robot=>robot.active_action_ref),plans:terminal.plans.length,reservations:terminal.reservations.length}};})()",
    )
    assert len(result["gaps"]) == 5
    assert result["gaps"][0] == {"firstSkipped": 11, "lastSkipped": 22, "count": 12}
    for _, ticks in result["windows"]:
        assert ticks == list(range(ticks[0], ticks[-1] + 1))
    assert {"task-assigned", "plan-installed", "action-started"} <= set(result["t50"])
    assert result["terminal"] == {
        "tasks": True,
        "running": False,
        "plans": 0,
        "reservations": 0,
    }
