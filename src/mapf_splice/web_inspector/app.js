const run = await fetch('/run.json').then(r => r.json());
const frames = run.frames;
const colors = ['#86c5ff','#ffad79','#d2a8ff','#7ee2b8','#f4cf65'];
const checkpoints = run.checkpoint_names;
let index = 0, timer = null, selectedAction = null;
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '—').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const refLabel = ref => ref?.label ?? '—';
const cellLabel = c => c ? `${c.row},${c.col}` : '—';
const cellKey = c => `${c.row}:${c.col}`;
const resourceLabel = r => r.type === 'vertex' ? `V(${cellLabel(r.cell)})` : `E(${cellLabel(r.first)}→${cellLabel(r.second)})`;

const allEvents = frames.flatMap((frame, frameIndex) => frame.events.map(event => ({...event, frameIndex})));
const bookmarkDefs = [
  ['Dependency','prospective-dependency'],['Cyclic SCC','prospective-scc-observed'],['Stable SCC','stable-scc-detected'],['Containment','containment-started'],['Quiescence','quiescence-reached']
];
const bookmarks = bookmarkDefs.map(([label,kind]) => ({label,kind,event:allEvents.find(e=>e.kind===kind)})).filter(x=>x.event);
$('bookmarks').innerHTML = bookmarks.map((b,i)=>`<button data-bookmark="${i}">${b.label} · T${b.event.tick}</button>`).join('');
$('bookmarks').onclick = e => { const b=e.target.closest('[data-bookmark]'); if(b){ index=bookmarks[+b.dataset.bookmark].event.frameIndex; render(); } };

const robotIds = [...new Set(frames.flatMap(f=>f.robots.map(r=>r.robot_id)))];
const kinds = [...new Set(allEvents.map(e=>e.kind))].sort();
$('robotFilter').innerHTML += robotIds.map(x=>`<option>${esc(x)}</option>`).join('');
$('kindFilter').innerHTML += kinds.map(x=>`<option>${esc(x)}</option>`).join('');
$('runMeta').innerHTML = `${esc(run.scenario_id)}<br>K=${run.committed_horizon} · ${frames.length} frames`;
$('slider').max = frames.length - 1;

function robotColor(id){ return colors[Math.max(0, robotIds.indexOf(id)) % colors.length]; }
function mapSvg(frame){
  const rows=run.map_rows, cols=rows[0].length, size=44, pad=28, w=cols*size+pad*2, h=rows.length*size+pad*2;
  const stations=new Map(run.stations.map(s=>[cellKey(s.cell),s]));
  const reservations=frame.reservations;
  let shapes='';
  rows.forEach((row,r)=>[...row].forEach((symbol,c)=>{
    const x=pad+c*size,y=pad+r*size, blocked=symbol==='#', station=stations.get(`${r}:${c}`);
    shapes += `<rect x="${x}" y="${y}" width="${size-2}" height="${size-2}" rx="5" fill="${blocked?'#20272c':'#121a1f'}" stroke="#263139"/>`;
    if(blocked) shapes += `<path d="M${x+10},${y+22}h${size-22}" stroke="#46525a" stroke-width="3"/>`;
    if(station) shapes += `<text x="${x+size/2}" y="${y+size/2+4}" text-anchor="middle" fill="${station.kind==='handoff'?'#66d9ef':'#b7f36b'}" font-size="9" font-family="monospace">${station.kind==='handoff'?'P':'D'}</text>`;
  }));
  const point=c=>[pad+c.col*size+size/2,pad+c.row*size+size/2];
  frame.plans.forEach(plan=>{
    const color=robotColor(plan.robot_id), actions=plan.actions.filter(a=>['completed','running','committed','preview'].includes(a.display_authority));
    if(!actions.length)return;
    let d=`M${point(actions[0].start).join(',')}`; actions.forEach(a=>d+=` L${point(a.end).join(',')}`);
    shapes += `<path d="${d}" fill="none" stroke="${color}" stroke-width="4" opacity=".36" stroke-linecap="round" stroke-dasharray="${actions.some(a=>a.preview)?'7 6':'none'}"/>`;
  });
  reservations.forEach(item=>{ if(item.resource.type==='vertex'){const [x,y]=point(item.resource.cell);shapes+=`<rect x="${x-13}" y="${y-13}" width="26" height="26" rx="6" fill="none" stroke="#b7f36b" stroke-width="2" opacity=".72"/>`;}});
  frame.preview.dependencies.forEach(dep=>{const robot=frame.robots.find(r=>r.robot_id===dep.waiting_robot_id);const target=dep.resource.cell||dep.resource.second;if(robot&&target){const [x1,y1]=point(robot.position),[x2,y2]=point(target);shapes+=`<path d="M${x1},${y1}L${x2},${y2}" stroke="#ff6b6b" stroke-width="2" stroke-dasharray="4 4" marker-end="url(#arrow)"/>`;}});
  frame.robots.forEach(robot=>{const [x,y]=point(robot.position),color=robotColor(robot.robot_id);shapes+=`<circle cx="${x}" cy="${y}" r="16" fill="${color}" stroke="${robot.contained?'#f4b860':'#081014'}" stroke-width="${robot.contained?4:2}"/><text x="${x}" y="${y+4}" text-anchor="middle" fill="#071014" font-size="10" font-weight="800" font-family="monospace">${esc(robot.robot_id)}</text>${robot.payload_task_id?`<circle cx="${x+12}" cy="${y-12}" r="5" fill="#f4b860"/>`:''}`;});
  return `<svg viewBox="0 0 ${w} ${h}" role="img"><defs><marker id="arrow" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto"><path d="M0 0L6 3L0 6z" fill="#ff6b6b"/></marker></defs>${shapes}</svg>`;
}

function renderPlans(frame){
  $('planCount').textContent=`${frame.plans.length} active`;
  $('plans').innerHTML=frame.plans.map(plan=>{const robot=frame.robots.find(r=>r.robot_id===plan.robot_id);return `<article class="plan"><div class="plan-head"><span class="robot-name" style="color:${robotColor(plan.robot_id)}">${esc(plan.robot_id)} · v${plan.plan_version}</span><span class="robot-sub">${esc(robot.task_status)} · (${cellLabel(robot.position)})${robot.contained?' · CONTAINED':''}</span></div><div class="lane">${plan.actions.map(a=>`<button class="action ${a.display_authority} ${selectedAction===a.action_ref.label?'selected':''}" data-action="${esc(a.action_ref.label)}" title="${esc(a.display_authority)}">${a.action_index}</button>`).join('')}</div></article>`}).join('') || '<div class="detail empty">No active plans in this frame.</div>';
}
function renderDetail(frame){
  const action=frame.plans.flatMap(p=>p.actions).find(a=>a.action_ref.label===selectedAction);
  $('actionDetail').classList.toggle('empty',!action);
  $('actionDetail').innerHTML=action?`<strong>${esc(action.action_ref.label)}</strong> · ${esc(action.kind)} · ${esc(action.display_authority)}<br>${cellLabel(action.start)} → ${cellLabel(action.end)} · duration ${action.duration_ticks}<br>claims: ${action.claims.map(resourceLabel).join(', ')}<br>dependencies: ${action.dependencies.map(refLabel).join(', ')||'none'}`:'Select an action to inspect its claims and dependencies.';
}
$('plans').onclick=e=>{const button=e.target.closest('[data-action]');if(button){selectedAction=button.dataset.action;render();}};

function renderGraph(frame){
  const robots=frame.robots.map(r=>r.robot_id), n=robots.length, cx=160,cy=76,rad=55,positions={};robots.forEach((id,i)=>{const a=-Math.PI/2+i*2*Math.PI/n;positions[id]=[cx+Math.cos(a)*rad,cy+Math.sin(a)*rad];});
  let svg='<defs><marker id="g-arrow" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto"><path d="M0 0L6 3L0 6z" fill="#ff6b6b"/></marker></defs>';
  frame.preview.dependencies.forEach((d,i)=>{const a=positions[d.waiting_robot_id],b=positions[d.blocking_robot_id];if(a&&b)svg+=`<path d="M${a[0]},${a[1]}L${b[0]},${b[1]}" stroke="#ff6b6b" stroke-width="2" opacity="${.65+i*.08}" marker-end="url(#g-arrow)"/>`;});
  robots.forEach(id=>{const [x,y]=positions[id];svg+=`<circle cx="${x}" cy="${y}" r="18" fill="#141c21" stroke="${robotColor(id)}" stroke-width="2"/><text x="${x}" y="${y+4}" text-anchor="middle" fill="${robotColor(id)}" font-size="10" font-family="monospace">${esc(id)}</text>`;});
  $('graph').innerHTML=`<svg viewBox="0 0 320 152">${svg}</svg>`;
  $('edgeCount').textContent=`${frame.preview.dependencies.length} edges`;
  const containmentById=new Map(frame.deadlock.containments.map(c=>[c.identity.map(x=>x.robot_id+'@'+x.plan_version).join(','),c]));
  $('sccs').innerHTML=frame.deadlock.candidates.map(c=>{const id=c.identity.map(x=>x.robot_id+'@'+x.plan_version).join(','),containment=containmentById.get(id);return `<div class="scc ${c.stable?'stable':''} ${containment?.quiescence_emitted?'quiescent':''}"><strong>${esc(id)}</strong>observation ${c.observation_count} / ${frame.deadlock.threshold}<br>${c.stable?'stable · ':''}${containment?.valid?'contained':''}${containment?.quiescence_emitted?' · quiescent':''}</div>`}).join('')||'<div class="scc"><strong>No cyclic SCC</strong>Current preview evidence is acyclic.</div>';
}
function renderEvents(frame){const robot=$('robotFilter').value,kind=$('kindFilter').value,events=frame.events.filter(e=>(!robot||e.robot_id===robot)&&(!kind||e.kind===kind));$('events').innerHTML=events.map(e=>`<div class="event"><span class="seq">#${e.sequence}</span><span class="phase">${esc(e.phase)}</span><span class="kind">${esc(e.kind)}</span><span>${esc(e.robot_id||'global')} ${esc(e.action_ref?.label||'')} ${esc(JSON.stringify(e.details))}</span></div>`).join('')||'<div class="event empty">No matching events at this checkpoint.</div>';}

function render(){const frame=frames[index];$('tickLabel').textContent=`Tick ${frame.tick}`;$('checkpointLabel').textContent=frame.checkpoint;$('slider').value=index;$('frameLabel').textContent=`${index+1} / ${frames.length}`;$('warehouse').innerHTML=mapSvg(frame);renderPlans(frame);renderDetail(frame);renderGraph(frame);renderEvents(frame);document.querySelectorAll('[data-bookmark]').forEach((b,i)=>b.classList.toggle('active',bookmarks[i].event.frameIndex===index));}
function move(delta){index=Math.max(0,Math.min(frames.length-1,index+delta));render();}
function moveTick(delta){const tick=frames[index].tick+delta;const candidates=frames.map((f,i)=>[f,i]).filter(([f])=>f.tick===tick);if(candidates.length){index=delta>0?candidates[0][1]:candidates.at(-1)[1];render();}}
function stop(){if(timer){clearInterval(timer);timer=null;$('play').textContent='Play';}}
function toggle(){if(timer){stop();return;}$('play').textContent='Pause';timer=setInterval(()=>{if(index>=frames.length-1){stop();return;}move(1);},+$('speed').value);}
$('prev').onclick=()=>move(-1);$('next').onclick=()=>move(1);$('prevTick').onclick=()=>moveTick(-1);$('nextTick').onclick=()=>moveTick(1);$('play').onclick=toggle;$('speed').onchange=()=>{if(timer){stop();toggle();}};$('slider').oninput=e=>{index=+e.target.value;render();};$('robotFilter').onchange=()=>renderEvents(frames[index]);$('kindFilter').onchange=()=>renderEvents(frames[index]);
addEventListener('keydown',e=>{if(['INPUT','SELECT'].includes(e.target.tagName))return;if(e.code==='Space'){e.preventDefault();toggle();}else if(e.key==='ArrowLeft'){e.preventDefault();e.shiftKey?moveTick(-1):move(-1);}else if(e.key==='ArrowRight'){e.preventDefault();e.shiftKey?moveTick(1):move(1);}});
render();
