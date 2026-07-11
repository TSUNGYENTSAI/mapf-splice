from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from mapf_splice.inspect import create_case_server
from mapf_splice.lifelong import LifelongRunConfig, run_lifelong_validation


ROOT = Path(__file__).parents[1]
CASES = ROOT / "validation/lifelong"


def _missing_dependency(reason: str) -> None:
    """Skip locally, but fail in CI so the browser contract is never silently unverified."""
    if os.environ.get("CI"):
        pytest.fail(reason, pytrace=False)
    pytest.skip(reason)


def _node_environment() -> tuple[str, dict[str, str]]:
    node = shutil.which("node")
    if node is None:
        bundled = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node"
        if bundled.exists():
            node = str(bundled)
    if node is None:
        _missing_dependency("Node.js is required for browser story tests")
    env = os.environ.copy()
    candidates = [
        ROOT / "node_modules",
        Path.home()
        / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules",
    ]
    module_root = next((path for path in candidates if (path / "playwright").exists()), None)
    if module_root is None:
        _missing_dependency("Playwright is required for browser story tests")
    env["NODE_PATH"] = str(module_root)
    return node, env


def test_all_stories_execute_in_a_real_browser_without_console_errors() -> None:
    node, env = _node_environment()
    config_names = [
        "three-robot-k3.json",
        "four-robot-nonparticipant.json",
        "three-robot-delayed.json",
        "random-k3-two-recoveries-seed615.json",
    ]
    replays = {
        name.removesuffix(".json"): run_lifelong_validation(
            LifelongRunConfig.from_json(CASES / name)
        ).replay
        for name in config_names
    }
    server = create_case_server(replays)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        script = r"""
const {chromium}=require('playwright');
(async()=>{
  const browser=await chromium.launch({channel:'chrome',headless:true});
  const page=await browser.newPage({viewport:{width:1440,height:810}});
  const errors=[];
  page.on('console',message=>{if(message.type()==='error')errors.push(message.text());});
  page.on('pageerror',error=>errors.push(error.message));
  await page.goto(process.argv[1]);
  await page.waitForFunction(()=>window.__MAPF_SPLICE_STORY__?.sequence.length>0);
  const summaries={};
  for(const id of ['A','B','C','D','E','F']){
    await page.selectOption('#storySelect',id);
    await page.waitForFunction(expected=>window.__MAPF_SPLICE_STORY__?.storyId===expected&&window.__MAPF_SPLICE_STORY__.sequence.length>0,id);
    const summary=await page.evaluate(()=>{const api=window.__MAPF_SPLICE_STORY__,sequence=api.sequence;return {count:sequence.length,first:[sequence[0].kind,sequence[0].sourceFrames[0].tick,sequence[0].sourceFrames[0].checkpoint],terminal:[sequence.at(-1).kind,sequence.at(-1).sourceFrames.at(-1).tick,sequence.at(-1).sourceFrames.at(-1).checkpoint],gaps:sequence.filter(item=>item.kind==='montage-gap').length};});
    summaries[id]=summary;
    await page.click('#nextStage');
    await page.click('#previousStage');
    await page.click('#play');
    await page.waitForTimeout(30);
    await page.click('#play');
    if(id==='F'){
      const gap=await page.evaluate(()=>window.__MAPF_SPLICE_STORY__.sequence.findIndex(item=>item.kind==='montage-gap'));
      await page.locator('#storySlider').evaluate((slider,value)=>{slider.value=value;slider.dispatchEvent(new Event('input',{bubbles:true}));},String(gap));
      if(!((await page.textContent('#checkpointLabel'))||'').includes('skipped ticks'))throw new Error('Story F gap label is not visible');
    }
    if(id==='A'&&!await page.locator('[data-action]').count()){
      const actionCursor=await page.evaluate(()=>window.__MAPF_SPLICE_STORY__.sequence.findIndex(item=>item.presentation.step==='actions'));
      await page.locator('#storySlider').evaluate((slider,value)=>{slider.value=value;slider.dispatchEvent(new Event('input',{bubbles:true}));},String(actionCursor));
      if(await page.locator('[data-action]').count()!==33)throw new Error('Story A did not render all actions');
    }
    if(id==='D'){
      const publication=await page.evaluate(()=>window.__MAPF_SPLICE_STORY__.sequence.findIndex(item=>item.presentation.step==='publication'));
      await page.locator('#storySlider').evaluate((slider,value)=>{slider.value=value;slider.dispatchEvent(new Event('input',{bubbles:true}));},String(publication));
      if(await page.locator('.splice-visual[data-publication="true"]').count()!==1)throw new Error('Story D publication is not atomic');
      if((await page.textContent('.transaction-gate')).includes('✓'))throw new Error('Story D fabricated validation checkmarks');
    }
    await page.locator('#storySlider').evaluate(slider=>{slider.value=slider.max;slider.dispatchEvent(new Event('input',{bubbles:true}));});
    if((await page.textContent('#play'))!=='Restart')throw new Error(`Story ${id} terminal control is not Restart`);
    await page.click('#play');
    const restarted=await page.evaluate(()=>window.__MAPF_SPLICE_STORY__.state);
    if(restarted.frameCursor!==0||restarted.status!=='idle')throw new Error(`Story ${id} explicit restart failed`);
  }
  const eOrder=await page.evaluate(async()=>{await window.__MAPF_SPLICE_STORY__.activateStory('E');const sequence=window.__MAPF_SPLICE_STORY__.sequence;return [sequence.findIndex(item=>item.sourceFrames[0].tick===36&&item.sourceFrames[0].checkpoint==='after-completions'),sequence.findIndex(item=>item.sourceFrames[0].tick===36&&item.sourceFrames[0].checkpoint==='after-action-start')];});
  const cOrder=await page.evaluate(async()=>{await window.__MAPF_SPLICE_STORY__.activateStory('C');const sequence=window.__MAPF_SPLICE_STORY__.sequence;return [sequence.findIndex(item=>item.presentation.stageId==='stable-cyclic-risk'),sequence.findIndex(item=>item.presentation.stageId==='containment-emphasis'),sequence.findIndex(item=>item.presentation.stageId==='quiescence'),sequence.findIndex(item=>item.presentation.stageId==='confirmed-cycle')];});
  await page.evaluate(()=>window.__MAPF_SPLICE_STORY__.activateStory('B'));
  await page.waitForFunction(()=>window.__MAPF_SPLICE_STORY__?.storyId==='B'&&window.__MAPF_SPLICE_STORY__.sequence.length>0);
  const bCursors=await page.evaluate(()=>{const sequence=window.__MAPF_SPLICE_STORY__.sequence,idOf=value=>value&&value.robot_id?value.robot_id:value,has=index=>(sequence[index].sourceFrames[0].recovery?.active_nonparticipants||[]).map(idOf).includes('R4');return {present:sequence.findIndex((item,index)=>item.kind==='runtime'&&has(index)),absent:sequence.findIndex((item,index)=>item.kind==='runtime'&&!has(index))};});
  const seekText=async cursor=>{await page.locator('#storySlider').evaluate((slider,value)=>{slider.value=value;slider.dispatchEvent(new Event('input',{bubbles:true}));},String(cursor));return (await page.textContent('#logicalVisual'))||'';};
  const bLabel={cursors:bCursors,absent:(await seekText(bCursors.absent)).includes('active non-participant'),present:(await seekText(bCursors.present)).includes('active non-participant')};
  await browser.close();
  console.log(JSON.stringify({summaries,errors,eOrder,cOrder,bLabel}));
})().catch(error=>{console.error(error);process.exit(1);});
"""
        result = subprocess.run(
            [node, "-e", script, base],
            cwd=ROOT,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(result.stdout)
        assert payload["errors"] == []
        assert payload["summaries"]["A"]["first"] == [
            "explain",
            18,
            "after-confirmation",
        ]
        assert payload["summaries"]["F"]["gaps"] == 5
        assert payload["summaries"]["F"]["terminal"] == [
            "runtime",
            147,
            "after-task-advance",
        ]
        assert 0 <= payload["eOrder"][0] < payload["eOrder"][1]
        assert payload["cOrder"] == sorted(payload["cOrder"])
        # R4's "active non-participant" label is provenance-gated: absent in the
        # pre-recovery lead-in, present only once the frame records it.
        assert payload["bLabel"]["cursors"]["absent"] >= 0
        assert payload["bLabel"]["cursors"]["present"] >= 0
        assert payload["bLabel"]["absent"] is False
        assert payload["bLabel"]["present"] is True
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
