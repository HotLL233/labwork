from decimal import Decimal, InvalidOperation, localcontext

D = Decimal

# Fields and formulas are deliberately explicit: no uploaded code or Excel eval.
METHODS = {
    'chloride': ('氯离子', 'titration', {'c': '0.1032', 'mw': '35.45', 'factor': '1', 'blank': '0'}),
    'hydrogen': ('氢离子', 'titration', {'c': '0.09969', 'mw': '1', 'factor': '1', 'blank': '0'}),
    'total_n': ('总氮', 'titration', {'c': '0.5128', 'mw': '14.01', 'factor': '1', 'blank': '0.05'}),
    'ammonia_n': ('氨氮', 'titration', {'c': '0.5128', 'mw': '14.01', 'factor': '1', 'blank': '0.05'}),
    'ammonia': ('氨水', 'titration', {'c': '0.5128', 'mw': '17.03', 'factor': '1', 'blank': '0'}),
    'effective_cl': ('有效氯', 'titration', {'c': '0.1051', 'mw': '35.45', 'factor': '0.5', 'blank': '0'}),
    'water': ('水分', 'instrument', {}),
    'water_diluted': ('水分（加水稀释换算）', 'water_diluted', {}),
    'fluoride': ('氟离子（两次质量稀释）', 'fluoride', {}),
    'loss': ('干燥失重', 'loss', {}),
    'melting': ('熔点', 'melting', {}),
}
FIELDS = {
    'titration': [('mass', '称量质量', 'g'), ('volume', '滴定体积', 'mL')],
    'instrument': [('result', '仪器水分结果', '%')],
    'water_diluted': [('result', '稀释样水分结果', '%'), ('original_mass', '原样质量', 'g'),
        ('total_mass', '加水后总质量', 'g'), ('water_result', '所加水水分', '%')],
    'fluoride': [('mass1', '一次称量', 'g'), ('total1', '一次稀释总质量', 'g'),
        ('mass2', '二次称量', 'g'), ('total2', '二次稀释总质量', 'g'), ('reading', '仪器示数', 'ppm')],
    'loss': [('mass', '样品质量', 'mg'), ('before', '恒重前样品与瓶总重', 'mg'),
        ('after1', '第一次恒重后总重', 'mg'), ('after2', '第二次恒重后总重', 'mg')],
    'melting': [('start', '初熔点', '℃'), ('end', '终熔点', '℃')],
}


def number(value, label='数值'):
    try:
        result = D(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(f'{label}必须是数字') from None
    if not result.is_finite() or abs(result) > D('1e10') or result.as_tuple().exponent < -12:
        raise ValueError(f'{label}超出支持的精度或范围')
    return result


def validate_params(kind, params):
    if kind == 'titration':
        for key in ('c', 'mw', 'factor'):
            if number(params.get(key), key) <= 0:
                raise ValueError(f'{key}必须大于零')
        if number(params.get('blank', '0'), '空白体积') < 0:
            raise ValueError('空白体积不能为负数')


def calculate(kind, values, params):
    if kind not in FIELDS:
        raise ValueError('尚未支持此计算方法')
    v = {key: number(values.get(key), label) for key, label, _ in FIELDS[kind]}
    validate_params(kind, params)
    for key, value in v.items():
        if kind != 'melting' and value < 0:
            raise ValueError('输入不能为负数')
        if key in {'mass', 'mass1', 'mass2', 'original_mass'} and value <= 0:
            raise ValueError('称量质量必须大于零')
    with localcontext() as context:
        context.prec = 36
        if kind == 'titration':
            corrected = v['volume'] - number(params['blank'])
            if corrected < 0:
                raise ValueError('滴定体积不能小于空白体积')
            result = number(params['c']) * corrected * number(params['mw']) * number(params['factor']) / v['mass'] / 10
            output = {'result': str(result), 'display': f'{result:.5f}', 'unit': '%'}
        elif kind == 'instrument':
            if v['result'] > 100:
                raise ValueError('水分百分数不能超过 100')
            output = {'result': str(v['result']), 'display': f"{v['result']:.5f}", 'unit': '%'}
        elif kind == 'water_diluted':
            if v['total_mass'] < v['original_mass'] or max(v['result'], v['water_result']) > 100:
                raise ValueError('总质量须不小于原样质量，水分须在 0–100% 之间')
            result = (v['result'] * v['total_mass'] - (v['total_mass'] - v['original_mass']) * v['water_result']) / v['original_mass']
            if not 0 <= result <= 100:
                raise ValueError('换算结果超出 0–100%，请核查稀释参数')
            output = {'result': str(result), 'display': f'{result:.5f}', 'unit': '%'}
        elif kind == 'fluoride':
            if v['total1'] < v['mass1'] or v['total2'] < v['mass2']:
                raise ValueError('稀释总质量不能小于取样质量')
            result = v['reading'] * v['total2'] / v['mass2'] * v['total1'] / v['mass1']
            output = {'result': str(result), 'display': f'{result:.5f}', 'unit': 'ppm', 'percent': str(result / 10000)}
        elif kind == 'loss':
            after = (v['after1'] + v['after2']) / 2
            if v['before'] < v['mass'] or after > v['before'] or v['before'] - after > v['mass']:
                raise ValueError('称量关系不合理，请核查恒重前后质量')
            result = (v['before'] - after) / v['mass'] * 100
            output = {'result': str(result), 'display': f'{result:.5f}', 'unit': '%'}
        else:
            if v['end'] < v['start']:
                raise ValueError('终熔点不能低于初熔点')
            output = {'start': str(v['start']), 'end': str(v['end']), 'display': f"{v['start']}–{v['end']}", 'unit': '℃'}
    return {k: str(x) for k, x in v.items()}, output


def formulas(kind, columns, row, params):
    cell = {key: f'{col}{row}' for key, col in columns.items()}
    if kind == 'titration':
        return {'result': f"{params['c']}*({cell['volume']}-{params['blank']})*{params['mw']}*{params['factor']}/{cell['mass']}/1000*100"}
    if kind == 'water_diluted':
        return {'result': f"({cell['raw_result']}*{cell['total_mass']}-({cell['total_mass']}-{cell['original_mass']})*{cell['water_result']})/{cell['original_mass']}"}
    if kind == 'fluoride':
        return {'result': f"{cell['reading']}*{cell['total2']}/{cell['mass2']}*{cell['total1']}/{cell['mass1']}", 'percent': f"{cell['result']}/10000"}
    if kind == 'loss':
        return {'result': f"({cell['before']}-({cell['after1']}+{cell['after2']})/2)/{cell['mass']}*100"}
    return {}
