// HU LATENCY MONITOR V5 — browser monotonic timing, independently checked Gazebo motion.
// Makes no inference or safety decisions; only observes the existing HU pipeline.
(() => {
  'use strict';
  const INTERVAL_MS = 150;
  const DEADLINE_MS = 16000;
  const REQUIRED_MOTION_M = 0.10;
  const names = ['Horus','Ra','Thoth','Anubis','Osiris'];
  const monitoredPaths = ['/api/hu/command','/api/hu/voice-auto','/api/hu/voice-accept','/api/hu/confirm'];
  const state = {trial:null, history:[], seq:0};
  const round = v => v == null || !Number.isFinite(v) ? null : Math.round(v*10)/10;
  const elapsed = (a,b) => Number.isFinite(a) && Number.isFinite(b) ? round(b-a) : null;
  const text = (id,s) => {const node=document.getElementById(id);if(node)node.textContent=s;};
  const snap = payload => Object.fromEntries((payload.drones||[]).filter(d=>names.includes(d.name)).map(d=>[d.name,{
    x:Number(d.x),y:Number(d.y),z:Number(d.z),yaw:Number(d.yaw),mode:String(d.mode||''),telemetry:d.telemetry===true
  }]));
  async function readState(){
    const response=await fetch('/api/state',{cache:'no-store'});
    if(!response.ok) throw Error('Gazebo telemetry unavailable: HTTP '+response.status);
    return await response.json();
  }
  function start(source,now,utterance='',onsetDetected=false){
    if(state.trial && !state.trial.finished) finish(state.trial,'INTERRUPTED','A new command began before the previous result was verified');
    const trial={seq:++state.seq,source,utterance,originAt:now,onsetDetected,transcriptAt:null,
      requestAt:null,decisionAt:null,dispatchAckAt:null,inferenceMs:null,huRttMs:null,
      confirmationWaitMs:0,clarificationWaitMs:0,holdStarted:null,holdKind:null,
      targets:[],action:null,baseline:null,baselineAt:null,perDrone:{},finished:false,
      status:'RECOGNIZING',lastSampleAt:null,replyStatus:'',confirmed:false};
    state.trial=trial;
    text('latency-status',source==='voice'?'Speech detected — measuring…':'Typed command — measuring…');
    readState().then(s=>{if(!trial.finished && !trial.dispatchAckAt){trial.baseline=snap(s);trial.baselineAt=performance.now();}})
      .catch(e=>{trial.baselineError=e.message;});
    return trial;
  }
  function serialize(trial,outcome,detail){
    const ts=new Date().toISOString();
    const p=trial.perDrone;
    const motionEntries=Object.values(p).filter(x=>Number.isFinite(x));
    const first=motionEntries.length?Math.min(...motionEntries):null;
    const all=trial.targets.length && trial.targets.every(n=>Number.isFinite(p[n]))?Math.max(...trial.targets.map(n=>p[n])):null;
    const motionStart=Number.isFinite(first)?trial.originAt+first:null;
    const allStart=Number.isFinite(all)?trial.originAt+all:null;
    const userWait=trial.confirmationWaitMs+trial.clarificationWaitMs;
    return {
      timestamp_utc:ts,source:trial.source,utterance:trial.utterance,action:trial.action||'',
      targets:[...trial.targets],outcome,detail:detail||'',
      voice_onset_detected:trial.source==='voice'&&trial.onsetDetected,
      asr_ms:trial.source==='voice'&&trial.onsetDetected?elapsed(trial.originAt,trial.transcriptAt):null,
      hu_roundtrip_ms:round(trial.huRttMs),hu_inference_ms:round(trial.inferenceMs),
      operator_confirmation_wait_ms:round(trial.confirmationWaitMs),
      transcript_clarification_wait_ms:round(trial.clarificationWaitMs),
      dispatch_ack_to_first_observed_ms:elapsed(trial.dispatchAckAt,motionStart),
      voice_to_first_observed_ms:trial.source==='voice'&&trial.onsetDetected?round(first):null,
      voice_to_all_observed_ms:trial.source==='voice'&&trial.onsetDetected?round(all):null,
      submitted_to_first_observed_ms:Number.isFinite(first)?elapsed(trial.requestAt,motionStart):null,
      submitted_to_all_observed_ms:Number.isFinite(all)?elapsed(trial.requestAt,allStart):null,
      per_drone_observed_ms:Object.fromEntries(Object.entries(p).map(([k,v])=>[k,round(v)])),
      sample_interval_ms:INTERVAL_MS,
      measurement:'browser performance.now() to first received Gazebo telemetry with >10cm displacement (or >7deg yaw on TURN); telemetry-poll bound, not physical actuator timestamp',
      version:'hu-latency-v5'
    };
  }
  function save(record){
    state.history.unshift(record);state.history.length=Math.min(state.history.length,100);
    update(record);
    fetch('/api/hu/latency',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(record)}).catch(()=>{});
  }
  function finish(trial,outcome,detail=''){
    if(!trial || trial.finished)return;
    trial.finished=true;
    clearTimeout(trial.timer);
    const r=serialize(trial,outcome,detail);save(r);
    if(state.trial===trial)state.trial=null;
  }
  function format(v){return v==null?'—':`${Math.round(v)} ms`;}
  function update(r){
    text('metric-e2e',r.voice_to_first_observed_ms!=null?format(r.voice_to_first_observed_ms):'—');
    text('metric-asr',format(r.asr_ms));
    text('metric-hu',format(r.hu_inference_ms));
    text('metric-ack',format(r.dispatch_ack_to_first_observed_ms));
    text('metric-all',format(r.voice_to_all_observed_ms));
    text('latency-status',`${r.outcome} · ${r.action||'—'} → ${r.targets.join(', ')||'none'}. ${r.detail||''}`);
    const tbody=document.getElementById('latency-log');if(!tbody)return;
    tbody.textContent='';
    for(const event of state.history.slice(0,8)){
      const row=document.createElement('tr');
      const values=[new Date(event.timestamp_utc).toLocaleTimeString(),event.utterance,event.outcome,
        format(event.asr_ms),format(event.hu_inference_ms),format(event.voice_to_first_observed_ms),
        format(event.voice_to_all_observed_ms)];
      for(const val of values){const td=document.createElement('td');td.textContent=val;row.append(td);}tbody.append(row);
    }
  }
  function moved(before,current,action){
    if(!before||!current||!current.telemetry||!before.telemetry)return false;
    if (![before.x,before.y,before.z,current.x,current.y,current.z].every(Number.isFinite))return false;
    const distance=Math.hypot(current.x-before.x,current.y-before.y,current.z-before.z);
    const angle=Number.isFinite(before.yaw)&&Number.isFinite(current.yaw)
      ?Math.abs((((current.yaw-before.yaw)+540)%360)-180):0;
    return distance>=REQUIRED_MOTION_M || (['TURN','SPIN'].includes(String(action).toUpperCase())&&angle>=7);
  }
  async function watch(trial){
    if(trial.finished)return;
    if(!trial.targets.length){return finish(trial,'UNVERIFIED','HU dispatch contained no target list');}
    if(!trial.baseline){return finish(trial,'UNVERIFIED','No pre-command Gazebo telemetry baseline available: '+(trial.baselineError||'unknown'));}
    for(const name of trial.targets){
      const d=trial.baseline[name];
      if(!d||!d.telemetry)return finish(trial,'UNVERIFIED','Missing fresh pre-command telemetry for '+name);
      if(['TAKEOFF','LANDING','MOVE','TURN'].includes(d.mode))return finish(trial,'UNVERIFIED','Drone '+name+' was already maneuvering; cannot isolate new motion');
    }
    const action=String(trial.action||'').toUpperCase();
    if(['HOVER','STOP'].includes(action))return finish(trial,'ACK_ONLY','Holding position may involve no observable displacement');
    if((action==='LAND'||action==='EMERGENCY_LAND')&&trial.targets.every(n=>trial.baseline[n].z<=.25))
      return finish(trial,'ALREADY_GROUNDED','No new descending motion expected; use grounded-state verification');
    const deadline=performance.now()+DEADLINE_MS;
    const loop=async()=>{
      if(trial.finished)return;
      try {
        const live=await readState();const arrival=performance.now();const snapshot=snap(live);
        for(const name of trial.targets){
          if(!Number.isFinite(trial.perDrone[name])&&moved(trial.baseline[name],snapshot[name],trial.action))
            trial.perDrone[name]=elapsed(trial.originAt,arrival);
        }
        if(trial.targets.every(n=>Number.isFinite(trial.perDrone[n])))
          return finish(trial,'OBSERVED_ALL','All targets exceeded the motion threshold in live Gazebo odometry');
        if(performance.now()>=deadline){
          const seen=trial.targets.filter(n=>Number.isFinite(trial.perDrone[n]));
          return finish(trial,seen.length?'OBSERVED_PARTIAL':'NO_MOTION_VERIFIED',
            `Verified ${seen.length}/${trial.targets.length} moving after ${DEADLINE_MS/1000}s`);
        }
      } catch(e){trial.lastError=e.message;}
      if(performance.now()>=deadline)return finish(trial,'UNVERIFIED',trial.lastError||'Gazebo state unavailable');
      trial.timer=setTimeout(loop,INTERVAL_MS);
    };
    loop();
  }
  const uninstrumentedApi=api;
  api=async function(path,data){
    const monitor=monitoredPaths.includes(path);
    let trial=state.trial;
    const t0=performance.now();
    if(monitor && (!trial || trial.finished) && ['/api/hu/command','/api/hu/voice-auto'].includes(path)){
      trial=start(path==='/api/hu/voice-auto'?'voice':'typed',t0,data.utterance||'',false);
    }
    if(monitor && trial && !trial.finished){
      trial.requestAt??=t0;
      if(path==='/api/hu/confirm' && trial.holdStarted!=null){
        trial.confirmationWaitMs+=t0-trial.holdStarted;trial.holdStarted=null;
      }
      if(path==='/api/hu/voice-accept' && trial.holdStarted!=null){
        trial.clarificationWaitMs+=t0-trial.holdStarted;trial.holdStarted=null;
      }
    }
    let result;
    try { result=await uninstrumentedApi(path,data); }
    catch(err){if(monitor && trial && !trial.finished)finish(trial,'REQUEST_ERROR',String(err.message));throw err;}
    const received=performance.now();
    if(!monitor || !trial || trial.finished)return result;
    if(path==='/api/hu/command'||path==='/api/hu/voice-auto'||path==='/api/hu/voice-accept'){
      trial.utterance||=data.utterance||'';
      trial.action=result.action||trial.action;
      trial.targets=result.targets||trial.targets;
      trial.inferenceMs=result.decision_source==='DETERMINISTIC MISSION PLANNER'?null:(Number.isFinite(Number(result.latency_ms))&&result.latency_ms!=null?Number(result.latency_ms):trial.inferenceMs);
      trial.huRttMs=(trial.huRttMs||0)+(received-t0);
      trial.decisionAt=received;
      if(result.band==='VOICE_CLARIFY'){trial.holdStarted=received;trial.holdKind='clarification';trial.status='VOICE_CLARIFY';}
      else if(result.band==='CONFIRM'){trial.holdStarted=received;trial.holdKind='confirmation';trial.status='CONFIRM';}
      else if(result.dispatched){trial.dispatchAckAt=received;trial.status='DISPATCHED';watch(trial);}
      else finish(trial,result.band||'NOT_DISPATCHED',result.reason||'HU did not dispatch');
    } else if(path==='/api/hu/confirm'){
      if(result.ok===false)finish(trial,'DISPATCH_REJECTED','Confirmation was not accepted');
      else{trial.dispatchAckAt=received;trial.status='DISPATCHED';trial.confirmed=true;watch(trial);}
    }
    return result;
  };
  if(typeof rec!=='undefined'&&rec){
    rec.onspeechstart=()=>{
      if(!pending && !busy)start('voice',performance.now(),'',true);
    };
    const oldResult=rec.onresult;
    rec.onresult=function(evt){
      if(!speaking){
        let found='';
        for(let i=evt.resultIndex||0;i<evt.results.length;i++){
          if(evt.results[i].isFinal && evt.results[i][0]?.transcript){found=evt.results[i][0].transcript;break;}
        }
        if(found){
          let trial=state.trial;
          if(!trial && !pending)trial=start('voice',performance.now(),found,false);
          if(trial && !pending && trial.transcriptAt==null){trial.transcriptAt=performance.now();trial.utterance=found;}
        }
      }
      return oldResult.call(this,evt);
    };
  }
  const originalMicClick=document.getElementById('mic').onclick;
  document.getElementById('mic').onclick=function(evt){
    if(armed && state.trial && !state.trial.finished) finish(state.trial,'STOPPED','Hands-free was stopped');
    if(typeof originalMicClick==='function')return originalMicClick.call(this,evt);
  };
  const originalCancel=api;
  api=async function(path,data){
    if(path==='/api/hu/cancel' && state.trial && !state.trial.finished){
      finish(state.trial,'CANCELLED','Voice approval cancelled or timed out');
    }
    return originalCancel(path,data);
  };
  function asCsvValue(v){
    const s=typeof v==='object'&&v!==null?JSON.stringify(v):String(v??'');
    // Formula-injection protection when opening exported CSV in a spreadsheet.
    const safe=/^[\s]*[=+@\-]/.test(s)?"'"+s:s;
    return '"'+safe.replace(/"/g,'""')+'"';
  }
  document.getElementById('export-latency').onclick=()=>{
    if(!state.history.length){alert('No completed latency observations yet.');return;}
    const cols=['timestamp_utc','source','utterance','action','targets','outcome','detail','voice_onset_detected',
      'asr_ms','hu_roundtrip_ms','hu_inference_ms','operator_confirmation_wait_ms','transcript_clarification_wait_ms',
      'dispatch_ack_to_first_observed_ms','voice_to_first_observed_ms','voice_to_all_observed_ms',
      'submitted_to_first_observed_ms','submitted_to_all_observed_ms','per_drone_observed_ms','sample_interval_ms'];
    const csv=[cols.join(','),...state.history.slice().reverse().map(row=>cols.map(k=>asCsvValue(row[k])).join(','))].join('\r\n');
    const blob=new Blob([csv],{type:'text/csv;charset=utf-8'}),url=URL.createObjectURL(blob);
    const a=document.createElement('a');a.href=url;a.download='HU_voice_to_motion_'+new Date().toISOString().replace(/[:.]/g,'-')+'.csv';
    document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),2000);
  };
})();
