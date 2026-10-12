import React, { useEffect, useRef, useState } from 'react';
import { api, ApiError, BenchmarkInput, categorizeError, ErrorCategory, ActionInput, ActionPlan, BusinessAction, Blocker, BlockerAction, Project, Run, UserTask } from './api';
import { ArtifactTabs } from './workbench';
import { SourcePanel, VersionHistory, ConfirmationPanel, DocumentStudio, documentTaskForType } from './panels';
import { useDialog } from './shell';
import { ProductCheckpoint, QuestionList } from './product-flow';
import { IntakeChat } from './intake-chat';
import { BenchmarkReport } from './benchmark-report';
import { pastedWebpageReference } from './benchmark-intake';
import { DraftConflictError, EditProof, useDirty, useNavigation } from './editing';
import { Icon } from './ui-icons';
import { phases, TaskStatus, ActionDialog, OptionDecision } from './guided';
import { activityNotice, beginActivity } from './activity';
import { canRecoverDraft } from './task-failure';
import { mergeProjectWrite } from './project-update';
import { contextSnapshot } from './scope-draft';
import { BlockerPanel } from './blockers';
import { BlockerFocus, blockerDestination, focusBlockerElement, stageBlockers } from './blocker-navigation';
import { ProgressControls, useTaskProgress } from './task-progress';
import { StageNavigation, StageLock, stageAccess, intakeStatusLabel, isScopeQuestion, questionPhase } from './workflow-access';
import { ConnectedDestination, ConnectedWorkspace } from './pm-prototype/ConnectedWorkspace';
import { refinementDestination } from './pm-prototype/refine-navigation';
import { RefinementPreparation, PreparationDependencies, PreparationState, PreparationStatus, PreparationSubmission } from './refinement-preparation';
import { DocumentDiscussionPanel } from './document-discussion';
import { documentDiscussionContext } from './document-discussion-context';
type Obj = Record<string, any>;
export type Compat = { status: 'ok' | 'incompatible' | 'unverified'; missing: string[] };
const categoryText: Record<ErrorCategory, string> = { 'not-found': '项目不存在或已被删除', 'route-missing': '所需接口缺失，前后端版本可能不兼容', network: '无法连接服务', 'non-json': '服务返回了无法识别的内容', session: '页面连接状态已失效，请重新加载', forbidden: '访问被拒绝', conflict: '内容版本已变化', server: '暂时无法加载此项目', unknown: '暂时无法加载此项目' };

export function Workspace({ id, active, models, onProjectsChanged, onProjectSnapshot, compat, onExit, onOpen }: { id: string; active: boolean; models: Obj; onProjectsChanged: () => Promise<void>; onProjectSnapshot?: (project: Project) => void; compat: Compat; onExit: () => void; onOpen: (afterOpen: () => void) => void }) {
  const [p, setP] = useState<Project | null>(null), [phase, setPhase] = useState(()=>sessionStorage.getItem('ra-phase-'+id)==='5'?5:4), [runs, setRuns] = useState<Run[]>([]), [tasks, setTasks] = useState<UserTask[]>([]);
  const [error,setError]=useState(''),[busy,setBusy]=useState(false);
  const [sourcesOpen, setSourcesOpen] = useState(false), [sourceText, setSourceText] = useState(''), [url, setUrl] = useState(''), [purpose, setPurpose] = useState('goal'), [dynamic, setDynamic] = useState(false);
  const [manualPageUrl,setManualPageUrl]=useState(''),[manualPageTitle,setManualPageTitle]=useState(''),[manualPageNotice,setManualPageNotice]=useState(''),[manualPagePendingUrl,setManualPagePendingUrl]=useState(''),[manualPageActive,setManualPageActive]=useState(false);
  const [historyOpen, setHistoryOpen] = useState(false), [history, setHistory] = useState<Obj[]>([]), [artifacts, setArtifacts] = useState<Obj>({ document_artifact: [] });
  const [docType, setDocType] = useState('prd'), [selectedDocId, setSelectedDocId] = useState(''), [rename, setRename] = useState(''), [renameOpen, setRenameOpen] = useState(false);
  const [documentQuestion, setDocumentQuestion] = useState<{id:string;nonce:number}>();
  const [, setShowAdvanced]=useState(false),[,setStage]=useState('ingest');
  const [pending, setPending] = useState<{ plan: ActionPlan; input: ActionInput; key: string; preparationToken?:number } | null>(null), [actionError, setActionError] = useState('');
  const [actionStale, setActionStale] = useState(false), [reviewFocusNonce, setReviewFocusNonce] = useState(0);
  const [optionDecision, setOptionDecision] = useState<Obj | null>(null), [previewOption, setPreviewOption] = useState<Obj | null>(null);
  const [focusRefs, setFocusRefs] = useState<Obj[]>([]);
  const [fatal, setFatal] = useState<ApiError | null>(null), [stale, setStale] = useState<string[]>([]), [staleNotice, setStaleNotice] = useState(''), [tasksLoaded, setTasksLoaded] = useState(false), [compatDismissed, setCompatDismissed] = useState(false);
  const [help,setHelp]=useState<Obj|null>(null),[helpText,setHelpText]=useState(''),[candidateOpen,setCandidateOpen]=useState(false),[candidateTitle,setCandidateTitle]=useState(''),[candidateText,setCandidateText]=useState('');
  const [acceptanceOpen,setAcceptanceOpen]=useState(false),[acceptanceRequirement,setAcceptanceRequirement]=useState(''),[acceptanceTitle,setAcceptanceTitle]=useState(''),[acceptanceText,setAcceptanceText]=useState('');
  const guard=useNavigation();
  const progress = useTaskProgress();
  const progressControls = useRef<ProgressControls>({cancel:async()=>{},refresh:async()=>{},open:()=>{}});
  useEffect(()=>progress.register(id,progressControls),[id,progress]);
  useEffect(()=>{if(p)progress.publish({projectId:id,projectName:p.name,tasks,busy,uncertain:Boolean(fatal)||stale.includes('任务列表')});},[id,p?.name,tasks,busy,Boolean(fatal),stale.includes('任务列表'),progress]);
  const analysisNavigation=useRef<{taskId:string;ingestCount:number;itemCount:number;questionCount:number;resolving?:boolean;connected?:boolean}|null>(null);
  const [preparation,setPreparation]=useState<PreparationState>({state:'idle'});
  const preparationDependencies=useRef<PreparationDependencies|null>(null);
  const preparationController=useRef<RefinementPreparation|null>(null);
  if(!preparationController.current)preparationController.current=new RefinementPreparation(()=>preparationDependencies.current!);
  const [connectedDestination,setConnectedDestination]=useState<ConnectedDestination>();
  const [focusQuestionId,setFocusQuestionId]=useState(''),[focusQuestionNonce,setFocusQuestionNonce]=useState(0);
  const [focusClauseId,setFocusClauseId]=useState(''),[focusClauseNonce,setFocusClauseNonce]=useState(0),[returnAnswerId,setReturnAnswerId]=useState('');
  const [focusRequirementId,setFocusRequirementId]=useState(''),[focusRequirementNonce,setFocusRequirementNonce]=useState(0);
  const [blockerFocus,setBlockerFocus]=useState<BlockerFocus>(),[blockerTask,setBlockerTask]=useState<{title:string;phase:number}|null>(null);
  const [focusClauseIds,setFocusClauseIds]=useState<string[]>([]),[focusAnswerId,setFocusAnswerId]=useState(''),[focusAnswerNonce,setFocusAnswerNonce]=useState(0);
  const [benchmarkSubmission,setBenchmarkSubmission]=useState<{taskId:string;message:string}>();
  const [interviewSubmission,setInterviewSubmission]=useState<{taskId:string;message:string;attachmentSourceIds:string[]}>();
  const [directInputNonce,setDirectInputNonce]=useState(0);
  const [directEntryDraft,setDirectEntryDraft]=useState<{text:string;nonce:number}>();
  const currentView=useRef({active,phase});currentView.current={active,phase};
  const detailRef=useRef<HTMLDetailsElement|null>(null);
  const connectedRef=useRef<HTMLDivElement|null>(null);
  function showMain(){analysisNavigation.current=null;const wasOpen=detailRef.current?.open;if(detailRef.current)detailRef.current.open=false;if(wasOpen)requestAnimationFrame(()=>connectedRef.current?.scrollIntoView({block:'nearest'}));}
  function openMain(next:Omit<ConnectedDestination,'nonce'>){guard(()=>{showMain();setConnectedDestination(previous=>({...next,nonce:(previous?.nonce||0)+1}));});}
  function openDetails(n:number,documentKind?:string,afterOpen?:()=>void){go(n,()=>{if(documentKind){setDocType(documentKind);setSelectedDocId('');setDocumentQuestion(undefined);}setConnectedDestination(previous=>({step:'delivery',nonce:(previous?.nonce||0)+1}));if(detailRef.current)detailRef.current.open=true;requestAnimationFrame(()=>{detailRef.current?.scrollIntoView({block:'start'});afterOpen?.();});});}
  function go(n:number,afterGo?:()=>void){if(n<3){openMain({step:n===0?'information':'refine',review:n>0});return;}if(n===3)n=4;guard(()=>{analysisNavigation.current=null;if(n!==phase){setFocusQuestionId('');setFocusAnswerId('');setFocusRequirementId('');setBlockerTask(null);setBlockerFocus(undefined);if(n>phase)scrollPositions.current[n]=0;}setPhase(n);sessionStorage.setItem('ra-phase-'+id,String(n));afterGo?.();});}
  function enterDirect(text?:string){go(1,()=>setDirectEntryDraft(current=>({text:text??sessionStorage.getItem('ra-chat-draft-'+id)??'',nonce:(current?.nonce||0)+1})));}
  // Locked editors stay mounted. Return to the prerequisite without requiring a
  // hidden, possibly conflicted draft to be saved or discarded first.
  function returnToPrerequisite(n:number){if(n<3){openMain({step:n===0?'information':'refine',review:n>0});return;}go(n);}
  const projectRef=useRef<Project|null>(null);
  const busyRef=useRef(false);
  const refreshTail = useRef<Promise<void>>(Promise.resolve()), root = '/projects/' + id;
  const mainScroll = useRef<HTMLDivElement | null>(null), scrollPositions = useRef<Record<number, number>>({});
  function formalScrollStart() {
    const container=mainScroll.current, details=detailRef.current;
    if(!container||!details)return 0;
    const header=container.querySelector('.project-bar');
    return Math.max(0,details.getBoundingClientRect().top-container.getBoundingClientRect().top+container.scrollTop-(header?.getBoundingClientRect().height||0));
  }
  useEffect(() => {
    if(mainScroll.current&&detailRef.current?.open)mainScroll.current.scrollTop=formalScrollStart()+(scrollPositions.current[phase]||0);
  }, [phase]);
  // Closing the drawer keeps both its draft (owned here) and the intake mounted.
  // Only leaving the project should offer to discard those unrelated edits.
  useDialog(sourcesOpen, () => {if(!busy)setSourcesOpen(false);});
  useDialog(!!help,()=>{if(!busy)guard(()=>setHelp(null));});
  useDialog(candidateOpen,()=>{if(!busy)guard(()=>setCandidateOpen(false));});
  useDialog(acceptanceOpen,()=>{if(!busy)guard(()=>setAcceptanceOpen(false));});
  async function saveManualSourceText(){if(!sourceText.trim())return;const submitted=sourceText;await mutate('/sources/text',manualPageActive?pastedWebpageReference(manualPageUrl,manualPageTitle,submitted):{title:'粘贴材料',text:submitted,purpose});setSourceText(current=>current===submitted?'':current);setManualPageUrl(manualPagePendingUrl);setManualPageTitle(manualPagePendingUrl?'页面标题待核对':'');setManualPageNotice(manualPagePendingUrl?'已有草稿已保存；现在可粘贴这份网页的实际正文。':'');setManualPageActive(Boolean(manualPagePendingUrl));if(manualPagePendingUrl)setPurpose('reference');setManualPagePendingUrl('');}
  async function saveSourceDraft(){if(sourceText.trim())await saveManualSourceText();if(url.trim()){const submitted=url;if(projectRef.current?.sources.some(s=>s.uri===submitted&&s.parse_status==='failed'))throw Error('该网址已留下读取失败记录。请修改网址或在资料中重新读取。');const source=await mutate('/sources/url',{url:submitted,dynamic,authorized_public:true,purpose}) as Obj;if(source.parse_status==='failed')throw Error('网址已留下读取失败记录，输入已保留。请核对失败原因或重新读取。');setUrl(current=>current===submitted?'':current);}}
  useDirty(id+'-sources',Boolean(sourceText.trim()||url.trim()),'项目资料',saveSourceDraft,()=>{setSourceText('');setUrl('');setManualPageUrl('');setManualPageTitle('');setManualPageNotice('');setManualPagePendingUrl('');setManualPageActive(false);});
  useDirty(id+'-help',Boolean(helpText.trim()),'对象讨论输入',async()=>{await mutate('/sources/text',{text:help?.label+'\n'+helpText,purpose:'goal'});setHelpText('');},()=>setHelpText(''));
  async function saveCandidate(){await mutate('/items',{title:candidateTitle,statement:candidateText,change_type:'new'});setCandidateTitle('');setCandidateText('');setCandidateOpen(false);}
  useDirty(id+'-candidate',Boolean(candidateTitle||candidateText),'候选需求',saveCandidate,()=>{setCandidateTitle('');setCandidateText('');});
  async function saveAcceptance(){if(!acceptanceRequirement)throw Error('请先选择本期需求');const submitted={requirement:acceptanceRequirement,title:acceptanceTitle,text:acceptanceText};await mutate('/items/'+submitted.requirement+'/acceptance',{title:submitted.title,statement:submitted.text});setAcceptanceTitle('');setAcceptanceText('');setAcceptanceRequirement('');setAcceptanceOpen(false);}
  useDirty(id+'-acceptance',Boolean(acceptanceTitle||acceptanceText),'关联验收候选',saveAcceptance,()=>{setAcceptanceTitle('');setAcceptanceText('');setAcceptanceRequirement('');setAcceptanceOpen(false);});
  useDialog(historyOpen, () => setHistoryOpen(false));
  useDialog(renameOpen, () => {if(!busy)setRenameOpen(false);});
  function refresh():Promise<void> {
    // Queue every batch, including mutation-triggered and manual reads. A save
    // must observe its own new revision even when a slow poll was already open.
    const run=refreshTail.current.then(async()=>{
      const records=Promise.allSettled([api<Run[]>(root+'/runs'),api<UserTask[]>(root+'/actions'),api<Obj>(root+'/artifacts')]);
      let project:Project;
      try{project=await api<Project>(root);}catch(e){setFatal(e as ApiError);throw e;}
      // A read already in flight may precede a confirmed write response.
      if(!projectRef.current||project.revision>=projectRef.current.revision){projectRef.current=project;setP(project);onProjectSnapshot?.(project);}setFatal(null);setStaleNotice('');
      const responses=await records;
      const failed:string[]=[];
      responses.forEach((result,index)=>{if(result.status==='rejected'){failed.push(['运行记录','任务列表','产物记录'][index]);return;}if(index===0)setRuns(result.value as Run[]);if(index===1){setTasks(result.value as UserTask[]);setTasksLoaded(true);}if(index===2)setArtifacts(result.value as Obj);});
      setStale(failed);
    });
    refreshTail.current=run.then(()=>{},()=>{});
    return run;
  }
  function onRefreshFail(e: unknown) { setStaleNotice(categoryText[categorizeError(e)]); }
  function retry() { void act(refresh); }
  async function act(fn: () => Promise<unknown>) { if(busyRef.current)return;busyRef.current=true;const finish=beginActivity('正在处理当前操作');setBusy(true);setError('');try{await fn();finish();}catch(e){const message=(e as Error).message;setError(message);finish(message,true);}finally{busyRef.current=false;setBusy(false);} }
  useEffect(() => { if (active) refresh().catch(onRefreshFail); }, [active]);
  useEffect(()=>{if(!active)preparationController.current?.cancel();},[active]);
  useEffect(()=>()=>{preparationController.current?.cancel();},[]);
  useEffect(()=>{preparationController.current?.observe(tasks);},[tasks]);
  const running = tasks.some(t => ['queued','running'].includes(t.status)) || runs.some(r => ['queued','running'].includes(r.status));
  useEffect(() => { if (!running) return; let stopped=false; let timer:ReturnType<typeof setTimeout>;
    async function poll(){try{await refresh();}catch(e){onRefreshFail(e);}finally{if(!stopped)timer=setTimeout(poll,800);}}
    timer=setTimeout(poll,800);return()=>{stopped=true;clearTimeout(timer);};
  }, [running]);
  useEffect(()=>{
    const intent=analysisNavigation.current;
    if(!intent)return;
    if(!active||(!intent.connected&&phase!==0)){analysisNavigation.current=null;return;}
    const task=tasks.find(t=>t.id===intent.taskId);
    if(!task||['queued','running'].includes(task.status)||busy||intent.resolving)return;
    if(!['succeeded','partial'].includes(task.status)){analysisNavigation.current=null;return;}
    intent.resolving=true;
    // The completion record can arrive after the project read in the same poll.
    // Read again before showing the result, and consume only this submitted task.
    void refresh().then(async()=>{
      if(analysisNavigation.current!==intent)return;
      analysisNavigation.current=null;
      if(!currentView.current.active||(!intent.connected&&currentView.current.phase!==0))return;
      const result=projectRef.current;
      if(result&&((result.messages.filter(m=>m.role==='assistant'&&m.stage==='ingest').length>intent.ingestCount)||result.items.length>intent.itemCount||result.questions.length>intent.questionCount)){
        if(intent.connected){openMain({step:'refine'});return;}
        await ensureIntakeSaved();if(currentView.current.active&&currentView.current.phase===0){scrollPositions.current[1]=0;go(1);}
      }
    }).catch(e=>{if(analysisNavigation.current===intent)analysisNavigation.current=null;onRefreshFail(e);});
  },[tasks,active,phase,busy]);
  useEffect(() => {
    if (!sourcesOpen || !focusRefs.length) return;
    const timer = setTimeout(() => { const el = document.getElementById('excerpt-' + focusRefs[0].excerpt_id); const details = el?.closest('details'); if (details) details.open = true; el?.scrollIntoView({ block: 'center' }); el?.classList.add('source-highlight'); }, 50);
    return () => {clearTimeout(timer);document.querySelectorAll('.source-highlight').forEach(el=>el.classList.remove('source-highlight'));};
  }, [sourcesOpen, focusRefs]);
  function editValue(project:Project, proof:EditProof):unknown {
    if(proof.kind==='scope')return project.product_context?.scope_ids||[];
    if(proof.kind==='answers')return Object.fromEntries(Object.keys(proof.mine as Obj).map(id=>[id,project.questions.find(q=>q.id===id)?.answer||'']));
    if(proof.kind==='question_note')return project.questions.find(q=>q.id===proof.id)?.deferred_note||'';
    if(proof.kind==='behavior'){const item=project.items.find(i=>i.id===proof.id);return {values:item?.behavior||{},change_type:item?.change_type||'unspecified'};}
    if(proof.kind==='item'){const item=project.items.find(i=>i.id===proof.id);return item&&{title:item.title,statement:item.statement,change_type:item.change_type||''};}
    if(proof.kind==='context')return contextSnapshot(project);
    if(proof.kind==='intake')return (project as Obj).intake||{};
    return project.questions.find(q=>q.id===proof.id)?.out_of_scope_reason||'';
  }
  const equal=(a:unknown,b:unknown)=>{const canonical=(value:any):any=>Array.isArray(value)?value.map(canonical):value&&typeof value==='object'?Object.fromEntries(Object.keys(value).sort().map(key=>[key,canonical(value[key])])):value;return JSON.stringify(canonical(a))===JSON.stringify(canonical(b));};
  async function mutate(path: string, body: Obj, method = 'POST') {
    const {__edit,...payload}=body;const proof=__edit as EditProof|undefined;
    const current=projectRef.current;
    if(proof&&current&&payload.expected_revision!==undefined&&payload.expected_revision!==current.revision){
      const latest=editValue(current,proof);
      if(!equal(latest,proof.before))throw new DraftConflictError(proof.before,latest,proof.mine);
      payload.expected_revision=current.revision;
    }
    const finish=beginActivity('正在保存并更新页面');
    try{const result=await api(root + path, method, { expected_revision: current?.revision, ...payload });
      // This atomic endpoint returns inline validation issues with HTTP 200.
      // A rejected table wrote nothing and must not produce a saved/refresh notice.
      if(path==='/refinement-confirmation'&&result?.confirmed===false)return result;
      const written=path==='/refinement-confirmation'&&result?.confirmed===true?result.project:result;
      // Preserve the server-confirmed value even if the follow-up read fails.
      // Derived readiness remains visibly stale until the full projection loads.
      if(current&&written?.id===id&&Array.isArray(written.items)&&written.revision>=current.revision){const confirmed=mergeProjectWrite(current,written) as Project;projectRef.current=confirmed;setP(confirmed);}
      else if(current&&written?.id&&typeof written.parse_status==='string'){const confirmed={...current,sources:current.sources.some(s=>s.id===written.id)?current.sources.map(s=>s.id===written.id?written:s):[...current.sources,written]};projectRef.current=confirmed;setP(confirmed);}
      await refresh().catch(e=>{onRefreshFail(e);activityNotice('修改已保存，但最新页面尚未取得。请重试刷新，不要重复提交。',true);});return result;}
    catch(e){if(e instanceof ApiError&&e.status===409&&e.code==='STALE_REVISION'){await refresh().catch(onRefreshFail);const latest=proof&&projectRef.current&&editValue(projectRef.current,proof);if(proof&&latest!==undefined)throw new DraftConflictError(proof.before,latest,proof.mine);}throw e;}finally{finish();}
  }
  async function planAction(action: BusinessAction, optionId?: string, target?: Obj, text = '', retryInput?: Partial<ActionInput>) {
    const current=projectRef.current;
    if (!current) return;
    if (actionBlockReason) throw Error(actionBlockReason);
    const input: ActionInput = { action, message: text, document_type: docType, option_id: optionId || null, max_calls: null, ...(target?{target:{kind:target.kind,id:target.id,...(target.section_id?{section_id:target.section_id}:{})}}:{}),...retryInput,expected_revision:current.revision };
    const plan = await api<ActionPlan>(root + '/actions/plan', 'POST', input);
    setActionError(''); setActionStale(false); setPending({ input, plan, key: crypto.randomUUID() });
  }
  async function benchmarkOperation<T>(operation:()=>Promise<T>):Promise<T>{
    if(busyRef.current||running)throw Error('当前操作正在执行，请等待后再对标。');
    busyRef.current=true;setBusy(true);setError('');
    try{return await operation();}finally{busyRef.current=false;setBusy(false);}
  }
  const importBenchmark=(url:string,dynamic:boolean)=>benchmarkOperation(async()=>{
    return await mutate('/sources/url',{url,dynamic,purpose:'reference',authorized_public:true}) as Obj;
  });
  const prepareBenchmark=(benchmark:BenchmarkInput,message:string)=>benchmarkOperation(async()=>{
    await planAction('organize',undefined,undefined,message,{benchmark});
  });
  function referenceSources(referenceUrl?:string){setFocusRefs([]);if(referenceUrl){if(sourceText.trim()){setManualPagePendingUrl(referenceUrl);setManualPageNotice(`已有未保存的文字草稿，已保留。保存该草稿后会预填 ${referenceUrl} 的来源信息。`);}else{setPurpose('reference');setManualPagePendingUrl('');setManualPageUrl(referenceUrl);setManualPageTitle('页面标题待核对');setManualPageActive(true);setManualPageNotice('已预填来源网址和待核对标题；请粘贴你实际看到的网页正文。');}}else if(!sourceText.trim())setPurpose('reference');setSourcesOpen(true);setTimeout(()=>focusBlockerElement(referenceUrl?'source-paste-'+id:'source-file-add-'+id),80);}
  async function startAction() {
    if (!pending||busyRef.current) return;
    if (actionBlockReason) { setActionError(actionBlockReason); return; }
    if(pending.preparationToken!==undefined){await preparationController.current?.authorize(pending.preparationToken);return;}
    busyRef.current=true;setBusy(true); setActionError('');
    const snapshot=projectRef.current;
    const ingestCount=snapshot?.messages.filter(m=>m.role==='assistant'&&m.stage==='ingest').length||0;
    try { const task=await api<UserTask>(root + '/actions', 'POST', { ...pending.input, plan_hash: pending.plan.plan_hash, idempotency_key: pending.key, authorize: pending.plan.recipients.some(r => r.needs_authorization) });
      if(active&&pending.input.action==='organize'&&!pending.input.target&&(phase===0||!detailRef.current?.open))analysisNavigation.current={taskId:task.id,ingestCount,itemCount:snapshot?.items.length||0,questionCount:snapshot?.questions.length||0,connected:!detailRef.current?.open};
      if(pending.input.benchmark)setBenchmarkSubmission({taskId:task.id,message:pending.input.message});
      if(pending.input.action==='interview'&&task.retry_input?.action==='interview'&&task.retry_input.message===pending.input.message&&
        JSON.stringify((task.retry_input.attachment_source_ids||[]).slice().sort())===JSON.stringify((pending.input.attachment_source_ids||[]).slice().sort())){
        setInterviewSubmission({taskId:task.id,message:task.retry_input.message,attachmentSourceIds:task.retry_input.attachment_source_ids||[]});
      }
      const submitted={...task,retry_input:task.retry_input||pending.input};
      setTasks(current=>[...current.filter(t=>t.id!==task.id),submitted]);recordProgressSubmission(submitted);setTasksLoaded(true);setPending(null);if(pending.input.target){setHelpText('');setHelp(null);}await refresh().catch(e=>{onRefreshFail(e);activityNotice('任务已提交，最新进度暂未取得；请刷新查看，不要重复发起。',true);}); }
    catch(e) { setActionError((e as Error).message); setActionStale(e instanceof ApiError && e.status===409 && ['STALE_REVISION','STALE_PLAN'].includes(e.code)); } finally { busyRef.current=false;setBusy(false); }
  }
  async function replanAction() {
    if(!pending||busyRef.current)return;
    if(pending.preparationToken!==undefined){await preparationController.current?.replan();return;}
    const old=pending;
    busyRef.current=true;setBusy(true);setActionError('');
    try{await refresh();await planAction(old.input.action,old.input.option_id||undefined,old.input.target,old.input.message,old.input);}
    catch(e){setActionError((e as Error).message);}
    finally{busyRef.current=false;setBusy(false);}
  }
  function closeAction(){if(pending?.preparationToken!==undefined)preparationController.current?.cancel();setPending(null);if(actionStale)void act(refresh);}
  function openReviewFindings(documentKind?:string){openDetails(4,documentKind|| (projectRef.current?.documents.prd?'prd':'mrd'),()=>setReviewFocusNonce(n=>n+1));}
  async function ensureIntakeSaved(){const current=projectRef.current;if(current&&!current.product_flow?.[0]?.complete){const intake=(current as Obj).intake||{};await mutate('/intake',{text:intake.text||'',platform:intake.platform||'',preserved:intake.preserved||''});}}
  async function organizeIntake(){const current=projectRef.current;if(current?.sources.some(s=>!s.excluded&&s.excerpts?.length))await ensureIntakeSaved();await planAction('organize');}
  async function saveDirectInput(text:string){await mutate('/intake/direct',{values:{intent:text}});if(!projectRef.current?.product_flow?.[0]?.complete)throw Error('输入已保存，但阶段状态尚未更新；请刷新核对后继续，当前文字保留。');}
  function navigate(tab:string){if(tab==='sources'){setSourcesOpen(true);return;}if(tab==='documents')openDetails(4);else openMain({step:'refine',scope:tab==='scope'});}
  function documentForQuestion(questionId:string){
    const current=projectRef.current;if(!current)return;
    return Object.entries(current.documents).find(([,document])=>documentDiscussionContext(current,tasks,runs,document.id).questions.some(q=>q.id===questionId));
  }
  function documentFollowups(current:Project){
    const document=current.documents[docType];if(!document)return [];
    const questions=documentDiscussionContext(current,tasks,runs,document.id).questions;
    const state=current.product_flow?.find(stage=>stage.step===3),ids=new Set(questions.map(q=>q.id));
    if(!questions.length||!state||state.missing.length&& !state.blockers?.length)return [];
    return (state.blockers||[]).every(row=>row.actions.some(action=>action.question_id&&ids.has(action.question_id)))?questions:[];
  }
  // Document follow-ups retain their reading context; only unrelated work uses
  // the full requirement sheet. The question still needs explicit review.
  function focusDocumentQuestion(kind:string,questionId:string){
    analysisNavigation.current=null;setSelectedDocId('');setDocType(kind);setPhase(4);sessionStorage.setItem('ra-phase-'+id,'4');
    setConnectedDestination(previous=>({step:'delivery',nonce:(previous?.nonce||0)+1}));
    if(detailRef.current)detailRef.current.open=true;
    setDocumentQuestion(previous=>({id:questionId,nonce:(previous?.nonce||0)+1}));
  }
  function openQuestion(questionId?:string){
    const document=questionId&&documentForQuestion(questionId);
    if(document){guard(()=>focusDocumentQuestion(document[0],questionId!));return;}
    openMain({step:'refine',questionId});
  }
  function openClause(item:Obj,behavior=false,questionId='',afterNavigate?:()=>void){openMain({step:'refine',itemId:item.id,questionId:questionId||undefined});}
  function returnToAnswer(){openMain({step:'refine',questionId:returnAnswerId||undefined});}
  function resolveBlocker(action:BlockerAction,row:Blocker,originPhase=phase){guard(()=>{
    try{
      const current=projectRef.current||p;if(!current)return;
      if(action.question_id){const document=documentForQuestion(action.question_id);if(document){focusDocumentQuestion(document[0],action.question_id);return;}}
      if(originPhase>=4&&action.kind==='stage'&&action.step===3){
        const questions=documentFollowups(current);
        if(questions.length){focusDocumentQuestion(docType,questions.find(q=>!q.decision_coverage_complete)?.id||questions[0].id);return;}
      }
      const mainTarget=refinementDestination(current,action);
      if(mainTarget){showMain();setConnectedDestination(previous=>({...mainTarget,nonce:(previous?.nonce||0)+1}));if(action.kind==='candidate')setCandidateOpen(true);if(action.kind==='add_acceptance'){setAcceptanceRequirement(action.item_id||'');setAcceptanceOpen(true);}return;}
      if(action.kind==='source'){setFocusRefs([]);setSourcesOpen(true);return;}
      if(detailRef.current)detailRef.current.open=true;
      if(originPhase===0&&action.kind==='context'){setDirectInputNonce(n=>n+1);return;}
      const destination=blockerDestination(current,action);
      analysisNavigation.current=null;setBlockerTask({title:row.title,phase:originPhase});setReturnAnswerId('');
      setFocusQuestionId('');setFocusAnswerId('');setFocusRequirementId('');
      setPhase(destination.phase);sessionStorage.setItem('ra-phase-'+id,String(destination.phase));
      setBlockerFocus(previous=>({action,nonce:(previous?.nonce||0)+1}));
      if(action.kind==='source'){setFocusRefs([]);setSourcesOpen(true);}
      if(action.kind==='candidate')setCandidateOpen(true);
      if(action.kind==='add_acceptance'&&destination.phase===2){setAcceptanceRequirement(action.item_id||'');setAcceptanceOpen(true);}
      if(action.kind==='acceptance'){
        setFocusClauseIds(destination.itemIds||[]);setFocusClauseId(destination.itemIds?.[0]||'');setFocusClauseNonce(n=>n+1);
      }
      if(action.kind==='question'){setFocusQuestionId(action.question_id!);setFocusQuestionNonce(n=>n+1);}
      if(action.kind==='answer'){setFocusAnswerId(action.question_id!);setFocusAnswerNonce(n=>n+1);}
      if(action.kind==='document'||action.kind==='review'){
        setSelectedDocId('');setDocType(action.document|| (current.documents.prd?'prd':'mrd'));
        if(action.kind==='review')setReviewFocusNonce(n=>n+1);
        else{
          const kind=action.document!,doc=current.documents[kind];
          const stale=!!doc&&(doc.brief_hash!==current.hashes.brief_hash||(current as Obj).stale_document_kinds?.includes(kind));
          const target=!doc||stale?'document-generate-':row.code==='document_review_pending'?'document-review-':'document-reading-';
          setTimeout(()=>focusBlockerElement(target+id+'-'+kind),80);
        }
      }
      if(action.kind==='stage')setTimeout(()=>{if(!focusBlockerElement('blockers-'+id+'-'+destination.phase))focusBlockerElement('checkpoint-continue-'+id+'-'+destination.phase);},80);
    }catch(e){setError((e as Error).message);}
  });}
  function returnToBlockers(){if(!blockerTask)return;if(blockerTask.phase<3){openMain({step:'refine',review:true});return;}guard(()=>{const target=blockerTask.phase;setPhase(target);sessionStorage.setItem('ra-phase-'+id,String(target));setBlockerTask(null);setTimeout(()=>focusBlockerElement('blockers-'+id+'-'+target),80);});}
  function blockedCheckpoint(latest:Project){setP(latest);projectRef.current=latest;setTimeout(()=>focusBlockerElement('blockers-'+id+'-'+phase),80);}
  function openHelp(target:Obj){guard(()=>{setHelp(target);setHelpText('');});}
  function stageAction(s: string) { const action: Record<string, BusinessAction> = { ingest: 'organize', vision: 'organize', brainstorm: 'explore', clarify: 'clarify', ui: 'prototype', prd: 'document', review: 'review', change: 'change' }; act(() => planAction(action[s] || 'organize')); }
  function recordProgressSubmission(task:UserTask){progress.publish({projectId:id,projectName:projectRef.current?.name||id,tasks:[task],busy:false,uncertain:false},task.id);}
  function retryUserTask(task?:UserTask){const input=task?.retry_input;if(!input?.action||typeof input.message!=='string'||typeof input.document_type!=='string'||(['clarify','change'].includes(input.action)&&Boolean(input.message)&&!input.target)){activityNotice('无法恢复原任务对象和输入；请回到原对象重新填写后发起。',true);return;}void act(()=>planAction(input.action!,undefined,undefined,'',input));}
  function recoverTask(task:UserTask){guard(()=>{let restored=false;void act(async()=>{
    if(actionBlockReason||running)throw Error(actionBlockReason||'当前项目仍有任务运行，请稍后恢复。');
    await api(root+'/actions/'+task.id+'/recover-draft','POST',{expected_revision:projectRef.current?.revision});
    await refresh();restored=true;
  }).then(()=>{if(restored){openMain({step:'refine'});
    activityNotice('已恢复待核对草稿，没有重新调用 AI；请核对并决定是否采纳。');
  }});});}
  function viewTaskResult(task:UserTask){
    if(canRecoverDraft(task)){recoverTask(task);return;}
    const documentKind=['mrd','prd'].includes(task.retry_input?.document_type||'')?task.retry_input!.document_type:undefined;
    if(task.action==='document'){openDetails(4,documentKind);return;}
    if(task.action==='review'){openReviewFindings(documentKind);return;}
    const target=task.retry_input?.target;
    if(target?.kind==='question'){openQuestion(target.id);return;}
    if(target?.kind==='item'){openMain({step:'refine',itemId:target.id});return;}
    if(target?.kind==='document'){
      const kind=Object.entries(projectRef.current?.documents||{}).find(([,document])=>document.id===target.id)?.[0];
      openDetails(4,kind);return;
    }
    if(['interview','organize','clarify','change','explore'].includes(task.action)){openMain({step:task.action==='interview'?'information':'refine'});return;}
    const destination:Record<BusinessAction,number>={interview:0,organize:1,explore:1,clarify:2,change:2,prototype:4,document:4,review:4};
    go(destination[task.action]);
  }
  progressControls.current={refresh,cancel:async task=>{await api(root+'/actions/'+task.id+'/cancel','POST');await refresh();},open:(task,retry,afterOpen)=>onOpen(()=>{afterOpen();if(retry)retryUserTask(task);else viewTaskResult(task);})};
  const actionBlockReason = fatal ? '最新项目内容读取失败，请重试刷新后再发起任务。' : !tasksLoaded ? '正在读取任务状态，请稍候。' : compat.status === 'incompatible' ? '后端缺少必需接口能力（' + compat.missing.join('、') + '），发起动作已暂停；请重启服务并重新加载本标签页' : stale.includes('任务列表') ? (tasksLoaded ? '任务状态读取失败，当前状态待刷新；页面显示的是此前取得的任务记录，发起动作已暂停，请重试刷新' : '任务状态尚未取得，发起动作已暂停；请先重试刷新') : '';
  preparationDependencies.current={
    active:()=>currentView.current.active,blocked:()=>actionBlockReason,
    status:()=>api<PreparationStatus>(root+'/refinement-preparation'),tasks:()=>api<UserTask[]>(root+'/actions'),
    plan:input=>api<ActionPlan>(root+'/actions/plan','POST',input),
    submit:async request=>{if(busyRef.current)throw Error('当前操作尚未完成，请稍候再整理。');busyRef.current=true;setBusy(true);setActionError('');try{return await api<UserTask>(root+'/actions','POST',{
      ...request.input,plan_hash:request.plan.plan_hash,idempotency_key:request.key,authorize:request.authorize});
      }finally{busyRef.current=false;setBusy(false);}},
    refresh,record:task=>{setTasks(current=>[...current.filter(t=>t.id!==task.id),task]);recordProgressSubmission(task);setTasksLoaded(true);},
    navigate:consumeIntent=>{guard(()=>{if(!consumeIntent())return;showMain();setConnectedDestination(previous=>({step:'refine',review:true,nonce:(previous?.nonce||0)+1}));});},state:setPreparation,
    authorization:request=>{setActionError('');setActionStale(false);if(request)setPending(request);else setPending(current=>current?.preparationToken!==undefined?null:current);},
    error:(message,isStale)=>{setActionError(message);setActionStale(isStale);},key:()=>crypto.randomUUID(),
    receipt:()=>{try{const raw=sessionStorage.getItem('ra-refinement-submission-'+id);if(!raw)return null;const value=JSON.parse(raw) as PreparationSubmission;
      return value.input?.action==='organize'&&typeof value.key==='string'&&typeof value.plan?.plan_hash==='string'?value:null;}catch{return null;}},
    saveReceipt:request=>{if(request)sessionStorage.setItem('ra-refinement-submission-'+id,JSON.stringify(request));else sessionStorage.removeItem('ra-refinement-submission-'+id);},
  };
  if (!p) {
    if (fatal) {
      const category = categorizeError(fatal);
      return <div hidden={!active} className="workspace"><article className="card" role="alert">
        <h2>{categoryText[category]}</h2>
        <p>{category === 'route-missing' ? '当前服务可能缺少此页面所需的接口；后端代码变更后需重启服务，本标签页需重新加载。' : category === 'session' ? '页面连接状态已失效，请重新加载后再进入项目。' : '可以重试加载，或返回项目列表。'}</p>
        <div className="toolbar"><button disabled={busy} onClick={retry}>{busy?'正在重新加载…':'重新加载'}</button><button onClick={onExit}>返回项目列表</button></div>
        <details><summary>诊断信息</summary><p>HTTP 状态：{fatal.status || '无响应'} · 接口：{fatal.path} · 错误码：{fatal.code} · 类型：{fatal.kind}</p></details>
      </article></div>;
    }
    return <div hidden={!active} className="workspace"><p>正在读取项目内容…</p></div>;
  }
  const direction = p.options.find(o => o.direction_status === 'selected');
  const phaseAccess=stageAccess(p,phase);
  const phaseActions=[['interview','organize'],['organize','explore'],['clarify','change'],['prototype','change'],['document','review'],['review']][phase];
  const latest=tasks.filter(t=>phaseActions.includes(t.action)||Boolean(t.retry_input?.target)&&(phase===1&&t.retry_input?.target?.kind==='item'||phase===2&&t.retry_input?.target?.kind==='question'||phase===4&&t.retry_input?.target?.kind==='document')).slice(-1)[0];
  const localTask=Boolean(latest?.retry_input?.target);
  const retryTask=()=>retryUserTask(latest);
  const shared = { busy, project: p, setTab: () => {}, onOption: (option: Obj, action: string) => setOptionDecision({ option, action }), onItem: (item: Obj, status: string) => act(() => mutate('/items/' + item.id, { selection_status: status })), onIncludeScope: (item:Obj,reason:string,revision:number) => mutate('/items/'+item.id+'/include-scope',{expected_revision:revision,reason}), onEditItem: (item: Obj, statement: string, fields: Obj = {}) => mutate('/items/' + item.id, { selection_status: item.selection_status, statement, ...fields }), onStage: stageAction, onActivate: (cid: string) => act(() => mutate('/ui-candidates/' + cid + '/activate', {})), onReject: (cid: string) => act(() => mutate('/ui-candidates/' + cid + '/reject', {})), onNavigate: navigate, previewUrl: '/api' + root + '/prototype', runs, models, onCancel: (rid: string) => act(async () => { await api(root + '/runs/' + rid + '/cancel', 'POST'); await refresh(); }), onResume: (rid: string) => stageAction(runs.find(r => r.id === rid)?.stage || 'ingest'), embedded: true, onSource: (refs: Obj[]) => { setFocusRefs(refs); setSourcesOpen(true); }, onPreviewOption: setPreviewOption };
  const currentPreviewOption = previewOption && (p.options.find(o => o.id === previewOption.id) || previewOption);
  const intakeTask=tasks.filter(t=>t.action==='organize').slice(-1)[0];
  const intakeLabel=intakeStatusLabel(p,intakeTask);
  const documentTask=documentTaskForType(tasks,docType);
  const documentTaskStatus=documentTask?<TaskStatus task={documentTask} busy={busy} uncertain={Boolean(fatal)||stale.includes('任务列表')} onRefresh={retry}
    onCancel={()=>act(async()=>{await api(root+'/actions/'+documentTask.id+'/cancel','POST');await refresh();})}
    onRetry={()=>retryUserTask(documentTask)}/>:stale.includes('任务列表')?<p role="status" className="notice">当前 {docType.toUpperCase()} 的任务状态暂未取得，请刷新核对，不要重复发起。<button disabled={busy} onClick={retry}>刷新任务状态</button></p>:undefined;
  const taskStatus = latest&&(phase===0||(phase===1&&latest.action==='organize')||localTask||latest.status!=='succeeded'||running)&&<><TaskStatus task={latest} busy={busy} uncertain={Boolean(fatal)||stale.includes('任务列表')} onRefresh={retry} onCancel={()=>act(async()=>{await api(root+'/actions/'+latest.id+'/cancel','POST');await refresh();})} onRetry={retryTask} onRecover={canRecoverDraft(latest)?()=>recoverTask(latest):undefined} onOpenDraft={phase!==1&&latest.action==='organize'&&!localTask&&stageAccess(p,1).allowed?()=>go(1):undefined} onViewResult={!localTask&&!(phase===0&&(p.messages.some(m=>m.role==='assistant'&&m.stage==='ingest')||p.items.length>0||p.questions.length>0))&&['succeeded','partial'].includes(latest.status)?()=>viewTaskResult(latest):undefined}/>{!latest.retry_input&&['failed','cancelled','paused_budget'].includes(latest.status)&&<p className="notice">原任务输入不可恢复。请从原对象重新输入并核对范围后发起。</p>}</>;
  const localDiscussionBlocker=(row:Blocker):Blocker =>
    ['prerequisite_stage','stage_incomplete'].includes(row.code)&&row.actions.some(action=>action.kind==='stage'&&action.step===3)&&documentFollowups(p).length
      ?{...row,code:'document_discussion',title:'本篇文档的讨论补充',message:'章节讨论有待核对内容。请在文档页处理本次补充，已有需求确认会保留。',actions:row.actions.map(action=>({...action,label:'处理本次文档讨论'}))}:row;
  const currentBlockers:Blocker[]=(phase===5&&!p.confirmation_blockers?p.confirmation_issues.map((message,n)=>({key:'legacy-'+n,code:'legacy',title:'交接条件',message,actions:[]})):stageBlockers(p,phase)).map(localDiscussionBlocker);
  return <div hidden={!active} className="project-workspace" ref={mainScroll} onScroll={e=>{if(e.target===e.currentTarget&&detailRef.current?.open)scrollPositions.current[phase]=Math.max(0,e.currentTarget.scrollTop-formalScrollStart());}}>
    <header className="project-bar"><div className="project-identity"><h1 title={p.name}>{p.name}</h1><small>内容草稿 v{p.revision} · {p.product_flow?.[5]?.complete?'当前版本已建立确认基线':p.active_baseline_id?'有历史确认基线，当前草稿须重新核对':'尚无正式确认基线'}</small></div><div className="toolbar"><button onClick={() => { setError(''); setFocusRefs([]); setSourcesOpen(true); }}>项目资料 · {p.sources.length}</button><button onClick={() => act(async () => { setHistory(await api<Obj[]>(root + '/history')); setHistoryOpen(true); })}>历史版本</button><button onClick={() => { setRename(p.name); setRenameOpen(true); }}>重命名</button></div></header>
    {compat.status === 'incompatible' && <div className="error banner" role="alert">后端缺少必需接口能力（{compat.missing.join('、')}），前后端版本可能不兼容；请重启服务并重新加载本标签页。发起动作已暂停，查看与返回不受影响。</div>}
    {compat.status === 'unverified' && !compatDismissed && <div className="notice banner" role="status">后端未提供能力信息，兼容性未核实；如遇功能异常，后端代码变更后需重启服务、标签页需重新加载。<button onClick={() => setCompatDismissed(true)}>知道了</button></div>}
    {staleNotice && <div className="error banner" role="alert">最新状态尚未取得：{staleNotice}<button onClick={retry}>重试</button><button onClick={() => setStaleNotice('')}>关闭</button></div>}
    {!staleNotice && stale.length > 0 && <div className="notice banner" role="status">部分状态未能更新（{stale.join('、')}）；页面显示的是已取得的最近内容。<button onClick={retry}>重试</button></div>}
    {error && <div className="error banner" role="alert">{error}<button onClick={() => act(refresh)}>刷新内容并保留输入</button><button onClick={() => setError('')}>关闭</button></div>}
    <ConnectedWorkspace deliveryBlockers={p.confirmation_blockers?.map(localDiscussionBlocker)} workspaceRef={connectedRef} active={active} id={id} project={p} tasks={tasks} busy={busy} running={running} actionBlockReason={actionBlockReason} mutate={mutate}
      destination={connectedDestination} onMainNavigation={showMain}
      preparation={preparation} onPrepareRefinement={retry=>{analysisNavigation.current=null;return preparationController.current!.prepare(retry);}}
      onCancelPreparation={()=>preparationController.current?.cancel()}
      onOrganize={instruction=>act(()=>planAction('organize',undefined,undefined,instruction||''))}
      onClarify={questionId=>act(()=>planAction('clarify',undefined,questionId?{kind:'question',id:questionId}:undefined))}
      onRecommendRequirement={(requirementId,instruction)=>act(()=>planAction('clarify',undefined,{kind:'item',id:requirementId},instruction))}
      onSources={refs=>{setFocusRefs(refs||[]);setSourcesOpen(true);}}
      chatPanel={(beforeSend,onDirect,onViewDraft)=><IntakeChat p={p} model={models.model||{}} busy={busy||running||['checking','running','authorization'].includes(preparation.state)} blocked={actionBlockReason} tasks={tasks} status={null}
        beforeSend={async()=>{await beforeSend();return projectRef.current||p;}}
        onRefreshProject={async()=>{await refresh();return projectRef.current||p;}}
        onPrepareInterview={async(message,currentProject,attachmentSourceIds)=>{if(projectRef.current?.revision!==currentProject.revision)throw Error('项目内容已更新，请刷新后重新核对本次对话范围。');await planAction('interview',undefined,undefined,message,{attachment_source_ids:attachmentSourceIds});}}
        onRefresh={retry} onStarted={async task=>{setTasks(current=>[...current.filter(t=>t.id!==task.id),task]);recordProgressSubmission(task);await refresh().catch(onRefreshFail);}}
        onSources={()=>setSourcesOpen(true)} onOrganize={()=>{void beforeSend().then(()=>act(()=>planAction('organize'))).catch(e=>setError((e as Error).message));}}
        onDirectSave={saveDirectInput} onDirectEntry={onDirect} go={n=>{if(n===1)onViewDraft();}}
        onContinue={onViewDraft} continuing={['checking','running','authorization'].includes(preparation.state)}
        onReferenceSources={referenceSources} onReferenceImport={importBenchmark} onBenchmark={prepareBenchmark} benchmarkSubmission={benchmarkSubmission} interviewSubmission={interviewSubmission}/>}
      onCreateRequirement={()=>setCandidateOpen(true)} onCreateAcceptance={requirementId=>{setAcceptanceRequirement(requirementId||'');setAcceptanceOpen(true);}}
      onDelivery={()=>openDetails(5)} onDocuments={()=>openDetails(4)} onRefresh={retry}
      onResolveBlocker={(action,row)=>resolveBlocker(action,row,5)}
      taskPanel={task=><TaskStatus task={task} busy={busy} uncertain={Boolean(fatal)||stale.includes('任务列表')} onRefresh={retry}
        onCancel={()=>act(async()=>{await api(root+'/actions/'+task.id+'/cancel','POST');await refresh();})} onRetry={()=>retryUserTask(task)}
        onRecover={canRecoverDraft(task)?()=>recoverTask(task):undefined} onViewResult={()=>viewTaskResult(task)}/>}/>
    <details ref={detailRef} className="connected-details"><summary>正式文档与确认（按需展开）</summary>
    <div className="connected-detail-return"><p>在这里阅读文档并处理章节讨论。已有需求确认会保留；需要调整整体范围时可返回完善需求。</p><button type="button" onClick={()=>openMain({step:'refine'})}>修改整体需求</button></div>
    <nav className="connected-formal-nav" aria-label="正式文档与确认"><button type="button" aria-current={phase===4?'page':undefined} onClick={()=>go(4)}>正式文档</button><button type="button" aria-current={phase===5?'page':undefined} onClick={()=>go(5)}>正式确认</button></nav>
    <div className="workflow-grid">
      <section className="workflow-main">
        <div className="workflow-page" data-phase={phase}><div className="workflow-heading"><div className="task-icon" aria-hidden="true"><Icon name="document"/></div><p className="eyebrow">第 3 / 3 步 · 确认交付 · {phase===5?'确认版本':'核对文档'}</p><h2>{phases[phase]}</h2><p className="muted">{['可以随便聊聊，资料也可随时补充。','纠正理解偏差，确定这次改什么、保留什么。','回答会影响业务行为的问题，未知不会被自动补齐。','对照原页面，核对本次增量与操作意图。','分别阅读并核对 MRD、PRD 的当前版本。','复核范围与交付版本，再由服务端建立确认基线。'][phase]}</p>{phase>0&&phase<3&&<aside className="task-guidance"><Icon name="target"/><div><b>{['先提供已知信息，缺失项稍后澄清','核对整理结果','为什么需要澄清这些问题？'][phase]}</b><p>{['我们将梳理现状、改动目标与约束条件，再由你核对。','分析建议尚非业务决定，请检查现状、目标、保持项和不在本期的范围。','这些问题涉及业务规则、流程和约束，直接影响后续设计、开发与验收。'][phase]}</p></div></aside>}{phase===4&&<aside className="document-version"><span>当前底稿</span><b>v{p.revision}</b><small>{(p as any).document_review_status?.[docType]?.reviewed?(p.product_flow?.[4]?.missing?.length?'此文档已核对 · 仍有交付阻塞':'此文档已核对 · 可继续检查'):'当前文档待核对'}</small></aside>}</div>
        <StageLock p={p} phase={phase} busy={busy||running} onGo={returnToPrerequisite} onRefresh={retry}/>{phaseAccess.allowed&&phase!==0&&!(phase===4&&(latest?.action==='document'||latest?.retry_input?.target?.kind==='document'))&&taskStatus}
        <div hidden={phase!==0}><p className="notice">聊天与直接填写在上方“提供信息”中共用同一项目草稿。</p></div>
        <BlockerPanel id={'blockers-'+id+'-'+phase} rows={phaseAccess.allowed?currentBlockers:[]} busy={busy||running} onResolve={resolveBlocker} onRefresh={retry}/>
        {phaseAccess.allowed&&blockerTask&&<p className="blocker-return" role="status">正在处理：{blockerTask.title}。<button type="button" disabled={busy||running} onClick={returnToBlockers}>返回阻塞清单</button></p>}
        {phaseAccess.allowed&&returnAnswerId&&(phase===1||phase===2)&&<p className="notice">正在核对回答关联条款。保存并采纳后，可返回原回答重新核对。<button type="button" disabled={busy||running} onClick={returnToAnswer}>返回原回答核对</button></p>}
        <div className="document-stage" hidden={phase!==4||!stageAccess(p,4).allowed} id={'document-result-'+id}>
          <DocumentStudio focusReviewNonce={reviewFocusNonce} taskStatus={documentTaskStatus} p={p} docType={docType} setDocType={next=>guard(()=>{setDocType(next);setSelectedDocId('');setDocumentQuestion(undefined);})} setSelectedDocId={next=>{if(next!==selectedDocId)guard(()=>setSelectedDocId(next));}} selectedDocId={selectedDocId} artifacts={artifacts} setStage={setStage} setShowAdvanced={setShowAdvanced} setTab={navigate} setOutcome={()=>{}} root={root} act={act} mutate={mutate} busy={busy||running} onHelp={openHelp} onGenerate={()=>act(()=>planAction('document'))} onSource={shared.onSource} onClarify={openQuestion}
            discussionPanel={p.documents[docType]&&<DocumentDiscussionPanel project={p} tasks={tasks} runs={runs} document={p.documents[docType]} busy={busy} running={running} mutate={mutate} focusQuestion={documentQuestion} onRefresh={retry} onSources={refs=>shared.onSource(refs||[])} onScope={()=>openMain({step:'refine',scope:true})} onClarify={questionId=>act(()=>planAction('clarify',undefined,{kind:'question',id:questionId,label:p.questions.find(q=>q.id===questionId)?.question||'章节补充问题'}))}/>}/>
          <ProductCheckpoint onBlocked={blockedCheckpoint} p={p} phase={4} busy={busy||running} mutate={mutate} go={go} getProject={()=>projectRef.current||p}/>
        </div>
        <section hidden={phase!==5||!stageAccess(p,5).allowed} id={'confirmation-'+id}><ConfirmationPanel onOpenDocument={kind=>openDetails(4,kind)} onClarify={openQuestion} p={p} busy={busy} setBusy={setBusy} setError={setError} setTab={navigate} setStage={setStage} setShowAdvanced={setShowAdvanced} root={root} act={act} refresh={refresh} onReview={()=>act(()=>planAction('review'))} onResolveReview={()=>openReviewFindings()}/></section>
        </div>
      </section>
    </div>
    </details>
    {phaseAccess.allowed&&help&&<div className="drawer-backdrop"><section className="source-drawer context-help" role="dialog" aria-modal="true" aria-label="当前对象的 AI 帮助"><button onClick={()=>guard(()=>setHelp(null))}>关闭帮助</button><p className="eyebrow">只围绕当前对象</p><h2>{help.label}</h2><small>{help.id}{help.section_id?' · '+help.section_id:''}</small><p>{help.kind==='document'?'文档局部修改需满足当前文档任务的前置条件；结果仍需核对。':'可在本步核对前求助；结果形成待核对候选，不会自动回答问题、采纳条目或确认范围。'}</p><label>希望怎样调整或澄清<textarea rows={5} value={helpText} onChange={e=>setHelpText(e.target.value)}/></label><button className="primary" disabled={busy||running||!helpText.trim()} onClick={()=>act(()=>planAction(help.kind==='document'||help.kind==='ui'?'change':'clarify',undefined,help,helpText))}>核对本次讨论任务</button><details><summary>已保存的讨论历史 · {p.messages.length}</summary>{p.messages.map((m,n)=><article key={n}><b>{m.role==='user'?'用户输入':'助手建议'} · {m.stage}</b><p>{m.text}</p></article>)}</details></section></div>}
    {candidateOpen&&<div className="modal-backdrop"><form className="modal" role="dialog" aria-modal="true" aria-label="补充候选需求" onSubmit={e=>{e.preventDefault();act(saveCandidate);}}><h2>补充一条候选需求</h2><p>保留你的原文和来源；保存不会自动采纳。</p><label>需求名称<input disabled={busy} required maxLength={200} value={candidateTitle} onChange={e=>setCandidateTitle(e.target.value)}/></label><label>需求原文<textarea disabled={busy} required maxLength={6000} value={candidateText} onChange={e=>setCandidateText(e.target.value)}/></label>{error&&<p role="alert">{error}</p>}<div className="toolbar"><button type="button" disabled={busy} onClick={()=>guard(()=>setCandidateOpen(false))}>取消</button><button disabled={busy} className="primary">保存候选</button></div></form></div>}
    {acceptanceOpen&&<div className="modal-backdrop"><form className="modal" role="dialog" aria-modal="true" aria-label="补充关联验收条件" onSubmit={e=>{e.preventDefault();act(saveAcceptance);}}><h2>补充关联验收条件</h2><p>请写明可观察的操作和预期结果。保存为候选，仍需在本步“待采纳候选”中明确采纳。</p><label>关联的本期需求<select required disabled={busy} value={acceptanceRequirement} onChange={e=>setAcceptanceRequirement(e.target.value)}><option value="">选择需求</option>{p.items.filter(i=>i.kind==='requirement'&&i.applies_to==='to_be'&&p.product_context?.scope_ids?.includes(i.id)).map(i=><option key={i.id} value={i.id}>{i.id}｜{i.title}</option>)}</select></label><label>验收名称<input required disabled={busy} maxLength={200} value={acceptanceTitle} onChange={e=>setAcceptanceTitle(e.target.value)}/></label><label>验收原文<textarea required disabled={busy} maxLength={6000} value={acceptanceText} onChange={e=>setAcceptanceText(e.target.value)}/></label>{error&&<p role="alert" className="error">{error}</p>}<div className="toolbar"><button type="button" disabled={busy} onClick={()=>guard(()=>setAcceptanceOpen(false))}>取消</button><button disabled={busy} className="primary">保存验收候选</button></div></form></div>}
    {sourcesOpen && <div className="drawer-backdrop"><section className="source-drawer" role="dialog" aria-modal="true" aria-label="项目资料"><button disabled={busy} onClick={() => setSourcesOpen(false)}>关闭项目资料</button><SourcePanel focusRefs={focusRefs} p={p} models={models} purpose={purpose} setPurpose={setPurpose} sourceText={sourceText} setSourceText={setSourceText} manualPageActive={manualPageActive} manualPageUrl={manualPageUrl} setManualPageUrl={setManualPageUrl} manualPageTitle={manualPageTitle} setManualPageTitle={setManualPageTitle} manualPageNotice={manualPageNotice} onSaveText={saveManualSourceText} url={url} setUrl={setUrl} dynamic={dynamic} setDynamic={setDynamic} busy={busy} act={act} error={error} mutate={mutate} refresh={refresh} root={root} pendingGrant={null} setPendingGrant={() => {}} guided /></section></div>}
    {historyOpen && <div className="drawer-backdrop"><section className="source-drawer" role="dialog" aria-modal="true" aria-label="历史版本"><button onClick={() => setHistoryOpen(false)}>返回当前任务</button><VersionHistory history={history} /><details><summary>完整讨论记录 · {p.messages.length}</summary>{p.messages.map((m,n)=><article className="card" key={n}><b>{m.role==='user'?'用户输入':'助手建议'} · {m.stage}</b><p>{m.text}</p></article>)}</details></section></div>}
    {renameOpen && <div className="modal-backdrop"><form className="modal" role="dialog" aria-label="重命名项目" onSubmit={e => { e.preventDefault(); if (!rename.trim()) return; act(async () => { await mutate('', { name: rename.trim() }, 'PATCH'); setRenameOpen(false); await onProjectsChanged(); }); }}><h2>重命名项目</h2><label>项目名称<input value={rename} onChange={e => setRename(e.target.value)} /></label><button type="button" onClick={() => setRenameOpen(false)}>取消</button><button disabled={!rename.trim() || busy}>保存</button></form></div>}
    {pending && <ActionDialog plan={pending.plan} input={pending.input} error={actionError} busy={busy} stale={actionStale} onReplan={replanAction} onClose={closeAction} onSubmit={startAction} />}
    {phaseAccess.allowed && optionDecision && <OptionDecision option={optionDecision.option} action={optionDecision.action} p={p} submit={mutate} onClose={() => setOptionDecision(null)} />}
  </div>;
}
