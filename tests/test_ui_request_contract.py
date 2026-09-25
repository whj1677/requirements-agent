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
