from app.result_conditions import facts,issues,uncertainty_event


def check(text):
    return issues(facts(text,location='synthetic.behavior.data',fallback_operation='update',fallback_subject='记录'))


def test_unknown_option_does_not_turn_a_result_catalog_into_an_unknown_event():
    text='任务编号、站点名称、派单结果均来自系统记录／资产台账，非空只读；派单结果为成功、失败、结果未知三选一，不设默认值；查询后更新显示系统返回的派单结果，不创建或修改派单任务记录。'
    assert uncertainty_event('派单结果为成功、失败、结果未知三选一，不设默认值') is None
    assert not check(text)


def test_another_unknown_or_inherited_timeout_still_blocks_unjustified_results():
    for text in [
        '派单结果为成功、失败、结果未知三选一；结果未知时原批已创建记录。',
        '超时结果未知；派单结果为成功、失败、结果未知三选一；原批已创建记录。',
        '派单结果为成功、失败、结果未知三选一；超时后原批未创建记录。',
        '结果未知，原批已创建记录。',
    ]:
        assert any(i['reason']=='unknown_result_asserted' for i in check(text)),text
