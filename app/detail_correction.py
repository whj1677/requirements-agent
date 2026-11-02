"""Finite authorized property assignments, not a general JSON patch protocol."""
import copy
import jsonschema
from .core import require,digest
from .detail_draft import model_schema
from .requirement_details import _objects


def write_scope(detail,findings):
    exact={path for finding in findings for path in finding['affected_paths']}
    objects=set();graph=False;states=False
    for finding in findings:
        if finding['dimension'] in ('state_identity','success_timing'):states=True
        if finding['dimension'] in ('operation_results','actor_paths','recovery_success','recovery_rejection','recovery_pending'):
            graph=True
            ids={eid for trace in finding['traces'] for eid in trace['edge_ids']}
            for edge in detail['flow']['edges']:
                if edge['id'] in ids:
                    objects.add('detail_contract.flow.edges.'+edge['id'])
                    objects.update('detail_contract.flow.nodes.'+edge[key] for key in ('from','to'))
    return dict(exact=exact,objects=objects,graph=graph,states=states)


def shapes():
    props=model_schema()['properties']
    return {section:(props['flow']['properties'][section.split('.')[1]] if section.startswith('flow.') else props[section])['items']
        for section in ('fields','interactions','state_transitions','flow.nodes','flow.edges')}


def allowed(detail,findings):
    scope=write_scope(detail,findings);schemas=shapes();values={}
    local=[f for f in findings if f['dimension']=='local_graph_kind_single_branch']
    if local:
        require(len(local)==len(findings)==1,'DETAIL_CORRECTION_SCOPE','节点类型技术纠正不得混入其他改写范围。')
        finding=local[0];binding=finding['graph_kind_binding'];nid=binding['node_id']
        nodes=detail['flow']['nodes'];edges=detail['flow']['edges'];matching=[n for n in nodes if n['id']==nid]
        path='detail_contract.flow.nodes.'+nid+'.kind'
        outgoing=[e['id'] for e in edges if e.get('from')==nid]
        require(digest(binding)==finding['graph_kind_binding_hash'] and digest(detail)==binding['candidate_hash']
            and binding['kind_path']==path and finding['affected_paths']==[path] and binding['original_kind']=='decision'
            and len(matching)==1 and matching[0].get('kind')=='decision' and len(outgoing)==1
            and outgoing==binding['outgoing_edge_ids']
            and [e['id'] for e in edges if e.get('to')==nid]==binding['incoming_edge_ids'],
            'DETAIL_CORRECTION_SCOPE','节点类型技术纠正的候选、定位或结构冻结已变化。')
        props=schemas['flow.nodes']['properties']
        return {path:dict(props['kind'],enum=['start','action','decision','wait','end']),
            'detail_contract.flow.nodes.'+nid+'.evidence.kind':props['evidence']['additionalProperties']},[]
    for section in detail['applicability']:
        path='detail_contract.applicability.'+section+'.reason'
        if path in scope['exact']:
            values[path]=model_schema()['properties']['applicability']['properties'][section]['properties']['reason']
    for section,item in _objects(detail):
        base='detail_contract.'+section+'.'+item['id'];props=schemas[section]['properties']
        for attribute,shape in props.items():
            if attribute in ('id','evidence'):continue
            path=base+'.'+attribute
            if base in scope['objects'] or path in scope['exact']:
                binding=next((finding for finding in findings if finding['dimension']=='local_acceptance_reference_missing'
                    and path in finding['affected_paths']),None)
                if binding:
                    values[path]=dict(shape,items={'enum':binding['allowed_refs']} if binding['allowed_refs'] else {'not':{}},
                        allOf=[{'contains':{'const':ref}} for ref in binding['preserved_refs']])
                    if not values[path]['allOf']:values[path].pop('allOf')
                    continue
                values[path]=shape
                values[base+'.evidence.'+attribute]=props['evidence']['additionalProperties']
    new_kinds=(['flow.nodes','flow.edges'] if scope['graph'] else [])+(['state_transitions'] if scope['states'] else [])
    return values,new_kinds


def contract(raw,outcomes):
    from .detail_semantics import obj
    rows=[];scopes={}
    for row in raw['requirements']:
        if outcomes.get(row['row_key'],{}).get('status')!='failed':continue
        for finding in outcomes[row['row_key']]['findings']:
            if finding['dimension']=='local_graph_kind_single_branch':
                require(digest(raw)==finding['graph_kind_binding']['raw_response_hash'] and
                    digest(outcomes[row['row_key']].get('reference_scope'))==finding['graph_kind_binding']['reference_scope_hash'],
                    'DETAIL_CORRECTION_SCOPE','节点类型技术纠正的原稿或动态引用范围已变化。')
        values,new_kinds=allowed(row['detail_contract'],outcomes[row['row_key']]['findings'])
        existing_ids=sorted(item['id'] for _,item in _objects(row['detail_contract']))
        scopes[row['row_key']]=dict(allowed_write_paths=sorted(values),new_object_kinds=new_kinds,
            existing_object_ids=existing_ids,
            value_types={path:shape.get('type','object') for path,shape in values.items()})
        value_shapes=list({digest(shape):shape for shape in values.values()}.values())
        assignment=obj(dict(path={'enum':sorted(values)},value={'anyOf':value_shapes})) if values else {'not':{}}
        new_variants=[]
        for section in new_kinds:
            shape=copy.deepcopy(shapes()[section])
            if existing_ids:shape['properties']['id']['not']={'enum':existing_ids}
            new_variants.append(obj(dict(section={'const':section},value=shape)))
        rows.append(obj(dict(row_key={'const':row['row_key']},
            updates=dict(type='array',items=assignment,maxItems=len(values)),
            new_objects=dict(type='array',items={'oneOf':new_variants} if new_variants else {'not':{}},maxItems=300))))
    return obj(dict(rows=dict(type='array',minItems=len(rows),maxItems=len(rows),
        items=rows[0] if len(rows)==1 else {'oneOf':rows}))),scopes


def merge(raw,response,outcomes,audit=None):
    schema,_=contract(raw,outcomes)
    require(not list(jsonschema.Draft202012Validator(schema).iter_errors(response)),
        'DETAIL_CORRECTION_INVALID','属性纠正patch结构、路径或新增对象种类无效。')
    expected={key for key,value in outcomes.items() if value['status']=='failed'}
    keys=[row['row_key'] for row in response['rows']]
    require(len(keys)==len(expected) and set(keys)==expected,'DETAIL_CORRECTION_SCOPE','属性纠正必须唯一覆盖本次失败行。')
    originals={row['row_key']:row['detail_contract'] for row in raw['requirements']};rows=[]
    for row in response['rows']:
        before=originals[row['row_key']];detail=copy.deepcopy(before)
        values,new_kinds=allowed(before,outcomes[row['row_key']]['findings'])
        paths=[u['path'] for u in row['updates']]
        require(len(paths)==len(set(paths)),'DETAIL_CORRECTION_SCOPE','属性纠正包含重复路径，不能后写覆盖前写。')
        objects={'detail_contract.'+section+'.'+item['id']:item for section,item in _objects(detail)}
        objects.update({'detail_contract.applicability.'+section:value for section,value in detail['applicability'].items()})
        for update in row['updates']:
            path=update['path']
            require(path in values and not list(jsonschema.Draft202012Validator(values[path]).iter_errors(update['value'])),
                'DETAIL_CORRECTION_SCOPE','属性纠正路径或值类型无效：'+path)
            if '.evidence.' in path:
                base,attribute=path.rsplit('.evidence.',1);objects[base].setdefault('evidence',{})[attribute]=copy.deepcopy(update['value'])
            else:
                base,attribute=path.rsplit('.',1);objects[base][attribute]=copy.deepcopy(update['value'])
        known={item['id'] for _,item in _objects(detail)}
        for addition in row['new_objects']:
            section=addition['section'];item=addition['value']
            require(section in new_kinds and item['id'] not in known,'DETAIL_CORRECTION_SCOPE','新增对象种类未获授权或ID冲突。')
            known.add(item['id'])
            collection=detail['flow'][section.split('.')[1]] if section.startswith('flow.') else detail[section]
            collection.append(copy.deepcopy(item))
        rows.append(dict(row_key=row['row_key'],detail_contract=detail))
        if audit is not None:
            audit.append(dict(protocol='detail-property-correction-2',row_key=row['row_key'],
                raw_patch_hash=digest(row),input_candidate_hash=digest(before),assembled_candidate_hash=digest(detail),
                assigned_paths=paths,new_objects=[dict(section=a['section'],id=a['value']['id']) for a in row['new_objects']],
                notice='程序仅按模型明确返回的授权属性赋值及新增对象合回原稿；不是模型完整重写，未经后续校验和复审不得采用。'))
    return dict(rows=rows)
