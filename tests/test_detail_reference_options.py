import copy
import json
import jsonschema
import pytest
from app.business_draft import SCHEMA, _bind_detail_references, _item_aliases, assemble
from app.core import digest, Problem
from app.detail_draft import canonical_detail
from tests.test_business_draft import setup


def bound(project, context):
    header={'schema':copy.deepcopy(SCHEMA)}
    _bind_detail_references(header,context,project)
    return header


def properties_named(node,name):
    if isinstance(node,dict):
        if name in node.get('properties',{}): yield node['properties'][name]
        for value in node.values(): yield from properties_named(value,name)
    elif isinstance(node,list):
        for value in node: yield from properties_named(value,name)


def test_empty_whitelist_does_not_pollute_other_refs_or_global_schema():
    before=digest(SCHEMA)
    context=dict(current_rows=[],readonly_current_state=[])
    head=bound({'items':[]},context)
    for schema in properties_named(head,'rule_refs'):
        jsonschema.validate([],schema)
        with pytest.raises(jsonschema.ValidationError):jsonschema.validate(['R1'],schema)
    for schema in properties_named(head,'acceptance_refs'):
        jsonschema.validate(['A1','A30'],schema)
        with pytest.raises(jsonschema.ValidationError):jsonschema.validate(['A31'],schema)
    for schema in properties_named(head,'field_refs'):jsonschema.validate(['F1'],schema)
    assert digest(SCHEMA)==before


def test_visible_typed_refs_only_and_consecutive_request_isolation(tmp_path):
    p,local,cfg,_,_,_=setup(tmp_path); original=digest(SCHEMA)
    template=copy.deepcopy(p['items'][0])
    for identity,kind,visibility in [('RULE-visible','rule',True),('RULE-hidden','rule',False),('AC-visible','acceptance',True)]:
        item=copy.deepcopy(template);item.update(id=identity,kind=kind,target_item_id=None)
        item['related_refs']=[local['requirement_id']] if visibility else []
        p['items'].append(item)
    messages,_,_=assemble(p,'clarify','',cfg,local_scope=local)
    ctx=json.loads(messages[1]['content'][0]['text']);aliases=_item_aliases(p)
    assert aliases['RULE-visible'] in ctx['detail_reference_options']['rule_refs']
    assert aliases['RULE-hidden'] not in ctx['detail_reference_options']['rule_refs']
    assert aliases['AC-visible'] in ctx['detail_reference_options']['acceptance_refs']
    assert aliases[local['requirement_id']] in ctx['detail_reference_options']['depends_on']
    second=bound({'items':[]},dict(current_rows=[],readonly_current_state=[]))
    for schema in properties_named(second,'rule_refs'):
        with pytest.raises(jsonschema.ValidationError):jsonschema.validate([aliases['RULE-visible']],schema)
    assert digest(SCHEMA)==original


def test_diagnostic_aliases_quarantined_with_exact_audit_only(tmp_path):
    p,_,_,_,_,_=setup(tmp_path);aliases=_item_aliases(p);ref=next(iter(aliases.values()))
    candidate=dict(status='unaccepted_ai_candidate',flow={'nodes':[{'id':'N1','label':'保留 R2 原话','rule_refs':['R2']}], 'edges':[]},
        interactions=[{'id':'I1','acceptance_refs':['A1','C2'],'depends_on':[ref]}],
        semantic_diagnostics={'source_quotes':[{'quote':'原句 R2','source':'S1'}],'human_bindings':[{'id':'current-user-message','hash':'abc'}]})
    row=dict(item_ref=ref,kind='requirement',related_clauses=[],rejected_detail_candidate=candidate,
        detail_contract={'depends_on':[ref]})
    context=dict(current_rows=[row],readonly_current_state=[],excerpts=[{'text':'原句 R2'}]);before=copy.deepcopy(context)
    bound(p,context)
    projected=row['rejected_detail_candidate'];audit=projected.pop('reference_projection_audit')
    assert audit['original_hash']==digest(candidate) and audit['projected_hash']==digest(projected)
    assert len(audit['removed'])==3
    for entry in audit['removed']:
        current=projected
        for key in entry['path'][:-1]:current=current[key]
        assert current[entry['path'][-1]]==[]
        current[entry['path'][-1]]=entry['original']
    assert projected==candidate
    assert context['excerpts']==before['excerpts'] and row['detail_contract']==before['current_rows'][0]['detail_contract']
    assert candidate==before['current_rows'][0]['rejected_detail_candidate']


def test_readonly_rule_allowed_but_wrong_kind_and_unknown_still_rejected(tmp_path):
    p,_,_,_,_,_=setup(tmp_path);item=copy.deepcopy(p['items'][0]);item.update(id='RULE-readonly',kind='rule');p['items'].append(item)
    aliases=_item_aliases(p)
    context=dict(current_rows=[],readonly_current_state=[dict(item_ref=aliases[item['id']],kind='rule')])
    head=bound(p,context)
    for schema in properties_named(head,'rule_refs'):
        jsonschema.validate([aliases[item['id']]],schema)
        for ref in ('R1','R999'):
            with pytest.raises(jsonschema.ValidationError):jsonschema.validate([ref],schema)
    with pytest.raises(Problem):canonical_detail({'depends_on':['A1']},{},{})


def test_shared_diagnostic_object_gets_each_path_audited_without_touching_source_identity(tmp_path):
    p,_,_,_,_,_=setup(tmp_path)
    interaction={'id':'I1','rule_refs':['R6']}
    diagnostics={'human_bindings':[{'statement_id':'current-user-message','rule_refs':['verbatim-metadata']}],
                 'candidate_quotes':[{'quote':'{"rule_refs":["R6"]}'}]}
    candidate={'interactions':[interaction],'interaction_path_facts':{'interactions':[{'interaction':interaction}]},
               'semantic_diagnostics':diagnostics}
    context={'current_rows':[{'related_clauses':[],'rejected_detail_candidate':candidate}], 'readonly_current_state':[]}
    bound(p,context);result=context['current_rows'][0]['rejected_detail_candidate']
    assert len(result['reference_projection_audit']['removed'])==2
    assert result['semantic_diagnostics']==diagnostics
    assert candidate['interactions'][0]['rule_refs']==['R6']


@pytest.mark.parametrize('acceptance', [[],[{'text':'','basis':'unknown','sources':[]}]])
def test_potential_A_does_not_make_absent_or_unknown_acceptance_reference_valid(tmp_path,acceptance):
    from app.business_draft import adapt
    from tests.test_business_draft import draft_row,packet
    from tests.test_detail_business_draft import sample_detail
    p,local,_,_,excerpts,_=setup(tmp_path)
    row=draft_row();row['acceptance']=acceptance;row['detail_contract']=sample_detail()
    row['detail_contract']['interactions'][0]['acceptance_refs']=['A1']
    _,report=adapt(packet(row),p,excerpts,'clarify',local)
    assert report['rejected'] or report['field_rejected']
    assert not any(entry.get('detail_contract') for entry in report['accepted'])
