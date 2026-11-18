from app.modules.sampling.rules import plan_sampling, QUOTA, MIN_CONTROL, _rank
from app.modules.locking.rules import decide
from copy import deepcopy


def test_quota_is_eight_with_reserved_control(sample):
    sm = sample['sampling']
    assert sm['quota'] == QUOTA == 8
    assert len(sm['selected']) == 8
    assert sm['control_selected'] >= MIN_CONTROL
    assert sm['risk_selected'] + sm['control_selected'] == 8


def test_dirty_covers_all_risk_subjects(sample):
    sm = sample['sampling']
    picked = {r['subject'] for r in sm['selected']}
    # 偏离（00001/00020）、复检问题（00001/00020）、隔离（00003/00004）涉及对象全部在列。
    assert {'00001', '00003', '00004', '00020'} <= picked
    risk_rows = [r for r in sm['selected'] if r['category'] == '风险覆盖']
    assert {r['subject'] for r in risk_rows} == {'00001', '00003', '00004', '00020'}
    # 每个风险席都带可复核依据与原因码。
    assert all(r['basis'] and r['risk_codes'] for r in risk_rows)
    # 对照席必须来自无异常受试者池。
    control = [r for r in sm['selected'] if r['category'] == '无异常对照']
    assert control and all(r['subject'] not in {'00001', '00003', '00004', '00020'} for r in control)


def test_clean_batch_all_control_still_determined(clean):
    sm = clean['sampling']
    assert sm['status'] == '名单已定'
    assert sm['risk_selected'] == 0
    assert sm['control_selected'] == 8
    assert len({r['subject'] for r in sm['selected']}) == 8


def test_deterministic_same_run_id(sample):
    again = plan_sampling(deepcopy(sample))
    assert [r['subject'] for r in again['selected']] == [r['subject'] for r in sample['sampling']['selected']]
    # 令牌函数对同批号同受试者稳定。
    assert _rank(sample['manifest']['run_id'], '00007') == _rank(sample['manifest']['run_id'], '00007')


def test_risk_overflow_blocks_determination(sample):
    s = deepcopy(sample)
    # 伪造 7 名风险受试者：超过风险席上限 6，八席无法在保留对照位前提下全覆盖。
    s['deviations'] = [{'subject': sub, 'visit': 'V1', 'id': '', 'code': 'MISSING_PRIMARY'}
                       for sub in list(s['context']['subjects'])[:7]]
    sm = plan_sampling(s)
    assert sm['status'] == '名单未定'
    assert sm['selected'] == []
    assert any('超过风险席上限' in x for x in sm['undetermined'])
    # 抽查未定直接阻断锁库。
    s['sampling'] = sm
    assert decide(s)['status'] == '待处理'
    assert decide(s)['sampling_pending'] is True


def test_subject_pool_below_quota_blocks(sample):
    s = deepcopy(sample)
    keep = list(s['context']['subjects'])[:5]
    s['context']['subjects'] = {k: s['context']['subjects'][k] for k in keep}
    sm = plan_sampling(s)
    assert sm['status'] == '名单未定'
    assert any('不足 8 个抽查名额' in x for x in sm['undetermined'])


def test_basis_mentions_digest_and_rule(sample):
    sm = sample['sampling']
    assert sm['source_digest'] == sample['manifest']['sha256']
    assert sm['run_id'] == sample['manifest']['run_id']
    assert sm['rule_version'] == sample['manifest']['rule_version']
    assert sm['source_digest'][:16] in sm['lock_basis']
    assert '重跑' in sm['lock_basis'] and '失效' in sm['lock_basis']


def test_quarantine_subject_from_raw_not_id_prefix(sample):
    # BAD1/BAD2 编号不含受试者号，但 raw.subject 指向 00003/00004，必须正确归因。
    sm = sample['sampling']
    picked = {r['subject']: r for r in sm['selected']}
    assert 'MISSING_VALUE' in picked['00003']['risk_codes']
    assert 'PROTOCOL_MISMATCH' in picked['00004']['risk_codes']
    # COLUMN 行的伪受试者 bad 未入组，不得进入名单。
    assert all(r['subject'] != 'bad' for r in sm['selected'])
