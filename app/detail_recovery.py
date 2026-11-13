"""Negative topology evidence only; node text/conditions remain semantic review."""
from collections import deque
import copy


def facts(detail,entries):
    nodes={n['id']:n for n in detail['flow']['nodes']}
    outgoing={key:[] for key in nodes}
    for edge in detail['flow']['edges']:outgoing[edge['from']].append(edge)
    regions={};frontiers={};waits={}
    for interaction,starts in entries.items():
        other=set().union(*(value for key,value in entries.items() if key!=interaction)) if len(entries)>1 else set()
        region=set();wait_paths={};todo=deque((node,[]) for node in sorted(starts));seen=set()
        while todo:
            node,path=todo.popleft()
            if node in seen:continue
            seen.add(node)
            kind=nodes[node]['kind']
            if nodes[node].get('interaction_id') not in (None,'',interaction):continue
            if kind=='wait':wait_paths[node]=path;continue
            if kind=='end' or node not in starts and (node in other or nodes[node].get('interaction_id') not in (None,'',interaction)):continue
            region.add(node)
            for edge in outgoing[node]:todo.append((edge['to'],path+[edge['id']]))
        regions[interaction]=region;waits[interaction]=wait_paths
        frontiers[interaction]={node for node in region if any(nodes[e['to']]['kind']=='end' for e in outgoing[node])}
    owners={node:sorted(key for key,value in frontiers.items() if node in value)
        for value in frontiers.values() for node in value}
    operations=[]
    for interaction,starts in entries.items():
        recoveries=[]
        for wait,prefix in sorted(waits[interaction].items()):
            todo=deque((e['to'],[e['id']]) for e in outgoing[wait]);seen=set();reachable={};secondary_waits=set()
            while todo:
                node,path=todo.popleft()
                if node in seen or node in starts:continue
                seen.add(node)
                if nodes[node]['kind']=='wait' and node!=wait:secondary_waits.add(node)
                if nodes[node]['kind'] in ('wait','end'):continue
                if node in owners:
                    end_edge=next(e for e in outgoing[node] if nodes[e['to']]['kind']=='end')
                    reachable[node]=dict(node_id=node,owners=owners[node],edge_ids=path+[end_edge['id']])
                for edge in outgoing[node]:todo.append((edge['to'],path+[edge['id']]))
            own=set(frontiers[interaction]);reached=set(reachable)
            # Re-entry may be an allowed business policy. Do not infer a ban.
            with_reentry=set();todo=deque(e['to'] for e in outgoing[wait]);seen=set()
            while todo:
                node=todo.popleft()
                if node in seen:continue
                seen.add(node)
                if nodes[node]['kind'] in ('wait','end'):continue
                if node in owners:with_reentry.add(node)
                todo.extend(e['to'] for e in outgoing[node])
            foreign_only=bool(own and reached and not secondary_waits and not own&with_reentry and
                all(len(owners[node])==1 and owners[node]!=[interaction] for node in reached))
            status=('no_original_result_anchor' if foreign_only else
                'no_counterexample' if own&reached else 'unverifiable')
            recoveries.append(dict(wait_id=wait,entry_to_wait_edge_ids=prefix,
                original_frontier=sorted(own),reachable_frontiers=list(reachable.values()),
                excluded_original_entries=sorted(starts),frontiers_including_reentry=sorted(with_reentry),unexpanded_secondary_waits=sorted(secondary_waits),status=status))
        operations.append(dict(interaction_id=interaction,entry_nodes=sorted(starts),
            direct_region=sorted(regions[interaction]),direct_frontier=sorted(frontiers[interaction]),recoveries=recoveries))
    return dict(version='negative-recovery-topology-1',operations=operations,
        limitation='只识别恢复只能到另一操作专属终点前沿的结构反例；可达不证明条件成立或业务结果正确，无法证明记unverifiable。')


def trace_witness(detail,entries,analysis,scope,start,end,recovery=False):
    """Structural premise/boundary only; never infer business completion."""
    nodes={n['id']:n for n in detail['flow']['nodes']};proof={}
    if recovery and nodes[start]['kind']=='wait' and nodes[start].get('interaction_id') in (None,'',scope):
        operation=next((o for o in analysis['operations'] if o['interaction_id']==scope),{})
        found=next((r for r in operation.get('recoveries',[]) if r['wait_id']==start),None)
        if found is not None:proof['recovery_premise']=dict(wait_id=start,entry_to_wait_edge_ids=found['entry_to_wait_edge_ids'])
    outgoing=[e for e in detail['flow']['edges'] if e['from']==end]
    if (nodes[end]['kind']=='action' and nodes[end].get('interaction_id')==scope and outgoing and
        all(nodes[e['to']].get('interaction_id') not in (None,'',scope) and
            e['to'] in entries.get(nodes[e['to']]['interaction_id'],set()) for e in outgoing)):
        proof['interaction_boundary']=dict(node_id=end,outgoing_edge_ids=[e['id'] for e in outgoing],
            next_entry_nodes=[e['to'] for e in outgoing])
    return proof


def guard(detail,analysis,outcome):
    """Called only after all review references/traces passed existing validation."""
    nodes={n['id']:n for n in detail['flow']['nodes']};edges={e['id']:e for e in detail['flow']['edges']}
    checks=outcome['checks'];audits=[]
    for operation in analysis['operations']:
        applicable=set()
        for check in checks:
            if check['scope']!=operation['interaction_id'] or check['dimension'] not in ('operation_results','actor_paths','recovery_success','recovery_rejection','recovery_pending'):continue
            if not (check['source_quotes'] or check['human_quotes']):continue
            for trace in check['traces']:
                recovery=check['dimension'] in ('recovery_success','recovery_rejection','recovery_pending')
                if (trace['kind']!='unknown' and not recovery) or not trace['edge_ids']:continue
                visited=[edges[trace['edge_ids'][0]]['from']]+[edges[e]['to'] for e in trace['edge_ids']]
                known_waits={r['wait_id'] for r in operation['recoveries']}
                if not set(visited)&set(operation['entry_nodes']) and not (recovery and visited[0] in known_waits):continue
                first_wait=next((n for n in visited if nodes[n]['kind']=='wait'),None)
                if first_wait:applicable.add(first_wait)
        for recovery in operation['recoveries']:
            audits.append(dict(interaction_id=operation['interaction_id'],**copy.deepcopy(recovery),
                applicability='source_bound_unknown_trace' if recovery['wait_id'] in applicable else 'not_applicable',
                blocks_acceptance=recovery['wait_id'] in applicable and recovery['status']=='no_original_result_anchor'))
    blocked=any(a['blocks_acceptance'] for a in audits)
    result=copy.deepcopy(outcome)
    applicable=[a for a in audits if a['applicability']=='source_bound_unknown_trace']
    status=('pending' if blocked else 'not_applicable' if not applicable else
        'unverifiable' if any(a['status']=='unverifiable' for a in applicable) else 'no_structural_counterexample')
    result['recovery_structure_guard']=dict(version=analysis['version'],model_status=outcome['status'],
        status=status,checks=audits,limitation=analysis['limitation'])
    if blocked and outcome['status']=='passed':
        result.update(status='pending',code='DETAIL_RECOVERY_UNVERIFIED',
            reason='未知结果的恢复路径只能到达另一操作的专属结果前沿，未找到本操作或共同结果前沿；详细定义保留待核对。此为结构诊断，不是新增业务决定。')
    # Existing genuine failures retain their correction opportunity and scope.
    return result
