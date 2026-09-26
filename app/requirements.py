"""Stable requirement identities and a portable projection of the existing store."""
import copy
from .core import digest, require, now, read_json, KIT
import jsonschema

TRACKED = ('requirement','rule','acceptance')
CONTENT_FIELDS = ('kind','title','statement','applies_to','epistemic_status','source_refs','related_refs','change_type','behavior','scope_evidence','classification_reason')
RELATIONS = ('parent_of','refines','replaces','depends_on','implements','verifies')


def delivery_items(p):
    selected=[i for i in p['items'] if i['selection_status']=='selected' and i['applies_to']=='to_be']
    scope=p.get('product_context',{}).get('scope_ids')
    if scope is None:return selected
    ids=set(scope)
    linked={r for i in selected if i['id'] in ids for r in i.get('related_refs',[])}
    return [i for i in selected if (i['id'] in ids if i['kind']=='requirement' else
        i['kind'] not in TRACKED or i['id'] in linked or bool(ids & set(i.get('related_refs',[]))))]


def namespace(p, item=None):
    return (item or {}).get('source_namespace') or 'urn:requirements-agent:project:'+p['id']


def requirement_ref(p, item):
    return dict(type=item['kind'],namespace=namespace(p,item),id=item['id'],version=item.get('content_version',1))


def item_content(item):
    return {k:copy.deepcopy(item.get(k)) for k in CONTENT_FIELDS}


def reconcile_items(before, p):
    """Called in the existing transaction; no read migration of historic payloads."""
    previous={i['id']:i for i in before['items']}
    ids=[i['id'] for i in p['items']]
    require(len(ids)==len(set(ids)),'IDENTITY_CONFLICT','同一项目出现重复条目编号，未保存',409)
    changes=[]
    for item in p['items']:
        if item['kind'] not in TRACKED:continue
        old=previous.get(item['id'])
        if old is not None:
            require(namespace(before,old)==namespace(p,item),'IDENTITY_CONFLICT','不能改变已有需求的来源身份',409)
            if item_content(old)==item_content(item):
                require(item.get('content_version',1)==old.get('content_version',1),'IDENTITY_CONFLICT','内容未变不能自行改版本',409)
                continue
        item['source_namespace']=namespace(before,old) if old else namespace(p,item)
        item['content_version']=(old.get('content_version',1)+1) if old else 1
        changes.append(dict(requirement_id=item['id'],source_namespace=item['source_namespace'],
                            version=item['content_version'],before=copy.deepcopy(old),after=copy.deepcopy(item),created=now()))
    return changes


def exchange(p, baseline_id=None):
    """No generated content and no inferred downstream implementation/test results."""
    entries=[]
    for item in p['items']:
        if item['kind'] not in TRACKED:continue
        content=item_content(item)
        entries.append(dict(**requirement_ref(p,item),project_id=p['id'],title=item['title'],
            statement=item['statement'],status=item['selection_status'],content=content,
            content_hash=digest(content),version_origin='recorded' if 'content_version' in item else 'legacy_v1',
            change_type=item.get('change_type') or ('existing' if item['applies_to']=='as_is' else 'unspecified'),
            source_refs=copy.deepcopy(item.get('source_refs',[]))))
    refs={i['id']:requirement_ref(p,i) for i in p['items'] if i['kind'] in TRACKED}
    relations=copy.deepcopy(p.get('requirement_relations',[]))
    for item in p['items']:
        if item['kind']=='acceptance':
            related=set(item.get('related_refs',[])) | {i['id'] for i in p['items'] if i['kind']=='requirement' and item['id'] in i.get('related_refs',[])}
            for rid in sorted(related):
                if rid in refs and refs[rid]['type']=='requirement':
                    relation=dict(type='verifies',source=refs[item['id']],target=refs[rid])
                    if relation not in relations:relations.append(relation)
    for r in relations:
        r['evidence_status']='linked_only'
        r['validity']='current' if all(refs.get(x['id'])==x for x in (r['source'],r['target']) if x['type'] in TRACKED) else 'needs_review'
    artifacts=[]
    for kind,doc in p['documents'].items():
        snapshot=doc.get('item_snapshot',[])
        referenced={rid for s in doc['content']['sections'] for b in s['blocks'] for rid in b['ref_ids']}
        artifacts.append(dict(type=kind,id=doc['id'],version=doc.get('document_version'),draft_revision=doc['draft_revision'],
            content_hash=digest(doc['content']),requirements=[requirement_ref(p,i) for i in snapshot if i['id'] in referenced and i['kind'] in TRACKED],
            snapshot_status='recorded' if 'item_snapshot' in doc else 'legacy_snapshot_unavailable'))
    if p.get('ui'):
        artifacts.append(dict(type='sketch',id=p['ui'].get('id') or digest(p['ui']['spec']),
            version=p['ui']['spec']['draft_revision'],content_hash=digest(p['ui']['spec']),
            requirements=copy.deepcopy(p['ui'].get('requirement_versions',[])),
            snapshot_status='recorded' if 'requirement_versions' in p['ui'] else 'legacy_snapshot_unavailable'))
    return dict(exchange_version='requirements-exchange-1',source_system='requirements-agent',
        projects=[dict(id=p['id'],name=p['name'],namespace=namespace(p),draft_revision=p['revision'])],
        requirements=entries,relations=relations,artifacts=artifacts,baseline_id=baseline_id,
        sources=[{k:s.get(k) for k in ('id','title','purpose','sha256','version','excluded')} for s in p['sources']],
        decisions=copy.deepcopy(p.get('stage_checks',{})),
        downstream_status=dict(frontend='unbound',backend='unbound',test_design='unbound'),test_executions=[])


def validate_exchange(value):
    schema=read_json(KIT/'examples/requirements-exchange.schema.json')
    errors=list(jsonschema.Draft202012Validator(schema).iter_errors(value))
    require(not errors,'EXCHANGE_INVALID','交换结构无效：'+'；'.join('/'.join(map(str,e.absolute_path))+': '+e.message[:180] for e in errors[:10]))
    require(value.get('exchange_version')=='requirements-exchange-1','EXCHANGE_INVALID','不支持的交换格式版本')
    require(value.get('source_system')=='requirements-agent' and isinstance(value.get('projects'),list),'EXCHANGE_INVALID','缺少来源系统或项目')
    seen={}
    for entry in value.get('requirements',[]):
        require(all(k in entry for k in ('namespace','id','version','content','content_hash','type')),'EXCHANGE_INVALID','缺少需求身份、版本或内容')
        require(entry['type'] in TRACKED and isinstance(entry['version'],int) and entry['version']>=1,'EXCHANGE_INVALID','需求类型或版本无效')
        require(entry['content_hash']==digest(entry['content']) and entry['statement']==entry['content']['statement'] and entry['title']==entry['content']['title'] and entry['type']==entry['content']['kind'],'EXCHANGE_CONFLICT','内容与哈希不一致，不能覆盖',409)
        require(entry['id'] not in seen,'EXCHANGE_CONFLICT','交换包重复编号：'+entry['id'],409)
        seen[entry['id']]=entry
    for r in value.get('relations',[]):
        require(r['type'] in RELATIONS,'EXCHANGE_INVALID','未知关系类型')
        for ref in (r['source'],r['target']):
            require(all(k in ref for k in ('type','id','version')),'EXCHANGE_INVALID','关系缺少对象类型、编号或版本')
            if ref['type'] in TRACKED:
                require(ref['id'] in seen and ref.get('namespace')==seen[ref['id']]['namespace'],'EXCHANGE_INVALID','关系引用不存在的需求身份')
        require(r.get('evidence_status')=='linked_only','EXCHANGE_INVALID','关系不能冒充测试执行结果')
    return value


def merge_exchange(existing, incoming):
    """Minimal offline consumer: repeat imports are no-ops; collisions are explicit."""
    validate_exchange(existing);validate_exchange(incoming)
    result=copy.deepcopy(existing)
    by_id={r['id']:r for r in result['requirements']}
    for entry in incoming['requirements']:
        previous=by_id.get(entry['id'])
        if previous:
            require(previous['namespace']==entry['namespace'],'EXCHANGE_CONFLICT','同编号来自不同命名空间：'+entry['id'],409)
            require(previous['version']!=entry['version'] or previous['content_hash']==entry['content_hash'],
                    'EXCHANGE_CONFLICT','同身份同版本内容冲突：'+entry['id'],409)
            require(previous['version']==entry['version'],'EXCHANGE_VERSION_REVIEW','版本不同，需明确选择导入版本；旧结果不会自动升级',409)
        else:
            result['requirements'].append(copy.deepcopy(entry));by_id[entry['id']]=entry
    for key in ('projects','relations','artifacts','sources'):
        for item in incoming.get(key,[]):
            if item not in result[key]:result[key].append(copy.deepcopy(item))
    return validate_exchange(result)
