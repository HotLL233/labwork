"""Read method definitions and copy source files into an isolated local preview directory."""
import importlib.util
import json
import shutil
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
platform = root.parent
sys.path.insert(0, str(platform))
from records.calculations import METHODS, FIELDS

from records import xlsx as module
projects=[]
samples={'mass':'1','volume':'10','result':'15','original_mass':'10','total_mass':'20','water_result':'100','mass1':'2','total1':'20','mass2':'5','total2':'50','reading':'1.2','before':'2000','after1':'1995','after2':'1995','start':'150','end':'153'}
project_methods={'D006':['water','water_diluted','total_n','ammonia_n','effective_cl','chloride','hydrogen','ammonia'],'Q':['water','fluoride','chloride','hydrogen','loss','melting']}
for code in ['A3','D006','EF','FB01','FPO','J001','K001','Q','Q007','RD05','S002','T001']:
    methods=[]
    for key in project_methods.get(code,[]):
        name,kind,params=METHODS[key]
        fields=[dict(key=k,label=label,unit=unit,sample=('100' if kind=='loss' and k=='mass' else '60' if kind=='water_diluted' and k=='result' else samples[k])) for k,label,unit in FIELDS[kind]]
        methods.append(dict(key=key,name=name,kind=kind,params=params,fields=fields,columns=module.columns_for(key),sheet=module.SHEET_NAMES[key]))
    projects.append(dict(code=code,name='环糊精' if code=='T001' else f'{code} 理化检测',methods=methods))
(root/'catalog.json').write_text(json.dumps(projects,ensure_ascii=False,indent=2),encoding='utf-8')
home=platform/'runtime/rust-preview'
home.mkdir(parents=True,exist_ok=True)
(home/'.labwork-rust-preview').write_text('0.0.2-alpha.1; local preview only')
(home/'workbooks').mkdir(exist_ok=True)
for code,path in {'D006':Path('C:/网盘/理化检测/理化检测/D006-理化/2026/2026.10/D006-理化-202610.xlsx'),'Q':Path('C:/网盘/理化检测/理化检测/Q-理化/2026/2026.10/Q-理化-2026.10.xlsx')}.items():
    target=home/'workbooks'/f'{code}.xlsx'
    if not target.exists():shutil.copyfile(path,target)
    print(f'{code}: isolated copy ready')
