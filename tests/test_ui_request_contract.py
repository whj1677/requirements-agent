import copy
import json
import jsonschema
import pytest
from app.provider import assemble, DEFAULT
from tests.test_prd01 import state
from tests.helpers import EXAMPLES


def test_ui_model_receives_complete_supported_schema(tmp_path):
    _,p=state(tmp_path)
    messages,_,_=assemble(p,'ui','',DEFAULT,tmp_path)
    header=json.loads(messages[0]['content'].split('可信任务头：')[1])
    serialized=json.dumps(header['schema'])
    assert 'wireframe.schema.json' not in serialized
    assert 'editable_roles' in serialized and 'dataset_id' in serialized
    value=copy.deepcopy(EXAMPLES['ui'])
    jsonschema.validate(value,header['schema'])
    value['questions']=[dict(temp_id='Q-NEW',question='新的业务决定')]
    with pytest.raises(jsonschema.ValidationError) as e:jsonschema.validate(value,header['schema'])
    assert e.value.validator=='const'


def test_real_response_envelope_accepts_simulation_protocol():
    from app.contracts import VALIDATOR
    from tests.test_ui01_preview import contacts_spec,price_spec
    for spec in (contacts_spec(),price_spec()):
        value=copy.deepcopy(EXAMPLES['ui']);value['result']['spec']=spec
        VALIDATOR.validate(value)


def test_filter_container_error_names_actual_component_and_fix():
    from app.contracts import validate_ui
    from tests.test_ui01_preview import contacts_spec,project
    from app.core import Problem
    value=contacts_spec()
    c=value['pages'][0]['regions'][0]['components'][0]
    c['interaction']=dict(action='filter',target_id='LIST',target_state=None)
    with pytest.raises(Problem) as error:validate_ui(value,project())
    assert all(x in error.value.message for x in ('FILTER','filters','button','action=none','LIST'))


def test_filter_all_option_is_explicit_and_validated():
    from app.contracts import validate_ui
    from tests.test_ui01_preview import contacts_spec,project
    from app.core import Problem
    spec=contacts_spec();field=spec['pages'][0]['regions'][0]['components'][0]['fields'][0]
    field.update(type='select',options=['所有姓名','小周'],filter_all_option='所有姓名')
    validate_ui(spec,project())
    field['filter_all_option']='不存在'
    with pytest.raises(Problem) as error:validate_ui(spec,project())
    assert 'filter_all_option' in error.value.message
    field['filter_all_option']='所有姓名';field['type']='text'
    with pytest.raises(Problem):validate_ui(spec,project())


def test_ui_context_keeps_business_snapshot_without_duplicate_artifacts(tmp_path):
    _,p=state(tmp_path)
    p['documents']['prd']={'id':'DOC-OLD','draft_revision':1,'content':{'private_large_document':'not needed for page rendering'}}
    p['messages']=[dict(role='user',stage='ui',text='保留旧流程，只改筛选',response=None),
        dict(role='assistant',stage='prd',text='讨论稿已保存',response={'large_output':'duplicate'})]
    messages,_,_=assemble(p,'ui','',DEFAULT,tmp_path)
    header=json.loads(messages[0]['content'].split('可信任务头：')[1])
    ctx=json.loads(messages[1]['content'][0]['text'])
    assert ctx['items']==p['items'] and ctx['questions']==p['questions']
    assert ctx['recent_messages'][0]['text']=='保留旧流程，只改筛选'
    assert 'duplicate' not in json.dumps(ctx) and 'private_large_document' not in json.dumps(ctx)
    assert ctx['document_status']['prd']['id']=='DOC-OLD'
    jsonschema.validate(header['structure_example'],header['schema'])
