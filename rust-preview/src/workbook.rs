use crate::{Method, Request, calc::{self, Values}};
use roxmltree::{Document, Node};
use serde_json::{Value, json};
use std::{collections::{BTreeMap,HashSet}, io::{Cursor,Read,Write}, path::Path};
use xmltree::{Element, XMLNode};
use zip::{ZipArchive, ZipWriter, write::SimpleFileOptions};

const MAIN:&str="http://schemas.openxmlformats.org/spreadsheetml/2006/main";
struct Book {parts:BTreeMap<String,Vec<u8>>,strings:Vec<String>,sheets:BTreeMap<String,String>,date1904:bool}
fn text(n:Node<'_, '_>)->String {n.descendants().filter(|n|n.is_element()&&n.tag_name().name()=="t").filter_map(|n|n.text()).collect()}
fn attr(n:Node<'_, '_>,key:&str)->String {n.attribute(key).unwrap_or("").to_owned()}
fn doc(bytes:&[u8])->Result<Document<'_>,String>{Document::parse(std::str::from_utf8(bytes).map_err(|_|"XML编码不支持")?).map_err(|_|"工作簿XML无效".into())}
fn read(bytes:&[u8])->Result<Book,String>{
    if bytes.len()>20*1024*1024{return Err("工作簿超过20MB".into());}
    let mut zip=ZipArchive::new(Cursor::new(bytes)).map_err(|_|"不是有效的xlsx")?;
    if zip.len()>3000{return Err("工作簿部件过多".into());}
    let mut parts=BTreeMap::new();let mut expanded=0u64;let mut names=HashSet::new();
    for i in 0..zip.len(){
        let mut f=zip.by_index(i).map_err(|_|"ZIP部件无效")?;let name=f.name().to_owned();
        expanded+=f.size();
        if expanded>100*1024*1024||!names.insert(name.clone())||name.split('/').any(|s|s=="..")||name.starts_with('/')||name.contains('\\')||name.contains("vbaProject"){return Err("不支持的工作簿结构或宏文件".into());}
        let mut data=Vec::new();f.read_to_end(&mut data).map_err(|_|"工作簿解压失败")?;
        if name.ends_with(".xml")&&data.windows(9).any(|s|s.eq_ignore_ascii_case(b"<!DOCTYPE")){return Err("不接受含DTD的XML".into());}
        parts.insert(name,data);
    }
    let mut strings=Vec::new();
    if let Some(s)=parts.get("xl/sharedStrings.xml"){for si in doc(s)?.descendants().filter(|n|n.has_tag_name("si")){strings.push(text(si));}}
    let relations=doc(parts.get("xl/_rels/workbook.xml.rels").ok_or("缺少工作簿关系")?)?;
    let rels=relations.descendants().filter(|n|n.has_tag_name("Relationship")&&n.attribute("TargetMode")!=Some("External")).map(|n|(attr(n,"Id"),attr(n,"Target"))).collect::<BTreeMap<_,_>>();
    let book=doc(parts.get("xl/workbook.xml").ok_or("缺少工作簿结构")?)?;
    let mut sheets=BTreeMap::new();
    for n in book.descendants().filter(|n|n.has_tag_name("sheet")){
        let id=n.attribute(("http://schemas.openxmlformats.org/officeDocument/2006/relationships","id")).ok_or("工作表关系无效")?;
        let target=rels.get(id).ok_or("工作表关系不存在")?;
        let target=if target.starts_with('/') {target.trim_start_matches('/').to_owned()}else{format!("xl/{target}")};
        if !target.starts_with("xl/worksheets/")||!parts.contains_key(&target){return Err("工作表链接无效".into());}
        sheets.insert(attr(n,"name"),target);
    }
    let date1904=book.descendants().any(|n|n.has_tag_name("workbookPr")&&matches!(n.attribute("date1904"),Some("1"|"true")));
    Ok(Book{parts,strings,sheets,date1904})
}
fn target<'a>(book:&'a Book,name:&str)->Result<&'a String,String>{
    let matches=book.sheets.iter().filter(|(s,_)|s.trim()==name.trim()).collect::<Vec<_>>();
    if matches.len()!=1{return Err("目标工作表缺失或名称歧义".into());}Ok(matches[0].1)
}
fn cell_value(n:Node<'_, '_>,strings:&[String])->Result<String,String>{
    if n.attribute("t")==Some("inlineStr"){return Ok(text(n));}
    let value=n.children().find(|n|n.has_tag_name("v")).and_then(|n|n.text()).unwrap_or("");
    if n.attribute("t")==Some("s"){return strings.get(value.parse::<usize>().map_err(|_|"共享字符串索引无效")?).cloned().ok_or("共享字符串不存在".into());}
    Ok(value.into())
}
fn coord(s:&str)->Result<(u32,u32),String>{
    let s=s.replace('$',"");let mut col=0u32;let mut row=String::new();
    for c in s.chars(){if c.is_ascii_uppercase()&&row.is_empty(){col=col.checked_mul(26).and_then(|v|v.checked_add(c as u32-'A' as u32+1)).ok_or("单元格坐标无效")?;}else if c.is_ascii_digit(){row.push(c);}else{return Err("单元格坐标无效".into());}}
    let row=row.parse::<u32>().map_err(|_|"行号无效")?;
    if col==0||col>16384||row==0||row>1048576{return Err("坐标超出Excel范围".into());}Ok((row,col))
}
fn letter(mut col:u32)->String {let mut out=String::new();while col>0{col-=1;out.insert(0,(b'A'+(col%26)as u8)as char);col/=26;}out}
fn colours(book:&Book)->Vec<String>{
    let mut out=vec!["FFFFFF","000000","E7E6E6","44546A","4472C4","ED7D31","A5A5A5","FFC000","5B9BD5","70AD47","0563C1","954F72"].into_iter().map(str::to_owned).collect::<Vec<_>>();
    if let Some(xml)=book.parts.get("xl/theme/theme1.xml"){if let Ok(d)=doc(xml){if let Some(scheme)=d.descendants().find(|n|n.is_element()&&n.tag_name().name()=="clrScheme"){
        let theme=scheme.children().filter(Node::is_element).map(|n|n.children().find(Node::is_element).map(|c|c.attribute("val").or(c.attribute("lastClr")).unwrap_or("000000").to_owned()).unwrap_or("000000".into())).collect::<Vec<_>>();
        if theme.len()==12{out=vec![theme[1].clone(),theme[0].clone(),theme[3].clone(),theme[2].clone()];out.extend_from_slice(&theme[4..]);}
    }}}
    out
}
fn colour(n:Option<Node<'_, '_>>,theme:&[String],fallback:&str)->String{
    let Some(n)=n else{return fallback.into()};
    let mut hex=n.attribute("rgb").map(str::to_owned).or_else(||n.attribute("theme").and_then(|x|x.parse::<usize>().ok()).and_then(|i|theme.get(i).cloned())).unwrap_or_else(||fallback.trim_start_matches('#').into());
    if hex.len()==8{hex=hex[2..].into();}
    if hex.len()!=6||!hex.bytes().all(|c|c.is_ascii_hexdigit()){return fallback.into();}
    if let Some(tint)=n.attribute("tint").and_then(|s|s.parse::<f64>().ok()).filter(|v|v.is_finite()&&v.abs()<=1.){
        hex=(0..3).map(|i|{let x=u8::from_str_radix(&hex[i*2..i*2+2],16).unwrap()as f64;format!("{:02X}",if tint<0.{x*(1.+tint)}else{x+(255.-x)*tint}.round()as u8)}).collect();
    }format!("#{hex}")
}
fn styles(book:&Book)->Result<(Vec<Value>,Vec<String>),String>{
    let Some(bytes)=book.parts.get("xl/styles.xml")else{return Ok((vec![json!({})],vec!["General".into()]));};
    let d=doc(bytes)?;let root=d.root_element();let theme=colours(book);
    let entries=|name:&str|root.children().find(|n|n.has_tag_name(name)).map(|n|n.children().filter(Node::is_element).collect::<Vec<_>>()).unwrap_or_default();
    let fonts=entries("fonts");let fills=entries("fills");let borders=entries("borders");
    let mut formats=BTreeMap::from([(0,"General".to_owned()),(2,"0.00".into()),(9,"0%".into()),(10,"0.00%".into()),(14,"yyyy-mm-dd".into()),(15,"yyyy-mm-dd".into()),(16,"mm-dd".into()),(17,"yyyy-mm".into()),(22,"yyyy-mm-dd hh:mm".into())]);
    for id in [27,28,29,30,31,34,35,36,50,51,52,53,54,57,58] {formats.insert(id,"yyyy-mm-dd".into());}
    for n in entries("numFmts"){if let Ok(id)=attr(n,"numFmtId").parse::<u32>(){formats.insert(id,attr(n,"formatCode"));}}
    let mut css=Vec::new();let mut num=Vec::new();
    for xf in entries("cellXfs"){
        let index=|key:&str|xf.attribute(key).and_then(|s|s.parse::<usize>().ok()).unwrap_or(usize::MAX);
        let mut style=json!({});
        if let Some(font)=fonts.get(index("fontId")).copied(){
            let child=|name:&str|font.children().find(|n|n.has_tag_name(name));
            let name=child("name").and_then(|n|n.attribute("val")).unwrap_or("Microsoft YaHei");
            style["fontFamily"]=json!(name.chars().filter(|c|c.is_alphanumeric()||*c==' '||*c=='-').collect::<String>());
            style["fontSize"]=json!(format!("{}px",child("sz").and_then(|n|n.attribute("val")).and_then(|s|s.parse::<f32>().ok()).unwrap_or(11.).clamp(6.,48.)*4./3.));
            style["fontWeight"]=json!(if child("b").is_some(){"700"}else{"400"});
            style["color"]=json!(colour(child("color"),&theme,"#172b22"));
            if child("i").is_some(){style["fontStyle"]=json!("italic");}
        }
        if let Some(fill)=fills.get(index("fillId")).copied(){if let Some(pattern)=fill.children().find(|n|n.has_tag_name("patternFill")){if pattern.attribute("patternType")==Some("solid"){style["backgroundColor"]=json!(colour(pattern.children().find(|n|n.has_tag_name("fgColor")),&theme,"#ffffff"));}}}
        if let Some(border)=borders.get(index("borderId")).copied(){for (side,property) in [("left","borderLeft"),("right","borderRight"),("top","borderTop"),("bottom","borderBottom")]{if let Some(n)=border.children().find(|n|n.has_tag_name(side)){if let Some(kind)=n.attribute("style"){
            let width=if ["medium","thick","double"].contains(&kind){2}else{1};let kind=if kind=="dashed"{"dashed"}else if kind=="dotted"{"dotted"}else if kind=="double"{"double"}else{"solid"};
            style[property]=json!(format!("{width}px {kind} {}",colour(n.children().find(|n|n.has_tag_name("color")),&theme,"#333333")));
        }}}}
        if let Some(align)=xf.children().find(|n|n.has_tag_name("alignment")){
            if let Some(v)=align.attribute("horizontal").filter(|v|["left","right","center","justify"].contains(v)){style["textAlign"]=json!(v);}
            if let Some(v)=align.attribute("vertical"){style["verticalAlign"]=json!(match v{"center"=>"middle","top"=>"top",_=>"bottom"});}
            style["whiteSpace"]=json!(if align.attribute("wrapText")==Some("1"){"normal"}else{"nowrap"});
        }
        css.push(style);num.push(formats.get(&xf.attribute("numFmtId").unwrap_or("0").parse::<u32>().unwrap_or(0)).cloned().unwrap_or("General".into()));
    }if css.is_empty(){css.push(json!({}));num.push("General".into());}Ok((css,num))
}
fn is_date_format(format:&str)->bool {
    let (mut quoted,mut bracket,mut skip)=(false,false,false);let mut tokens=String::new();
    for c in format.to_lowercase().chars() {
        if skip {skip=false;continue;}
        if c=='"' {quoted=!quoted;continue;}
        if quoted {continue;}
        if c=='[' {bracket=true;continue;}if c==']' {bracket=false;continue;}if bracket {continue;}
        if matches!(c,'\\'|'_'|'*') {skip=true;continue;}
        tokens.push(c);
    }
    (tokens.contains('y')||tokens.contains('d'))&&tokens.contains('m')
}
fn date_serial(date:chrono::NaiveDate,date1904:bool)->i64 {
    let base=if date1904 {chrono::NaiveDate::from_ymd_opt(1904,1,1).unwrap()}else if date<chrono::NaiveDate::from_ymd_opt(1900,3,1).unwrap(){chrono::NaiveDate::from_ymd_opt(1899,12,31).unwrap()}else{chrono::NaiveDate::from_ymd_opt(1899,12,30).unwrap()};
    (date-base).num_days()
}
fn serial_date(n:i64,date1904:bool)->Option<chrono::NaiveDate> {
    let base=if date1904{chrono::NaiveDate::from_ymd_opt(1904,1,1)?}else if n<60{chrono::NaiveDate::from_ymd_opt(1899,12,31)?}else{chrono::NaiveDate::from_ymd_opt(1899,12,30)?};
    base.checked_add_signed(chrono::Duration::try_days(n)?)
}
fn cell_date(value:&str,date1904:bool)->Option<chrono::NaiveDate> {
    let value=value.trim();
    let parsed=["%Y-%m-%d","%Y/%m/%d","%Y.%m.%d","%Y%m%d"].iter().find_map(|f|chrono::NaiveDate::parse_from_str(value,f).ok())
        .or_else(||value.parse::<i64>().ok().and_then(|n|serial_date(n,date1904)));
    parsed.filter(|d|(1900..=2100).contains(&chrono::Datelike::year(d)))
}
fn display(value:&str,format:&str,numeric:bool,date1904:bool)->String{
    if !numeric||value.is_empty(){return value.into();}let Ok(mut n)=value.parse::<f64>()else{return value.into()};
    if !n.is_finite(){return value.into();}
    let lower=format.to_lowercase();
    if is_date_format(format)&&n>=-1461.&&n<73416.{if !date1904&&n as i64==60{return "1900-02-29".into();}if let Some(date)=serial_date(n as i64,date1904){return if lower.contains('y'){date.to_string()}else{date.format("%m-%d").to_string()};}}
    if format=="General" {return value.into();}
    let suffix=if format.contains('%'){n*=100.;"%"}else{""};
    let decimals=format.split('.').nth(1).map(|s|s.chars().take_while(|c|*c=='0'||*c=='#').count()).unwrap_or(0).min(8);
    format!("{n:.decimals$}{suffix}")
}
pub fn view(bytes:&[u8],method:&Method,highlight:&[u32])->Result<Value,String>{
    let sheet=&method.sheet;
    let book=read(bytes)?;let path=target(&book,sheet)?;let d=doc(&book.parts[path])?;let (css,num)=styles(&book)?;
    let mut all=BTreeMap::new();let mut heights=BTreeMap::new();let mut max_col=1;let mut max_row=3;
    for row in d.descendants().filter(|n|n.has_tag_name("row")){
        let r=attr(row,"r").parse::<u32>().map_err(|_|"行号无效")?;
        heights.insert(r,row.attribute("ht").and_then(|s|s.parse::<f32>().ok()).unwrap_or(18.)*4./3.);
        for cell in row.children().filter(|n|n.has_tag_name("c")){
            let reference=attr(cell,"r");let (r,c)=coord(&reference)?;max_col=max_col.max(c);max_row=max_row.max(r);
            let value=cell_value(cell,&book.strings)?;let style=cell.attribute("s").and_then(|s|s.parse::<usize>().ok()).unwrap_or(0);
            let formula=cell.children().find(|n|n.has_tag_name("f")).and_then(|n|n.text()).unwrap_or("");
            let numeric=!matches!(cell.attribute("t"),Some("s"|"inlineStr"|"str"|"e"|"b"));
            let is_date=r>=4&&reference.trim_end_matches(|c:char|c.is_ascii_digit())==method.columns["date"];
            let shown=if is_date {cell_date(&value,book.date1904).map(|d|d.to_string()).unwrap_or_else(||value.clone())}else{display(&value,num.get(style).map(String::as_str).unwrap_or("General"),numeric,book.date1904)};
            all.insert((r,c),json!({"ref":reference,"value":shown,"raw":value,"formula":formula,"style":style,"highlight":highlight.contains(&r)}));
        }
    }
    max_col=max_col.min(40); // shortcut: method preview displays A:AN; wider templates need pagination before onboarding.
    let mut widths=vec![90f32;max_col as usize];
    for n in d.descendants().filter(|n|n.has_tag_name("col")){
        let start=attr(n,"min").parse::<u32>().unwrap_or(1);let end=attr(n,"max").parse::<u32>().unwrap_or(start).min(max_col);
        let width=n.attribute("width").and_then(|s|s.parse::<f32>().ok()).unwrap_or(12.)*7.+5.;
        for c in start..=end{widths[(c-1)as usize]=width.clamp(35.,650.);}
    }
    let mut merges=Vec::new();
    for n in d.descendants().filter(|n|n.has_tag_name("mergeCell")){let range=attr(n,"ref");let mut bounds=range.split(':');let a=coord(bounds.next().ok_or("合并范围无效")?)?;let b=coord(bounds.next().unwrap_or(&range))?;merges.push(json!({"row":a.0,"col":a.1,"rowspan":b.0-a.0+1,"colspan":b.1-a.1+1}));}
    let tail=max_row.saturating_sub(12).max(4);let selected=(1..=3).chain(tail..=max_row).chain(highlight.iter().copied()).collect::<std::collections::BTreeSet<_>>();
    let rows=selected.iter().map(|r|json!({"number":r,"height":heights.get(r).copied().unwrap_or(24.),"cells":(1..=max_col).map(|c|all.get(&(*r,c)).cloned().unwrap_or_else(||json!({"ref":format!("{}{r}",letter(c)),"value":"","raw":"","formula":"","style":0,"highlight":highlight.contains(r)}))).collect::<Vec<_>>()})).collect::<Vec<_>>();
    Ok(json!({"name":sheet,"rows":rows,"columns":widths.iter().enumerate().map(|(i,w)|json!({"label":letter(i as u32+1),"width":w})).collect::<Vec<_>>(),"styles":css,"merges":merges,"last_row":max_row,"tail_start":tail,"sheet_names":book.sheets.keys().collect::<Vec<_>>()}))
}
fn el(name:&str)->Element{let mut e=Element::new(name);e.namespace=Some(MAIN.into());e}
fn value_cell(reference:String,style:Option<String>,value:String,text:bool,formula:Option<&String>)->Element{
    let mut cell=el("c");cell.attributes.insert("r".into(),reference);if let Some(style)=style{cell.attributes.insert("s".into(),style);}
    if text{cell.attributes.insert("t".into(),"inlineStr".into());let mut string=el("is");let mut t=el("t");t.attributes.insert("xml:space".into(),"preserve".into());t.children.push(XMLNode::Text(value));string.children.push(XMLNode::Element(t));cell.children.push(XMLNode::Element(string));}
    else{if let Some(formula)=formula{let mut f=el("f");f.children.push(XMLNode::Text(formula.clone()));cell.children.push(XMLNode::Element(f));}let mut v=el("v");v.children.push(XMLNode::Text(value));cell.children.push(XMLNode::Element(v));}cell
}
fn last_data_row(book:&Book,method:&Method)->Result<u32,String>{
    let d=doc(&book.parts[target(book,&method.sheet)?])?;
    let mut last=3;
    for cell in d.descendants().filter(|n|n.has_tag_name("c")){
        let reference=attr(cell,"r");let (r,_)=coord(&reference)?;
        let col=reference.trim_end_matches(|c:char|c.is_ascii_digit());
        if r>=4&&!cell_value(cell,&book.strings)?.is_empty()&&method.columns.iter().any(|(key,c)|key!="date"&&key!="notes"&&c==col){last=last.max(r);}
    }Ok(last)
}
pub fn next_row(bytes:&[u8],method:&Method)->Result<u32,String>{Ok(last_data_row(&read(bytes)?,method)?+1)}
pub fn append(bytes:&[u8],method:&Method,request:&Request,outputs:&[Values])->Result<(Vec<u8>,Vec<u32>),String>{
    let book=read(bytes)?;let path=target(&book,&method.sheet)?.clone();let d=doc(&book.parts[&path])?;
    let last=last_data_row(&book,method)?;let mut source_styles=BTreeMap::new();let mut values=BTreeMap::new();
    for cell in d.descendants().filter(|n|n.has_tag_name("c")){
        let reference=attr(cell,"r");let (r,_)=coord(&reference)?;let col=reference.trim_end_matches(|c:char|c.is_ascii_digit()).to_owned();let value=cell_value(cell,&book.strings)?;
        values.insert(reference.clone(),value);
        if r==3{source_styles.insert(col,attr(cell,"s"));}
    }
    if request.entries.len()!=outputs.len()||outputs.is_empty(){return Err("计算行数不一致".into());}
    let (mut previous_date,mut previous_batch)=(String::new(),String::new());
    for r in 4..=last {
        if let Some(v)=values.get(&format!("{}{r}",method.columns["date"])).filter(|v|!v.is_empty()) {
            let date=cell_date(v,book.date1904).map(|d|d.to_string()).unwrap_or_else(||v.clone());if date!=previous_date {previous_batch.clear();}previous_date=date;
        }
        if let Some(v)=values.get(&format!("{}{r}",method.columns["batch"])).filter(|v|!v.is_empty()){previous_batch=v.clone();}
    }
    let mut replacements=BTreeMap::new();
    let (_,formats)=styles(&book)?;
    let date_col=&method.columns["date"];
    let source_index=source_styles.get(date_col).and_then(|s|s.parse::<usize>().ok()).unwrap_or(0);
    if !formats.get(source_index).is_some_and(|s|is_date_format(s)) {
        let mut styles_xml=Element::parse(Cursor::new(book.parts.get("xl/styles.xml").ok_or("缺少单元格样式")?)).map_err(|_|"样式XML无效")?;
        let xfs=styles_xml.get_mut_child("cellXfs").ok_or("缺少单元格样式")?;
        let count=xfs.children.iter().filter_map(|n|n.as_element()).count();
        let mut xf=xfs.children.iter().filter_map(|n|n.as_element()).nth(source_index).ok_or("日期样式无效")?.clone();
        xf.attributes.insert("numFmtId".into(),"14".into());xf.attributes.insert("applyNumberFormat".into(),"1".into());
        let existing=xfs.children.iter().filter_map(|n|n.as_element()).position(|n|n==&xf);
        source_styles.insert(date_col.clone(),existing.unwrap_or(count).to_string());
        if existing.is_none(){
            xfs.children.push(XMLNode::Element(xf));xfs.attributes.insert("count".into(),(count+1).to_string());
            let mut xml=Vec::new();styles_xml.write(&mut xml).map_err(|_|"生成日期样式失败")?;replacements.insert("xl/styles.xml".to_owned(),xml);
        }
    }
    if !values.get("B1").is_some_and(|s|s.contains('批')){return Err("表头不匹配，不能写入".into());}
    for (key,col) in &method.columns{
        if method.kind!="water_diluted"&&calc::keys(&method.kind).contains(&key.as_str())&&key!="result"&&values.get(&format!("{col}1")).is_none_or(String::is_empty){return Err("输入列缺少表头，需要核定映射".into());}
    }
    let rows=(last+1..last+1+outputs.len()as u32).collect::<Vec<_>>();if rows.iter().any(|r|*r>1048576){return Err("记录区已满".into());}
    for ref_value in values.keys(){let (r,_)=coord(ref_value)?;if rows.contains(&r)&&!values[ref_value].is_empty(){return Err("待写入行已占用".into());}}
    for merged in d.descendants().filter(|n|n.has_tag_name("mergeCell")){
        let range=attr(merged,"ref");let ends=range.split(':').map(coord).collect::<Result<Vec<_>,_>>()?;
        if rows.iter().any(|r|*r>=ends[0].0&&*r<=ends.last().unwrap().0){return Err("待写入行涉及合并单元格，请核定映射".into());}
    }
    let mut root=Element::parse(Cursor::new(&book.parts[&path])).map_err(|_|"工作表XML无效")?;
    let data=root.get_mut_child("sheetData").ok_or("缺少记录区")?;
    for row in data.children.iter_mut().filter_map(XMLNode::as_mut_element) {
        if row.attributes.get("r").and_then(|r|r.parse::<u32>().ok()).is_some_and(|r|r>=4&&r<=last) {
            for cell in row.children.iter_mut().filter_map(XMLNode::as_mut_element) {
                let reference=cell.attributes.get("r").cloned().unwrap_or_default();
                if reference.trim_end_matches(|c:char|c.is_ascii_digit())==date_col&& !matches!(cell.attributes.get("t").map(String::as_str),Some("s"|"inlineStr"|"str"|"e"|"b")) {
                    if let Some(date)=values.get(&reference).and_then(|v|cell_date(v,book.date1904)) {
                        let serial=date_serial(date,book.date1904).to_string();
                        if cell.get_child("f").is_none()||values.get(&reference)==Some(&serial) {
                            if let Some(v)=cell.get_mut_child("v"){v.children=vec![XMLNode::Text(serial)];}
                            if let Some(style)=source_styles.get(date_col){cell.attributes.insert("s".into(),style.clone());}
                        }
                    }
                }
            }
        }
    }
    let mut expected_labels=Vec::new();
    for (index,row_no) in rows.iter().enumerate(){
        let entry=&request.entries[index];
        let mut raw=entry.measurement.clone();if method.kind=="water_diluted"{if let Some(value)=raw.remove("result"){raw.insert("raw_result".into(),value);}}
        raw.extend(outputs[index].clone());
        let date=chrono::NaiveDate::parse_from_str(&entry.date,"%Y-%m-%d").map_err(|_|"日期无效")?;
        let date_value=if entry.date==previous_date {String::new()}else{date_serial(date,book.date1904).to_string()};
        let batch_value=if entry.date==previous_date&&entry.batch==previous_batch {String::new()}else{entry.batch.clone()};
        expected_labels.push((date_value.clone(),batch_value.clone()));
        raw.extend(Values::from([("date".into(),date_value),("batch".into(),batch_value),("notes".into(),entry.notes.clone())]));
        previous_date=entry.date.clone();previous_batch=entry.batch.clone();
        let formulas=calc::formulas(&method.kind,&method.columns,*row_no,&method.params);
        data.children.retain(|n|!matches!(n,XMLNode::Element(e)if e.name=="row"&&e.attributes.get("r")==Some(&row_no.to_string())));
        let mut row=el("row");row.attributes.insert("r".into(),row_no.to_string());
        let mut cols=method.columns.iter().collect::<Vec<_>>();cols.sort_by_key(|(_,col)|(col.len(),col.as_str()));
        for (key,col) in cols{if let Some(value)=raw.get(key){row.children.push(XMLNode::Element(value_cell(format!("{col}{row_no}"),source_styles.get(col).filter(|s|!s.is_empty()).cloned(),value.clone(),key=="batch"||key=="notes",formulas.get(key))));}}
        data.children.push(XMLNode::Element(row));
    }
    data.children.sort_by_key(|n|match n{XMLNode::Element(e)if e.name=="row"=>e.attributes.get("r").and_then(|s|s.parse::<u32>().ok()).unwrap_or(0),_=>0});
    if let Some(dimension)=root.get_mut_child("dimension"){
        let old=dimension.attributes.get("ref").cloned().unwrap_or("A1".into());let (_,old_col)=coord(old.split(':').next_back().unwrap())?;
        let new_col=method.columns.values().map(|c|coord(&format!("{c}1")).map(|(_,col)|col)).collect::<Result<Vec<_>,_>>()?.into_iter().max().unwrap_or(1).max(old_col);
        dimension.attributes.insert("ref".into(),format!("A1:{}{}",letter(new_col),rows.last().unwrap()));
    }
    let mut xml=Vec::new();root.write(&mut xml).map_err(|_|"生成工作表失败")?;
    replacements.insert(path.clone(),xml);
    let mut original=ZipArchive::new(Cursor::new(bytes)).map_err(|_|"ZIP无效")?;let mut output=ZipWriter::new(Cursor::new(Vec::new()));
    for i in 0..original.len(){let file=original.by_index(i).map_err(|_|"ZIP部件无效")?;if let Some(xml)=replacements.get(file.name()){output.start_file(file.name(),SimpleFileOptions::default().compression_method(file.compression())).map_err(|_|"创建工作表失败")?;output.write_all(xml).map_err(|_|"写入工作表失败")?;}else{output.raw_copy_file(file).map_err(|_|"保留ZIP部件失败")?;}}
    let candidate=output.finish().map_err(|_|"生成工作簿失败")?.into_inner();let check=read(&candidate)?;
    if book.parts.iter().any(|(name,bytes)|!replacements.contains_key(name)&&check.parts.get(name)!=Some(bytes)){return Err("无关工作簿部件发生变化".into());}
    let result=doc(&check.parts[&path])?;
    for (row,(date,batch)) in rows.iter().zip(expected_labels){for (key,expected) in [("date",date),("batch",batch)]{let reference=format!("{}{row}",method.columns[key]);let cell=result.descendants().find(|n|n.has_tag_name("c")&&n.attribute("r")==Some(&reference)).ok_or("回读记录失败")?;if cell_value(cell,&check.strings)?!=expected{return Err("回读日期或批号不一致".into());}}}
    Ok((candidate,rows))
}
#[cfg(windows)]
pub fn replace_file(source:&Path,target:&Path)->std::io::Result<()>{
    use std::os::windows::ffi::OsStrExt;
    #[link(name="kernel32")] unsafe extern "system"{fn MoveFileExW(old:*const u16,new:*const u16,flags:u32)->i32;}
    let source=source.as_os_str().encode_wide().chain(Some(0)).collect::<Vec<_>>();let target=target.as_os_str().encode_wide().chain(Some(0)).collect::<Vec<_>>();
    if unsafe{MoveFileExW(source.as_ptr(),target.as_ptr(),1|8)}==0{Err(std::io::Error::last_os_error())}else{Ok(())}
}
#[cfg(not(windows))]
pub fn replace_file(source:&Path,target:&Path)->std::io::Result<()>{std::fs::rename(source,target)}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn excel_date_systems_and_early_1900() {
        for date in ["1900-01-01","1900-02-28","1900-03-01","2026-10-09","2100-12-31"] {
            let d=chrono::NaiveDate::parse_from_str(date,"%Y-%m-%d").unwrap();
            for system in [false,true] {assert_eq!(serial_date(date_serial(d,system),system),Some(d));}
        }
        assert_eq!(display("46304","yyyy-mm-dd",true,false),"2026-10-09");
        assert_eq!(display("44842","yyyy-mm-dd",true,true),"2026-10-09");
        assert_eq!(display("60","yyyy-mm-dd",true,false),"1900-02-29");
        assert_eq!(display("46304","General",true,false),"46304");
        assert_eq!(display("","yyyy-mm-dd",true,false),"");
        assert_eq!(serial_date(i64::MAX,false),None);
        assert_eq!(serial_date(i64::MIN,true),None);
        assert!(!is_date_format("0.00\"mg/d\""));
        assert!(!is_date_format("[Magenta]0.00"));
        assert!(is_date_format("[$-409]m/d/yyyy"));
        for value in ["20261008","2026-10-08","2026/10/8","2026.10.8","46303"] {assert_eq!(cell_date(value,false).unwrap().to_string(),"2026-10-08");}
        assert!(cell_date("20260230",false).is_none());
    }
}
