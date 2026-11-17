from datetime import date
from decimal import Decimal,InvalidOperation,ROUND_HALF_UP
from app.errors import DomainError

SIGNED=('通过','已补退')

def parse_day(value):
    try:return date.fromisoformat(str(value))
    except ValueError:raise DomainError('DATE','日期无效')

def parse_net(value):
    try:net=Decimal(str(value))
    except InvalidOperation:raise DomainError('NUMBER','补退净额无效')
    if not net.is_finite():raise DomainError('NUMBER','补退净额无效')
    return str(net.quantize(Decimal('.01'),rounding=ROUND_HALF_UP))

def decide(base,corrected,deadline):
    # 未签审：证据未固定，修订旧批次即可；已签审且更正晚于截止：另立补退结论。
    if base['decision'] not in SIGNED:return 'revise'
    if corrected<=deadline:return 'settle'
    return 'supplement'

def change_ids(base):
    ids=set()
    for p in base.get('policies',[]):
        ids.update(c['id'] for c in p.get('changes',[]));ids.update(p.get('cancelled',[]))
    return ids

def net_total(supplements):
    total=sum((Decimal(s['net']) for s in supplements),Decimal(0))
    return str(total.quantize(Decimal('.01'),rounding=ROUND_HALF_UP))
