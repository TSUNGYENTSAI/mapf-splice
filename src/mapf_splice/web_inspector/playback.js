const anchorKey = anchor => `${anchor.tick}\u0000${anchor.checkpoint}`;
const anchor = (tick, checkpoint) => ({tick, checkpoint});

const graphFields = [
  'robots[].position','robots[].active_action_ref','robots[].remaining_ticks','robots[].plan_version',
  'plans[].actions[].display_authority','preview.dependencies','preview.cyclic_sccs',
  'deadlock.newly_stable','deadlock.containment','confirmed_wait_for','recovery.state',
  'recovery.paths','recovery.expected_plan_versions','recovery.installed_plan_versions'
];
const graphEvents = [
  'action-completed','stable-scc-detected','containment-started','quiescence-reached',
  'confirmed-wait-for-built','hard-deadlock-confirmed','recovery-proposal-ready',
  'recovery-install-succeeded','recovery-admission-evaluated','recovery-prefix-granted',
  'action-started','recovery-completed'
];
const graphCheckpoints = [
  'after-completions','after-action-start','after-preview','after-confirmation',
  'after-recovery-install','after-recovery-completion'
];

const runtimeStage = (id, label, entry, through, logicalView, explanation, options = {}) => ({
  id,label,entry,through,logicalView,explanation,overlays:options.overlays || [],holds:options.holds || []
});
const explainStep = (id, label, sources, sourceFields, step, logicalView, explanation, durationMs = 800) => ({
  id,label,sources,sourceFields,step,logicalView,explanation,durationMs
});

export const STORIES = {
  A:{id:'A',title:'Paths Are Not Executable Actions',claim:'Synchronized MAPF paths become independently timed actions with explicit precedence.',caseId:'four-robot-nonparticipant',conclusion:'Synchronized positions → executable actions → explicit precedence.',terminalFrame:anchor(19,'after-action-start'),segments:[{
    id:'compiler-explainer',playbackMode:'explain',interval:{from:anchor(18,'after-confirmation'),to:anchor(19,'after-action-start')},steps:[
      explainStep('synchronized-paths','Synchronized paths',[anchor(18,'after-confirmation')],['recovery.paths'],'paths','compiler','All three solver paths share timestep columns; they are not yet an execution policy.'),
      explainStep('compiled-actions','Compiled actions',[anchor(18,'after-recovery-install')],['plans[].actions[]'],'actions','compiler','Every recorded recovery path step is present as an explicit move or wait action.',700),
      explainStep('same-robot-edges','Same-robot sequencing',[anchor(18,'after-recovery-install')],['plans[].actions[].dependencies'],'same-robot-edges','compiler','Recorded dependencies preserve each robot’s own action order.',700),
      explainStep('cross-robot-edges','Cross-robot precedence',[anchor(18,'after-recovery-install')],['plans[].actions[].dependencies'],'cross-robot-edges','compiler','Recorded cross-robot edges preserve safe synchronized ordering without lockstep motion.',900),
      explainStep('resource-claims','Resource claims',[anchor(18,'after-recovery-install')],['plans[].actions[].claims'],'claims','compiler','Each action carries its recorded vertex and edge claims.',700),
      explainStep('executable-context','Executable context',[anchor(19,'after-admission')],['recovery.admission','plans[].actions[].display_authority'],'admitted-context','compiler','The ordinary admission phase grants replay-recorded recovery authority.',700),
      explainStep('running-context','Running context',[anchor(19,'after-action-start')],['robots[].active_action_ref','robots[].remaining_ticks'],'running-context','compiler','Independently timed actions now run under the compiled precedence.',900)
    ]
  }]},
  B:{id:'B',title:'Local Recovery, Not Global Replanning',claim:'Only the affected robots enter recovery; normal work continues outside the scope.',caseId:'four-robot-nonparticipant',conclusion:'R1–R3 coordinated locally · R4 keeps its plan.',terminalFrame:anchor(34,'after-recovery-completion'),segments:[{
    id:'local-recovery',playbackMode:'continuous',canonicalCheckpoint:'after-action-start',interval:{from:anchor(12,'after-action-start'),to:anchor(34,'after-recovery-completion')},selection:{visibleFields:graphFields,eventKinds:graphEvents,mandatoryCheckpoints:graphCheckpoints},stages:[
      runtimeStage('all-active','All robots active',anchor(12,'after-action-start'),anchor(13,'after-preview'),'prospective-scc','Four robots retain ordinary plan authority.'),
      runtimeStage('prospective','Prospective dependencies',anchor(14,'after-action-start'),anchor(15,'after-preview'),'prospective-scc','Recorded future dependencies form before containment.',{overlays:['prospective-edges']}),
      runtimeStage('stable-contained','Stable SCC & containment',anchor(16,'after-action-start'),anchor(16,'after-preview'),'prospective-scc','The replay contains R1–R3 while R4 remains outside the scope.',{overlays:['prospective-edges','affected-scope'],holds:[{at:anchor(16,'after-preview'),durationMs:900}]}),
      runtimeStage('authority-drain','Committed authority drains',anchor(17,'after-completions'),anchor(18,'after-action-start'),'prospective-scc','Already committed actions finish before confirmation.',{overlays:['prospective-edges','affected-scope']}),
      runtimeStage('quiescence-confirmation','Quiescence & confirmation',anchor(18,'after-preview'),anchor(18,'after-confirmation'),'confirmed-scc','Quiescence precedes the authoritative confirmed wait-for graph.',{overlays:['affected-scope','confirmed-edges'],holds:[{at:anchor(18,'after-preview'),durationMs:700}]}),
      runtimeStage('atomic-splice','Atomic splice',anchor(18,'after-recovery-install'),anchor(18,'after-recovery-install'),'splice','Participant plans publish together; R4 is compared from exact frames.',{holds:[{at:anchor(18,'after-recovery-install'),durationMs:900}]}),
      runtimeStage('recovery-execution','Recovery execution',anchor(19,'after-completions'),anchor(33,'after-action-start'),'execution','Admission, starts, completions, and remaining ticks come from each replay frame.'),
      runtimeStage('recovery-complete','Recovery completes',anchor(34,'after-completions'),anchor(34,'after-recovery-completion'),'execution','The exact recovery-completion event releases the incident.',{holds:[{at:anchor(34,'after-recovery-completion'),durationMs:1000}]})
    ]
  }]},
  C:{id:'C',title:'Detect, Contain, Then Confirm',claim:'A predicted cycle is evidence of risk, not yet an authoritative deadlock.',caseId:'three-robot-k3',conclusion:'Observe → Contain → Drain → Confirm.',terminalFrame:anchor(18,'after-confirmation'),segments:[{
    id:'detect-contain-confirm',playbackMode:'continuous',canonicalCheckpoint:'after-action-start',interval:{from:anchor(14,'after-action-start'),to:anchor(18,'after-confirmation')},selection:{visibleFields:graphFields.slice(0,10),eventKinds:graphEvents.slice(0,7),mandatoryCheckpoints:['after-completions','after-action-start','after-preview']},stages:[
      runtimeStage('future-dependencies','Future dependencies',anchor(14,'after-action-start'),anchor(15,'after-preview'),'prospective-scc','Previewed resource claims produce a prospective dependency graph.',{overlays:['prospective-edges']}),
      runtimeStage('stable-cyclic-risk','Stable cyclic risk',anchor(16,'after-action-start'),anchor(16,'after-preview'),'prospective-scc','Repeated observation makes the prospective SCC stable enough to contain.',{overlays:['prospective-edges','affected-scope'],holds:[{at:anchor(16,'after-preview'),durationMs:900}]}),
      {id:'containment-emphasis',label:'Affected scope contained',kind:'explain',entry:anchor(16,'after-preview'),through:anchor(16,'after-preview'),sourceFields:['deadlock.containment','preview.dependencies'],logicalView:'prospective-scc',overlays:['prospective-edges','affected-scope'],explanation:'The full affected scope is contained before confirmation.',durationMs:800},
      runtimeStage('authority-drains','Existing authority drains',anchor(17,'after-completions'),anchor(17,'after-preview'),'prospective-scc','Already committed motion drains; preview evidence is still not authoritative.',{overlays:['prospective-edges','affected-scope']}),
      runtimeStage('quiescence','Quiescence reached',anchor(18,'after-completions'),anchor(18,'after-preview'),'prospective-scc','Committed authority has drained while the affected scope remains contained.',{overlays:['affected-scope'],holds:[{at:anchor(18,'after-preview'),durationMs:800}]}),
      runtimeStage('confirmed-cycle','Confirmed wait-for cycle',anchor(18,'after-confirmation'),anchor(18,'after-confirmation'),'confirmed-scc','A fresh wait-for graph confirms R1–R2 while R3 remains in scope.',{overlays:['confirmed-edges','affected-scope'],holds:[{at:anchor(18,'after-confirmation'),durationMs:1000}]})
    ]
  }]},
  D:{id:'D',title:'Atomic Plan Splice',claim:'Every affected plan generation changes together—or none of them change.',caseId:'four-robot-nonparticipant',conclusion:'All participant plans change—or none do.',terminalFrame:anchor(18,'after-recovery-install'),segments:[{
    id:'splice-explainer',playbackMode:'explain',interval:{from:anchor(18,'after-confirmation'),to:anchor(18,'after-recovery-install')},steps:[
      explainStep('proposal-ready','Proposal ready',[anchor(18,'after-confirmation')],['recovery.participants','recovery.expected_plan_versions','recovery.incident'],'proposal','splice','The proposal is bound to recorded scope, trigger core, confirmation tick, and expected versions.'),
      explainStep('validation-boundary','Validation boundary',[anchor(18,'after-confirmation')],['recovery.participants','recovery.expected_plan_versions'],'validation','splice','Installation revalidates the group as one gate; these are requirements, not six recorded pass results.'),
      explainStep('atomic-publication','Atomic publication',[anchor(18,'after-confirmation'),anchor(18,'after-recovery-install')],['recovery.expected_plan_versions','recovery.installed_plan_versions','events'],'publication','splice','One recorded install event publishes R1–R3 v3 together while exact comparison keeps R4 at v2.',900)
    ]
  }]},
  E:{id:'E',title:'Asynchronous Recovery Through an ADG',claim:'Explicit causality keeps recovery safe when robot action durations differ.',caseId:'three-robot-delayed',conclusion:'R1 waits for R2 · dependency satisfied · R1 starts.',terminalFrame:anchor(36,'after-action-start'),requiredActions:{predecessor:'R2@3:2',successor:'R1@3:2'},segments:[{
    id:'delayed-handoff',playbackMode:'continuous',canonicalCheckpoint:'after-action-start',interval:{from:anchor(33,'after-action-start'),to:anchor(36,'after-action-start')},selection:{visibleFields:['robots[].position','robots[].active_action_ref','robots[].remaining_ticks','plans[].actions[].status','plans[].actions[].display_authority','plans[].actions[].dependencies','recovery.admission'],eventKinds:['action-completed','recovery-admission-evaluated','recovery-prefix-granted','action-started'],mandatoryCheckpoints:['tick-start','after-completions','after-admission','after-action-start']},stages:[
      runtimeStage('predecessor-starts','Predecessor starts',anchor(33,'after-action-start'),anchor(33,'after-action-start'),'adg','R2@3:2 is running with the replay-recorded remaining ticks.'),
      runtimeStage('delay-progresses','Delay progresses',anchor(34,'after-action-start'),anchor(34,'after-action-start'),'adg','The deterministic delay progresses without changing the dependency.'),
      runtimeStage('successor-waits','Successor waits',anchor(35,'tick-start'),anchor(35,'after-action-start'),'adg','R1@3:2 remains non-running while R2@3:2 is active.'),
      runtimeStage('dependency-completes','Dependency completes',anchor(36,'after-completions'),anchor(36,'after-completions'),'adg','R2 completes before R1 starts in the same phased tick.',{holds:[{at:anchor(36,'after-completions'),durationMs:800}]}),
      runtimeStage('successor-admission','Successor admission',anchor(36,'after-admission'),anchor(36,'after-admission'),'adg','Recorded bounded-prefix admission commits the successor.'),
      runtimeStage('successor-starts','Successor starts',anchor(36,'after-action-start'),anchor(36,'after-action-start'),'adg','R1@3:2 starts after its recorded predecessor completed.',{holds:[{at:anchor(36,'after-action-start'),durationMs:1000}]})
    ]
  }]},
  F:{id:'F',title:'Return to Lifelong Operation',claim:'Recovery is a bounded interruption inside continuous task execution.',caseId:'random-k3-two-recoveries-seed615',conclusion:'Recover → Resume → Redispatch → Recover again → Drain.',terminalFrame:anchor(147,'after-task-advance'),segments:[
    {id:'first-incident',label:'First incident formation',playbackMode:'montage',rate:1,canonicalCheckpoint:'after-action-start',interval:{from:anchor(6,'after-action-start'),to:anchor(10,'after-recovery-install')},selection:null},
    {id:'first-tail',label:'First recovery tail',playbackMode:'montage',rate:1,canonicalCheckpoint:'after-action-start',interval:{from:anchor(23,'after-action-start'),to:anchor(26,'after-recovery-completion')},selection:null},
    {id:'resumed',label:'Resumed operation',playbackMode:'montage',rate:1.5,canonicalCheckpoint:'after-action-start',interval:{from:anchor(27,'tick-start'),to:anchor(32,'after-action-start')},selection:null},
    {id:'useful-work',label:'Later useful work',playbackMode:'montage',rate:1.5,canonicalCheckpoint:'after-action-start',interval:{from:anchor(48,'after-action-start'),to:anchor(52,'after-action-start')},selection:null},
    {id:'second-tail',label:'Second recovery tail',playbackMode:'montage',rate:1,canonicalCheckpoint:'after-action-start',interval:{from:anchor(92,'after-action-start'),to:anchor(95,'after-recovery-completion')},selection:null},
    {id:'clean-drain',label:'Clean drain',playbackMode:'montage',rate:2,canonicalCheckpoint:'after-action-start',interval:{from:anchor(143,'after-action-start'),to:anchor(147,'after-task-advance')},selection:null}
  ].map(segment => ({...segment,selection:{visibleFields:['robots[].position','robots[].active_action_ref','robots[].remaining_ticks','robots[].plan_version','tasks[].status','tasks[].assigned_robot_id','deadlock.containment','confirmed_wait_for','recovery.state','recovery.installed_plan_versions','reservations'],eventKinds:['task-released','task-assigned','task-status-changed','plan-installed','action-completed','action-started','containment-started','confirmed-wait-for-built','recovery-install-succeeded','recovery-completed'],mandatoryCheckpoints:['after-completions','after-release','after-task-advance','after-action-start','after-preview','after-confirmation','after-recovery-install','after-recovery-completion']},stages:[runtimeStage(segment.id,segment.label,segment.interval.from,segment.interval.to,'lifecycle',segment.label)]}))}
};

export const STORY_C_PLAYBACK = STORIES.C;

function fail(message) { throw new Error(`Invalid story playback: ${message}`); }

export function createFrameLookup(replay) {
  if (!Array.isArray(replay?.frames) || replay.frames.length === 0) fail('replay has no frames');
  const checkpointNames = new Set(replay.checkpoint_names || []);
  if (!checkpointNames.size) fail('replay has no checkpoint_names');
  const byAnchor = new Map();
  replay.frames.forEach((frame, replayIndex) => {
    if (!checkpointNames.has(frame.checkpoint)) fail(`frame checkpoint is absent from checkpoint_names: ${frame.checkpoint}`);
    const key = anchorKey(frame);
    if (byAnchor.has(key)) fail(`duplicate frame anchor T${frame.tick} ${frame.checkpoint}`);
    byAnchor.set(key, {...frame, replayIndex});
  });
  return {byAnchor, checkpointNames};
}

export function resolveAnchor(lookup, value, label = 'anchor') {
  if (!lookup.checkpointNames.has(value.checkpoint)) fail(`${label} uses unknown checkpoint ${value.checkpoint}`);
  const frame = lookup.byAnchor.get(anchorKey(value));
  if (!frame) fail(`${label} does not resolve: T${value.tick} ${value.checkpoint}`);
  return frame;
}

function valuesAtPath(value, path) {
  const parts = path.replaceAll('[]', '').split('.');
  let values = [value];
  for (const part of parts) values = values.flatMap(item => Array.isArray(item?.[part]) ? item[part] : [item?.[part]]);
  return values;
}

export function displaySnapshot(frame, visibleFields) {
  return JSON.stringify(visibleFields.map(path => valuesAtPath(frame, path)));
}

function hasSourceField(frame, path) { return valuesAtPath(frame, path).some(value => value !== undefined); }
function relevantEvents(frame, eventKinds) { const allowed = new Set(eventKinds); return (frame.events || []).filter(event => allowed.has(event.kind)); }
function durationFor(frame, canonical, holdMs, rate = 1) { return holdMs || Math.round((frame.checkpoint === canonical ? 350 : 250) / rate); }

function resolveStages(lookup, stages) {
  let lastEntry = -1;
  return stages.map((stage, order) => {
    const entry = resolveAnchor(lookup, stage.entry, `stage ${stage.id} entry`);
    const through = resolveAnchor(lookup, stage.through, `stage ${stage.id} through`);
    if (entry.replayIndex < lastEntry) fail(`stage ${stage.id} moves backward`);
    if (through.replayIndex < entry.replayIndex) fail(`stage ${stage.id} has a reversed range`);
    lastEntry = entry.replayIndex;
    return {...stage, order, entryIndex:entry.replayIndex, throughIndex:through.replayIndex};
  });
}

export function activeStageFor(stages, replayIndex) {
  return stages.find(stage => stage.kind !== 'explain' && stage.entryIndex <= replayIndex && replayIndex <= stage.throughIndex) || null;
}

function presentation(stage, durationMs, segmentIndex, extra = {}) {
  return {durationMs,stageId:stage.id,stageOrder:stage.order ?? 0,segmentIndex,logicalView:stage.logicalView,overlays:stage.overlays || [],caption:stage.caption || '',explanation:stage.explanation || '',...extra};
}

function actionByLabel(frame, label) { return (frame.plans || []).flatMap(plan => plan.actions || []).find(action => action.action_ref?.label === label); }

function validateStorySpecific(story, sequence, terminal, replay) {
  if (story.id === 'E') {
    const frame = sequence[0].sourceFrames[0];
    const predecessor = actionByLabel(frame, story.requiredActions.predecessor);
    const successor = actionByLabel(frame, story.requiredActions.successor);
    if (!predecessor || !successor) fail('Story E required action identities are absent');
    if (!(successor.dependencies || []).some(item => item.label === predecessor.action_ref.label)) fail('Story E required dependency is absent');
  }
  if (story.id === 'A') {
    const install = sequence.find(item => item.presentation.step === 'actions')?.sourceFrames[0];
    const participants = new Set(install?.recovery?.participants || []);
    const actions = (install?.plans || []).filter(plan => participants.has(plan.robot_id)).flatMap(plan => plan.actions || []);
    const dependencies = actions.flatMap(action => (action.dependencies || []).map(dependency => ({action,dependency})));
    if ((install?.recovery?.paths || []).length !== 3 || actions.length !== 33) fail('Story A compiler evidence count mismatch');
    if (dependencies.filter(pair => pair.action.action_ref.robot_id === pair.dependency.robot_id).length !== 30 || dependencies.filter(pair => pair.action.action_ref.robot_id !== pair.dependency.robot_id).length !== 10) fail('Story A dependency evidence count mismatch');
  }
  if (story.id === 'D') {
    const after = sequence.at(-1).sourceFrames.at(-1);
    if (!(after.events || []).some(event => event.kind === 'recovery-install-succeeded')) fail('Story D installation event is absent');
  }
  if (story.id === 'F') {
    if ((terminal.tasks || []).some(task => task.status !== 'completed')) fail('Story F terminal tasks are not drained');
    if ((terminal.robots || []).some(robot => robot.active_action_ref || robot.remaining_ticks)) fail('Story F terminal has a running action');
    if ((terminal.reservations || []).length) fail('Story F terminal has reservations');
    if (!replay.termination_reason || replay.final_tick === undefined) fail('Story F termination metadata is absent');
  }
}

export function buildStorySequence(replay, story) {
  const lookup = createFrameLookup(replay);
  const terminal = resolveAnchor(lookup, story.terminalFrame, 'terminal frame');
  const emitted = [];
  let lastSourceIndex = -1;
  let priorSegmentTerminal = null;

  story.segments.forEach((segment, segmentIndex) => {
    const from = resolveAnchor(lookup, segment.interval.from, `segment ${segment.id} start`);
    const to = resolveAnchor(lookup, segment.interval.to, `segment ${segment.id} end`);
    if (from.replayIndex < lastSourceIndex || to.replayIndex < from.replayIndex) fail(`segment ${segment.id} is not monotonic`);
    if (priorSegmentTerminal) {
      const firstSkipped = priorSegmentTerminal.tick + 1;
      const lastSkipped = from.tick - 1;
      const count = Math.max(0, lastSkipped - firstSkipped + 1);
      emitted.push({kind:'montage-gap',replayIndex:null,sourceFrames:[priorSegmentTerminal,from],presentation:{durationMs:700,stageId:segment.id,stageOrder:0,segmentIndex,logicalView:'lifecycle',overlays:[],caption:count ? `T${firstSkipped}–T${lastSkipped} · ${count} skipped ticks` : 'Presentation cut · no whole ticks skipped',explanation:'Presentation transition between configured continuous windows.',gap:{firstSkipped,lastSkipped,count}}});
    }

    if (segment.playbackMode === 'explain') {
      let previousExplainIndex = -1;
      (segment.steps || []).forEach((step, order) => {
        const sources = step.sources.map((source, index) => resolveAnchor(lookup, source, `explain ${step.id} source ${index + 1}`));
        for (let index = 1; index < sources.length; index += 1) if (sources[index].replayIndex < sources[index - 1].replayIndex) fail(`explain ${step.id} source frames move backward`);
        if (sources[0].replayIndex < lastSourceIndex) fail(`explain ${step.id} moves backward`);
        if (sources[0].replayIndex === previousExplainIndex && order === 0) fail(`explain ${step.id} repeats a source outside one explain segment`);
        step.sourceFields.forEach(path => { if (!sources.some(source => hasSourceField(source, path))) fail(`explain ${step.id} missing source field ${path}`); });
        emitted.push({kind:'explain',replayIndex:null,sourceFrames:sources,presentation:presentation({...step,order},step.durationMs || 800,segmentIndex,{step:step.step,sourceFields:step.sourceFields})});
        previousExplainIndex = sources.at(-1).replayIndex;
        lastSourceIndex = previousExplainIndex;
      });
    } else {
      const stages = resolveStages(lookup, segment.stages || []);
      const selection = segment.selection || {};
      const visibleFields = selection.visibleFields || [];
      const eventKinds = selection.eventKinds || [];
      const mandatory = new Set(selection.mandatoryCheckpoints || []);
      mandatory.forEach(checkpoint => { if (!lookup.checkpointNames.has(checkpoint)) fail(`segment ${segment.id} uses unknown mandatory checkpoint ${checkpoint}`); });
      const canonical = segment.canonicalCheckpoint || 'after-action-start';
      const frames = replay.frames.map((frame, replayIndex) => ({...frame,replayIndex})).filter(frame => from.replayIndex <= frame.replayIndex && frame.replayIndex <= to.replayIndex);
      const byTick = new Map();
      frames.forEach(frame => byTick.set(frame.tick,[...(byTick.get(frame.tick) || []),frame]));
      let previousSnapshot;
      for (let tick = from.tick; tick <= to.tick; tick += 1) {
        const tickFrames = byTick.get(tick) || [];
        if (!tickFrames.length) fail(`segment ${segment.id} omits tick ${tick}`);
        const candidates = [];
        tickFrames.forEach(frame => {
          const stage = activeStageFor(stages, frame.replayIndex);
          const snapshot = displaySnapshot(frame, visibleFields);
          const changed = previousSnapshot === undefined || snapshot !== previousSnapshot;
          previousSnapshot = snapshot;
          if (!stage) return;
          const stageBoundary = frame.replayIndex === stage.entryIndex || frame.replayIndex === stage.throughIndex;
          const selected = frame.replayIndex === from.replayIndex || frame.replayIndex === to.replayIndex || frame.checkpoint === canonical || stageBoundary || relevantEvents(frame,eventKinds).length > 0 || (mandatory.has(frame.checkpoint) && changed);
          if (selected) candidates.push({frame,stage});
        });
        if (!candidates.length) fail(`segment ${segment.id} has no emitted frame for tick ${tick}`);
        candidates.forEach(({frame,stage}) => {
          if (frame.replayIndex < lastSourceIndex) fail(`runtime item moves backward at frame ${frame.replayIndex}`);
          const hold = (stage.holds || []).find(item => anchorKey(item.at) === anchorKey(frame));
          emitted.push({kind:'runtime',replayIndex:frame.replayIndex,sourceFrames:[frame],presentation:presentation(stage,durationFor(frame,canonical,hold?.durationMs,segment.rate || 1),segmentIndex)});
          lastSourceIndex = frame.replayIndex;
          stages.filter(item => item.kind === 'explain' && item.entryIndex === frame.replayIndex).forEach(item => {
            (item.sourceFields || []).forEach(path => { if (!hasSourceField(frame,path)) fail(`explain ${item.id} missing source field ${path}`); });
            emitted.push({kind:'explain',replayIndex:null,sourceFrames:[frame],presentation:presentation(item,item.durationMs || 800,segmentIndex,{step:item.id,sourceFields:item.sourceFields || []})});
          });
        });
      }
    }
    priorSegmentTerminal = to;
  });

  if (!emitted.length) fail('story emits no items');
  const finalSource = emitted.at(-1).sourceFrames.at(-1);
  if (finalSource.replayIndex !== terminal.replayIndex) fail('terminal frame is not the final emitted item');
  validateStorySpecific(story,emitted,terminal,replay);
  return emitted;
}

export function createPlaybackController({sequence,storyId = null,onChange = () => {},schedule = setTimeout,cancel = clearTimeout,now = Date.now}) {
  if (!sequence.length) fail('controller sequence is empty');
  let frameCursor=0,status='idle',timer=null,deadline=null,remainingMs=sequence[0].presentation.durationMs;
  const state = () => ({storyId,segmentIndex:sequence[frameCursor].presentation.segmentIndex,frameCursor,activeStageId:sequence[frameCursor].presentation.stageId,status,elapsedInHoldMs:sequence[frameCursor].presentation.durationMs-remainingMs,item:sequence[frameCursor],remainingMs});
  const notify = () => onChange(state());
  const clear = () => { if (timer !== null) cancel(timer); timer=null; deadline=null; };
  const scheduleCurrent = () => {
    deadline=now()+remainingMs;
    timer=schedule(() => {
      timer=null;
      if (frameCursor === sequence.length-1) { status='complete'; remainingMs=0; deadline=null; notify(); return; }
      frameCursor+=1; remainingMs=sequence[frameCursor].presentation.durationMs; notify(); scheduleCurrent();
    },remainingMs);
  };
  const seek = next => { clear(); frameCursor=Math.max(0,Math.min(sequence.length-1,next)); remainingMs=sequence[frameCursor].presentation.durationMs; status=frameCursor===sequence.length-1?'complete':'paused'; notify(); };
  return {
    getState:state,
    play(){ if(status==='playing'||status==='complete') return; status='playing'; notify(); scheduleCurrent(); },
    pause(){ if(status!=='playing') return; remainingMs=Math.max(0,deadline-now()); clear(); status='paused'; notify(); },
    toggle(){ status==='playing'?this.pause():this.play(); },
    previous(){ seek(frameCursor-1); },next(){ seek(frameCursor+1); },seek,
    restart(){ clear(); frameCursor=0; remainingMs=sequence[0].presentation.durationMs; status='idle'; notify(); },
    destroy(){ clear(); }
  };
}
