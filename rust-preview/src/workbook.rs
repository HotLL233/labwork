use crate::{Method, Request, calc::{self, Values}};
use roxmltree::{Document, Node};
use serde_json::{Value, json};
use std::{collections::{BTreeMap,HashSet}, io::{Cursor,Read,Write}, path::Path};
use xmltree::{Element, XMLNode};
use zip::{ZipArchive, ZipWriter, write::SimpleFileOptions};

const MAIN:&str="http://schemas.openxmlformats.org/spreadsheetml/2006/main";
#[derive(Clone,serde::Serialize,serde::Deserialize)]
pub struct FormatRequest {
    pub sheet:String,pub range:String,
    pub number_format:Option<String>,pub font_name:Option<String>,pub font_size:Option<f64>,pub bold:Option<bool>,
    pub font_color:Option<String>,pub fill_color:Option<String>,pub alignment:Option<String>,pub column_width:Option<f64>,pub row_height:Option<f64>,
}
fn allowed_number_format(format:&str)->bool {
    if ["General","yyyy-mm-dd","yyyy/mm/dd","yyyy年m月d日"].contains(&format){return true;}
    let value=format.strip_suffix('%').unwrap_or(format);
    value=="0"||value.strip_prefix("0.").is_some_and(|s|!s.is_empty()&&s.len()<=8&&s.bytes().all(|b|b==b'0'))
}
fn style_section<'a>(root:&'a mut Element,name:&str)->&'a mut Element {
    if root.get_child(name).is_none(){
        let order=["numFmts","fonts","fills","borders","cellStyleXfs","cellXfs","cellStyles","dxfs","tableStyles","colors","extLst"];
        let rank=order.iter().position(|n|*n==name).unwrap();
        let position=root.children.iter().position(|n|n.as_element().is_some_and(|e|order.iter().position(|n|*n==e.name).unwrap_or(99)>rank)).unwrap_or(root.children.len());
        let mut section=el(name);section.attributes.insert("count".into(),"0".into());root.children.insert(position,XMLNode::Element(section));
    }
    root.get_mut_child(name).unwrap()
}
fn intern_style(root:&mut Element,section:&str,item:Element)->usize {
    let container=style_section(root,section);
    fn equivalent(a:&Element,b:&Element)->bool{
        if a.name!=b.name||a.attributes!=b.attributes{return false;}
        let left=a.children.iter().filter_map(XMLNode::as_element).collect::<Vec<_>>();let right=b.children.iter().filter_map(XMLNode::as_element).collect::<Vec<_>>();
        left.len()==right.len()&&left.iter().all(|a|right.iter().any(|b|equivalent(a,b)))
    }
    if let Some(index)=container.children.iter().filter_map(XMLNode::as_element).position(|e|equivalent(e,&item)){return index;}
    let index=container.children.iter().filter_map(XMLNode::as_element).count();container.children.push(XMLNode::Element(item));container.attributes.insert("count".into(),(index+1).to_string());index
}
fn child_value(parent:&mut Element,name:&str,key:&str,value:String){
    parent.children.retain(|n|!matches!(n,XMLNode::Element(e)if e.name==name));
    let mut child=el(name);child.attributes.insert(key.into(),value);parent.children.push(XMLNode::Element(child));
}
fn formatted_style(styles:&mut Element,index:usize,request:&FormatRequest)->Result<usize,String>{
    let mut xf=styles.get_child("cellXfs").and_then(|n|n.children.iter().filter_map(XMLNode::as_element).nth(index)).cloned().ok_or("单元格样式索引无效")?;
    if let Some(format)=&request.number_format {
        let formats=style_section(styles,"numFmts");
        let existing=formats.children.iter().filter_map(XMLNode::as_element).find(|e|e.attributes.get("formatCode")==Some(format)).and_then(|e|e.attributes.get("numFmtId")).and_then(|n|n.parse::<u32>().ok());
        let id=if let Some(id)=existing{id}else{
            let id=formats.children.iter().filter_map(XMLNode::as_element).filter_map(|e|e.attributes.get("numFmtId")).filter_map(|n|n.parse::<u32>().ok()).max().unwrap_or(163).max(163).checked_add(1).ok_or("数字格式数量超限")?;
            let mut n=el("numFmt");n.attributes.insert("numFmtId".into(),id.to_string());n.attributes.insert("formatCode".into(),format.clone());formats.children.push(XMLNode::Element(n));formats.attributes.insert("count".into(),formats.children.iter().filter_map(XMLNode::as_element).count().to_string());id
        };
        xf.attributes.insert("numFmtId".into(),id.to_string());xf.attributes.insert("applyNumberFormat".into(),"1".into());
    }
    if request.font_name.is_some()||request.font_size.is_some()||request.bold.is_some()||request.font_color.is_some(){
        let font_index=xf.attributes.get("fontId").and_then(|n|n.parse::<usize>().ok()).unwrap_or(0);
        let mut font=styles.get_child("fonts").and_then(|n|n.children.iter().filter_map(XMLNode::as_element).nth(font_index)).cloned().ok_or("字体索引无效")?;
        if let Some(name)=&request.font_name{child_value(&mut font,"name","val",name.clone());}
        if let Some(size)=request.font_size{child_value(&mut font,"sz","val",size.to_string());}
        if let Some(bold)=request.bold{font.children.retain(|n|!matches!(n,XMLNode::Element(e)if e.name=="b"));if bold{font.children.push(XMLNode::Element(el("b")));}}
        if let Some(color)=&request.font_color{child_value(&mut font,"color","rgb",format!("FF{}",color[1..].to_ascii_uppercase()));}
        let id=intern_style(styles,"fonts",font);xf.attributes.insert("fontId".into(),id.to_string());xf.attributes.insert("applyFont".into(),"1".into());
    }
    if let Some(color)=&request.fill_color {
        let mut fill=el("fill");let mut pattern=el("patternFill");pattern.attributes.insert("patternType".into(),"solid".into());child_value(&mut pattern,"fgColor","rgb",format!("FF{}",color[1..].to_ascii_uppercase()));child_value(&mut pattern,"bgColor","indexed","64".into());fill.children.push(XMLNode::Element(pattern));
        let id=intern_style(styles,"fills",fill);xf.attributes.insert("fillId".into(),id.to_string());xf.attributes.insert("applyFill".into(),"1".into());
    }
    if let Some(alignment)=&request.alignment{
        if xf.get_child("alignment").is_none(){let position=xf.children.iter().position(|n|n.as_element().is_some_and(|e|["protection","extLst"].contains(&e.name.as_str()))).unwrap_or(xf.children.len());xf.children.insert(position,XMLNode::Element(el("alignment")));}
        xf.get_mut_child("alignment").unwrap().attributes.insert("horizontal".into(),alignment.clone());xf.attributes.insert("applyAlignment".into(),"1".into());
    }
    Ok(intern_style(styles,"cellXfs",xf))
}
pub fn format_cells(bytes:&[u8],request:&FormatRequest)->Result<Vec<u8>,String>{
    let (a,b)=merge_bounds(&request.range)?;
    if b.1-a.1+1>200||(b.0-a.0+1)as u64*(b.1-a.1+1)as u64>10000{return Err("每次最多设置10000个单元格、200列".into());}
    if request.number_format.as_ref().is_some_and(|s|!allowed_number_format(s)){return Err("不支持的数字格式".into());}
    if request.font_name.as_ref().is_some_and(|s|s.trim().is_empty()||s.chars().count()>80||s.chars().any(char::is_control)){return Err("字体名称须为1至80个可显示字符".into());}
    if request.font_size.is_some_and(|s|!s.is_finite()||!(6.0..=72.0).contains(&s)){return Err("字号须为6至72".into());}
    for color in [&request.font_color,&request.fill_color].into_iter().flatten(){if color.len()!=7||!color.starts_with('#')||!color[1..].bytes().all(|b|b.is_ascii_hexdigit()){return Err("颜色须为#RRGGBB".into());}}
    if request.alignment.as_ref().is_some_and(|s|!["left","center","right"].contains(&s.as_str())){return Err("对齐方式须为left、center或right".into());}
    if request.column_width.is_some_and(|s|!s.is_finite()||!(2.0..=100.0).contains(&s)){return Err("列宽须为2至100".into());}
    if request.row_height.is_some_and(|s|!s.is_finite()||!(8.0..=200.0).contains(&s)){return Err("行高须为8至200磅".into());}
    let book=read(bytes)?;let path=target(&book,&request.sheet)?.clone();
    let original=doc(&book.parts[&path])?;
    let date_columns=original.descendants().filter(|n|n.has_tag_name("c")).filter_map(|n|coord(&attr(n,"r")).ok().filter(|(r,_)|*r<=3).and_then(|(_,c)|cell_value(n,&book.strings).ok().filter(|v|v.trim()=="日期").map(|_|c))).collect::<HashSet<_>>();
    let column_styles=original.descendants().filter(|n|n.has_tag_name("col")).filter_map(|n|Some((n.attribute("min")?.parse::<u32>().ok()?,n.attribute("max")?.parse::<u32>().ok()?,n.attribute("style")?.parse::<usize>().ok()?))).collect::<Vec<_>>();
    let changes_style=request.number_format.is_some()||request.font_name.is_some()||request.font_size.is_some()||request.bold.is_some()||request.font_color.is_some()||request.fill_color.is_some()||request.alignment.is_some();
    let mut styles=editable_xml(book.parts.get("xl/styles.xml").ok_or("工作簿缺少样式")?)?;
    let mut root=editable_xml(&book.parts[&path])?;
    let data=root.get_mut_child("sheetData").ok_or("缺少工作表数据区")?;
    let mut rows=BTreeMap::new();let mut other=Vec::new();
    for node in std::mem::take(&mut data.children){match node{XMLNode::Element(row)if row.name=="row"=>{let r=row.attributes.get("r").and_then(|r|r.parse::<u32>().ok()).filter(|r|(1..=1048576).contains(r)).ok_or("工作表行号无效")?;if rows.insert(r,row).is_some(){return Err("工作表行号重复".into());}},node=>other.push(node)}}
    let mut cache=BTreeMap::new();
    for r in a.0..=b.0 {
        let row=rows.entry(r).or_insert_with(||{let mut row=el("row");row.attributes.insert("r".into(),r.to_string());row});
        if let Some(height)=request.row_height{row.attributes.insert("ht".into(),height.to_string());row.attributes.insert("customHeight".into(),"1".into());}
        if !changes_style{continue;}
        let row_style=row.attributes.get("s").map(|s|s.parse::<usize>()).transpose().map_err(|_|"行样式无效")?;
        for c in a.1..=b.1 {
            let reference=format!("{}{r}",letter(c));
            let position=if let Some(i)=row.children.iter().position(|n|matches!(n,XMLNode::Element(e)if e.name=="c"&&e.attributes.get("r")==Some(&reference))){i}else{let mut cell=el("c");cell.attributes.insert("r".into(),reference);row.children.push(XMLNode::Element(cell));row.children.len()-1};
            let cell=row.children[position].as_mut_element().unwrap();
            let old=cell.attributes.get("s").map(|s|s.parse::<usize>()).transpose().map_err(|_|"单元格样式无效")?.or(row_style).or_else(||column_styles.iter().rev().find(|(min,max,_)|*min<=c&&*max>=c).map(|(_,_,s)|*s)).unwrap_or(0);
            if r>=4&&date_columns.contains(&c)&&request.number_format.as_ref().is_some_and(|f|is_date_format(f))&&cell.get_child("f").is_none()&&cell.attributes.get("t").is_none_or(|t|t=="n"){
                if let Some(value)=cell.get_mut_child("v"){
                    if let Some(raw)=value.get_text().filter(|s|s.len()==8&&s.bytes().all(|b|b.is_ascii_digit())){
                        if let Ok(date)=chrono::NaiveDate::parse_from_str(&raw,"%Y%m%d"){value.children=vec![XMLNode::Text(date_serial(date,book.date1904).to_string())];}
                    }
                }
            }
            let new=if let Some(index)=cache.get(&old){*index}else{let index=formatted_style(&mut styles,old,request)?;cache.insert(old,index);index};
            cell.attributes.insert("s".into(),new.to_string());
        }
        row.children.sort_by_key(|n|match n{XMLNode::Element(c)if c.name=="c"=>c.attributes.get("r").and_then(|r|coord(r).ok()).map(|(_,c)|c).unwrap_or(0),_=>u32::MAX});
    }
    data.children=rows.into_values().map(XMLNode::Element).chain(other).collect();
    if let Some(width)=request.column_width {
        let mut cols=root.get_child("cols").cloned().unwrap_or_else(||el("cols"));let original=cols.children.iter().filter_map(XMLNode::as_element).cloned().collect::<Vec<_>>();let mut adjusted=Vec::new();
        for column in &original{
            let min=column.attributes.get("min").and_then(|n|n.parse::<u32>().ok()).ok_or("列范围无效")?;let max=column.attributes.get("max").and_then(|n|n.parse::<u32>().ok()).ok_or("列范围无效")?;
            if min==0||max<min||max>16384{return Err("列范围无效".into());}
            if max<a.1||min>b.1{adjusted.push(column.clone());}else{
                if min<a.1{let mut left=column.clone();left.attributes.insert("max".into(),(a.1-1).to_string());adjusted.push(left);}
                if max>b.1{let mut right=column.clone();right.attributes.insert("min".into(),(b.1+1).to_string());adjusted.push(right);}
            }
        }
        for c in a.1..=b.1 {
            let mut col=original.iter().rev().find(|n|n.attributes.get("min").and_then(|s|s.parse::<u32>().ok()).is_some_and(|min|min<=c)&&n.attributes.get("max").and_then(|s|s.parse::<u32>().ok()).is_some_and(|max|max>=c)).cloned().unwrap_or_else(||el("col"));
            col.attributes.extend([("min".into(),c.to_string()),("max".into(),c.to_string()),("width".into(),width.to_string()),("customWidth".into(),"1".into())]);adjusted.push(col);
        }
        adjusted.sort_by_key(|c|c.attributes["min"].parse::<u32>().unwrap());cols.children=adjusted.into_iter().map(XMLNode::Element).collect();
        if let Some(position)=root.children.iter().position(|n|matches!(n,XMLNode::Element(e)if e.name=="cols")){root.children[position]=XMLNode::Element(cols);}else{let position=root.children.iter().position(|n|matches!(n,XMLNode::Element(e)if e.name=="sheetData")).unwrap();root.children.insert(position,XMLNode::Element(cols));}
    }
    if let Some(dimension)=root.get_mut_child("dimension") {
        let old=dimension.attributes.get("ref").cloned().unwrap_or("A1".into());let (old_start,old_end)=merge_bounds(&old)?;
        dimension.attributes.insert("ref".into(),format!("{}{}:{}{}",letter(old_start.1.min(a.1)),old_start.0.min(a.0),letter(old_end.1.max(b.1)),old_end.0.max(b.0)));
    }
    let mut sheet_xml=Vec::new();root.write(&mut sheet_xml).map_err(|_|"保存单元格格式失败")?;let mut styles_xml=Vec::new();styles.write(&mut styles_xml).map_err(|_|"保存样式失败")?;
    let mut original=ZipArchive::new(Cursor::new(bytes)).map_err(|_|"ZIP无效")?;let mut output=ZipWriter::new(Cursor::new(Vec::new()));
    for i in 0..original.len(){let file=original.by_index(i).map_err(|_|"ZIP部件无效")?;let replacement=if file.name()==path{Some(&sheet_xml)}else if file.name()=="xl/styles.xml"{Some(&styles_xml)}else{None};if let Some(xml)=replacement{output.start_file(file.name(),SimpleFileOptions::default().compression_method(file.compression())).map_err(|_|"创建格式文件失败")?;output.write_all(xml).map_err(|_|"写入格式文件失败")?;}else{output.raw_copy_file(file).map_err(|_|"保留ZIP部件失败")?;}}
    let candidate=output.finish().map_err(|_|"生成格式工作簿失败")?.into_inner();let check=read(&candidate)?;
    if book.parts.iter().any(|(name,data)|name!=&path&&name!="xl/styles.xml"&&check.parts.get(name)!=Some(data)){return Err("非目标工作簿部件发生变化".into());}Ok(candidate)
}
struct Book {parts:BTreeMap<String,Vec<u8>>,strings:Vec<String>,sheets:BTreeMap<String,String>,order:Vec<String>,date1904:bool}
fn text(n:Node<'_, '_>)->String {n.descendants().filter(|n|n.is_element()&&n.tag_name().name()=="t").filter_map(|n|n.text()).collect()}
fn attr(n:Node<'_, '_>,key:&str)->String {n.attribute(key).unwrap_or("").to_owned()}
fn doc(bytes:&[u8])->Result<Document<'_>,String>{Document::parse(std::str::from_utf8(bytes).map_err(|_|"XML编码不支持")?).map_err(|_|"工作簿XML无效".into())}
fn editable_xml(bytes:&[u8])->Result<Element,String>{
    let source=std::str::from_utf8(bytes).map_err(|_|"XML编码不支持")?;
    let document=doc(bytes)?;
    let mut root=Element::parse_with_config(Cursor::new(bytes),xmltree::ParserConfig::new().ignore_comments(false).whitespace_to_characters(true)).map_err(|_|"工作簿XML无效")?;
    fn restore(element:&mut Element,node:Node<'_, '_>,source:&str)->Result<(),String>{
        if element.name!=node.tag_name().name(){return Err("XML结构不一致，未写入".into());}
        // xmltree drops attribute prefixes, including Excel's drawing and print relationships.
        element.attributes.clear();
        for attr in node.attributes(){
            let name=source.get(attr.range_qname()).ok_or("XML属性名称无效")?;
            element.attributes.insert(name.into(),attr.value().into());
        }
        let mut originals=node.children().filter(Node::is_element);
        for child in element.children.iter_mut().filter_map(XMLNode::as_mut_element){
            restore(child,originals.next().ok_or("XML结构不一致，未写入")?,source)?;
        }
        if originals.next().is_some(){return Err("XML结构不一致，未写入".into());}Ok(())
    }
    restore(&mut root,document.root_element(),source)?;Ok(root)
}
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
    let mut sheets=BTreeMap::new();let mut order=Vec::new();
    for n in book.descendants().filter(|n|n.has_tag_name("sheet")){
        let id=n.attribute(("http://schemas.openxmlformats.org/officeDocument/2006/relationships","id")).ok_or("工作表关系无效")?;
        let target=rels.get(id).ok_or("工作表关系不存在")?;
        let target=if target.starts_with('/') {target.trim_start_matches('/').to_owned()}else{format!("xl/{target}")};
        if !target.starts_with("xl/worksheets/")||!parts.contains_key(&target){return Err("工作表链接无效".into());}
        let name=attr(n,"name");if sheets.insert(name.clone(),target).is_some(){return Err("工作表名称重复".into());}order.push(name);
    }
    let date1904=book.descendants().any(|n|n.has_tag_name("workbookPr")&&matches!(n.attribute("date1904"),Some("1"|"true")));
    Ok(Book{parts,strings,sheets,order,date1904})
}
pub fn sheet_names(bytes:&[u8])->Result<Vec<String>,String>{Ok(read(bytes)?.order)}
#[derive(Clone,serde::Serialize,serde::Deserialize)]
pub struct MonthRule {
    pub sheet:String,
    #[serde(rename="start_row")]
    pub data_start_row:u32,
    #[serde(rename="columns")]
    pub clear_columns:Vec<String>,
}
pub fn discover(bytes:&[u8])->Result<Vec<MonthRule>,String>{
    let book=read(bytes)?;let mut rules=Vec::new();
    for sheet in &book.order {
        if sheet.trim()=="目录"{rules.push(MonthRule{sheet:sheet.clone(),data_start_row:3,clear_columns:Vec::new()});continue;}
        let d=doc(&book.parts[target(&book,sheet)?])?;let mut headers=BTreeMap::new();
        for cell in d.descendants().filter(|n|n.has_tag_name("c")) {
            let (row,col)=coord(&attr(cell,"r"))?;
            if row==1 {headers.insert(col,cell_value(cell,&book.strings)?);}
        }
        let Some(batch)=headers.iter().find(|(_,v)|v.contains('批')).map(|(c,_)|*c)else{rules.push(MonthRule{sheet:sheet.clone(),data_start_row:3,clear_columns:Vec::new()});continue};
        let parameter_end=headers.iter().filter(|(c,_)|**c>batch).find(|(_,v)|["试剂","规格","浓度","系数","mol","M="].iter().any(|word|v.contains(word))).map(|(c,_)|c-1).unwrap_or_else(||headers.keys().copied().max().unwrap_or(batch));
        let end=headers.iter().find(|(c,v)|**c>batch&&v.contains("备注")).map(|(c,_)|*c).unwrap_or(parameter_end).min(parameter_end);
        let example=d.descendants().filter(|n|n.has_tag_name("c")&&n.attribute("r")==Some(format!("{}3",letter(batch)).as_str())).filter_map(|n|cell_value(n,&book.strings).ok()).any(|v|matches!(v.trim(),"例"|"示例"|"举例"));
        // Formula dependencies identify data columns even when their top cells hold reagent parameters.
        let mut clear=(1..=end.min(40)).collect::<std::collections::BTreeSet<_>>();
        if let Ok(method)=generic_method(&book,sheet,0){for column in method.columns.values(){clear.insert(coord(&format!("{column}1"))?.1);}}
        rules.push(MonthRule{sheet:sheet.clone(),data_start_row:if example{4}else{3},clear_columns:clear.into_iter().map(letter).collect()});
    }
    Ok(rules)
}
pub fn blank_month(bytes:&[u8],rules:&[MonthRule])->Result<Vec<u8>,String>{
    if rules.is_empty(){return Err("请先确认至少一个工作表的清空范围".into());}
    let book=read(bytes)?;let mut replacements=BTreeMap::new();let mut seen=HashSet::new();
    for rule in rules {
        let path=target(&book,&rule.sheet)?.clone();
        if !seen.insert(path.clone()){return Err("同一工作表不能重复设置清空范围".into());}
        if !(3..=1048576).contains(&rule.data_start_row){return Err("清空范围须从第3行或之后开始".into());}
        if rule.clear_columns.is_empty(){continue;}
        if rule.sheet.trim()=="目录" {return Err("目录仅用于浏览，不能清空".into());}
        let columns=rule.clear_columns.iter().map(|c|coord(&format!("{c}1")).and_then(|(r,col)|if r==1&&letter(col)==*c{Ok(col)}else{Err("清空列必须使用A、B等大写列名".into())})).collect::<Result<HashSet<_>,String>>()?;
        if let Ok(method)=generic_method(&book,&rule.sheet,0){for key in method.params.keys(){if let Some(reference)=key.strip_prefix("fixed:"){let (row,col)=coord(reference)?;if row>=rule.data_start_row&&columns.contains(&col){return Err(format!("{}的清空范围包含固定参数{reference}，请调整起始行或列后再确认",rule.sheet));}}}}
        let mut root=editable_xml(&book.parts[&path])?;
        let data=root.get_mut_child("sheetData").ok_or("缺少工作表数据区")?;
        for row in data.children.iter_mut().filter_map(XMLNode::as_mut_element) {
            for cell in row.children.iter_mut().filter_map(XMLNode::as_mut_element).filter(|e|e.name=="c") {
                let (r,c)=coord(cell.attributes.get("r").ok_or("缺少单元格坐标")?)?;
                if cell.get_child("f").is_some() {
                    cell.children.retain(|n|!matches!(n,XMLNode::Element(e)if e.name=="v"));
                } else if r>=rule.data_start_row&&columns.contains(&c) {
                    cell.attributes.remove("t");cell.children.retain(|n|!matches!(n,XMLNode::Element(e)if e.name=="v"||e.name=="is"));
                }
            }
        }
        let mut xml=Vec::new();root.write(&mut xml).map_err(|_|"生成月度模板失败")?;replacements.insert(path,xml);
    }
    let mut original=ZipArchive::new(Cursor::new(bytes)).map_err(|_|"ZIP无效")?;let mut output=ZipWriter::new(Cursor::new(Vec::new()));
    for i in 0..original.len(){let file=original.by_index(i).map_err(|_|"ZIP部件无效")?;if let Some(xml)=replacements.get(file.name()){output.start_file(file.name(),SimpleFileOptions::default().compression_method(file.compression())).map_err(|_|"创建月模板失败")?;output.write_all(xml).map_err(|_|"写入月模板失败")?;}else{output.raw_copy_file(file).map_err(|_|"保留工作簿部件失败")?;}}
    let candidate=output.finish().map_err(|_|"生成月模板失败")?.into_inner();let check=read(&candidate)?;
    if book.parts.iter().any(|(name,bytes)|!replacements.contains_key(name)&&check.parts.get(name)!=Some(bytes)){return Err("非目标工作簿部件发生变化".into());}
    Ok(candidate)
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
fn merge_bounds(range:&str)->Result<((u32,u32),(u32,u32)),String>{
    let mut ends=range.split(':');let a=coord(ends.next().ok_or("合并范围无效")?)?;let b=coord(ends.next().unwrap_or(range))?;
    if ends.next().is_some()||b.0<a.0||b.1<a.1{return Err("合并范围顺序无效".into());}Ok((a,b))
}
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
            style["fontSize"]=json!(format!("{}px",child("sz").and_then(|n|n.attribute("val")).and_then(|s|s.parse::<f32>().ok()).unwrap_or(11.).clamp(6.,72.)*4./3.));
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
fn formatted_date(date:chrono::NaiveDate,format:&str)->String{
    date.format(match format{"yyyy/mm/dd"=>"%Y/%m/%d","yyyy年m月d日"=>"%Y年%-m月%-d日","mm-dd"=>"%m-%d","yyyy-mm"=>"%Y-%m",_=>"%Y-%m-%d"}).to_string()
}
fn display(value:&str,format:&str,numeric:bool,date1904:bool)->String{
    if !numeric||value.is_empty(){return value.into();}let Ok(mut n)=value.parse::<f64>()else{return value.into()};
    if !n.is_finite(){return value.into();}
    if is_date_format(format)&&n>=-1461.&&n<73416.{if !date1904&&n as i64==60{return "1900-02-29".into();}if let Some(date)=serial_date(n as i64,date1904){return formatted_date(date,format);}}
    if format=="General" {return value.into();}
    let suffix=if format.contains('%'){n*=100.;"%"}else{""};
    let decimals=format.split('.').nth(1).map(|s|s.chars().take_while(|c|*c=='0'||*c=='#').count()).unwrap_or(0).min(8);
    format!("{n:.decimals$}{suffix}")
}
#[derive(Default)]
struct LabelGroup { date:String, batch:String, date_row:u32, batch_row:u32 }
impl LabelGroup {
    fn observe(&mut self,date:&str,batch:&str,row:u32)->(bool,bool) {
        let same_date=!date.is_empty()&&date==self.date;
        if !date.is_empty()&&!same_date {self.date=date.into();self.date_row=row;self.batch.clear();self.batch_row=0;}
        let same_batch=!self.date.is_empty()&&!batch.is_empty()&&batch==self.batch;
        if !batch.is_empty()&&!same_batch {self.batch=batch.into();self.batch_row=row;}
        (same_date,same_batch)
    }
}
fn historical_labels(d:&Document<'_>,book:&Book,method:&Method)->Result<(HashSet<String>,BTreeMap<u32,Vec<u32>>,LabelGroup),String>{
    let mut groups=LabelGroup::default();let mut hidden=HashSet::new();let mut context=BTreeMap::new();
    let mut cells=BTreeMap::new();
    for cell in d.descendants().filter(|n|n.has_tag_name("c")){cells.insert(attr(cell,"r"),cell);}
    let mut merged=Vec::new();
    for cell in d.descendants().filter(|n|n.has_tag_name("mergeCell")) {
        merged.push(merge_bounds(&attr(cell,"ref"))?);
    }
    for row in 4..=last_data_row(book,method)? {
        let references=[format!("{}{row}",method.columns["date"]),format!("{}{row}",method.columns["batch"])];
        let values=references.iter().map(|r|cells.get(r).map(|n|cell_value(*n,&book.strings)).transpose().map(|v|v.unwrap_or_default())).collect::<Result<Vec<_>,_>>()?;
        let date=cell_date(&values[0],book.date1904).map(|d|d.to_string()).unwrap_or_else(||values[0].clone());
        let repeated=groups.observe(&date,&values[1],row);
        for (reference,duplicate) in references.iter().zip([repeated.0,repeated.1]) {
            let position=coord(reference)?;
            if duplicate&&cells.get(reference).is_some_and(|n|!n.children().any(|n|n.has_tag_name("f")))&&!merged.iter().any(|(a,b)|position.0>=a.0&&position.0<=b.0&&position.1>=a.1&&position.1<=b.1) {hidden.insert(reference.clone());}
        }
        context.insert(row,vec![groups.date_row,groups.batch_row].into_iter().filter(|r|*r>=4).collect());
    }
    Ok((hidden,context,groups))
}
pub fn view(bytes:&[u8],method:&Method,highlight:&[u32])->Result<Value,String>{
    view_inner(bytes,&method.sheet,Some(method),highlight,None)
}
pub fn raw_view(bytes:&[u8],sheet:&str,start_row:u32)->Result<Value,String>{
    if start_row==0||start_row>1048576{return Err("分页起始行超出范围".into());}
    view_inner(bytes,sheet,None,&[],Some(start_row))
}
fn view_inner(bytes:&[u8],sheet:&str,method:Option<&Method>,highlight:&[u32],page:Option<u32>)->Result<Value,String>{
    let book=read(bytes)?;let path=target(&book,sheet)?;let d=doc(&book.parts[path])?;let (css,num)=styles(&book)?;
    let explicit_formats=book.parts.get("xl/styles.xml").map(|b|doc(b).map(|d|d.descendants().find(|n|n.has_tag_name("cellXfs")).map(|n|n.children().filter(Node::is_element).enumerate().filter_map(|(i,n)|n.attribute("numFmtId").and_then(|v|v.parse::<u32>().ok()).filter(|id|*id>=164).map(|_|i)).collect::<HashSet<_>>()).unwrap_or_default())).transpose()?.unwrap_or_default();
    let (hidden,context)=if let Some(method)=method{let (hidden,context,_)=historical_labels(&d,&book,method)?;(hidden,context)}else{(HashSet::new(),BTreeMap::new())};
    let date_columns=d.descendants().filter(|n|n.has_tag_name("c")).filter_map(|n|coord(&attr(n,"r")).ok().filter(|(r,_)|*r<=3).and_then(|(_,c)|cell_value(n,&book.strings).ok().filter(|v|v.trim()=="日期").map(|_|c))).collect::<HashSet<_>>();
    let mut all=BTreeMap::new();let mut heights=BTreeMap::new();let mut max_col=1;let mut max_row=3;
    for row in d.descendants().filter(|n|n.has_tag_name("row")){
        let r=attr(row,"r").parse::<u32>().map_err(|_|"行号无效")?;
        heights.insert(r,row.attribute("ht").and_then(|s|s.parse::<f32>().ok()).unwrap_or(18.)*4./3.);
        for cell in row.children().filter(|n|n.has_tag_name("c")){
            let reference=attr(cell,"r");let (r,c)=coord(&reference)?;max_col=max_col.max(c);max_row=max_row.max(r);
            let value=cell_value(cell,&book.strings)?;let style=cell.attribute("s").and_then(|s|s.parse::<usize>().ok()).unwrap_or(0);
            let formula=cell.children().find(|n|n.has_tag_name("f")).and_then(|n|n.text()).unwrap_or("");
            let numeric=!matches!(cell.attribute("t"),Some("s"|"inlineStr"|"str"|"e"|"b"));
            let is_date=r>=4&&method.map(|m|reference.trim_end_matches(|c:char|c.is_ascii_digit())==m.columns["date"]).unwrap_or_else(||date_columns.contains(&c));
            let format=num.get(style).map(String::as_str).unwrap_or("General");
            let shown=if is_date&&(is_date_format(format)||(format=="General"&&!explicit_formats.contains(&style))) {cell_date(&value,book.date1904).map(|d|formatted_date(d,format)).unwrap_or_else(||value.clone())}else{display(&value,format,numeric,book.date1904)};
            all.insert((r,c),json!({"ref":reference,"value":if hidden.contains(&reference){String::new()}else{shown},"raw":value,"formula":formula,"style":style,"highlight":highlight.contains(&r)}));
        }
    }
    let full_columns=max_col;
    max_col=max_col.min(if page.is_some(){200}else{40}); // shortcut: wider raw sheets need column pagination before onboarding.
    let mut widths=vec![90f32;max_col as usize];
    for n in d.descendants().filter(|n|n.has_tag_name("col")){
        let start=attr(n,"min").parse::<u32>().map_err(|_|"列范围无效")?;let full_end=attr(n,"max").parse::<u32>().map_err(|_|"列范围无效")?;
        if start==0||full_end<start||full_end>16384{return Err("列范围超出Excel范围或顺序无效".into());}let end=full_end.min(max_col);
        let width=n.attribute("width").and_then(|s|s.parse::<f32>().ok()).unwrap_or(12.)*7.+5.;
        for c in start..=end{widths[(c-1)as usize]=width.clamp(35.,650.);}
    }
    let mut merges=Vec::new();
    for n in d.descendants().filter(|n|n.has_tag_name("mergeCell")){let (a,b)=merge_bounds(&attr(n,"ref"))?;merges.push(json!({"row":a.0,"col":a.1,"rowspan":b.0-a.0+1,"colspan":b.1-a.1+1}));}
    let tail=max_row.saturating_sub(12).max(4);let mut selected=if let Some(start)=page{(start..=max_row.min(start+199)).collect::<std::collections::BTreeSet<_>>()}else{(1..=3).chain(tail..=max_row).chain(highlight.iter().copied()).collect::<std::collections::BTreeSet<_>>()};
    // Keep group headers visible when the tail starts halfway through a day's results.
    for row in selected.clone(){if let Some(anchors)=context.get(&row){selected.extend(anchors);}}
    let rows=selected.iter().map(|r|json!({"number":r,"height":heights.get(r).copied().unwrap_or(24.),"cells":(1..=max_col).map(|c|all.get(&(*r,c)).cloned().unwrap_or_else(||json!({"ref":format!("{}{r}",letter(c)),"value":"","raw":"","formula":"","style":0,"highlight":highlight.contains(r)}))).collect::<Vec<_>>()})).collect::<Vec<_>>();
    Ok(json!({"name":sheet,"rows":rows,"columns":widths.iter().enumerate().map(|(i,w)|json!({"label":letter(i as u32+1),"width":w})).collect::<Vec<_>>(),"styles":css,"merges":merges,"last_row":max_row,"tail_start":tail,"sheet_names":book.order,"start_row":page.unwrap_or(1),"next_row":page.filter(|start|start+199<max_row).map(|start|start+200),"truncated":page.is_some_and(|start|start>1||start+199<max_row)||full_columns>max_col,"columns_truncated":full_columns>max_col}))
}
fn expanded_formulas(d:&Document<'_>)->Result<BTreeMap<(u32,u32),String>,String>{
    let mut masters=BTreeMap::new();let mut formulas=BTreeMap::new();
    for cell in d.descendants().filter(|n|n.has_tag_name("c")) {
        if let Some(f)=cell.children().find(|n|n.has_tag_name("f")) {
            if matches!(f.attribute("t"),Some("array"|"dataTable")){return Err("数组或数据表公式尚未支持".into());}
            if let Some(text)=f.text().filter(|s|!s.is_empty()) {
                let position=coord(&attr(cell,"r"))?;
                if f.attribute("t")==Some("shared"){masters.insert(attr(f,"si"),(position,text.to_owned()));}
                formulas.insert(position,text.to_owned());
            }
        }
    }
    for cell in d.descendants().filter(|n|n.has_tag_name("c")) {
        if let Some(f)=cell.children().find(|n|n.has_tag_name("f")) {
            if f.text().is_none_or(str::is_empty){
                let (origin,expression)=masters.get(&attr(f,"si")).ok_or("共享公式缺少主公式")?;let position=coord(&attr(cell,"r"))?;
                formulas.insert(position,crate::formula::translate(expression,position.0 as i32-origin.0 as i32,position.1 as i32-origin.1 as i32)?);
            }
        }
    }
    Ok(formulas)
}
fn generic_method(book:&Book,sheet:&str,index:usize)->Result<Method,String>{
    if sheet.trim()=="目录"{return Err("目录仅用于导航".into());}
    let d=doc(&book.parts[target(book,sheet)?])?;let mut cells=BTreeMap::new();
    for cell in d.descendants().filter(|n|n.has_tag_name("c")){cells.insert(coord(&attr(cell,"r"))?,cell_value(cell,&book.strings)?);}
    let value=|row,col|cells.get(&(row,col)).cloned().unwrap_or_default();
    let date=cells.iter().find(|((r,_),v)|*r<=2&&v.trim()=="日期").map(|((_,c),_)|*c).ok_or("没有识别到日期列，暂只浏览")?;
    let batch=cells.iter().find(|((r,_),v)|*r==1&&v.contains('批')).map(|((_,c),_)|*c).ok_or("没有识别到批号列，暂只浏览")?;
    let notes=cells.iter().find(|((r,c),v)|*r==1&&*c>batch&&v.contains("备注")).map(|((_,c),_)|*c);
    let formulas=expanded_formulas(&d)?;
    let mut selected=BTreeMap::new();
    for ((row,col),expression) in &formulas {if *row>=3&&*col>batch{selected.insert(*col,(*row,expression.clone()));}}
    let template=selected.values().map(|(r,_)|*r).max().unwrap_or(3);
    let mut params=Values::from([("template_row".into(),template.to_string()),("source".into(),format!("原工作表「{sheet}」最新公式，定位第{template}行；常数保留原式"))]);
    let mut inputs=std::collections::BTreeSet::new();let mut columns=Values::from([("date".into(),letter(date)),("batch".into(),letter(batch))]);
    for (column,(row,expression)) in &selected {
        let mut fixed=std::collections::BTreeSet::new();
        for reference in crate::formula::references(expression)? {
            let (r,c)=coord(&reference)?;let absolute_row=reference.trim_end_matches(|c:char|c.is_ascii_digit()).ends_with('$');
            if absolute_row||(r<4&&r!=*row) {
                let address=format!("{}{r}",letter(c));let parameter=value(r,c);
                crate::formula::evaluate(&address,&Values::from([(address.clone(),parameter.clone())])).map_err(|e|format!("固定参数{address}无有效数值：{e}"))?;
                params.insert(format!("fixed:{address}"),parameter);fixed.insert(address);
            }else if r==*row {
                if c<=batch||c==date{return Err("公式引用了日期或批号，不能作为数值计算".into());}
                if !selected.contains_key(&c){inputs.insert(c);}
            }else {return Err(format!("公式{column}列引用其他测定行{reference}，需要核定后才能计算"));}
        }
        let anchored=crate::formula::anchor(expression,&fixed)?;
        let expression=crate::formula::translate(&anchored,template as i32-*row as i32,0)?;
        params.insert(format!("f:{}",letter(*column)),expression);
        columns.insert(format!("col_{}",letter(*column)),letter(*column));
    }
    let is_manual=selected.is_empty();
    if is_manual {
        let end=cells.iter().filter(|((r,c),v)|*r==1&&*c>batch&&["试剂","规格","mol","浓度"].iter().any(|s|v.contains(s))).map(|((_,c),_)|*c).min().unwrap_or(16385);
        for ((r,c),header) in &cells {if *r==1&&*c>batch&&*c<end&&notes.is_none_or(|n|*c<n)&&!header.trim().is_empty(){inputs.insert(*c);}}
        params.insert("source".into(),format!("原工作表「{sheet}」未含计算公式，按原表登记"));
    }
    let mut fields=Vec::new();
    for c in inputs {
        let column=letter(c);let key=format!("col_{column}");
        if params.contains_key(&format!("fixed:{column}{template}")){return Err("同一单元格同时作为固定参数和当前输入，需要核定映射".into());}
        let label=[value(1,c),value(3,c)].into_iter().find(|v|!v.trim().is_empty()&&v.parse::<bigdecimal::BigDecimal>().is_err()).unwrap_or_else(||format!("{column}列"));
        let sample=cells.iter().rev().find(|((r,col),v)|*r>=3&&*col==c&&!v.trim().is_empty()&&!formulas.contains_key(&(*r,*col))).map(|(_,v)|v.clone()).unwrap_or_default();
        let numeric=!is_manual||(!sample.is_empty()&&sample.parse::<bigdecimal::BigDecimal>().is_ok())||["质量","体积","含量","结果","%","ppm","COD","熔点","温度"].iter().any(|word|label.contains(word));
        fields.push(calc::Field{key:key.clone(),label,unit:value(2,c),sample:if numeric&&sample.parse::<bigdecimal::BigDecimal>().is_err(){"1".into()}else{sample},input_type:if numeric{"number".into()}else{"text".into()}});
        columns.insert(key,column);
    }
    if let Some(notes)=notes.filter(|n|!columns.values().any(|c|c==&letter(*n))){columns.insert("notes".into(),letter(notes));}
    if is_manual&&fields.is_empty()&&!columns.contains_key("notes"){return Err("未识别到可登记的结果列，暂只浏览".into());}
    let method=Method{key:format!("sheet_{index}"),name:sheet.trim().to_owned(),kind:if is_manual{"manual".into()}else{"workbook_formula".into()},params,fields,columns,sheet:sheet.to_owned()};
    // Detect dependency cycles without assuming that the example inputs have valid nonzero denominators.
    let mut pending=method.params.iter().filter_map(|(k,f)|k.strip_prefix("f:").map(|c|(c.to_owned(),f.clone()))).collect::<BTreeMap<_,_>>();let mut resolved=std::collections::BTreeSet::new();
    while !pending.is_empty(){let next=pending.iter().find(|(_,f)|crate::formula::references(f).is_ok_and(|refs|refs.iter().all(|r|{let p=coord(r).unwrap();p.0!=template||!pending.contains_key(&letter(p.1))||resolved.contains(&letter(p.1))}))).map(|(c,_)|c.clone()).ok_or("工作表公式存在循环依赖")?;pending.remove(&next);resolved.insert(next);}
    Ok(method)
}
pub fn generic_methods(bytes:&[u8])->Result<Vec<Method>,String>{let book=read(bytes)?;Ok(book.order.iter().enumerate().filter_map(|(i,s)|generic_method(&book,s,i).ok()).collect())}
pub fn generic_error(bytes:&[u8],sheet:&str)->Option<String>{match read(bytes){Ok(book)=>generic_method(&book,sheet,book.order.iter().position(|s|s==sheet).unwrap_or(0)).err(),Err(e)=>Some(e)}}
pub fn generic_calculate(method:&Method,raw:&Values)->Result<Values,String>{
    let template=method.params.get("template_row").and_then(|r|r.parse::<u32>().ok()).ok_or("缺少原公式定位行")?;
    let mut values=Values::new();let mut output=Values::new();
    for (key,value) in &method.params {if let Some(reference)=key.strip_prefix("fixed:"){values.insert(reference.into(),value.clone());}}
    for field in &method.fields {
        let value=raw.get(&field.key).ok_or_else(||format!("缺少{}",field.label))?;
        if field.input_type=="text" {if value.chars().count()>2000{return Err(format!("{}最多2000字",field.label));}}
        else {if value.len()>80{return Err(format!("{}数值过长",field.label));}crate::formula::evaluate("A1",&Values::from([("A1".into(),value.clone())])).map_err(|e|format!("{}：{e}",field.label))?;}
        output.insert(field.key.clone(),value.clone());
        values.insert(format!("{}{template}",method.columns[&field.key]),value.clone());
    }
    let mut pending=method.params.iter().filter_map(|(k,f)|k.strip_prefix("f:").map(|c|(c.to_owned(),f.clone()))).collect::<BTreeMap<_,_>>();
    while !pending.is_empty(){
        let next=pending.iter().find(|(_,f)|crate::formula::references(f).is_ok_and(|refs|refs.iter().all(|r|values.contains_key(&r.replace('$',""))))).map(|(c,f)|(c.clone(),f.clone())).ok_or("公式存在循环依赖或缺少引用数值")?;
        let value=crate::formula::evaluate(&next.1,&values)?;values.insert(format!("{}{template}",next.0),value.clone());output.insert(format!("col_{}",next.0),value);pending.remove(&next.0);
    }
    let result=method.params.keys().filter_map(|k|k.strip_prefix("f:")).filter_map(|c|output.get(&format!("col_{c}")).map(|v|(c,v))).map(|(c,v)|{let n=v.parse::<bigdecimal::BigDecimal>().unwrap();format!("{c}: {}",n.with_scale_round(5,bigdecimal::RoundingMode::HalfEven))}).collect::<Vec<_>>().join("；");
    output.insert("display".into(),if result.is_empty(){"已登记".into()}else{result});output.insert("unit".into(),String::new());Ok(output)
}
fn el(name:&str)->Element{let mut e=Element::new(name);e.namespace=Some(MAIN.into());let mut ns=xmltree::Namespace::empty();ns.put("",MAIN);e.namespaces=Some(ns);e}
fn value_cell(mut cell:Element,value:String,text:bool,formula:Option<&String>)->Element{
    cell.attributes.remove("t");
    cell.children.retain(|n|!matches!(n,XMLNode::Element(e)if e.name=="v"||e.name=="f"||e.name=="is"));
    let mut contents=Vec::new();
    if text{cell.attributes.insert("t".into(),"inlineStr".into());let mut string=el("is");let mut t=el("t");t.attributes.insert("xml:space".into(),"preserve".into());t.children.push(XMLNode::Text(value));string.children.push(XMLNode::Element(t));contents.push(XMLNode::Element(string));}
    else{if let Some(formula)=formula{let mut f=el("f");f.children.push(XMLNode::Text(formula.clone()));contents.push(XMLNode::Element(f));}let mut v=el("v");v.children.push(XMLNode::Text(value));contents.push(XMLNode::Element(v));}
    let position=cell.children.iter().position(|n|matches!(n,XMLNode::Element(e)if e.name=="extLst")).unwrap_or(cell.children.len());
    cell.children.splice(position..position,contents);cell
}
fn last_data_row(book:&Book,method:&Method)->Result<u32,String>{
    let d=doc(&book.parts[target(book,&method.sheet)?])?;
    let mut last=3;
    for cell in d.descendants().filter(|n|n.has_tag_name("c")){
        let reference=attr(cell,"r");let (r,_)=coord(&reference)?;
        let col=reference.trim_end_matches(|c:char|c.is_ascii_digit());
        if r>=4&&!cell_value(cell,&book.strings)?.is_empty()&&method.columns.iter().any(|(key,c)|key!="date"&&(key!="notes"||method.kind=="manual")&&c==col){last=last.max(r);}
    }Ok(last)
}
fn append_start(book:&Book,method:&Method,count:u32)->Result<u32,String>{
    let mut start=last_data_row(book,method)?+1;let d=doc(&book.parts[target(book,&method.sheet)?])?;
    let columns=method.columns.values().map(|c|coord(&format!("{c}1")).map(|(_,c)|c)).collect::<Result<HashSet<_>,_>>()?;
    let mut ranges=d.descendants().filter(|n|n.has_tag_name("mergeCell")).map(|n|merge_bounds(&attr(n,"ref"))).collect::<Result<Vec<_>,_>>()?;
    ranges.sort_by_key(|(a,_)|a.0);
    if count==0||count>200{return Err("每次支持1至200行测定".into());}
    for (a,b) in ranges {
        if a.0>start+count-1{break;}
        if b.0>=start&&columns.iter().any(|c|*c>=a.1&&*c<=b.1){start=b.0+1;}
    }
    if start+count-1>1048576{return Err("没有足够的连续空行，记录区已满".into());}Ok(start)
}
pub fn next_row(bytes:&[u8],method:&Method)->Result<u32,String>{append_start(&read(bytes)?,method,1)}
pub fn append(bytes:&[u8],method:&Method,request:&Request,outputs:&[Values])->Result<(Vec<u8>,Vec<u32>),String>{
    let book=read(bytes)?;let path=target(&book,&method.sheet)?.clone();let d=doc(&book.parts[&path])?;
    let mut source_styles=BTreeMap::new();let mut values=BTreeMap::new();
    for cell in d.descendants().filter(|n|n.has_tag_name("c")){
        let reference=attr(cell,"r");let (r,_)=coord(&reference)?;let col=reference.trim_end_matches(|c:char|c.is_ascii_digit()).to_owned();let value=cell_value(cell,&book.strings)?;
        values.insert(reference.clone(),value);
        if r==3{source_styles.insert(col,attr(cell,"s"));}
    }
    if let Some(template)=method.params.get("template_row").and_then(|r|r.parse::<u32>().ok()) {
        for cell in d.descendants().filter(|n|n.has_tag_name("c")) {let reference=attr(cell,"r");if coord(&reference)?.0==template {let style=attr(cell,"s");if !style.is_empty(){source_styles.insert(reference.trim_end_matches(|c:char|c.is_ascii_digit()).into(),style);}}}
    }
    if request.entries.len()!=outputs.len()||outputs.is_empty(){return Err("计算行数不一致".into());}
    let (_,_,mut groups)=historical_labels(&d,&book,method)?;
    let mut replacements=BTreeMap::new();
    let (_,formats)=styles(&book)?;
    let date_col=&method.columns["date"];
    let date_sample=values.iter().filter_map(|(reference,value)|{let (r,c)=coord(reference).ok()?;
        (r>=3&&letter(c)==*date_col&&cell_date(value,book.date1904).is_some()).then_some((r,value))
    }).max_by_key(|(r,_)|*r).map(|(_,v)|v.as_str());
    if !values.get(&format!("{}1",method.columns["batch"])).is_some_and(|s|s.contains('批')){return Err("表头不匹配，不能写入".into());}
    for (key,col) in &method.columns{
        if method.kind!="water_diluted"&&calc::keys(&method.kind).contains(&key.as_str())&&key!="result"&&values.get(&format!("{col}1")).is_none_or(String::is_empty){return Err("输入列缺少表头，需要核定映射".into());}
    }
    let start=append_start(&book,method,outputs.len()as u32)?;
    let rows=(start..start+outputs.len()as u32).collect::<Vec<_>>();
    let mapped=method.columns.values().map(|c|coord(&format!("{c}1")).map(|(_,c)|c)).collect::<Result<HashSet<_>,_>>()?;
    for ref_value in values.keys(){let (r,c)=coord(ref_value)?;if rows.contains(&r)&&mapped.contains(&c)&&!values[ref_value].is_empty(){return Err("待写入行已占用".into());}}
    for merged in d.descendants().filter(|n|n.has_tag_name("mergeCell")){
        let (a,b)=merge_bounds(&attr(merged,"ref"))?;
        if rows.iter().any(|r|*r>=a.0&&*r<=b.0)&&mapped.iter().any(|c|*c>=a.1&&*c<=b.1){return Err("待写入行涉及合并单元格，请核定映射".into());}
    }
    let mut root=editable_xml(&book.parts[&path])?;
    let column_styles=d.descendants().filter(|n|n.has_tag_name("col")).filter_map(|n|Some((n.attribute("min")?.parse::<u32>().ok()?,n.attribute("max")?.parse::<u32>().ok()?,n.attribute("style")?.to_owned()))).collect::<Vec<_>>();
    let column_style=|col:&str|{let number=coord(&format!("{col}1")).unwrap().1;column_styles.iter().rev().find(|(min,max,_)|*min<=number&&*max>=number).map(|(_,_,style)|style)};
    let data=root.get_mut_child("sheetData").ok_or("缺少记录区")?;
    let mut expected_labels=Vec::new();
    for (index,row_no) in rows.iter().enumerate(){
        let entry=&request.entries[index];
        let mut raw=entry.measurement.clone();if method.kind=="water_diluted"{if let Some(value)=raw.remove("result"){raw.insert("raw_result".into(),value);}}
        raw.extend(outputs[index].clone());
        let date=chrono::NaiveDate::parse_from_str(&entry.date,"%Y-%m-%d").map_err(|_|"日期无效")?;
        let repeated=groups.observe(&entry.date,&entry.batch,*row_no);
        let template=data.children.iter().filter_map(XMLNode::as_element).filter(|e|e.name=="row"&&e.attributes.get("r").and_then(|r|r.parse::<u32>().ok()).is_some_and(|r|r>=3&&r<*row_no)).max_by_key(|e|e.attributes["r"].parse::<u32>().unwrap()).cloned();
        let old=data.children.iter().position(|n|matches!(n,XMLNode::Element(e)if e.name=="row"&&e.attributes.get("r")==Some(&row_no.to_string())));
        let mut row=if let Some(position)=old{match data.children.remove(position){XMLNode::Element(row)=>row,_=>unreachable!()}}else{
            let mut row=el("row");if let Some(template)=&template{
                row.prefix=template.prefix.clone();row.namespaces=template.namespaces.clone();
                row.attributes=template.attributes.iter().filter(|(key,_)|!matches!(key.as_str(),"r"|"spans"|"uid")&&!key.ends_with(":uid")).map(|(key,value)|(key.clone(),value.clone())).collect();
            }row
        };row.attributes.insert("r".into(),row_no.to_string());
        let date_ref=format!("{date_col}{row_no}");
        let target_date=row.children.iter().filter_map(XMLNode::as_element).find(|e|e.name=="c"&&e.attributes.get("r")==Some(&date_ref));
        let template_date=template.as_ref().and_then(|r|r.children.iter().filter_map(XMLNode::as_element).find(|c|c.name=="c"&&c.attributes.get("r").is_some_and(|r|r.trim_end_matches(|c:char|c.is_ascii_digit())==date_col)));
        let date_cell=target_date.or(template_date);
        let date_style=date_cell.and_then(|cell|cell.attributes.get("s")).or_else(||row.attributes.get("s")).or_else(||column_style(date_col)).or_else(||if date_cell.is_none(){source_styles.get(date_col)}else{None});
        let format=formats.get(date_style.and_then(|s|s.parse::<usize>().ok()).unwrap_or(0)).map(String::as_str).unwrap_or("General");
        let (date_value,date_text)=if repeated.0{(String::new(),false)}else if is_date_format(format)||date_sample.is_some_and(|v|v.parse::<i64>().is_ok()&&v.len()!=8){(date_serial(date,book.date1904).to_string(),false)}else if date_sample.is_some_and(|v|v.contains('-')||v.contains('/')||v.contains('.')){
            (date.format(if date_sample.unwrap().contains('/') {"%Y/%m/%d"}else if date_sample.unwrap().contains('.') {"%Y.%m.%d"}else{"%Y-%m-%d"}).to_string(),true)
        }else{(date.format("%Y%m%d").to_string(),false)};
        let batch_value=if repeated.1 {String::new()}else{entry.batch.clone()};
        expected_labels.push((date_value.clone(),batch_value.clone()));
        raw.extend(Values::from([("date".into(),date_value),("batch".into(),batch_value),("notes".into(),entry.notes.clone())]));
        let formulas=if method.kind=="workbook_formula" {
            let template=method.params.get("template_row").and_then(|r|r.parse::<i32>().ok()).ok_or("缺少原公式定位行")?;
            method.params.iter().filter_map(|(k,f)|k.strip_prefix("f:").map(|c|(c,f))).map(|(c,f)|Ok((format!("col_{c}"),crate::formula::translate(f,*row_no as i32-template,0)?))).collect::<Result<BTreeMap<_,_>,String>>()?
        }else{calc::formulas(&method.kind,&method.columns,*row_no,&method.params)};
        let mut cols=method.columns.iter().collect::<Vec<_>>();cols.sort_by_key(|(_,col)|(col.len(),col.as_str()));
        for (key,col) in cols{if let Some(value)=raw.get(key){
            let text=key=="batch"||key=="notes"||(key=="date"&&date_text)||method.fields.iter().any(|f|f.key==*key&&f.input_type=="text");let reference=format!("{col}{row_no}");
            let old=row.children.iter().position(|n|matches!(n,XMLNode::Element(e)if e.name=="c"&&e.attributes.get("r")==Some(&reference)));
            let cell=if let Some(position)=old{match row.children.remove(position){XMLNode::Element(cell)=>cell,_=>unreachable!()}}else{
                let mut cell=el("c");cell.attributes.insert("r".into(),reference);
                let prototype=template.as_ref().and_then(|r|r.children.iter().filter_map(XMLNode::as_element).find(|c|c.name=="c"&&c.attributes.get("r").is_some_and(|r|r.trim_end_matches(|c:char|c.is_ascii_digit())==col.as_str())));
                let style=if let Some(cell)=prototype{cell.attributes.get("s")}else if row.attributes.contains_key("s")||column_style(col).is_some(){None}else{source_styles.get(col)};
                if let Some(style)=style.filter(|s|!s.is_empty()){cell.attributes.insert("s".into(),style.clone());}cell
            };
            row.children.push(XMLNode::Element(value_cell(cell,value.clone(),text,formulas.get(key))));
        }}
        row.children.sort_by_key(|n|match n{XMLNode::Element(c)if c.name=="c"=>(0,c.attributes.get("r").and_then(|r|coord(r).ok()).map(|(_,c)|c).unwrap_or(0)),_=>(1,0)});
        data.children.push(XMLNode::Element(row));
    }
    data.children.sort_by_key(|n|match n{XMLNode::Element(e)if e.name=="row"=>e.attributes.get("r").and_then(|s|s.parse::<u32>().ok()).unwrap_or(0),_=>0});
    if let Some(dimension)=root.get_mut_child("dimension"){
        let old=dimension.attributes.get("ref").cloned().unwrap_or("A1".into());let (old_start,old_end)=merge_bounds(&old)?;
        let new_col=method.columns.values().map(|c|coord(&format!("{c}1")).map(|(_,col)|col)).collect::<Result<Vec<_>,_>>()?.into_iter().max().unwrap_or(1).max(old_end.1);
        dimension.attributes.insert("ref".into(),format!("{}{}:{}{}",letter(old_start.1.min(mapped.iter().copied().min().unwrap_or(1))),old_start.0.min(rows[0]),letter(new_col),old_end.0.max(*rows.last().unwrap())));
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
    fn editable_xml_preserves_qualified_attributes_and_whitespace(){
        let source=format!(r#"<x:worksheet xmlns:x="{MAIN}" xmlns:a="urn:a" xmlns:b="urn:b" a:id="first" b:id="second"><x:sheetData><x:row r="1"><x:c r="A1" t="inlineStr"><x:is><x:t xml:space="preserve"> </x:t></x:is><x:extLst a:id="kept"/></x:c><x:extLst/></x:row></x:sheetData></x:worksheet>"#);
        let mut root=editable_xml(source.as_bytes()).unwrap();
        let row=root.get_mut_child("sheetData").unwrap().get_mut_child("row").unwrap();
        let cell=row.children.remove(0).as_element().unwrap().clone();
        row.children.insert(0,XMLNode::Element(value_cell(cell," ".into(),true,None)));
        row.children.insert(1,XMLNode::Element(el("c")));
        let mut result=Vec::new();root.write(&mut result).unwrap();let d=doc(&result).unwrap();
        assert_eq!(d.root_element().attribute(("urn:a","id")),Some("first"));
        assert_eq!(d.root_element().attribute(("urn:b","id")),Some("second"));
        assert!(d.descendants().filter(Node::is_element).all(|n|n.tag_name().namespace()==Some(MAIN)));
        let cell=d.descendants().find(|n|n.has_tag_name("c")).unwrap();
        assert_eq!(cell.children().filter(Node::is_element).map(|n|n.tag_name().name()).collect::<Vec<_>>(),vec!["is","extLst"]);
        let t=cell.descendants().find(|n|n.has_tag_name("t")).unwrap();assert_eq!(t.text(),Some(" "));assert_eq!(t.attribute(("http://www.w3.org/XML/1998/namespace","space")),Some("preserve"));
    }
    #[test]
    fn formatting_preserves_values_and_reuses_styles(){
        let mut zip=ZipWriter::new(Cursor::new(Vec::new()));
        for (name,xml) in [
            ("xl/workbook.xml",format!(r#"<workbook xmlns="{MAIN}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="测试" sheetId="1" r:id="r1"/></sheets></workbook>"#)),
            ("xl/_rels/workbook.xml.rels",r#"<Relationships><Relationship Id="r1" Target="worksheets/sheet1.xml"/></Relationships>"#.into()),
            ("xl/styles.xml",format!(r#"<styleSheet xmlns="{MAIN}"><fonts count="1"><font><name val="宋体"/><sz val="11"/></font></fonts><fills count="1"><fill><patternFill patternType="none"/></fill></fills><borders count="1"><border><left style="thin"/></border></borders><cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"><alignment vertical="top" wrapText="1"/></xf></cellXfs></styleSheet>"#)),
            ("xl/worksheets/sheet1.xml",format!(r#"<worksheet xmlns="{MAIN}"><dimension ref="A1:D4"/><cols><col min="1" max="4" width="12" hidden="0"/></cols><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>日期</t></is></c></row><row r="4" ht="18" outlineLevel="1"><c r="A4"><v>46303</v></c><c r="B4"><v>1.234</v></c><c r="D4"><f>B4*2</f><v>2.468</v></c></row></sheetData></worksheet>"#)),
            ("docProps/custom.xml","<unchanged/>".into())
        ]{zip.start_file(name,SimpleFileOptions::default()).unwrap();zip.write_all(xml.as_bytes()).unwrap();}
        let source=zip.finish().unwrap().into_inner();
        let request:FormatRequest=serde_json::from_value(json!({"sheet":"测试","range":"B3:C4","number_format":"0.00","font_name":"Arial","font_size":14,"bold":true,"font_color":"#112233","fill_color":"#FFFF00","alignment":"center","column_width":18,"row_height":32})).unwrap();
        let candidate=format_cells(&source,&request).unwrap();let book=read(&candidate).unwrap();let sheet=doc(&book.parts["xl/worksheets/sheet1.xml"]).unwrap();
        let cell=|reference:&str|sheet.descendants().find(|n|n.has_tag_name("c")&&n.attribute("r")==Some(reference)).unwrap();
        assert_eq!(cell_value(cell("B4"),&book.strings).unwrap(),"1.234");assert_eq!(cell("D4").children().find(|n|n.has_tag_name("f")).unwrap().text(),Some("B4*2"));
        assert_eq!(sheet.descendants().find(|n|n.has_tag_name("row")&&n.attribute("r")==Some("4")).unwrap().attribute("outlineLevel"),Some("1"));
        let widths=sheet.descendants().filter(|n|n.has_tag_name("col")).map(|n|(attr(n,"min"),attr(n,"max"),attr(n,"width"),attr(n,"hidden"))).collect::<Vec<_>>();
        assert_eq!(widths,vec![("1".into(),"1".into(),"12".into(),"0".into()),("2".into(),"2".into(),"18".into(),"0".into()),("3".into(),"3".into(),"18".into(),"0".into()),("4".into(),"4".into(),"12".into(),"0".into())]);
        let (css,_)=styles(&book).unwrap();let style=cell("B4").attribute("s").unwrap().parse::<usize>().unwrap();assert_eq!(css[style]["fontFamily"],"Arial");assert_eq!(css[style]["backgroundColor"],"#FFFF00");assert_eq!(css[style]["verticalAlign"],"top");assert_eq!(css[style]["whiteSpace"],"normal");assert_eq!(css[style]["textAlign"],"center");
        let repeated=read(&format_cells(&candidate,&request).unwrap()).unwrap();
        for section in ["fonts","fills","cellXfs","numFmts"]{let count=|b:&Book|{let d=doc(&b.parts["xl/styles.xml"]).unwrap();d.descendants().find(|n|n.has_tag_name(section)).unwrap().children().filter(Node::is_element).count()};assert_eq!(count(&book),count(&repeated),"{section}");}
        for format in ["yyyy/mm/dd","yyyy年m月d日","General"]{let mut date=request.clone();date.range="A4".into();date.number_format=Some(format.into());let bytes=format_cells(&source,&date).unwrap();let view=raw_view(&bytes,"测试",1).unwrap();let text=view["rows"].as_array().unwrap().iter().flat_map(|r|r["cells"].as_array().unwrap()).find(|c|c["ref"]=="A4").unwrap()["value"].as_str().unwrap().to_owned();assert_eq!(text,match format{"yyyy/mm/dd"=>"2026/10/08","yyyy年m月d日"=>"2026年10月8日",_=>"46303"});}
        let mut compact=ZipWriter::new(Cursor::new(Vec::new()));
        for (name,bytes) in &read(&source).unwrap().parts{compact.start_file(name,SimpleFileOptions::default()).unwrap();if name=="xl/worksheets/sheet1.xml"{compact.write_all(String::from_utf8_lossy(bytes).replace("46303","20261008").as_bytes()).unwrap();}else{compact.write_all(bytes).unwrap();}}
        let compact=compact.finish().unwrap().into_inner();let mut date=request.clone();date.range="A4".into();date.number_format=Some("yyyy/mm/dd".into());
        let formatted=read(&format_cells(&compact,&date).unwrap()).unwrap();let d=doc(&formatted.parts["xl/worksheets/sheet1.xml"]).unwrap();assert_eq!(cell_value(d.descendants().find(|n|n.has_tag_name("c")&&n.attribute("r")==Some("A4")).unwrap(),&formatted.strings).unwrap(),"46303");
        for range in ["A0","XFE1","B4:A1","A1:A10001","A1:GS1"]{let mut bad=request.clone();bad.range=range.into();assert!(format_cells(&source,&bad).is_err(),"{range}");}
        for (key,value) in [("font_size",json!(73)),("row_height",json!(7)),("column_width",json!(1)),("font_color",json!("red")),("number_format",json!("0.000000000")),("alignment",json!("justify")),("font_name",json!(""))]{let mut bad=serde_json::to_value(&request).unwrap();bad[key]=value;assert!(format_cells(&source,&serde_json::from_value(bad).unwrap()).is_err(),"{key}");}
    }
    #[test]
    #[ignore = "requires LABWORK_INVENTORY pointing to original workbooks"]
    fn original_generic_methods_roundtrip(){
        let inventory:Value=serde_json::from_slice(&std::fs::read(std::env::var("LABWORK_INVENTORY").unwrap()).unwrap()).unwrap();
        let mut total=0;let mut report=Vec::new();let mut sheet_total=0;
        for item in inventory.as_array().unwrap(){
            let path=item["Path"].as_str().unwrap();let source=std::fs::read(path).unwrap();let book=read(&source).unwrap();let methods=generic_methods(&source).unwrap();
            sheet_total+=book.order.len();
            let unsupported=book.order.iter().filter(|s|s.trim()!="目录"&&!methods.iter().any(|m|m.sheet==**s)).map(|s|json!({"sheet":s,"reason":generic_error(&source,s)})).collect::<Vec<_>>();
            report.push(json!({"project":item["Name"].as_str().unwrap().split('-').next().unwrap(),"sheets":book.order.len(),"supported_methods":methods.len(),"formula_methods":methods.iter().filter(|m|m.kind=="workbook_formula").count(),"manual_methods":methods.iter().filter(|m|m.kind=="manual").count(),"read_only_sheets":unsupported,"original_unchanged":true}));
            println!("{}: {} methods",item["Name"],methods.len());
            for sheet in &book.order {if sheet.trim()!="目录"&&!methods.iter().any(|m|m.sheet==*sheet){println!("  unsupported {sheet}: {:?}",generic_error(&source,sheet));}}
            for method in methods {
                let raw=method.fields.iter().map(|f|(f.key.clone(),if f.input_type=="text"{"测试".into()}else{"1".into()})).collect::<Values>();
                let outputs=generic_calculate(&method,&raw).unwrap_or_else(|e|panic!("{} / {}: {e}",item["Name"],method.sheet));
                let request:Request=serde_json::from_value(json!({"project":"TEST","method":method.key,"entries":[{"date":"2026-10-10","batch":"GENERIC-TEST","measurement":raw}]})).unwrap();
                let (candidate,rows)=append(&source,&method,&request,&[outputs.clone()]).unwrap_or_else(|e|panic!("{} / {} append: {e}",item["Name"],method.sheet));
                let candidate=read(&candidate).unwrap();let path=target(&candidate,&method.sheet).unwrap();let doc=doc(&candidate.parts[path]).unwrap();
                for (key,value) in &outputs {if let Some(col)=method.columns.get(key){let reference=format!("{col}{}",rows[0]);let cell=doc.descendants().find(|n|n.has_tag_name("c")&&n.attribute("r")==Some(&reference)).unwrap();assert_eq!(cell_value(cell,&candidate.strings).unwrap(),*value);}}
                total+=1;
            }
            if item["Name"].as_str().unwrap().starts_with("K001-") {
                let rules=discover(&source).unwrap();let rule=rules.iter().find(|r|r.sheet.trim()=="硫酸根").unwrap();
                assert!(rule.clear_columns.contains(&"H".into())&&rule.clear_columns.contains(&"I".into()));assert_eq!(rule.data_start_row,4);
                let candidate=blank_month(&source,&rules).unwrap();let blank=read(&candidate).unwrap();
                let source_sheet=doc(&book.parts[target(&book,&rule.sheet).unwrap()]).unwrap();let blank_sheet=doc(&blank.parts[target(&blank,&rule.sheet).unwrap()]).unwrap();
                let after=blank_sheet.descendants().filter(|n|n.has_tag_name("c")).map(|n|(attr(n,"r"),n)).collect::<BTreeMap<_,_>>();
                for cell in source_sheet.descendants().filter(|n|n.has_tag_name("c")) {
                    let reference=attr(cell,"r");let (row,col)=coord(&reference).unwrap();
                    if (col==8||col==9)&&row>=4{assert!(cell_value(after[&reference],&blank.strings).unwrap().is_empty());}
                    if reference=="I1"||reference=="I2"{assert_eq!(cell_value(cell,&book.strings).unwrap(),cell_value(after[&reference],&blank.strings).unwrap());}
                }
                let inherited=generic_methods(&candidate).unwrap();let method=inherited.iter().find(|m|m.sheet.trim()=="硫酸根").unwrap();
                let raw=method.fields.iter().map(|f|(f.key.clone(),"1".into())).collect();assert!(generic_calculate(method,&raw).is_ok());
            }
            assert_eq!(std::fs::read(path).unwrap(),source);
        }
        assert!(total>100);println!("generic calculation/write roundtrip methods: {total}");
        let report=json!({"version":env!("CARGO_PKG_VERSION"),"workbooks":report,"total_sheets":sheet_total,"calculation_and_write_roundtrips":total,"source_files_unchanged":true,"candidate_results_readback_verified":true,"month_formula_inputs_cleared_fixed_headers_preserved":true});
        std::fs::write(Path::new(env!("CARGO_MANIFEST_DIR")).join("../runtime/generic-workbook-regression.json"),serde_json::to_vec_pretty(&report).unwrap()).unwrap();
    }
    #[test]
    #[ignore = "requires LABWORK_INVENTORY pointing to the read-only local workbook inventory"]
    fn original_workbooks_discovery_and_month_templates() {
        let inventory:Value=serde_json::from_slice(&std::fs::read(std::env::var("LABWORK_INVENTORY").expect("inventory path")).unwrap()).unwrap();
        let mut sheet_count=0;
        for item in inventory.as_array().unwrap() {
            let path=item["Path"].as_str().unwrap();let original=std::fs::read(path).unwrap();
            let names=sheet_names(&original).unwrap();assert_eq!(names.len(),item["SheetCount"].as_u64().unwrap()as usize);
            for name in &names {
                let view=raw_view(&original,name,1).unwrap();assert!(view["rows"].as_array().unwrap().len()<=200);
                assert_eq!(&view["name"],name);
                sheet_count+=1;
            }
            let rules=discover(&original).unwrap();assert!(!rules.is_empty());
            let candidate=blank_month(&original,&rules).unwrap();let source=read(&original).unwrap();let blank=read(&candidate).unwrap();
            for rule in &rules {
                let path=target(&source,&rule.sheet).unwrap();let d=doc(&source.parts[path]).unwrap();let output=doc(&blank.parts[path]).unwrap();
                let cells=output.descendants().filter(|n|n.has_tag_name("c")).map(|n|(attr(n,"r"),n)).collect::<BTreeMap<_,_>>();
                for cell in d.descendants().filter(|n|n.has_tag_name("c")) {
                    let reference=attr(cell,"r");let (row,col)=coord(&reference).unwrap();let actual=cells[&reference];
                    assert_eq!(cell.attribute("s"),actual.attribute("s"));
                    if let Some(formula)=cell.children().find(|n|n.has_tag_name("f")).filter(|_|!rule.clear_columns.is_empty()) {
                        let actual_formula=actual.children().find(|n|n.has_tag_name("f")).unwrap();assert_eq!(formula.text(),actual_formula.text());assert_eq!(formula.attributes().map(|a|(a.name(),a.value())).collect::<BTreeMap<_,_>>(),actual_formula.attributes().map(|a|(a.name(),a.value())).collect::<BTreeMap<_,_>>());
                        assert!(!actual.children().any(|n|n.has_tag_name("v")));
                    } else if row>=rule.data_start_row&&rule.clear_columns.contains(&letter(col)) {
                        assert!(cell_value(actual,&blank.strings).unwrap().is_empty());
                    } else {assert_eq!(cell_value(cell,&source.strings).unwrap(),cell_value(actual,&blank.strings).unwrap());}
                }
            }
            assert_eq!(std::fs::read(path).unwrap(),original);
        }
        println!("read-only fixture verification: {} workbooks, {sheet_count} sheets",inventory.as_array().unwrap().len());
    }
    #[test]
    fn historical_labels_keep_raw_context_formulas_and_cross_day_batches() {
        let projects:Vec<crate::Project>=serde_json::from_str(include_str!("../catalog.json")).unwrap();
        let method=projects.iter().find(|p|p.code=="D006").unwrap().methods.iter().find(|m|m.key=="chloride").unwrap();
        let mut zip=ZipWriter::new(Cursor::new(Vec::new()));
        let sheet=format!(r#"<worksheet xmlns="{MAIN}"><sheetData>
          <row r="1"><c r="B1" t="inlineStr"><is><t>批号</t></is></c><c r="C1" t="inlineStr"><is><t>质量</t></is></c><c r="D1" t="inlineStr"><is><t>体积</t></is></c></row>
          <row r="2"><c r="A2" t="inlineStr"><is><t>日期</t></is></c><c r="F2"><v>35.45</v></c></row>
          <row r="3"><c r="A3" s="0"/></row>
          <row r="4"><c r="A4"><v>46303</v></c><c r="B4" t="inlineStr"><is><t>LOT-1</t></is></c><c r="C4"><v>1</v></c><c r="F4"><v>0.1032</v></c></row>
          <row r="5"><c r="A5"><v>20261008</v></c><c r="B5" t="inlineStr"><is><t>LOT-1</t></is></c><c r="C5"><v>2</v></c></row>
          <row r="6"><c r="A6"><v>46304</v></c><c r="B6" t="inlineStr"><is><t>LOT-1</t></is></c><c r="C6"><v>3</v></c></row>
          <row r="7"><c r="B7" t="inlineStr"><is><t>LOT-1</t></is></c></row>
          <row r="8"><c r="A8"><f>A6</f><v>46304</v></c></row>
          <row r="9"><c r="B9" t="str"><f>B6</f><v>LOT-1</v></c></row>
          <row r="10"><c r="A10"><v>46304</v></c></row>
          <row r="24"><c r="C24"><v>4</v></c><c r="E24"><f>C24*2</f><v>8</v></c></row>
          <row r="25" ht="30"><c r="J25"><v>99</v></c></row>
        </sheetData><mergeCells count="2"><mergeCell ref="A10:A11"/><mergeCell ref="J25:K25"/></mergeCells></worksheet>"#);
        for (name,xml) in [
            ("xl/workbook.xml",format!(r#"<workbook xmlns="{MAIN}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="目录" sheetId="2" r:id="rId2"/><sheet name="{}" sheetId="1" r:id="rId1"/></sheets></workbook>"#,method.sheet)),
            ("xl/_rels/workbook.xml.rels",r#"<Relationships><Relationship Id="rId1" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Target="worksheets/sheet2.xml"/></Relationships>"#.into()),
            ("xl/styles.xml",format!(r#"<styleSheet xmlns="{MAIN}"><cellXfs count="1"><xf numFmtId="0"/></cellXfs></styleSheet>"#)),
            ("xl/worksheets/sheet1.xml",sheet),
            ("xl/worksheets/sheet2.xml",format!(r#"<worksheet xmlns="{MAIN}"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>目录</t></is></c></row></sheetData></worksheet>"#)),
        ] {zip.start_file(name,SimpleFileOptions::default()).unwrap();zip.write_all(xml.as_bytes()).unwrap();}
        let source=zip.finish().unwrap().into_inner();
        let view=view(&source,method,&[5,7,8,9,10]).unwrap();
        let cell=|reference:&str|view["rows"].as_array().unwrap().iter().flat_map(|r|r["cells"].as_array().unwrap()).find(|c|c["ref"]==reference).unwrap();
        assert_eq!(cell("A5")["value"],"");assert_eq!(cell("A5")["raw"],"20261008");
        assert_eq!(cell("B5")["value"],"");assert_eq!(cell("B6")["value"],"LOT-1");assert_eq!(cell("B7")["value"],"");
        assert_eq!(cell("A6")["value"],"2026-10-09");
        assert_eq!(cell("A8")["formula"],"A6");assert_eq!(cell("B9")["value"],"LOT-1");assert_eq!(cell("A10")["value"],"2026-10-09");
        let request:Request=serde_json::from_value(json!({"project":"D006","method":"chloride","entries":[{"date":"2026-10-09","batch":"LOT-1","measurement":{"mass":"1","volume":"1"}},{"date":"2026-10-10","batch":"LOT-1","measurement":{"mass":"2","volume":"2"}}]})).unwrap();
        let outputs=request.entries.iter().map(|e|calc::calculate(&method.kind,&e.measurement,&method.params).unwrap()).collect::<Vec<_>>();
        let (candidate,rows)=append(&source,method,&request,&outputs).unwrap();assert_eq!(rows,vec![25,26]);
        let book=read(&candidate).unwrap();let d=doc(&book.parts["xl/worksheets/sheet1.xml"]).unwrap();
        let cell=|reference:&str|d.descendants().find(|n|n.has_tag_name("c")&&n.attribute("r")==Some(reference)).unwrap();
        for reference in ["A25","B25"] {assert_eq!(cell_value(cell(reference),&book.strings).unwrap(),"");}
        for (reference,value) in [("A5","20261008"),("B5","LOT-1"),("B7","LOT-1")]{assert_eq!(cell_value(cell(reference),&book.strings).unwrap(),value);}
        assert_eq!(book.parts["xl/styles.xml"],read(&source).unwrap().parts["xl/styles.xml"],"record append must not rewrite styles");
        assert_eq!(cell_value(cell("B6"),&book.strings).unwrap(),"LOT-1");
        assert_eq!(cell_value(cell("B26"),&book.strings).unwrap(),"LOT-1");
        assert_eq!(cell_value(cell("A26"),&book.strings).unwrap(),"46305");
        for (reference,formula) in [("A8","A6"),("B9","B6"),("E24","C24*2")] {assert_eq!(cell(reference).children().find(|n|n.has_tag_name("f")).unwrap().text(),Some(formula));}
        assert_eq!(cell_value(cell("C5"),&book.strings).unwrap(),"2");
        assert_eq!(cell_value(cell("J25"),&book.strings).unwrap(),"99");
        assert_eq!(cell("J25").parent().unwrap().attribute("ht"),Some("30"));
        for row_style in [true,false]{
            let source_book=read(&source).unwrap();let mut root=editable_xml(&source_book.parts["xl/worksheets/sheet1.xml"]).unwrap();
            let data=root.get_mut_child("sheetData").unwrap();data.children.retain(|n|!n.as_element().is_some_and(|r|r.attributes.get("r").is_some_and(|r|r.parse::<u32>().unwrap()>4)));
            let row=data.children.iter_mut().filter_map(XMLNode::as_mut_element).find(|r|r.attributes.get("r").is_some_and(|r|r=="4")).unwrap();
            row.children.retain(|n|!n.as_element().is_some_and(|c|c.attributes.get("r").is_some_and(|r|r=="A4")));
            if row_style{row.attributes.extend([("s".into(),"1".into()),("customFormat".into(),"1".into())]);}else{let mut cols=el("cols");let mut col=el("col");col.attributes.extend([("min".into(),"1".into()),("max".into(),"1".into()),("style".into(),"1".into())]);cols.children.push(XMLNode::Element(col));root.children.insert(0,XMLNode::Element(cols));}
            let mut sheet=Vec::new();root.write(&mut sheet).unwrap();let mut fixture=ZipWriter::new(Cursor::new(Vec::new()));
            for (name,part) in &source_book.parts{fixture.start_file(name,SimpleFileOptions::default()).unwrap();if name=="xl/worksheets/sheet1.xml"{fixture.write_all(&sheet).unwrap();}else if name=="xl/styles.xml"{fixture.write_all(format!(r#"<styleSheet xmlns="{MAIN}"><cellXfs count="2"><xf numFmtId="0"/><xf numFmtId="14"/></cellXfs></styleSheet>"#).as_bytes()).unwrap();}else{fixture.write_all(part).unwrap();}}
            let (candidate,rows)=append(&fixture.finish().unwrap().into_inner(),method,&request,&outputs).unwrap();assert_eq!(rows,vec![5,6]);let book=read(&candidate).unwrap();let d=doc(&book.parts["xl/worksheets/sheet1.xml"]).unwrap();
            for (reference,value) in [("A5","46304"),("A6","46305")]{let cell=d.descendants().find(|n|n.has_tag_name("c")&&n.attribute("r")==Some(reference)).unwrap();assert_eq!(cell.attribute("s"),None,"new date must inherit row or column style");assert_eq!(cell_value(cell,&book.strings).unwrap(),value);}
        }
        let format:FormatRequest=serde_json::from_value(json!({"sheet":method.sheet,"range":"A4","number_format":"yyyy/mm/dd"})).unwrap();
        let formatted=format_cells(&source,&format).unwrap();
        let (appended,_)=append(&formatted,method,&request,&outputs).unwrap();
        let formatted_view=raw_view(&appended,&method.sheet,1).unwrap();
        let date=formatted_view["rows"].as_array().unwrap().iter().flat_map(|r|r["cells"].as_array().unwrap()).find(|c|c["ref"]=="A4").unwrap();
        assert_eq!(date["value"],"2026/10/08","append must preserve user-selected historical date format");
        for (number_format,expected) in [(Some("General"),"46303"),(None,"2026-10-08")] {
            let format:FormatRequest=serde_json::from_value(json!({"sheet":method.sheet,"range":"A4","number_format":number_format,"fill_color":"#FF0000","alignment":"right"})).unwrap();
            let formatted=format_cells(&source,&format).unwrap();let (appended,_)=append(&formatted,method,&request,&outputs).unwrap();let v=raw_view(&appended,&method.sheet,1).unwrap();
            let cell=v["rows"].as_array().unwrap().iter().flat_map(|r|r["cells"].as_array().unwrap()).find(|c|c["ref"]=="A4").unwrap();assert_eq!(cell["value"],expected);
            let (styles,_)=styles(&read(&appended).unwrap()).unwrap();let style=&styles[cell["style"].as_u64().unwrap()as usize];assert_eq!(style["backgroundColor"],"#FF0000");assert_eq!(style["textAlign"],"right");
        }
        let format:FormatRequest=serde_json::from_value(json!({"sheet":method.sheet,"range":"A25:F26","fill_color":"#00FF00","row_height":40})).unwrap();
        let formatted=format_cells(&source,&format).unwrap();let (appended,rows)=append(&formatted,method,&request,&outputs).unwrap();assert_eq!(rows,vec![25,26]);
        let b=read(&appended).unwrap();let d=doc(&b.parts["xl/worksheets/sheet1.xml"]).unwrap();let (css,_)=styles(&b).unwrap();
        for reference in ["A25","B25","C25","A26","B26","C26"]{let cell=d.descendants().find(|n|n.has_tag_name("c")&&n.attribute("r")==Some(reference)).unwrap();let style=cell.attribute("s").unwrap().parse::<usize>().unwrap();assert_eq!(css[style]["backgroundColor"],"#00FF00");assert_eq!(cell.parent().unwrap().attribute("ht"),Some("40"));}
        assert!(d.descendants().any(|n|n.has_tag_name("mergeCell")&&n.attribute("ref")==Some("A10:A11")));
        let source_book=read(&source).unwrap();let source_doc=doc(&source_book.parts["xl/worksheets/sheet1.xml"]).unwrap();
        assert_eq!(cell_value(source_doc.descendants().find(|n|n.attribute("r")==Some("A5")).unwrap(),&source_book.strings).unwrap(),"20261008");
        assert_eq!(sheet_names(&source).unwrap(),vec!["目录".to_owned(),method.sheet.clone()]);
        let raw=raw_view(&source,&method.sheet,1).unwrap();assert_eq!(raw["rows"].as_array().unwrap().len(),25);
        assert!(!raw["truncated"].as_bool().unwrap());
        let page=raw_view(&source,&method.sheet,20).unwrap();assert_eq!(page["rows"][0]["number"],20);assert_eq!(page["truncated"],true);
        assert!(raw_view(&source,&method.sheet,0).is_err());
        assert_eq!(discover(&source).unwrap().len(),2);
        let rule=MonthRule{sheet:method.sheet.clone(),data_start_row:3,clear_columns:vec!["A".into(),"B".into(),"C".into(),"D".into(),"E".into()]};
        let blank=blank_month(&source,&[rule.clone()]).unwrap();let blank_book=read(&blank).unwrap();let blank_doc=doc(&blank_book.parts["xl/worksheets/sheet1.xml"]).unwrap();
        let blank_cell=|reference:&str|blank_doc.descendants().find(|n|n.has_tag_name("c")&&n.attribute("r")==Some(reference)).unwrap();
        for reference in ["A4","B4","C4","A5","C24"]{assert_eq!(cell_value(blank_cell(reference),&blank_book.strings).unwrap(),"");}
        for (reference,value) in [("F2","35.45"),("F4","0.1032"),("B1","批号")] {assert_eq!(cell_value(blank_cell(reference),&blank_book.strings).unwrap(),value);}
        for (reference,formula) in [("A8","A6"),("B9","B6"),("E24","C24*2")] {let c=blank_cell(reference);assert_eq!(c.children().find(|n|n.has_tag_name("f")).unwrap().text(),Some(formula));assert!(c.children().all(|n|!n.has_tag_name("v")));}
        assert_eq!(blank_cell("A3").attribute("s"),Some("0"));
        for (name,part) in &source_book.parts {if name!="xl/worksheets/sheet1.xml"{assert_eq!(blank_book.parts[name],*part);}}
        assert!(blank_month(&source,&[]).is_err());assert!(blank_month(&source,&[rule.clone(),rule.clone()]).is_err());
        assert!(blank_month(&source,&[MonthRule{sheet:"目录".into(),..rule.clone()}]).is_err());
        assert!(blank_month(&source,&[MonthRule{data_start_row:2,..rule}]).is_err());
        for invalid in ["<cols><col min=\"0\" max=\"1\"/></cols>","<cols><col min=\"5\" max=\"1\"/></cols>","<cols><col min=\"1\" max=\"16385\"/></cols>","<mergeCells><mergeCell ref=\"B8:A4\"/></mergeCells>","<mergeCells><mergeCell ref=\"A1:B2:C3\"/></mergeCells>"] {
            let mut malformed=ZipWriter::new(Cursor::new(Vec::new()));
            for (name,bytes) in &source_book.parts {
                malformed.start_file(name,SimpleFileOptions::default()).unwrap();
                if name=="xl/worksheets/sheet1.xml" {let xml=String::from_utf8(bytes.clone()).unwrap().replace("</worksheet>",&format!("{invalid}</worksheet>"));malformed.write_all(xml.as_bytes()).unwrap();}
                else {malformed.write_all(bytes).unwrap();}
            }
            assert!(raw_view(&malformed.finish().unwrap().into_inner(),&method.sheet,1).is_err(),"{invalid}");
        }
    }
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
