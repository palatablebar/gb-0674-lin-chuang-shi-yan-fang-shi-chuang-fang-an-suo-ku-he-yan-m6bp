def decide(state):
    c=state['counts'];explained=len(state['records'])+len(state['quarantine'])+c['blank']+c['duplicate']+c['repeated_header'];blocked=len(state['deviations'])+len(state['retest_issues'])+len(state['quarantine'])
    sampling=state.get('sampling')
    # 抽查名单未定时不能放行：名额、风险覆盖与对照位约束在 sampling 模块裁定。
    sampling_pending=bool(sampling and sampling['status']!='名单已定')
    data_clean=not blocked and c['input']==explained
    return {'input':c['input'],'accepted':len(state['records']),'quarantined':len(state['quarantine']),'row_balanced':c['input']==explained,'completed':len(state['completed']),'expected':len(state['context']['subjects'])*len(state['context']['required_visits']),'deviations':len(state['deviations']),'retest_issues':len(state['retest_issues']),'blocked':blocked,'sampling_status':sampling['status'] if sampling else '未执行','sampling_pending':sampling_pending,'status':'可锁库' if data_clean and not sampling_pending else '待处理'}
def apply(state):
    state['balance']=decide(state);state['views']['locking']={'title':'锁库闸门','rows':[state['balance']],'metrics':{'应访视':state['balance']['expected'],'已完成':state['balance']['completed'],'阻断项':state['balance']['blocked'],'抽查':state['balance']['sampling_status']}}
