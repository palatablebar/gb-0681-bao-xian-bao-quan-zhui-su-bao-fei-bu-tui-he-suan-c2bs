import io,csv,json,hashlib
from app.supplement import net_total

def bundle(state):
    out=io.StringIO(newline='');writer=csv.writer(out)
    writer.writerow(['记录类型','批次','来源行','业务ID','原因码','内容'])
    rid=state.get('run_id','preview')
    writer.writerow(['运行清单',rid,'','','',json.dumps(state.get('manifest',{}),ensure_ascii=False,sort_keys=True)])
    writer.writerow(['对账',rid,'','','',json.dumps(state.get('reconcile',{}),ensure_ascii=False,sort_keys=True)])
    for row in state.get('calculations',[]):writer.writerow(['计算',rid,row.get('line',''),row.get('id',''),row.get('code',''),json.dumps(row,ensure_ascii=False,sort_keys=True)])
    for row in state['quarantine']:writer.writerow(['隔离',rid,row['line'],row.get('id',''),row['code'],json.dumps(row.get('raw',{}),ensure_ascii=False,sort_keys=True)])
    # 补退结论追加在原证据之后，原始行与签名保持不变
    for s in state.get('supplements',[]):
        content={'id':s['id'],'target':s['target'],'net':s['net'],'corrected':s['corrected'],'deadline':s['deadline'],'base_version':s['base_version'],'base_sha256':s['base_sha256']}
        writer.writerow(['补退',rid,'',s['cancel_id'],'SUPPLEMENT',json.dumps(content,ensure_ascii=False,sort_keys=True)])
    if state.get('supplements'):writer.writerow(['补退净额',rid,'','','',net_total(state['supplements'])])
    return '\ufeff'+out.getvalue()

def checksum(state):
    return hashlib.sha256(bundle(state).encode('utf-8')).hexdigest()
