import importlib,os
from flask import Flask,jsonify,request,redirect,Response
from app.config import MODULES
from app.errors import DomainError
from app.workflow import run,load
from app import store
from app.web import payload
from app.supplement import decide,parse_day,parse_net,change_ids
from app.modules.m7_archive.export_out import bundle

def create_app():
    app=Flask(__name__);app.config['MAX_CONTENT_LENGTH']=2_000_000
    for entry in MODULES:app.register_blueprint(importlib.import_module('app.modules.'+entry[0]+'.handler').bp)
    @app.errorhandler(DomainError)
    def domain_error(error):return jsonify(code=error.code,message=error.message),error.status
    @app.errorhandler(404)
    def missing(error):return jsonify(code='NOT_FOUND',message='入口不存在'),404
    @app.get('/')
    def index():return redirect('/'+MODULES[0][0])
    @app.get('/healthz')
    def health():return jsonify(status='ok')
    @app.post('/api/runs')
    def submit():
        content,params=payload();state=run(content,params);return jsonify(store.save(state['run_id'],state))
    @app.get('/api/runs')
    def list_runs():return jsonify(store.history())
    @app.get('/api/runs/<run_id>')
    def get_run(run_id):return jsonify(store.get(run_id))
    @app.post('/api/runs/<run_id>/review')
    def review(run_id):
        data=request.get_json(silent=True) or {}
        if not isinstance(data.get('version'),int):raise DomainError('VERSION','请提交复核版本')
        return jsonify(store.review(run_id,data['version'],data.get('decision')))
    @app.post('/api/runs/<run_id>/supplement')
    def supplement(run_id):
        data=request.get_json(silent=True) or {}
        if not isinstance(data,dict):raise DomainError('PAYLOAD','请求必须是对象')
        cancel_id=str(data.get('cancel_id','')).strip();target=str(data.get('target','')).strip()
        if not cancel_id or not target:raise DomainError('MISSING','缺少撤销编号或旧变更编号')
        corrected=parse_day(data.get('corrected'));deadline=parse_day(data.get('deadline'));net=parse_net(data.get('net'))
        base=store.get(run_id)
        if target not in change_ids(base):raise DomainError('REFERENCE','撤销未指向本批次旧变更')
        action=decide(base,corrected,deadline)
        if action=='revise':return jsonify(action=action,run_id=run_id,message='批次未签审，将撤销并入输入后重新跑批，修订旧批次即可')
        if action=='settle':return jsonify(action=action,run_id=run_id,message='实收更正未晚于退费截止，柜面当天正常退费，无需补退结论')
        result,duplicate=store.supplement(run_id,cancel_id,target,net,corrected.isoformat(),deadline.isoformat())
        return jsonify(action='supplement',supplement=result,duplicate=duplicate,supplement_net=store.get(run_id)['supplement_net'])
    @app.get('/api/runs/<run_id>/supplements')
    def supplements(run_id):
        state=store.get(run_id);return jsonify(supplements=state['supplements'],supplement_net=state['supplement_net'])
    @app.get('/api/runs/<run_id>/signoffs')
    def signoffs(run_id):return jsonify(signoffs=store.get(run_id)['signoffs'])
    @app.get('/api/archive/<run_id>/export.csv')
    def download(run_id):
        state=store.get(run_id)
        if not state.get('reconcile',{}).get('balanced'):raise DomainError('UNBALANCED','对账未平')
        return Response(bundle(state),mimetype='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename=evidence.csv'})
    with app.app_context():
        if not store.history():
            state=run(load('dirty.csv'),{});store.save(state['run_id'],state)
    return app

app=create_app()
if __name__=='__main__':app.run(host='0.0.0.0',port=8000)
