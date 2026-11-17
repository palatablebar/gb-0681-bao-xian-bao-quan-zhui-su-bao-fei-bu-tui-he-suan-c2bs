import csv,io,sqlite3
from datetime import date
import pytest
from app import store
from app.errors import DomainError
from app.supplement import decide,net_total,parse_net

def signed(client):
    r=client.post('/api/runs',json={'sample':'clean.csv'}).get_json()
    client.post('/api/runs/'+r['run_id']+'/review',json={'version':r['review_version'],'decision':'通过'})
    return r['run_id']

def late(client,rid,cancel_id='C900',net='-120.00'):
    return client.post('/api/runs/'+rid+'/supplement',json={'cancel_id':cancel_id,'target':'00005','net':net,'corrected':'2026-11-17','deadline':'2026-11-15'})

def test_decide_branches():
    assert decide({'decision':'待复核'},date(2026,11,17),date(2026,11,15))=='revise'
    assert decide({'decision':'退回'},date(2026,11,17),date(2026,11,15))=='revise'
    assert decide({'decision':'通过'},date(2026,11,14),date(2026,11,15))=='settle'
    assert decide({'decision':'通过'},date(2026,11,17),date(2026,11,15))=='supplement'
    assert decide({'decision':'已补退'},date(2026,11,17),date(2026,11,15))=='supplement'

def test_unsigned_batch_revises(client):
    rid=client.post('/api/runs',json={'sample':'clean.csv'}).get_json()['run_id']
    r=late(client,rid);assert r.status_code==200 and r.get_json()['action']=='revise'
    assert client.get('/api/runs/'+rid+'/supplements').get_json()['supplements']==[]

def test_within_deadline_settles(client):
    rid=signed(client)
    r=client.post('/api/runs/'+rid+'/supplement',json={'cancel_id':'C901','target':'00005','net':'-120.00','corrected':'2026-11-14','deadline':'2026-11-15'})
    assert r.get_json()['action']=='settle' and client.get('/api/runs/'+rid+'/supplements').get_json()['supplements']==[]

def test_supplement_created_and_signoff_traceable(client):
    rid=signed(client);before=client.get('/api/runs/'+rid).get_json()
    r=late(client,rid);body=r.get_json()
    assert r.status_code==200 and body['action']=='supplement' and body['duplicate'] is False
    sup=body['supplement']
    assert sup['base_version']==before['review_version'] and sup['base_sha256']==before['archive']['sha256']
    after=client.get('/api/runs/'+rid).get_json()
    assert after['decision']=='已补退' and after['review_version']==before['review_version']+1
    assert after['archive']['sha256']==before['archive']['sha256'] and after['calculations']==before['calculations']
    signoffs=client.get('/api/runs/'+rid+'/signoffs').get_json()['signoffs']
    assert (before['review_version'],'通过',before['archive']['sha256']) in [(s['version'],s['decision'],s['sha256']) for s in signoffs]

def test_retry_same_supplement_adds_no_refund(client):
    rid=signed(client);a=late(client,rid).get_json();b=late(client,rid).get_json()
    assert b['duplicate'] is True and a['supplement']['id']==b['supplement']['id']
    sups=client.get('/api/runs/'+rid+'/supplements').get_json()
    assert len(sups['supplements'])==1 and sups['supplement_net']=='-120.00'

def test_retry_with_different_net_conflicts(client):
    rid=signed(client);late(client,rid)
    r=late(client,rid,net='-120.01');assert r.status_code==409 and r.get_json()['code']=='CONFLICT'
    assert len(client.get('/api/runs/'+rid+'/supplements').get_json()['supplements'])==1

def test_distinct_cancel_adds_own_conclusion(client):
    rid=signed(client);late(client,rid,'C900','-120.00');late(client,rid,'C901','30.00')
    sups=client.get('/api/runs/'+rid+'/supplements').get_json()
    assert len(sups['supplements'])==2 and sups['supplement_net']=='-90.00'

def test_target_must_reference_batch_change(client):
    rid=signed(client)
    r=client.post('/api/runs/'+rid+'/supplement',json={'cancel_id':'C902','target':'99999','net':'-1.00','corrected':'2026-11-17','deadline':'2026-11-15'})
    assert r.status_code==422 and r.get_json()['code']=='REFERENCE'

def test_bad_supplement_payload(client):
    rid=signed(client)
    assert client.post('/api/runs/'+rid+'/supplement',json={'target':'00005','net':'-1','corrected':'2026-11-17','deadline':'2026-11-15'}).status_code==422
    assert client.post('/api/runs/'+rid+'/supplement',json={'cancel_id':'C','target':'00005','net':'abc','corrected':'2026-11-17','deadline':'2026-11-15'}).get_json()['code']=='NUMBER'
    assert client.post('/api/runs/'+rid+'/supplement',json={'cancel_id':'C','target':'00005','net':'-1','corrected':'17/11','deadline':'2026-11-15'}).get_json()['code']=='DATE'

def test_page_and_download_share_net(client):
    rid=signed(client);late(client,rid)
    page=client.get('/api/runs/'+rid).get_json()
    rows=list(csv.reader(io.StringIO(client.get('/api/archive/'+rid+'/export.csv').data.decode('utf-8-sig'))))
    net=[r for r in rows if r[0]=='补退净额'];sup=[r for r in rows if r[0]=='补退']
    assert len(sup)==1 and len(net)==1 and net[0][5]==page['supplement_net']=='-120.00'
    html=client.get('/m7_archive').data.decode()
    assert '补退结论' in html and '-120.00' in html

def test_failed_switch_keeps_old_version_and_signoff(client,monkeypatch,tmp_path):
    rid=signed(client);before=client.get('/api/runs/'+rid).get_json()
    real=store.connection
    class FailSwitch:
        def __init__(self):self.db=real()
        def __enter__(self):self.db.__enter__();return self
        def __exit__(self,*exc):return self.db.__exit__(*exc)
        def execute(self,sql,params=()):
            if sql.lstrip().startswith('UPDATE runs'):raise sqlite3.OperationalError('forced switch failure')
            return self.db.execute(sql,params)
    monkeypatch.setattr(store,'connection',FailSwitch)
    with pytest.raises(sqlite3.OperationalError):late(client,rid)
    monkeypatch.setattr(store,'connection',real)
    after=client.get('/api/runs/'+rid).get_json()
    assert after['decision']=='通过' and after['review_version']==before['review_version'] and after['supplements']==[]
    assert any(s['decision']=='通过' and s['sha256']==before['archive']['sha256'] for s in after['signoffs'])
    assert late(client,rid).status_code==200

def test_supplement_store_direct(tmp_path,monkeypatch):
    monkeypatch.setenv('DB_PATH',str(tmp_path/'r.db'))
    from app.workflow import load,run
    state=run(load('clean.csv'),{});store.save(state['run_id'],state)
    with pytest.raises(DomainError) as e:store.supplement(state['run_id'],'C1','00005','-1.00','2026-11-17','2026-11-15')
    assert e.value.code=='UNSIGNED'
    store.review(state['run_id'],1,'通过')
    sup,dup=store.supplement(state['run_id'],'C1','00005','-1.00','2026-11-17','2026-11-15')
    assert dup is False and store.supplement(state['run_id'],'C1','00005','-1.00','2026-11-17','2026-11-15')[1] is True
    assert net_total(store.get(state['run_id'])['supplements'])=='-1.00'

def test_parse_net_rejects_bad():
    for bad in ('abc','NaN','Infinity',None):
        with pytest.raises(DomainError):parse_net(bad)
    assert parse_net(-120.1)=='-120.10' and parse_net('0.005')=='0.01'
