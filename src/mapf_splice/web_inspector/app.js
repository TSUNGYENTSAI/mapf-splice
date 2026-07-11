import {STORIES, buildStorySequence, createPlaybackController} from './playback.js';

const cases = await fetch('/cases.json').then(response => response.json());
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '—').replace(/[&<>"']/g, character => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]));
const cellKey = cell => `${cell.row}:${cell.col}`;
const cellLabel = cell => cell ? `${cell.row},${cell.col}` : '—';
const refLabel = reference => reference?.label ?? '—';
const robotPalette = {R1:'#4388bd',R2:'#e27b3e',R3:'#8a63b8',R4:'#379273'};
const fallbackPalette = ['#4388bd','#e27b3e','#8a63b8','#379273','#c68b2f'];

let run=null,frames=[],allEvents=[],robotIds=[],activeStoryId='B',emittedSequence=[],playbackController=null,evidenceTab='graph',activationError=null;
const currentStory = () => STORIES[activeStoryId];
const playbackState = () => playbackController?.getState();
const currentItem = () => playbackState()?.item;
const currentFrame = () => currentItem()?.kind==='montage-gap' ? currentItem().sourceFrames[0] : currentItem()?.sourceFrames.at(-1);
const currentStage = () => currentStory().segments.flatMap(segment => segment.stages || segment.steps || []).find(stage => stage.id === currentItem()?.presentation.stageId) || {id:'error',label:'Unavailable'};
const robotColor = id => robotPalette[id] || fallbackPalette[Math.max(0,robotIds.indexOf(id)) % fallbackPalette.length];
const idList = items => (items || []).map(item => item.robot_id ?? item);
const exactFrame = (tick,checkpoint) => frames.find(frame => frame.tick===tick&&frame.checkpoint===checkpoint);
const metric = (value,label) => `<div class="metric"><strong>${esc(value)}</strong><small>${esc(label)}</small></div>`;
const sourceHas = name => currentItem()?.presentation.overlays.includes(name);
const actionByLabel = (frame,label) => (frame?.plans || []).flatMap(plan => plan.actions || []).find(action => action.action_ref?.label===label);
const robotById = (frame,id) => frame?.robots.find(robot => robot.robot_id===id);

function scopeFor(frame) {
  if (!frame) return [];
  if (frame.deadlock?.containment?.scope) return idList(frame.deadlock.containment.scope);
  if (frame.recovery?.incident?.scope) return idList(frame.recovery.incident.scope);
  return [];
}
function coreFor(frame) {
  if (!frame) return [];
  if (frame.deadlock?.containment?.trigger_core) return idList(frame.deadlock.containment.trigger_core);
  if (frame.recovery?.incident?.trigger_core) return idList(frame.recovery.incident.trigger_core);
  return [];
}

function mapSvg(frame) {
  const rows=run.map_rows,cols=rows[0].length,size=44,pad=28,w=cols*size+pad*2,h=rows.length*size+pad*2;
  const stations=new Map(run.stations.map(station=>[cellKey(station.cell),station]));
  const scope=new Set(scopeFor(frame)),core=new Set(coreFor(frame)),uid=`${activeStoryId}-${playbackState().frameCursor}`;
  let shapes='';
  rows.forEach((row,r)=>[...row].forEach((symbol,c)=>{const x=pad+c*size,y=pad+r*size,blocked=symbol==='#',station=stations.get(`${r}:${c}`);shapes+=`<rect x="${x}" y="${y}" width="${size-2}" height="${size-2}" rx="5" fill="${blocked?'#e2e5e2':'#fbfbf8'}" stroke="#e0e4e1"/>`;if(blocked)shapes+=`<path d="M${x+10},${y+22}h${size-22}" stroke="#aeb6b2" stroke-width="3"/>`;if(station)shapes+=`<text x="${x+size/2}" y="${y+size/2+4}" text-anchor="middle" fill="${station.kind==='handoff'?'#326c86':'#39755a'}" font-size="9" font-weight="800" font-family="monospace">${station.kind==='handoff'?'P':'D'}</text>`;}));
  const point=cell=>[pad+cell.col*size+size/2,pad+cell.row*size+size/2];
  (frame.plans || []).forEach(plan=>{const actions=plan.actions.filter(action=>['completed','running','committed','preview'].includes(action.display_authority));if(!actions.length)return;let path=`M${point(actions[0].start).join(',')}`;actions.forEach(action=>path+=` L${point(action.end).join(',')}`);const recoveryPlan=(frame.recovery?.installed_plan_versions||[]).some(version=>version.robot_id===plan.robot_id&&version.plan_version===plan.plan_version);shapes+=`<path d="${path}" fill="none" stroke="${recoveryPlan?'#326c86':robotColor(plan.robot_id)}" stroke-width="${recoveryPlan?6:3}" opacity="${recoveryPlan?'.48':'.3'}" stroke-linecap="round"/>`;});
  if(sourceHas('prospective-edges')) (frame.preview?.dependencies || []).forEach(dependency=>{const waiting=frame.robots.find(robot=>robot.robot_id===dependency.waiting_robot_id),target=dependency.resource?.cell||dependency.resource?.second;if(waiting&&target){const [x1,y1]=point(waiting.position),[x2,y2]=point(target);shapes+=`<path d="M${x1},${y1}L${x2},${y2}" stroke="#8ba0aa" stroke-width="2" stroke-dasharray="6 5" marker-end="url(#map-arrow-${uid})"/>`;}});
  frame.robots.forEach(robot=>{const [x,y]=point(robot.position),inScope=sourceHas('affected-scope')&&scope.has(robot.robot_id),inCore=inScope&&core.has(robot.robot_id);if(inScope)shapes+=`<circle cx="${x}" cy="${y}" r="27" fill="rgba(216,149,50,.14)" stroke="#d89532" stroke-width="${inCore?4:2}" stroke-dasharray="${inCore?'none':'5 4'}"/>`;if(robot.active_action_ref)shapes+=`<circle cx="${x}" cy="${y}" r="21" fill="none" stroke="${robotColor(robot.robot_id)}" stroke-width="3" stroke-dasharray="16 8"/>`;shapes+=`<circle cx="${x}" cy="${y}" r="15" fill="${robotColor(robot.robot_id)}" stroke="#fff" stroke-width="2"/><text x="${x}" y="${y+4}" text-anchor="middle" fill="#fff" font-size="9" font-weight="800" font-family="monospace">${esc(robot.robot_id)}</text>`;});
  return `<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="Fleet at T${frame.tick} ${esc(frame.checkpoint)}"><defs><marker id="map-arrow-${uid}" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto"><path d="M0 0L7 3.5L0 7z" fill="#8ba0aa"/></marker></defs>${shapes}</svg>`;
}

const graphPositions={R1:[180,92],R2:[292,190],R3:[72,190],R4:[332,55]};
function dependencyGraph(frame) {
  const confirmed=currentItem().presentation.logicalView==='confirmed-scc'&&Boolean(frame.confirmed_wait_for),scope=scopeFor(frame),core=coreFor(frame),confirmedCore=new Set((frame.confirmed_wait_for?.cyclic_sccs||[]).flat()),uid=`graph-${activeStoryId}-${playbackState().frameCursor}`;
  const graphIds=activeStoryId==='B'?robotIds:robotIds.filter(id=>id!=='R4');
  const edges=confirmed?(frame.confirmed_wait_for?.edges||[]):(frame.preview?.dependencies||[]);
  let svg=`<defs><marker id="head-${uid}" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto"><path d="M0 0L7 3.5L0 7z" fill="${confirmed?'#344d5b':'#718a97'}"/></marker></defs>`;
  if(sourceHas('affected-scope')&&scope.length)svg+=`<path d="M35,55 Q35,35 55,35 H292 Q312,35 312,55 V220 Q312,240 292,240 H55 Q35,240 35,220 Z" fill="rgba(216,149,50,.10)" stroke="#d89532" stroke-width="2" stroke-dasharray="7 5"/><text x="48" y="56" fill="#a66b1f" font-size="10" font-weight="800">AFFECTED SCOPE</text>`;
  edges.forEach(edge=>{const a=graphPositions[edge.waiting_robot_id],b=graphPositions[edge.blocking_robot_id];if(a&&b)svg+=`<path d="M${a[0]},${a[1]} L${b[0]},${b[1]}" fill="none" stroke="${confirmed?'#344d5b':'#718a97'}" stroke-width="${confirmed?3:2}" stroke-dasharray="${confirmed?'none':'7 5'}" marker-end="url(#head-${uid})"/>`;});
  graphIds.forEach(id=>{const [x,y]=graphPositions[id],cycle=confirmed?confirmedCore.has(id):core.includes(id),affected=scope.includes(id)&&!cycle;if(affected)svg+=`<circle cx="${x}" cy="${y}" r="27" fill="none" stroke="#d89532" stroke-width="3"/>`;svg+=`<circle cx="${x}" cy="${y}" r="20" fill="#fff" stroke="${cycle?'#344d5b':robotColor(id)}" stroke-width="${cycle?5:3}"/><circle cx="${x}" cy="${y}" r="12" fill="${robotColor(id)}"/><text x="${x}" y="${y+4}" text-anchor="middle" fill="#fff" font-size="9" font-weight="800">${esc(id)}</text>`;if(id==='R4'&&idList(frame.recovery?.active_nonparticipants||[]).includes('R4'))svg+=`<text x="${x}" y="${y+33}" text-anchor="middle" fill="#68747c" font-size="9">active non-participant</text>`;});
  return `<svg viewBox="0 0 380 270" role="img" aria-label="${confirmed?'Confirmed wait-for graph':'Prospective dependency graph'}">${svg}</svg>`;
}

function compilerVisual(frame) {
  const step=currentItem().presentation.step,recovery=frame.recovery || {},participants=new Set(recovery.participants || []),plans=(frame.plans || []).filter(plan=>participants.has(plan.robot_id));
  if(step==='paths') {const paths=recovery.paths || [];const width=Math.max(1,...paths.map(path=>path.cells.length));return `<div class="compiler-visual paths" data-path-count="${paths.length}"><div class="path-times">${Array.from({length:width},(_,i)=>`<i>t${i}</i>`).join('')}</div>${paths.map(path=>`<div class="path-row" data-robot="${esc(path.robot_id)}"><b style="color:${robotColor(path.robot_id)}">${esc(path.robot_id)}</b>${path.cells.map(cell=>`<span>${esc(cellLabel(cell))}</span>`).join('')}</div>`).join('')}</div>`;}
  const actions=plans.flatMap(plan=>plan.actions.map(action=>({...action,robot_id:plan.robot_id})));
  const pairs=actions.flatMap(action=>(action.dependencies||[]).map(dependency=>({action,dependency,same:action.action_ref.robot_id===dependency.robot_id})));
  const showSame=step==='same-robot-edges',showCross=step==='cross-robot-edges',showClaims=step==='claims';
  const edgeList=showSame?pairs.filter(pair=>pair.same):showCross?pairs.filter(pair=>!pair.same):[];
  if(showSame||showCross)return `<div class="compiler-visual edge-list" data-edge-count="${edgeList.length}"><header>${edgeList.length} recorded ${showSame?'same-robot':'cross-robot'} dependencies</header>${edgeList.map(pair=>`<div data-dependency="${esc(refLabel(pair.dependency))}"><code>${esc(refLabel(pair.dependency))}</code><i>→</i><code>${esc(refLabel(pair.action.action_ref))}</code></div>`).join('')}</div>`;
  return `<div class="compiler-visual action-lanes" data-action-count="${actions.length}">${plans.map(plan=>`<section data-robot="${esc(plan.robot_id)}"><header style="color:${robotColor(plan.robot_id)}">${esc(plan.robot_id)} · ${plan.actions.length} actions</header>${plan.actions.map(action=>`<article data-action="${esc(refLabel(action.action_ref))}"><b>${esc(refLabel(action.action_ref))}</b><span>${esc(action.kind)} · ${cellLabel(action.start)} → ${cellLabel(action.end)}</span>${showClaims?`<small>${esc((action.claims||[]).map(claim=>JSON.stringify(claim)).join(' · ')||'no claims')}</small>`:''}</article>`).join('')}</section>`).join('')}</div>`;
}

function comparisonFrames() {
  if(activeStoryId==='D'&&currentItem().sourceFrames.length>1)return [currentItem().sourceFrames[0],currentItem().sourceFrames.at(-1)];
  return [exactFrame(18,'after-confirmation'),exactFrame(18,'after-recovery-install')];
}
function spliceVisual() {
  const [before,after]=comparisonFrames(),publication=currentItem().presentation.step==='publication'||currentItem().presentation.stageId==='atomic-splice',displayFrame=publication?after:before;
  const beforeVersions=Object.fromEntries(before.robots.map(robot=>[robot.robot_id,robot.plan_version])),afterVersions=Object.fromEntries(after.robots.map(robot=>[robot.robot_id,robot.plan_version]));
  const participants=new Set(before.recovery?.participants || []);
  const rows=before.robots.map(robot=>{const id=robot.robot_id,changed=beforeVersions[id]!==afterVersions[id];return `<div class="splice-row" data-robot="${esc(id)}"><div style="--robot:${robotColor(id)}"><b>${esc(id)}</b><span>Plan v${beforeVersions[id]}</span></div><i class="${publication&&changed?'published':''}">→</i><div class="${publication&&changed?'replacement':'unchanged'}"><b>${publication?`${esc(id)} · Plan v${displayFrame===after?afterVersions[id]:beforeVersions[id]}`:'awaiting publication'}</b><span>${participants.has(id)?'transaction participant':'outside participant set'}</span></div></div>`;}).join('');
  const requirements=['incident identity','plan versions','positions and tasks','quiescence','reservation state','compiled ADG'];
  const installEvent=(after.events||[]).find(event=>event.kind==='recovery-install-succeeded');
  return `<div class="splice-visual" data-publication="${publication}"><div class="splice-labels"><span>Exact before</span><span>${publication?'Exact after':'Group gate'}</span></div>${rows}<div class="transaction-gate ${publication?'passed':'active'}">${requirements.map(item=>`<span>○ ${esc(item)}</span>`).join('')}</div>${publication&&installEvent?`<p class="install-event">Recorded event #${esc(installEvent.sequence)} · recovery-install-succeeded</p>`:''}</div>`;
}

function adgVisual(frame) {
  const predecessor=actionByLabel(frame,'R2@3:2'),successor=actionByLabel(frame,'R1@3:2');
  const dependency=(successor?.dependencies||[]).find(item=>item.label===predecessor?.action_ref?.label),satisfied=Boolean(dependency&&predecessor.status==='completed');
  const predRobot=robotById(frame,'R2'),succRobot=robotById(frame,'R1');
  const runningLabel=(action,robot)=>action?.status==='running'?`RUNNING · ${robot?.remaining_ticks} tick${robot?.remaining_ticks===1?'':'s'} remaining`:action?.status?.toUpperCase()||'ABSENT';
  const successorLabel=successor?.status==='running'?`RUNNING · ${succRobot?.remaining_ticks} tick remaining`:satisfied&&successor?.display_authority==='committed'?'READY · COMMITTED':satisfied?'DEPENDENCY SATISFIED · PREVIEW':successor?.display_authority==='preview'?'WAITING · PREVIEW':successor?.status?.toUpperCase();
  return `<div class="adg-visual" data-predecessor-status="${esc(predecessor?.status)}" data-successor-status="${esc(successor?.status)}"><div class="adg-node ${esc(predecessor?.status)}"><div class="progress-ring" style="--robot:${robotColor('R2')}"><span>${predecessor?.status==='completed'?'✓':'R2'}</span></div><div><b>${esc(refLabel(predecessor?.action_ref))}</b><span>${esc(runningLabel(predecessor,predRobot))}</span></div></div><div class="adg-edge ${satisfied?'satisfied':''}"><span>${satisfied?'recorded predecessor completed':'recorded dependency unmet'}</span><i>↓</i></div><div class="adg-node ${esc(successor?.status)}"><div class="progress-ring" style="--robot:${robotColor('R1')}"><span>R1</span></div><div><b>${esc(refLabel(successor?.action_ref))}</b><span>${esc(successorLabel)}</span></div></div></div>`;
}

function lifecycleVisual(frame) {
  const stages=currentStory().segments.flatMap(segment=>segment.stages || []),active=currentItem().presentation.stageId;
  return `<div class="milestone-visual">${stages.map((stage,index)=>{const isActive=stage.id===active;return `<div class="milestone ${isActive?'active':''}"><i>${index+1}</i><div><b>${esc(stage.label)}</b><span>${esc(isActive&&currentItem().kind==='montage-gap'?currentItem().presentation.caption:`T${stage.entry.tick}–T${stage.through.tick}`)}</span></div></div>`;}).join('')}</div>`;
}

function executionVisual(frame) {
  return `<div class="execution-visual">${frame.robots.map(robot=>`<article><b style="color:${robotColor(robot.robot_id)}">${esc(robot.robot_id)}</b><span>${esc(refLabel(robot.active_action_ref))}</span><small>${robot.active_action_ref?`${robot.remaining_ticks} ticks remaining`:'not running'}</small></article>`).join('')}</div>`;
}

function exactR4Comparison() {const before=exactFrame(18,'after-confirmation'),after=exactFrame(18,'after-recovery-install');return before&&after?`${robotById(before,'R4')?.plan_version} → ${robotById(after,'R4')?.plan_version}`:'unavailable';}
function minimalEvidence(frame) {
  if(currentItem().kind==='montage-gap'){const [before,after]=currentItem().sourceFrames,gap=currentItem().presentation.gap;return metric(`T${before.tick} ${before.checkpoint}`,'preceding runtime source')+metric(`T${after.tick} ${after.checkpoint}`,'following runtime source')+metric(gap.count,'skipped whole ticks')+metric('presentation only','item kind');}
  const view=currentItem().presentation.logicalView;
  if(view==='compiler'){if(currentItem().presentation.step==='paths'){const paths=frame.recovery?.paths||[],columns=paths.reduce((max,path)=>Math.max(max,(path.cells||[]).length),0);return metric(paths.length,'recorded paths')+metric(columns?`shared t0–t${columns-1}`:'none','timestep columns')+metric('next reveal','compiled actions')+metric('not inferred','dependency evidence');}const participants=new Set(frame.recovery?.participants||[]),actions=(frame.plans||[]).filter(plan=>participants.has(plan.robot_id)).flatMap(plan=>plan.actions),deps=actions.flatMap(action=>(action.dependencies||[]).map(dependency=>({action,dependency})));return metric(frame.recovery?.paths?.length||0,'recorded paths')+metric(actions.length,'recorded actions')+metric(deps.filter(pair=>pair.action.action_ref.robot_id===pair.dependency.robot_id).length,'same-robot edges')+metric(deps.filter(pair=>pair.action.action_ref.robot_id!==pair.dependency.robot_id).length,'cross-robot edges');}
  if(activeStoryId==='B')return metric(scopeFor(frame).join(', ')||'not contained','affected scope')+metric((frame.recovery?.active_nonparticipants||[]).join(', ')||'none recorded','active non-participants')+metric(exactR4Comparison(),'R4 exact version comparison')+metric(frame.events.map(event=>event.kind).join(', ')||'none','current checkpoint events');
  if(activeStoryId==='C'){const confirmed=Boolean(frame.confirmed_wait_for);return metric(coreFor(frame).join(', ')||'—',confirmed?'incident trigger core':'prospective core')+metric(scopeFor(frame).join(', ')||'—','affected scope')+metric((frame.confirmed_wait_for?.cyclic_sccs||[]).flat().join(', ')||'not yet','confirmed cycle')+metric(frame.deadlock?.containment?.state||'observing','authority state');}
  if(view==='splice'){const [before,after]=comparisonFrames();return metric((before.recovery?.participants||[]).join(', '),'participants')+metric(before.recovery?.incident?.confirmation_tick,'confirmation tick')+metric((before.recovery?.incident?.trigger_core||[]).map(item=>item.robot_id).join(', '),'trigger core')+metric((after.events||[]).some(event=>event.kind==='recovery-install-succeeded')?'recorded':'absent','installation event');}
  if(view==='adg'){const predecessor=actionByLabel(frame,'R2@3:2'),successor=actionByLabel(frame,'R1@3:2');return metric(`${refLabel(predecessor?.action_ref)} → ${refLabel(successor?.action_ref)}`,'recorded dependency')+metric(predecessor?.status,'predecessor status')+metric(robotById(frame,'R2')?.remaining_ticks,'R2 remaining ticks')+metric(robotById(frame,'R1')?.remaining_ticks,'R1 remaining ticks');}
  const completed=frame.tasks.filter(task=>task.status==='completed').length,running=frame.robots.filter(robot=>robot.active_action_ref).length;
  const base=metric(allEvents.filter(event=>event.kind==='recovery-completed'&&event.tick<=frame.tick).length,'recorded recoveries completed')+metric(`${completed}/${frame.tasks.length}`,'recorded tasks completed')+metric(running,'running actions')+metric(frame.reservations.length,'reservations');
  return activeStoryId==='F'&&frame.tick===147&&frame.checkpoint==='after-task-advance'?base+metric(frame.plans.length,'live plans')+metric(`${run.termination_reason} · final_tick ${run.final_tick}`,'recorded run metadata'):base;
}

function renderLogical(frame) {
  const view=currentItem().presentation.logicalView;let title,visual;
  if(view==='compiler'){title='Replay-backed compiler';visual=compilerVisual(frame);}
  else if(view==='prospective-scc'||view==='confirmed-scc'){title=view==='confirmed-scc'&&frame.confirmed_wait_for?'Confirmed wait-for graph':'Prospective dependency graph';visual=dependencyGraph(frame);}
  else if(view==='splice'){title='Plan-generation transaction';visual=spliceVisual();}
  else if(view==='adg'){title='Local ADG handoff';visual=adgVisual(frame);}
  else if(view==='execution'){title='Recovery execution';visual=executionVisual(frame);}
  else{title='Task and recovery lifecycle';visual=lifecycleVisual(frame);}
  $('logicalTitle').textContent=title;$('logicalState').textContent=currentStage().label;$('logicalVisual').className=`logical-visual story-${activeStoryId.toLowerCase()} view-${view}`;$('logicalVisual').innerHTML=visual;$('explanationText').textContent=currentItem().presentation.explanation;$('minimalEvidence').innerHTML=minimalEvidence(frame);
}

function evidenceRows(entries){return `<div class="evidence-section">${entries.map(([label,value])=>`<div class="evidence-row"><span>${esc(label)}</span><span>${esc(value)}</span></div>`).join('')}</div>`;}
function renderEvidence() {
  const item=currentItem(),frame=currentFrame(),recovery=frame.recovery,tabs={sources:'Item sources',graph:'Logical graph',plans:'Plans and actions',reservations:'Reservations',transaction:'Recovery transaction',events:'Trace events',raw:'Raw replay frame'};
  $('evidenceTabs').innerHTML=Object.entries(tabs).map(([id,label])=>`<button data-tab="${id}" class="${id===evidenceTab?'active':''}">${label}</button>`).join('');let content='';
  if(evidenceTab==='sources')content=evidenceRows([['emitted kind',item.kind],['emitted cursor',playbackState().frameCursor],['stage',item.presentation.stageId],['sources',item.sourceFrames.map(source=>`#${source.replayIndex} · T${source.tick} ${source.checkpoint}`).join(' | ')],['source fields',item.presentation.sourceFields?.join(', ')||'runtime frame'],['caption',item.presentation.caption||'none']]);
  if(evidenceTab==='graph')content=evidenceRows([['prospective edges',(frame.preview?.dependencies||[]).map(edge=>`${edge.waiting_robot_id} → ${edge.blocking_robot_id}`).join(' · ')||'none'],['cycle core',coreFor(frame).join(', ')||'none'],['affected scope',scopeFor(frame).join(', ')||'none'],['confirmed edges',(frame.confirmed_wait_for?.edges||[]).map(edge=>`${edge.waiting_robot_id} → ${edge.blocking_robot_id}`).join(' · ')||'none'],['confirmed SCCs',(frame.confirmed_wait_for?.cyclic_sccs||[]).map(group=>group.join(', ')).join(' · ')||'none']]);
  if(evidenceTab==='plans')content=(frame.plans||[]).map(plan=>`<section class="evidence-section"><h3 style="color:${robotColor(plan.robot_id)}">${esc(plan.robot_id)} · plan v${plan.plan_version}</h3>${plan.actions.map(action=>`<div class="evidence-row"><span>${esc(refLabel(action.action_ref))}</span><span>${esc(action.kind)} · ${esc(action.status)} · ${esc(action.display_authority)} · ${cellLabel(action.start)} → ${cellLabel(action.end)} · deps ${(action.dependencies||[]).map(refLabel).join(', ')||'none'}</span></div>`).join('')}</section>`).join('')||'No active plans.';
  if(evidenceTab==='reservations')content=(frame.reservations||[]).map(reservation=>evidenceRows([['resource',JSON.stringify(reservation.resource)],['owners',JSON.stringify(reservation.owners)],['robot ids',JSON.stringify(reservation.robot_ids)],['plan versions',JSON.stringify(reservation.plan_versions)]])).join('')||'No reservations at this frame.';
  if(evidenceTab==='transaction')content=evidenceRows([['state',recovery?.state],['confirmation tick',recovery?.incident?.confirmation_tick],['trigger core',(recovery?.incident?.trigger_core||[]).map(item=>item.robot_id).join(', ')],['affected scope',(recovery?.incident?.scope||[]).map(item=>item.robot_id).join(', ')],['participants',recovery?.participants?.join(', ')],['active non-participants',recovery?.active_nonparticipants?.join(', ')],['expected versions',recovery?.expected_plan_versions?.map(item=>`${item.robot_id}@${item.plan_version}`).join(', ')],['installed versions',recovery?.installed_plan_versions?.map(item=>`${item.robot_id}@${item.plan_version}`).join(', ')],['failure',recovery?.failure_reason]]);
  if(evidenceTab==='events')content=(frame.events||[]).map(event=>`<section class="evidence-section"><h3>#${event.sequence} · ${esc(event.kind)}</h3><div class="raw">${esc(JSON.stringify(event,null,2))}</div></section>`).join('')||'No events at this checkpoint.';
  if(evidenceTab==='raw')content=`<div class="raw">${esc(JSON.stringify(frame,null,2))}</div>`;
  $('evidenceContent').innerHTML=content;
}

function stageEntries() {const ids=[];return emittedSequence.map((item,cursor)=>({item,cursor})).filter(({item})=>item.kind!=='montage-gap'&&!ids.includes(item.presentation.stageId)&&ids.push(item.presentation.stageId));}
function render() {
  if(activationError)return;
  const story=currentStory(),item=currentItem(),frame=currentFrame(),state=playbackState();
  $('storyLabel').textContent=`Story ${activeStoryId}`;$('caseLabel').textContent=story.caseId;$('storyTitle').textContent=story.title;$('storyClaim').textContent=story.claim;
  $('tickLabel').textContent=item.kind==='montage-gap'?'CUT':`T${frame.tick}`;$('checkpointLabel').textContent=item.kind==='montage-gap'?item.presentation.caption:frame.checkpoint;$('stageName').textContent=currentStage().label;
  $('physicalMap').innerHTML=mapSvg(frame)+(item.kind==='montage-gap'?`<div class="montage-gap-card"><b>Presentation transition</b><span>${esc(item.presentation.caption)}</span></div>`:'');$('physicalConclusion').textContent=story.conclusion;
  $('mapLegend').innerHTML='<span><i class="scope"></i>replay scope</span><span><i class="route"></i>recorded path</span>';
  const entries=stageEntries(),activeOrder=entries.findIndex(entry=>entry.item.presentation.stageId===item.presentation.stageId);$('stages').innerHTML=entries.map((entry,index)=>`<button class="stage ${index<activeOrder?'done':''} ${index===activeOrder?'active':''}" data-cursor="${entry.cursor}">${esc(currentStory().segments.flatMap(segment=>segment.stages||segment.steps||[]).find(stage=>stage.id===entry.item.presentation.stageId)?.label||entry.item.presentation.stageId)}</button>`).join('');
  $('storySlider').max=emittedSequence.length-1;$('storySlider').value=state.frameCursor;$('play').textContent=state.status==='playing'?'Pause':state.status==='complete'?'Restart':state.status==='paused'?'Resume':'Play';
  renderLogical(frame);if($('evidenceDrawer').classList.contains('open'))renderEvidence();
}

function renderActivationError(error) {activationError=error;$('logicalTitle').textContent='Playback configuration error';$('logicalState').textContent='blocked';$('logicalVisual').innerHTML=`<div class="activation-error" role="alert">${esc(error.message)}</div>`;$('explanationText').textContent='Playback is blocked because exact replay evidence could not be validated.';$('minimalEvidence').innerHTML='';$('physicalMap').innerHTML='';$('play').disabled=true;}
function togglePlay(){const state=playbackState();if(!state)return;if(state.status==='complete'){playbackController.restart();return;}playbackController.toggle();}
async function activateStory(id) {
  playbackController?.destroy();playbackController=null;emittedSequence=[];activationError=null;activeStoryId=id;$('storySelect').value=id;$('play').disabled=true;
  try {const story=currentStory();const response=await fetch(`/runs/${encodeURIComponent(story.caseId)}.json`);if(!response.ok)throw new Error(`Cannot load replay source ${story.caseId}`);run=await response.json();frames=run.frames;robotIds=[...new Set(frames.flatMap(frame=>frame.robots.map(robot=>robot.robot_id)))];const seen=new Set();allEvents=frames.flatMap(frame=>frame.events).filter(event=>{if(seen.has(event.sequence))return false;seen.add(event.sequence);return true;});emittedSequence=buildStorySequence(run,story);playbackController=createPlaybackController({sequence:emittedSequence,storyId:id,onChange:render});$('play').disabled=false;render();}
  catch(error){renderActivationError(error);}
}
function setDrawer(open){$('evidenceDrawer').classList.toggle('open',open);$('evidenceDrawer').setAttribute('aria-hidden',String(!open));$('drawerBackdrop').hidden=!open;if(open&&currentItem())renderEvidence();}

$('storySelect').innerHTML=Object.values(STORIES).map(story=>`<option value="${story.id}">${story.id} · ${esc(story.title)}</option>`).join('');
$('storySelect').onchange=event=>activateStory(event.target.value);$('previousStage').onclick=()=>playbackController?.previous();$('nextStage').onclick=()=>playbackController?.next();$('play').onclick=togglePlay;$('storySlider').oninput=event=>playbackController?.seek(+event.target.value);$('stages').onclick=event=>{const button=event.target.closest('[data-cursor]');if(button)playbackController?.seek(+button.dataset.cursor);};$('openEvidence').onclick=()=>setDrawer(true);$('closeEvidence').onclick=()=>setDrawer(false);$('drawerBackdrop').onclick=()=>setDrawer(false);$('evidenceTabs').onclick=event=>{const button=event.target.closest('[data-tab]');if(button){evidenceTab=button.dataset.tab;renderEvidence();}};$('toggleExport').onclick=()=>document.body.classList.toggle('export-view');
addEventListener('keydown',event=>{if(['INPUT','SELECT'].includes(event.target.tagName))return;if(event.code==='Space'){event.preventDefault();togglePlay();}else if(event.key==='ArrowLeft')playbackController?.previous();else if(event.key==='ArrowRight')playbackController?.next();else if(event.key==='Escape')setDrawer(false);});

window.__MAPF_SPLICE_STORY__={get storyId(){return activeStoryId;},get sequence(){return emittedSequence;},get state(){return playbackState();},activateStory};
const captureItemMetadata = item => ({
  kind:item.kind,
  replayIndex:item.replayIndex,
  sources:item.sourceFrames.map(frame=>({replayIndex:frame.replayIndex,tick:frame.tick,checkpoint:frame.checkpoint})),
  presentation:structuredClone(item.presentation)
});
window.__MAPF_SPLICE_CAPTURE__=Object.freeze({
  getStoryId:()=>activeStoryId,
  getCursor:()=>playbackState()?.frameCursor ?? null,
  getItemCount:()=>emittedSequence.length,
  getItems:()=>emittedSequence.map(captureItemMetadata),
  getCurrentItemMetadata:()=>currentItem()?captureItemMetadata(currentItem()):null,
  activateStory,
  seek:cursor=>playbackController?.seek(cursor),
  restart:()=>playbackController?.restart(),
  setExportView:enabled=>document.body.classList.toggle('export-view',Boolean(enabled))
});
await activateStory('B');
