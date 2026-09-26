"""Server validation must enforce the review-only contract, not trust the prompt."""
import copy
import pytest
from app.contracts import validate_response
from app.core import Problem
from app.store import Store
from tests.helpers import EXAMPLES, prepared


@pytest.mark.parametrize('mutation', ['proposal', 'question', 'context'])
def test_review_cannot_mutate_the_draft(tmp_path, mutation):
    project=prepared(Store(tmp_path))
    before=copy.deepcopy(project)
    value=copy.deepcopy(EXAMPLES['review'])
    if mutation=='proposal':
        value['proposals']=[dict(temp_id='TMP-unauthorized',action='add',target_item_id=None,
            kind='requirement',title='Unexpected edit',statement='Review must not create this.',
            applies_to='to_be',epistemic_status='proposed',source_refs=[],related_refs=[])]
    elif mutation=='question':
        value['questions']=[dict(temp_id='QTMP-unauthorized',topic_key='unexpected',
            question='Unexpected question?',why='Not a review mutation.',options=[],blocking=True,
            related_refs=[],source_refs=[])]
    else:
        value['product_context_proposal']={k:'Unexpected edit' for k in
            ('product','module','intent','current_state','users','value','change_scope','preserve_scope','out_of_scope','priority')}
    with pytest.raises(Problem,match='语义审查只报告发现') as error:
        validate_response(value,'review',project,project['sources'][0]['excerpts'])
    assert error.value.code=='SEMANTIC_BLOCKED'
    assert project==before
