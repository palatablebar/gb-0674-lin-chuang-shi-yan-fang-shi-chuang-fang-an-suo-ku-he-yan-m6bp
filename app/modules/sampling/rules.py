import hashlib

# 锁定批质量复核固定名额：八名受试者。
QUOTA = 8
# 无论风险面多大，至少保留两个对照位给无异常受试者，高风险对象不得占满名额。
MIN_CONTROL = 2
RULE_TAG = 'qa-sampling-2026.11'


def _rank(run_id, subject):
    """同批号+同受试者得到稳定次序，全程无随机数；批号由源数据摘要决定。"""
    digest = hashlib.sha256(f'{RULE_TAG}|{run_id}|{subject}'.encode()).hexdigest()
    return int(digest[:12], 16)


def _risk_pool(state):
    """汇总偏离、复检问题、隔离记录涉及的受试者，并保留可复核依据。"""
    reasons = {}
    # 复检问题只带记录号，受试者要通过记录索引还原（含被隔离的孤儿行）。
    id_subject = {r['id']: r['subject'] for r in state['records']}
    for row in state['quarantine']:
        raw = row.get('raw') or {}
        if raw.get('id'):
            id_subject.setdefault(raw['id'], raw.get('subject'))

    def mark(subject, code, evidence):
        if subject not in state['context']['subjects']:
            return
        reasons.setdefault(subject, {'codes': set(), 'evidence': []})
        reasons[subject]['codes'].add(code)
        reasons[subject]['evidence'].append(evidence)

    for row in state['deviations']:
        mark(row['subject'], row['code'], f"偏离 {row['visit']} {row['code']}（记录 {row.get('id') or '缺失'}）")
    for row in state['retest_issues']:
        subject = id_subject.get(row['id'], str(row['id']).split('-')[0])
        mark(subject, row['code'], f"复检 {row['id']} {row['code']}（引用 {row.get('parent', '')}）")
    for row in state['quarantine']:
        # 隔离行保留的是原始行：受试者在 raw.subject（如 BAD1/BAD2 属于 00003/00004），
        # 编号列可能不可用，不能从 id 前缀猜。
        subject = (row.get('raw') or {}).get('subject') or str(row['id']).split('-')[0]
        mark(subject, row['code'], f"隔离 第{row['line']}行 {row['id']} {row['code']}")
    return reasons


def plan_sampling(state):
    run_id = state['manifest']['run_id']
    source_digest = state['manifest']['sha256']
    rule_version = state['manifest']['rule_version']
    subjects = sorted(state['context']['subjects'])
    risk = _risk_pool(state)
    risk_subjects = sorted(risk)
    control_subjects = sorted(set(subjects) - set(risk_subjects))

    risk_cap = QUOTA - MIN_CONTROL
    # 风险面超过容量则无法在八席内完成覆盖，名单不定，不得放行。
    undetermined = []
    if len(subjects) < QUOTA:
        undetermined.append(f'受试者仅 {len(subjects)} 名，不足 {QUOTA} 个抽查名额')
    if len(risk_subjects) > risk_cap:
        undetermined.append(f'风险受试者 {len(risk_subjects)} 名超过风险席上限 {risk_cap}（须保留 {MIN_CONTROL} 个对照位），八席无法全覆盖')

    selected = []
    if not undetermined:
        if risk_subjects:
            # 有风险面：风险对象全覆盖，剩余席位给无异常对照，且对照不少于 MIN_CONTROL。
            picked_risk = sorted(risk_subjects, key=lambda s: (_rank(run_id, s), s))
            picked_control = sorted(control_subjects, key=lambda s: (_rank(run_id, s), s))[: QUOTA - len(picked_risk)]
        else:
            # 无风险批：不存在可覆盖对象，八席全部作为代表性对照抽样，名单仍然确定。
            picked_risk = []
            picked_control = sorted(control_subjects, key=lambda s: (_rank(run_id, s), s))[:QUOTA]
        for index, subject in enumerate(picked_risk, 1):
            selected.append({'subject': subject, 'category': '风险覆盖', 'rank': index,
                             'risk_codes': '、'.join(sorted(risk[subject]['codes'])),
                             'basis': '；'.join(risk[subject]['evidence'])})
        for offset, subject in enumerate(picked_control, len(picked_risk) + 1):
            selected.append({'subject': subject, 'category': '无异常对照', 'rank': offset,
                             'risk_codes': '',
                             'basis': f'无偏离、复检问题与隔离记录；对照位次序令牌 {_rank(run_id, subject)}'})

    status = '名单已定' if not undetermined else '名单未定'
    return {
        'quota': QUOTA,
        'min_control': MIN_CONTROL,
        'status': status,
        'undetermined': undetermined,
        'risk_total': len(risk_subjects),
        'control_total': len(control_subjects),
        'risk_selected': sum(1 for r in selected if r['category'] == '风险覆盖'),
        'control_selected': sum(1 for r in selected if r['category'] == '无异常对照'),
        'risk_cap': risk_cap,
        'selected': selected,
        'rule_tag': RULE_TAG,
        'run_id': run_id,
        'source_digest': source_digest,
        'rule_version': rule_version,
        'selection_basis': (
            f'固定 {QUOTA} 席；偏离/复检/隔离涉及受试者全部纳入风险覆盖，风险席上限 {risk_cap}；'
            f'至少保留 {MIN_CONTROL} 个无异常对照位；同席内按 sha256({RULE_TAG}|批号|受试者) 令牌升序确定性选取'
        ),
        'lock_basis': (
            f'源数据摘要 {source_digest[:16]} 与规则版本 {rule_version} 决定批号 {run_id}；'
            '同批号重跑名单字节一致；源数据或规则变化生成新批号，旧名单与旧锁库依据随旧批一并失效'
        ),
    }


def apply(state):
    state['sampling'] = plan_sampling(state)
    state['views']['sampling'] = {
        'title': '锁定批质量抽查',
        'rows': state['sampling']['selected'],
        'metrics': {
            '抽查名额': state['sampling']['quota'],
            '风险覆盖席': state['sampling']['risk_selected'],
            '对照席': state['sampling']['control_selected'],
            '状态': state['sampling']['status'],
        },
    }
