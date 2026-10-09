'use strict';
const LabGrid = {
  maxRows: 200,
  date(value) {
    const s=String(value).trim();
    if(/^\d{8}$/.test(s))return `${s.slice(0,4)}-${s.slice(4,6)}-${s.slice(6,8)}`;
    const match=s.match(/^(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?$/);
    if(match)return `${match[1]}-${match[2].padStart(2,'0')}-${match[3].padStart(2,'0')}`;
    if(/^\d{5}$/.test(s)){const d=new Date(Date.UTC(1899,11,30)+Number(s)*86400000);return d.toISOString().slice(0,10);}
    return s;
  },
  paste(rows,keys,startRow,startCol,text,empty) {
    const matrix=text.replace(/\r/g,'').replace(/\n$/,'').split('\n').map(r=>r.split('\t'));
    if(matrix.length+startRow>this.maxRows||matrix.some(r=>r.length+startCol>keys.length))throw Error('粘贴范围超过200行或本方法字段列数');
    const result=rows.map(r=>({...r}));
    while(result.length<matrix.length+startRow)result.push(empty());
    matrix.forEach((r,i)=>r.forEach((v,c)=>{const key=keys[startCol+c];result[startRow+i][key]=key==='date'?this.date(v):v.trim();}));
    return result;
  },
  series(values,offset,key) {
    const last=values.at(-1),previous=values.at(-2);
    if(key==='date'&&/^\d{4}-\d{2}-\d{2}$/.test(last)) {
      const current=Date.parse(last+'T00:00:00Z'),before=Date.parse(previous+'T00:00:00Z');
      const step=values.length>1?current-before:86400000;
      if(Number.isFinite(current)&&Number.isFinite(step))return new Date(current+step*offset).toISOString().slice(0,10);
    }
    if(key!=='batch'&&key!=='notes'&&last!==''&&Number.isFinite(Number(last))&& (values.length===1||previous!==''&&Number.isFinite(Number(previous)))) {
      const step=values.length>1?Number(last)-Number(previous):1;
      return String(Number((Number(last)+step*offset).toFixed(12)));
    }
    const a=String(last).match(/^(.*?)(\d+)$/),b=String(previous).match(/^(.*?)(\d+)$/);
    if(key==='batch'&&a&&(values.length===1||b&&a[1]===b[1])) {
      const step=values.length>1?Number(a[2])-Number(b[2]):1;
      const n=Number(a[2])+step*offset;
      if(Number.isSafeInteger(n)&&n>=0)return a[1]+String(n).padStart(a[2].length,'0');
    }
    return values[(values.length-1+offset)%values.length];
  },
  fill(rows,keys,selection,endRow,mode) {
    const result=rows.map(r=>({...r}));
    if(!Number.isInteger(endRow)||endRow>=rows.length||endRow>=this.maxRows||endRow<=selection.r1)throw Error('请在选区下方已有行中选择整数填充终点');
    for(let c=selection.c0;c<=selection.c1;c++) {
      const key=keys[c],values=rows.slice(selection.r0,selection.r1+1).map(r=>r[key]||'');
      for(let r=selection.r1+1;r<=endRow;r++)result[r][key]=mode==='series'?this.series(values,r-selection.r1,key):values[(r-selection.r1-1)%values.length];
    }
    return result;
  },
  labels(rows) {
    let date='',batch='';
    return rows.map(r=>{
      const nextDate=this.date(r.date||date);
      if(nextDate!==date||r._requireBatch)batch='';
      date=nextDate;batch=r.batch?.trim()||batch;
      return {...r,date,batch};
    });
  },
  entries(rows,fields) {
    return this.labels(rows).map((r,i)=>{
      if(!r.date||!r.batch)throw Error(`第${i+1}行缺少日期或批号；新批号须明确填写，同组空白行可继承上一行`);
      return {date:r.date,batch:r.batch,notes:r.notes||'',measurement:Object.fromEntries(fields.map(f=>[f.key,r[f.key]||'']))};
    });
  },
  remove(rows,first,last) {
    const kept=this.labels(rows).filter((_,i)=>i<first||i>last);
    return kept.map((r,i)=>({...r,_requireBatch:r._requireBatch||(!r.batch&&(i===0||!!kept[i-1].batch))}));
  }
};
if(typeof module!=='undefined')module.exports=LabGrid;
