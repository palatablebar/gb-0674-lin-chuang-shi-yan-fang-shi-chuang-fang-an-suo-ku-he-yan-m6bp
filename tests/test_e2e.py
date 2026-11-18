from fastapi.testclient import TestClient
from app import main,store
from app.engine import evaluate
import pytest

def client(tmp_path,monkeypatch):
    monkeypatch.setattr(main,'DB_PATH',str(tmp_path/'test.sqlite'));return TestClient(main.app)
def test_api_idempotent(tmp_path,monkeypatch):
    c=client(tmp_path,monkeypatch);a=c.post('/api/runs',json={}).json();b=c.post('/api/runs',json={}).json();assert a['manifest']['run_id']==b['manifest']['run_id'];assert len(c.get('/api/runs').json())==1
def test_stale_review(tmp_path,monkeypatch):
    c=client(tmp_path,monkeypatch);a=c.post('/api/runs',json={}).json();url='/api/runs/'+a['manifest']['run_id']+'/review';assert c.post(url,json={'revision':1}).status_code==200;assert c.post(url,json={'revision':1}).status_code==409
def test_seven_entries(tmp_path,monkeypatch):
    c=client(tmp_path,monkeypatch)
    for k in main.MODULES:
        assert c.post('/api/'+k,json={}).status_code==200
        assert c.get('/'+k).status_code==200
def test_bad_dataset(tmp_path,monkeypatch):
    c=client(tmp_path,monkeypatch);assert c.post('/api/runs',json={'dataset':'../bad'}).status_code==422
def test_missing_run(tmp_path,monkeypatch):
    c=client(tmp_path,monkeypatch);assert c.get('/api/runs/none').status_code==404
def test_restart_store(tmp_path,sample):
    p=str(tmp_path/'durable.sqlite');store.save(p,sample);assert store.get(p,sample['manifest']['run_id'])['manifest']['sha256']==sample['manifest']['sha256']
def test_bad_revision(tmp_path,monkeypatch):
    c=client(tmp_path,monkeypatch);s=c.post('/api/runs',json={}).json();assert c.post('/api/runs/'+s['manifest']['run_id']+'/review',json={'revision':0}).status_code==422

def test_clean_navigation_run_preserved(tmp_path,monkeypatch):
    c=client(tmp_path,monkeypatch)
    run=c.post('/api/runs',json={'dataset':'clean'}).json()
    rid=run['manifest']['run_id']
    for k in main.MODULES:
        html=c.get('/'+k+'?run_id='+rid).text
        assert '?run_id='+rid in html
        assert rid in html
    assert c.get('/api/export/'+rid).status_code==200

def test_custom_csv_snapshot_and_navigation(tmp_path,monkeypatch):
    from app.config import ROOT
    c=client(tmp_path,monkeypatch)
    original=(ROOT/'sample_data/clean.csv').read_text()
    # 改成独立于所有仓内样例的证据编号，追踪其跨页与重跑行为。
    first=original.splitlines()[1].split(',')[0]
    custom=original.replace(first,first+'-CUSTOM')
    result=c.post('/api/runs',json={'csv_text':custom}).json()
    rid=result['manifest']['run_id']
    assert '-CUSTOM' in str(result['records']) or '-CUSTOM' in str(result['quarantine'])
    for key in main.MODULES:
        response=c.get('/'+key+'?run_id='+rid)
        assert response.status_code==200
        assert '?run_id='+rid in response.text
    repeated=c.post('/api/runs/'+rid+'/rerun',json={})
    assert repeated.status_code==200
    assert repeated.json()['manifest']['run_id']==rid
    assert repeated.json()['manifest']['sha256']==result['manifest']['sha256']

def test_sampling_list_stable_across_rerun(tmp_path,monkeypatch):
    c=client(tmp_path,monkeypatch)
    first=c.post('/api/runs',json={}).json()
    rid=first['manifest']['run_id']
    before=[r['subject'] for r in first['sampling']['selected']]
    rerun=c.post('/api/runs/'+rid+'/rerun',json={}).json()
    # 同一批重跑：批号不变、名单不新增不改换。
    assert rerun['manifest']['run_id']==rid
    after=[r['subject'] for r in rerun['sampling']['selected']]
    assert after==before and len(after)==8
    assert rerun['sampling']['source_digest']==first['sampling']['source_digest']

def test_source_update_invalidates_old_list(tmp_path,monkeypatch):
    c=client(tmp_path,monkeypatch)
    from app.config import ROOT
    original=(ROOT/'sample_data/clean.csv').read_text()
    old=c.post('/api/runs',json={'csv_text':original}).json()
    # 源数据更新（改一个检验值）：新批号、新名单，旧批旧名单仍可查但不再是当前依据。
    updated=original.replace('118,mg/dL','119,mg/dL',1)
    new=c.post('/api/runs',json={'csv_text':updated}).json()
    assert new['manifest']['run_id']!=old['manifest']['run_id']
    assert new['manifest']['sha256']!=old['manifest']['sha256']
    history={r['id'] for r in c.get('/api/runs').json()}
    assert {old['manifest']['run_id'],new['manifest']['run_id']}<=history
    old_fetched=c.get('/api/runs/'+old['manifest']['run_id']).json()
    assert old_fetched['sampling']['source_digest']==old['manifest']['sha256']

def test_sampling_gate_blocks_lock_and_archive_pages(tmp_path,monkeypatch):
    c=client(tmp_path,monkeypatch)
    run=c.post('/api/runs',json={}).json()
    rid=run['manifest']['run_id']
    # 脏批数据问题未清，锁库与归档页面必须显示待处理，且抽查页给出名单依据。
    locking=c.get('/locking?run_id='+rid).text
    archive=c.get('/archive?run_id='+rid).text
    sampling=c.get('/sampling?run_id='+rid).text
    assert '待处理' in locking and '待处理' in archive
    assert '抽查名单' in sampling and '锁库依据' in sampling
    bundle=c.get('/api/export/'+rid)
    assert bundle.status_code==200
    import zipfile,io
    z=zipfile.ZipFile(io.BytesIO(bundle.content))
    assert 'sampling.json' in z.namelist()

def test_undetermined_sampling_page_blocks_release(tmp_path,monkeypatch):
    from app.config import ROOT
    c=client(tmp_path,monkeypatch)
    original=(ROOT/'sample_data/clean.csv').read_text()
    # 让 7 名受试者各缺一条 PRIMARY：风险面 7 名超过风险席 6，名单必须未定。
    lines=original.splitlines()
    dropped=[ln for ln in lines[1:] if ln.startswith(('00001-V1','00002-V1','00003-V1','00004-V1','00005-V1','00006-V1','00007-V1'))]
    custom='\n'.join([lines[0]]+[ln for ln in lines[1:] if ln not in dropped])
    run=c.post('/api/runs',json={'csv_text':custom}).json()
    rid=run['manifest']['run_id']
    assert run['sampling']['status']=='名单未定'
    page=c.get('/sampling?run_id='+rid).text
    assert '名单未定，锁库与归档不得放行' in page
    assert '超过风险席上限' in page
    assert run['balance']['status']=='待处理'

def test_rerun_failure_keeps_previous_version(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient as _TC
    monkeypatch.setattr(main,'DB_PATH',str(tmp_path/'test.sqlite'))
    c=_TC(main.app,raise_server_exceptions=False)
    run=c.post('/api/runs',json={}).json()
    rid=run['manifest']['run_id']
    # 重算接口在评估抛错时不得覆盖库内旧版：旧批仍可读取，名单与摘要原样保留。
    import app.main as main_mod
    original=main_mod.evaluate
    def boom(*a,**k): raise RuntimeError('重算失败')
    main_mod.evaluate=boom
    try:
        resp=c.post('/api/runs/'+rid+'/rerun',json={})
        assert resp.status_code==500
    finally:
        main_mod.evaluate=original
    kept=c.get('/api/runs/'+rid).json()
    assert kept['manifest']['sha256']==run['manifest']['sha256']
    assert [r['subject'] for r in kept['sampling']['selected']]==[r['subject'] for r in run['sampling']['selected']]

