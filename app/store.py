import sqlite3,json,os,hashlib
from pathlib import Path
from datetime import datetime,timezone
from app.errors import DomainError
from app.supplement import net_total

def connection():
    path=Path(os.environ.get('DB_PATH','data/records.db'));path.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(path,timeout=10);db.row_factory=sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, body TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, decision TEXT NOT NULL DEFAULT "待复核")')
    db.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY,value TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS signoffs (run_id TEXT NOT NULL,version INTEGER NOT NULL,decision TEXT NOT NULL,sha256 TEXT NOT NULL DEFAULT "",PRIMARY KEY (run_id,version))')
    db.execute('CREATE TABLE IF NOT EXISTS supplements (id TEXT PRIMARY KEY,base_run_id TEXT NOT NULL,base_version INTEGER NOT NULL,base_sha256 TEXT NOT NULL,cancel_id TEXT NOT NULL,target TEXT NOT NULL,net TEXT NOT NULL,corrected TEXT NOT NULL,deadline TEXT NOT NULL,created TEXT NOT NULL)')
    return db

def save(run_id, body):
    with connection() as db:
        db.execute('INSERT OR IGNORE INTO runs(id,body) VALUES (?,?)',(run_id,json.dumps(body,ensure_ascii=False)))
        db.execute("INSERT INTO meta(key,value) VALUES ('latest',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(run_id,))
    return get(run_id)

def get(run_id):
    with connection() as db:
        r=db.execute('SELECT * FROM runs WHERE id=?',(run_id,)).fetchone()
        if not r:raise DomainError('NOT_FOUND','批次不存在',404)
        supplements=[dict(x) for x in db.execute('SELECT * FROM supplements WHERE base_run_id=? ORDER BY rowid',(run_id,)).fetchall()]
        signoffs=[dict(x) for x in db.execute('SELECT * FROM signoffs WHERE run_id=? ORDER BY version',(run_id,)).fetchall()]
    body=json.loads(r['body']);body.update(run_id=r['id'],review_version=r['version'],decision=r['decision'],
        supplements=supplements,supplement_net=net_total(supplements),signoffs=signoffs);return body

def history():
    with connection() as db:rows=db.execute("SELECT id,version,decision FROM runs ORDER BY CASE WHEN id=(SELECT value FROM meta WHERE key='latest') THEN 0 ELSE 1 END,rowid DESC LIMIT 100").fetchall()
    return [dict(r) for r in rows]

def review(run_id, version, decision):
    if decision not in ('通过','退回'):raise DomainError('DECISION','复核状态无效')
    with connection() as db:
        cur=db.execute('UPDATE runs SET version=version+1,decision=? WHERE id=? AND version=?',(decision,run_id,version))
        if cur.rowcount!=1:
            if not db.execute('SELECT 1 FROM runs WHERE id=?',(run_id,)).fetchone():raise DomainError('NOT_FOUND','批次不存在',404)
            raise DomainError('STALE','复核版本已更新，请刷新',409)
        sha=json.loads(db.execute('SELECT body FROM runs WHERE id=?',(run_id,)).fetchone()['body']).get('archive',{}).get('sha256','')
        db.execute('INSERT INTO signoffs(run_id,version,decision,sha256) VALUES (?,?,?,?) ON CONFLICT(run_id,version) DO NOTHING',(run_id,version+1,decision,sha))
    return get(run_id)

def supplement(run_id, cancel_id, target, net, corrected, deadline):
    # 单事务一次性切换：快照原始签审、写入补退结论、条件版本更新；任一步失败整体回滚，旧版保留。
    sid=hashlib.sha256((run_id+'|'+cancel_id).encode('utf-8')).hexdigest()[:20]
    with connection() as db:
        row=db.execute('SELECT * FROM runs WHERE id=?',(run_id,)).fetchone()
        if not row:raise DomainError('NOT_FOUND','批次不存在',404)
        hit=db.execute('SELECT * FROM supplements WHERE id=?',(sid,)).fetchone()
        if hit:
            if hit['net']!=net:raise DomainError('CONFLICT','同一补退金额不一致',409)
            return dict(hit),True
        if row['decision'] not in ('通过','已补退'):raise DomainError('UNSIGNED','批次未签审，应修订旧批次',409)
        sha=json.loads(row['body']).get('archive',{}).get('sha256','')
        if row['decision']=='通过':
            db.execute('INSERT INTO signoffs(run_id,version,decision,sha256) VALUES (?,?,?,?) ON CONFLICT(run_id,version) DO NOTHING',(run_id,row['version'],'通过',sha))
        created=datetime.now(timezone.utc).isoformat()
        db.execute('INSERT INTO supplements(id,base_run_id,base_version,base_sha256,cancel_id,target,net,corrected,deadline,created) VALUES (?,?,?,?,?,?,?,?,?,?)',
            (sid,run_id,row['version'],sha,cancel_id,target,net,corrected,deadline,created))
        cur=db.execute("UPDATE runs SET version=version+1,decision='已补退' WHERE id=? AND version=?",(run_id,row['version']))
        if cur.rowcount!=1:raise DomainError('STALE','批次版本已更新，请刷新',409)
        return {'id':sid,'base_run_id':run_id,'base_version':row['version'],'base_sha256':sha,'cancel_id':cancel_id,'target':target,'net':net,'corrected':corrected,'deadline':deadline,'created':created},False
