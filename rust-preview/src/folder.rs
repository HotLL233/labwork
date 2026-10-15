//! User-selected local project root. Scanning and association never modify originals.
use super::*;
use rusqlite::OptionalExtension;
use std::{collections::BTreeSet, path::Path as FsPath};

const MAX_FILES:usize=1000;
const MAX_TOTAL:u64=128*1024*1024;

#[derive(Serialize)]
pub struct Entry {
    id:String, project:String, name:String, month:String, path:String, state:String, message:String, previous_path:Option<String>, selectable:bool, selected:bool,
    #[serde(skip)] hash:String,
    #[serde(skip)] mirror:Option<String>,
}
#[derive(Serialize)]
pub struct Scan {
    root:String, fingerprint:String, entries:Vec<Entry>, issues:Vec<String>, can_bind:bool, latest_month:Option<String>,
}
fn conflict(message:impl ToString)->Error{(StatusCode::CONFLICT,Json(json!({"error":message.to_string()})))}
pub fn initialize(db:&Connection)->Result<(),rusqlite::Error>{db.execute_batch("CREATE TABLE IF NOT EXISTS source_folder_settings(id INTEGER PRIMARY KEY CHECK(id=1),path TEXT NOT NULL);")}
pub fn saved(db:&Connection)->Result<Option<String>,Error>{db.query_row("SELECT path FROM source_folder_settings WHERE id=1",[],|r|r.get(0)).optional().map_err(error)}
fn same(a:&str,b:&str)->bool{a.eq_ignore_ascii_case(b)}
fn reparse(path:&FsPath)->Result<bool,Error>{
    let metadata=fs::symlink_metadata(path).map_err(error)?;
    #[cfg(windows)] {use std::os::windows::fs::MetadataExt;Ok(metadata.file_attributes()&0x400!=0)}
    #[cfg(not(windows))] {Ok(metadata.file_type().is_symlink())}
}
fn local_root(app:&App,input:&str)->Result<PathBuf,Error>{
    let root=local_directory(app,input)?;
    if fs::canonicalize(&app.home).map_err(error)?.starts_with(&root){return Err(error("所选文件夹不能包含平台数据目录，请进入具体项目文件夹"));}
    Ok(root)
}
fn local_directory(app:&App,input:&str)->Result<PathBuf,Error>{
    let path=PathBuf::from(input.trim());
    if !path.is_absolute()||input.trim().starts_with("\\\\")||input.trim().starts_with("//"){return Err(error("请选择服务器本机文件夹，不支持网络共享"));}
    if !path.is_dir(){return Err(error("总文件夹不存在或无法读取"));}
    for ancestor in path.ancestors(){if reparse(ancestor)?{return Err(error("总文件夹路径不能经过符号链接或目录联接"));}}
    let root=fs::canonicalize(path).map_err(error)?;
    if root.to_string_lossy().starts_with(r"\\?\UNC\"){return Err(error("不支持网络共享总文件夹"));}
    source::external(app,&root)?;
    Ok(root)
}
fn ignored(name:&str)->bool{
    let lower=name.to_ascii_lowercase();
    name.starts_with(['.','~'])||lower.contains("backup")||lower.split(['.','-','_',' ']).any(|s|s=="bak")||name.contains("备份")
}
fn safe_source(app:&App,root:&FsPath,input:&str)->Result<PathBuf,Error>{
    let source=source::local_path(input)?;source::external(app,&source)?;
    if !source.starts_with(root){return Err(error("文件实际路径已超出所选总文件夹"));}
    for ancestor in FsPath::new(input).ancestors(){if reparse(ancestor)?{return Err(error("文件路径经过符号链接或目录联接"));}}
    Ok(source)
}
fn period(year:&str,month:&str)->Option<String>{
    let year=year.strip_suffix('年').unwrap_or(year);
    if year.len()!=4||!year.bytes().all(|b|b.is_ascii_digit()){return None;}
    let prefixed=month.strip_prefix(&format!("{year}.")).or_else(||month.strip_prefix(&format!("{year}-")));
    if prefixed.is_some_and(|number|number.len()!=2){return None;}
    let number=prefixed.unwrap_or_else(||month.strip_suffix('月').unwrap_or(month));
    if number.is_empty()||number.len()>2||!number.bytes().all(|b|b.is_ascii_digit()){return None;}
    let value=format!("{year}-{:02}",number.parse::<u32>().ok()?);
    months::valid(&value).then_some(value)
}
fn year(text:&str)->Option<&str>{
    let number=text.strip_suffix('年').unwrap_or(text);
    (number.len()==4&&number.bytes().all(|b|b.is_ascii_digit())&&("1900"..="2100").contains(&number)).then_some(number)
}
fn month_number(text:&str)->bool{
    let number=text.strip_suffix('月').unwrap_or(text);
    !number.is_empty()&&number.len()<=2&&number.bytes().all(|b|b.is_ascii_digit())&&number.parse::<u8>().is_ok_and(|n|(1..=12).contains(&n))
}
// Read explicit year/month evidence; never infer a missing date from the server clock.
fn period_tokens(text:&str)->Vec<(String,usize,usize)>{
    let mut result=Vec::new();let bytes=text.as_bytes();
    for (start,_) in text.char_indices(){
        if start+4>bytes.len()||!bytes[start..start+4].iter().all(u8::is_ascii_digit)||start>0&&bytes[start-1].is_ascii_digit(){continue;}
        let Some(y)=year(&text[start..start+4])else{continue};
        let rest=&text[start+4..];
        let (tail,separated)=if let Some(s)=rest.strip_prefix(['.','-','_','年']){(s,true)}else{(rest,false)};
        let size=tail.bytes().take_while(u8::is_ascii_digit).count();
        if size==0||size>2||!separated&&size!=2{continue;}
        let number=&tail[..size];
        if !separated&&number.len()!=2{continue;}
        let value=format!("{y}-{:02}",number.parse::<u8>().unwrap_or(0));
        if months::valid(&value){let end=text.len()-tail.len()+size+usize::from(tail[size..].starts_with('月'))*'月'.len_utf8();result.push((value,start,end));}
    }result
}
fn periods(text:&str)->Vec<(String,usize)>{period_tokens(text).into_iter().map(|(value,start,_)|(value,start)).collect()}
pub fn next_filename(name:&str,newmonth:&str)->Result<String,Error>{
    if !months::valid(newmonth){return Err(error("新月份无效"));}
    let mut result=String::new();let mut previous=0;
    for (_,start,end) in period_tokens(name){
        result.push_str(&name[previous..start]);let token=&name[start..end];let rest=&token[4..];
        let separator=rest.chars().next().filter(|c|!c.is_ascii_digit());
        result.push_str(&newmonth[..4]);
        if let Some(separator)=separator{result.push(separator);}
        let digits=rest.trim_start_matches(['.','-','_','年']).bytes().take_while(u8::is_ascii_digit).count();
        if digits==1{result.push_str(&newmonth[5..].parse::<u8>().unwrap().to_string());}else{result.push_str(&newmonth[5..]);}
        if token.ends_with('月'){result.push('月');}previous=end;
    }
    result.push_str(&name[previous..]);Ok(result)
}
fn identity(root:&FsPath,path:&FsPath)->Result<(String,String,PathBuf),String>{
    let relative=path.strip_prefix(root).map_err(|_|"文件超出所选目录")?;
    let dirs=relative.parent().unwrap_or_else(||FsPath::new("")).components().map(|s|s.as_os_str().to_string_lossy().into_owned()).collect::<Vec<_>>();
    let mut context=Vec::<PathBuf>::new();let mut ancestor=Some(root);
    while let Some(directory)=ancestor{
        context.push(directory.to_path_buf());
        let name=directory.file_name().and_then(|s|s.to_str()).unwrap_or("");
        if year(name).is_none()&&!month_number(name)&&periods(name).is_empty(){break;}
        ancestor=directory.parent();
    }
    context.reverse();let context_len=context.len();let mut directory=root.to_path_buf();
    for name in &dirs{directory.push(name);context.push(directory.clone());}
    let mut found=BTreeSet::new();let mut years=BTreeSet::new();let mut current_year=None;
    for directory in &context{
        let name=directory.file_name().and_then(|s|s.to_str()).unwrap_or("");
        if let Some(y)=year(name){years.insert(y.to_owned());current_year=Some(y.to_owned());}
        for (value,_) in periods(name){found.insert(value);}
        if month_number(name){if let Some(y)=&current_year{if let Some(value)=period(y,name){found.insert(value);}}}
    }
    let filename=path.file_stem().and_then(|s|s.to_str()).ok_or("文件名不是有效文本")?;
    let filename_periods=periods(filename);
    for (value,_) in &filename_periods{found.insert(value.clone());}
    if found.is_empty(){return Err("无法识别年月；目录或文件名需包含 YYYYMM、YYYY.MM、YYYY-MM、YYYY年M月，或年份/月目录".into());}
    if found.len()!=1{return Err("目录与文件名包含不同年月，请核对后再关联".into());}
    let month=found.into_iter().next().unwrap();
    if years.iter().any(|y|y!=&month[..4]){return Err("年份目录与年月不一致，请核对后再关联".into());}
    let prefix=filename_periods.first().map(|(_,start)|filename[..*start].trim_end_matches(['-','_','.',' ','（','('])).filter(|name|!name.is_empty());
    let candidates=context.iter().take_while(|p|p.file_name().and_then(|s|s.to_str()).is_some_and(|name|year(name).is_none()&&!month_number(name)&&periods(name).is_empty())).collect::<Vec<_>>();
    let matching=prefix.map(|prefix|candidates.iter().filter(|p|p.file_name().and_then(|s|s.to_str()).is_some_and(|name|same(name,prefix)||default_code(name)==default_code(prefix))).copied().collect::<Vec<_>>()).unwrap_or_default();
    let relative_candidates=candidates.iter().filter(|p|p.as_path()!=root&&p.starts_with(root)).copied().collect::<Vec<_>>();
    let project_dir=if matching.len()==1{Some(matching[0].clone())}
        else if matching.len()>1{return Err("多个目录均符合文件中的项目名称，无法确定项目，请进入项目文件夹后再扫描".into());}
        else if relative_candidates.len()==1{Some(relative_candidates[0].clone())}
        else if relative_candidates.len()>1{return Err("存在多层非年月目录，无法确定项目；文件名可包含项目名称和年月，或进入项目文件夹后再扫描".into());}
        else{context[..context_len].iter().rev().find(|p|p.file_name().and_then(|s|s.to_str()).is_some_and(|name|year(name).is_none()&&!month_number(name)&&periods(name).is_empty())).cloned()};
    let name=if dirs.is_empty()&&context_len==1&&matching.is_empty(){prefix.map(str::to_owned).or_else(||project_dir.as_ref().and_then(|p|p.file_name()).map(|s|s.to_string_lossy().into_owned()))}else{project_dir.as_ref().and_then(|p|p.file_name()).map(|s|s.to_string_lossy().into_owned())}.ok_or("无法识别项目名称，请选择项目所在目录")?;
    Ok((name,month,project_dir.unwrap_or_else(||root.to_path_buf())))
}
#[cfg(test)]
fn project_directory(root:&FsPath,path:&FsPath)->Result<Option<PathBuf>,Error>{
    let (name,_,directory)=identity(root,path).map_err(error)?;
    if directory.file_name().and_then(|s|s.to_str()).is_some_and(|folder|folder==name){Ok(Some(directory))}else{Ok(None)}
}
fn default_code(name:&str)->String{
    let prefix=name.chars().take_while(|c|c.is_ascii_alphanumeric()||*c=='-'||*c=='_').collect::<String>();
    let prefix=prefix.trim_end_matches(['-','_']).to_ascii_uppercase();
    if prefix.is_empty(){format!("P_{}",&hash(name.as_bytes())[..12])}
    else if prefix.len()>50{format!("{}_{}",&prefix[..35],&hash(name.as_bytes())[..12])}else{prefix}
}
fn paths(directory:&FsPath)->Result<Vec<PathBuf>,Error>{
    let mut entries=Vec::new();
    for entry in fs::read_dir(directory).map_err(error)?{if entries.len()==MAX_FILES*5{return Err(error("目录内容超过5000项，请选择更具体的项目文件夹"));}entries.push(entry.map_err(error)?.path());}
    entries.sort();Ok(entries)
}
fn hidden(path:&FsPath)->Result<bool,Error>{
    #[cfg(windows)] {use std::os::windows::fs::MetadataExt;Ok(fs::symlink_metadata(path).map_err(error)?.file_attributes()&2!=0)}
    #[cfg(not(windows))] {let _=path;Ok(false)}
}
pub fn browse(app:&App,input:Option<&str>)->Result<Value,Error>{
    let Some(input)=input.filter(|s|!s.trim().is_empty())else{
        let mut directories=Vec::new();
        #[cfg(windows)]
        for drive in b'A'..=b'Z'{
            #[link(name="kernel32")] unsafe extern "system" {fn GetDriveTypeW(root:*const u16)->u32;}
            let path=format!("{}:\\",drive as char);let wide=path.encode_utf16().chain(Some(0)).collect::<Vec<_>>();
            if matches!(unsafe{GetDriveTypeW(wide.as_ptr())},2|3|6)&&FsPath::new(&path).is_dir(){directories.push(json!({"name":path,"path":path,"selectable":true}));}
        }
        #[cfg(not(windows))] directories.push(json!({"name":"/","path":"/","selectable":true}));
        return Ok(json!({"path":null,"parent":null,"directories":directories,"roots":true,"truncated":false}));
    };
    let directory=local_directory(app,input)?;
    if directory.file_name().and_then(|s|s.to_str()).is_some_and(ignored){return Err(error("备份或隐藏目录不作为关联来源"));}
    let mut directories=Vec::new();let mut truncated=false;
    for (index,entry) in fs::read_dir(&directory).map_err(error)?.enumerate(){
        if index==MAX_FILES*5{truncated=true;break;}
        let Ok(entry)=entry else{continue};let path=entry.path();
        let Some(name)=path.file_name().and_then(|s|s.to_str())else{continue};
        if ignored(name)||reparse(&path).unwrap_or(true)||hidden(&path).unwrap_or(true)||!path.is_dir(){continue;}
        let Ok(canonical)=fs::canonicalize(&path)else{continue};
        if source::external(app,&canonical).is_err(){continue;}
        if directories.len()==500{truncated=true;break;}
        directories.push(json!({"name":name,"path":source::display_path(&canonical),"selectable":true}));
    }
    directories.sort_by(|a,b|a["name"].as_str().cmp(&b["name"].as_str()));
    Ok(json!({"path":source::display_path(&directory),"parent":directory.parent().map(source::display_path),"directories":directories,"roots":false,"truncated":truncated}))
}
fn walk(root:&FsPath,directory:&FsPath,files:&mut Vec<PathBuf>,issues:&mut Vec<String>,count:&mut usize)->Result<(),Error>{
    for path in paths(directory)? {
        *count+=1;if *count>MAX_FILES*5{return Err(error("目录内容超过5000项，请选择仅包含项目的总文件夹"));}
        let name=path.file_name().and_then(|s|s.to_str()).ok_or_else(||error("文件名不是有效文本"))?;
        if ignored(name)||hidden(&path)?{continue;}
        if reparse(&path)?{issues.push(format!("不支持符号链接或目录联接：{}",source::display_path(&path)));continue;}
        let depth=path.strip_prefix(root).map_err(error)?.components().count();
        if path.is_dir(){
            if depth<=8{walk(root,&path,files,issues,count)?;}
            else {issues.push(format!("目录超过8层，已跳过：{}",source::display_path(&path)));}
        }else if path.extension().is_some_and(|s|s.to_string_lossy().eq_ignore_ascii_case("xlsx")) {
            if files.len()==MAX_FILES{return Err(error("一次最多识别1000个工作簿"));}files.push(path);
        }
    }Ok(())
}
fn links(db:&Connection)->Result<Vec<(String,String,source::Binding)>,Error>{
    db.prepare("SELECT month,project,path,hash FROM source_links ORDER BY month,project").map_err(error)?.query_map([],|r|Ok((r.get(0)?,r.get(1)?,source::Binding{path:r.get(2)?,hash:r.get(3)?}))).map_err(error)?.collect::<Result<Vec<_>,_>>().map_err(error)
}
fn project_codes(app:&App)->Result<BTreeSet<String>,Error>{
    let mut result=BTreeSet::new();
    for month in months::list(&app.home)?{for path in paths(&months::directory(&app.home,&month).join("workbooks"))?{if path.extension().is_some_and(|s|s=="xlsx"){if let Some(code)=path.file_stem().and_then(|s|s.to_str()){result.insert(code.to_owned());}}}}
    Ok(result)
}
pub fn scan(app:&App,input:&str)->Result<Scan,Error>{
    let root=local_root(app,input)?;let mut files=Vec::new();let mut issues=Vec::new();
    walk(&root,&root,&mut files,&mut issues,&mut 0)?;
    let total=files.iter().try_fold(0u64,|total,path|fs::metadata(path).map(|m|total.saturating_add(m.len())).map_err(error))?;
    if total>MAX_TOTAL{return Err(error("工作簿总大小超过128 MiB，请减少本次关联范围"));}
    let existing=links(&app.db)?;let names=months::names(&app.home)?;let reserved=project_codes(app)?;
    let mut projects=BTreeMap::<String,String>::new();let mut entries=Vec::new();
    for path in files {
        let display=source::display_path(&path);
        let mut entry=Entry{id:hash(display.to_ascii_uppercase().as_bytes()),project:String::new(),name:path.file_stem().unwrap_or_default().to_string_lossy().into_owned(),month:String::new(),path:display,state:"conflict".into(),message:String::new(),previous_path:None,selectable:false,selected:false,hash:String::new(),mirror:None};
        let (name,month,project_dir)=match identity(&root,&path){Ok(identity)=>identity,Err(message)=>{entry.message=message;entries.push(entry);continue;}};
        entry.name=name;entry.month=month;
        if entry.name.chars().count()>100{entry.message="项目文件夹名称最多100字".into();entries.push(entry);continue;}
        let display_root=PathBuf::from(source::display_path(&root));
        let mut matches=existing.iter().filter(|(_,_,binding)|identity(&display_root,FsPath::new(&binding.path)).is_ok_and(|(name,_,directory)|name==entry.name&&same(&source::display_path(&directory),&source::display_path(&project_dir)))).map(|(_,code,_)|code.clone()).collect::<BTreeSet<_>>();
        matches.extend(names.iter().filter(|(_,name)|*name==&entry.name).map(|(code,_)|code.clone()));
        if matches.len()>1{entry.message=format!("项目目录对应多个已有项目：{}",matches.into_iter().collect::<Vec<_>>().join("、"));entries.push(entry);continue;}
        let matched=!matches.is_empty();entry.project=matches.into_iter().next().unwrap_or_else(||default_code(&entry.name));
        let reserved_matches=reserved.iter().filter(|code|same(code,&entry.project)).cloned().collect::<Vec<_>>();
        if reserved_matches.len()>1{entry.message="平台中有多个大小写不同的同名项目，请先核对编号".into();entries.push(entry);continue;}
        if let Some(code)=reserved_matches.first(){entry.project=code.clone();}
        let folded=entry.project.to_ascii_uppercase();
        if let Some(other)=projects.get(&folded){if other!=&entry.name{entry.message=format!("项目编号与目录 {other} 冲突，请调整项目文件夹名称");entries.push(entry);continue;}}
        projects.insert(folded,entry.name.clone());
        let unconfirmed=!matched&&!reserved_matches.is_empty();
        let checked=(||{
            let source=safe_source(app,&root,&entry.path)?;
            let bytes=fs::read(&source).map_err(error)?;import::validate(&bytes,&Project{code:entry.project.clone(),name:entry.name.clone(),methods:vec![]}).map_err(error)?;
            entry.hash=hash(&bytes);entry.path=source::display_path(&source);
            if existing.iter().any(|(month,code,b)|same(&b.path,&entry.path)&&(month!=&entry.month||code!=&entry.project)){return Err(conflict("此文件已关联其他项目或月份"));}
            let mirror=months::directory(&app.home,&entry.month).join("workbooks").join(format!("{}.xlsx",entry.project));
            if mirror.exists(){
                let bytes_mirror=fs::read(&mirror).map_err(error)?;entry.mirror=Some(hash(&bytes_mirror));
                if workbook::sheet_names(&bytes).map_err(error)?!=workbook::sheet_names(&bytes_mirror).map_err(error)?{return Err(conflict("已有月份工作簿的工作表名称或顺序不同，不会覆盖"));}
            }
            if unconfirmed&&entry.mirror.as_deref()!=Some(entry.hash.as_str()){return Err(conflict("此编号已存在，目录名称或原关联无法确认，且该月副本与新文件不同；不会猜测或覆盖"));}
            if let Some((_,_,binding))=existing.iter().find(|(month,code,_)|month==&entry.month&&code==&entry.project){
                if !same(&binding.path,&entry.path){
                    if entry.mirror.is_none()||(entry.hash!=binding.hash&&entry.mirror.as_deref()!=Some(entry.hash.as_str())){return Err(conflict(format!("原关联为 {}，新文件与原关联及该月副本均不一致；不会替换",binding.path)));}
                    entry.previous_path=Some(binding.path.clone());entry.state="relink".into();entry.message=format!("原关联 {} → {}，待确认迁移",binding.path,entry.path);
                }else{entry.state=if entry.mirror.is_some(){"linked"}else{"import"}.into();entry.message=if entry.mirror.is_some(){"已关联，将跳过"}else{"恢复缺失的月份副本"}.into();}
            }else{entry.state=if entry.mirror.is_some(){"link"}else{"import"}.into();entry.message=if entry.mirror.is_some(){"关联已有项目，保留月份副本"}else{"导入月份副本并关联原文件"}.into();}
            entry.selectable=true;entry.selected=true;Ok::<_,Error>(())
        })();
        if let Err((_,body))=checked{entry.message=body.0["error"].as_str().unwrap_or("无法读取工作簿").into();entry.state="conflict".into();}
        entries.push(entry);
    }
    let mut project_names=BTreeMap::<String,BTreeSet<String>>::new();
    for entry in &entries{if !entry.project.is_empty(){project_names.entry(entry.project.to_ascii_uppercase()).or_default().insert(entry.name.clone());}}
    for entry in &mut entries{if project_names.get(&entry.project.to_ascii_uppercase()).is_some_and(|names|names.len()>1){entry.selectable=false;entry.selected=false;entry.state="conflict".into();entry.message="多个不同项目名称生成相同编号，请调整项目目录名称或逐个在项目中确认关联".into();}}
    let mut duplicates=BTreeMap::<(String,String),usize>::new();
    for entry in &entries{if entry.selectable{*duplicates.entry((entry.project.to_ascii_uppercase(),entry.month.clone())).or_default()+=1;}}
    for entry in &mut entries {if entry.selectable&&duplicates.get(&(entry.project.to_ascii_uppercase(),entry.month.clone())).is_some_and(|n|*n>1){entry.selected=false;entry.message=format!("同一项目、同一月份有多个候选，请仅勾选一本；{}",entry.message);}}
    entries.sort_by(|a,b|(&a.project,&a.month,&a.path).cmp(&(&b.project,&b.month,&b.path)));
    if entries.is_empty(){issues.push("未发现可检查的xlsx工作簿，请进入项目所在文件夹".into());}
    let pending:bool=app.db.query_row("SELECT EXISTS(SELECT 1 FROM previews WHERE state='writing')",[],|r|r.get(0)).map_err(error)?;
    if pending{issues.push("存在尚未完成的写入，请先在写入历史中完成恢复".into());}
    let can_bind=!pending&&entries.iter().any(|e|e.selectable);
    let latest_month=entries.iter().filter(|e|e.selectable).map(|e|&e.month).filter(|m|months::valid(m)).max().cloned();
    let evidence=entries.iter().map(|e|json!({"entry":e,"hash":e.hash,"mirror":e.mirror})).collect::<Vec<_>>();
    let fingerprint=hash(&serde_json::to_vec(&json!({"root":source::display_path(&root),"entries":evidence,"issues":issues,"months":months::list(&app.home)?,"names":names,"links":existing,"saved":saved(&app.db)?,"rules":months::saved_rules(&app.home)?})).map_err(error)?);
    Ok(Scan{root:source::display_path(&root),fingerprint,entries,issues,can_bind,latest_month})
}

pub fn bind(app:&App,input:&str,fingerprint:&str,selected:Option<&[String]>)->Result<Value,Error>{
    let plan=scan(app,input)?;
    if fingerprint.is_empty()||fingerprint!=plan.fingerprint{return Err(conflict("目录、原文件或平台项目已变化，请重新扫描并确认"));}
    if !plan.can_bind{return Err(conflict("没有可关联工作簿，或有尚未完成的写入；未导入任何项目"));}
    let selected=selected.map(<[String]>::to_vec).unwrap_or_else(||plan.entries.iter().filter(|e|e.selected).map(|e|e.id.clone()).collect());
    let chosen=selected.iter().collect::<BTreeSet<_>>();
    if chosen.is_empty()||chosen.len()!=selected.len(){return Err(conflict("请勾选需要关联的工作簿，不能重复选择"));}
    if selected.iter().any(|id|!plan.entries.iter().any(|e|&e.id==id&&e.selectable)){return Err(conflict("勾选项不存在或无法关联，请重新扫描后核对"));}
    let entries=plan.entries.iter().filter(|e|chosen.contains(&e.id)).collect::<Vec<_>>();
    let mut keys=BTreeSet::new();
    for entry in &entries{if !keys.insert((entry.project.to_ascii_uppercase(),entry.month.clone())){return Err(conflict("同一项目、同一月份只能关联一本工作簿，请取消其他勾选"));}}
    let latest=entries.iter().map(|e|e.month.as_str()).max().ok_or_else(||error("没有可导入月份"))?;
    let existing_months=months::list(&app.home)?;
    let no_history:bool=app.db.query_row("SELECT NOT EXISTS(SELECT 1 FROM previews) AND NOT EXISTS(SELECT 1 FROM source_links)",[],|r|r.get(0)).map_err(error)?;
    let archive_month=if existing_months.len()==1&&existing_months[0].as_str()>latest&&no_history&&project_codes(app)?.is_empty()&&months::names(&app.home)?.is_empty()&&months::saved_rules(&app.home)?.is_empty()&&paths(&months::directory(&app.home,&existing_months[0]))?.iter().all(|p|p.file_name().is_some_and(|n|n=="workbooks")&&p.is_dir()&&fs::read_dir(p).is_ok_and(|mut r|r.next().is_none())){Some(existing_months[0].clone())}else{None};
    let names_path=app.home.join("project-names.json");
    let old_names=match fs::read(&names_path){Ok(bytes)=>Some(bytes),Err(e) if e.kind()==std::io::ErrorKind::NotFound=>None,Err(e)=>return Err(error(e))};
    let transaction=app.db.unchecked_transaction().map_err(error)?;
    let stage=app.home.join(format!(".folder-import-{}",random_id()));fs::create_dir(&stage).map_err(error)?;
    let archive=app.home.join("backups").join(format!("folder-bootstrap-{}",random_id()));
    let mut created=Vec::<(PathBuf,String)>::new();let mut created_months=Vec::new();let mut archived=false;let mut names_changed=false;
    let result=(||{
        for (index,entry) in entries.iter().enumerate(){if entry.state=="import"{let source=safe_source(app,&fs::canonicalize(&plan.root).map_err(error)?,&entry.path)?;let bytes=fs::read(source).map_err(error)?;if hash(&bytes)!=entry.hash{return Err(conflict("原文件在导入期间发生变化，请重新扫描"));}fs::write(stage.join(format!("{index}.xlsx")),bytes).map_err(error)?;}}
        if let Some(month)=&archive_month{fs::rename(months::directory(&app.home,month),&archive).map_err(error)?;archived=true;}
        let mut names=months::names(&app.home)?;
        for (index,entry) in entries.iter().enumerate(){
            let directory=months::directory(&app.home,&entry.month);
            if !directory.exists(){fs::create_dir(&directory).map_err(error)?;created_months.push(directory.clone());}
            fs::create_dir_all(directory.join("workbooks")).map_err(error)?;
            if entry.state=="import"{let destination=directory.join("workbooks").join(format!("{}.xlsx",entry.project));fs::hard_link(stage.join(format!("{index}.xlsx")),&destination).map_err(error)?;created.push((destination,entry.hash.clone()));}
            if entry.state!="linked"{transaction.execute("INSERT INTO source_links(month,project,path,hash) VALUES(?1,?2,?3,?4) ON CONFLICT(month,project) DO UPDATE SET path=excluded.path,hash=excluded.hash",params![entry.month,entry.project,entry.path,entry.hash]).map_err(error)?;}
            names.insert(entry.project.clone(),entry.name.clone());
        }
        months::save_json(&names_path,&names)?;names_changed=true;
        for entry in &entries{let source=safe_source(app,&fs::canonicalize(&plan.root).map_err(error)?,&entry.path)?;if hash(&fs::read(source).map_err(error)?)!=entry.hash{return Err(conflict("原文件在导入期间发生变化，请重新扫描"));}}
        transaction.execute("INSERT INTO source_folder_settings(id,path) VALUES(1,?1) ON CONFLICT(id) DO UPDATE SET path=excluded.path",[&plan.root]).map_err(error)?;
        transaction.commit().map_err(error)
    })();
    if let Err(cause)=result {
        let rollback=(||{
            for (path,expected) in &created {if hash(&fs::read(path).map_err(error)?)!=*expected{return Err(conflict("导入副本已变化，无法自动回滚"));}fs::remove_file(path).map_err(error)?;}
            for directory in created_months.iter().rev(){let books=directory.join("workbooks");if books.exists(){fs::remove_dir(books).map_err(error)?;}fs::remove_dir(directory).map_err(error)?;}
            if names_changed{if let Some(bytes)=old_names{let temporary=stage.join("names-rollback.json");fs::write(&temporary,bytes).map_err(error)?;workbook::replace_file(&temporary,&names_path).map_err(error)?;}else{fs::remove_file(&names_path).map_err(error)?;}}
            if archived{fs::rename(&archive,months::directory(&app.home,archive_month.as_deref().unwrap())).map_err(error)?;}Ok::<_,Error>(())
        })();
        if rollback.is_err(){return Err(error(format!("导入失败，文件回滚受阻；源文件未修改，隔离副本保留在 {}，请先核对后重试",stage.display())));}
        let _=fs::remove_dir_all(&stage);return Err(cause);
    }
    let _=fs::remove_dir_all(&stage);
    Ok(json!({"month":latest,"source_folder":plan.root,"imported":entries.iter().filter(|e|e.state=="import").count(),"linked":entries.iter().filter(|e|e.state=="link"||e.state=="relink").count(),"relinked":entries.iter().filter(|e|e.state=="relink").count(),"skipped":entries.iter().filter(|e|e.state=="linked").count(),"unselected":plan.entries.len()-entries.len(),"archived_empty_month":archive_month,"empty_month_backup":if archived{Some(source::display_path(&archive))}else{None}}))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn folder_periods_codes_and_ignored_files(){
        for (year,month,expected) in [("2026","2026.07","2026-07"),("2026年","7月","2026-07"),("2027","01","2027-01"),("2026","2026-12","2026-12")]{assert_eq!(period(year,month).as_deref(),Some(expected));}
        for (year,month) in [("2026","2025.07"),("2026","13"),("year","01"),("2026","0001"),("2026","2026.7"),("2026","2026.07月")]{assert!(period(year,month).is_none());}
        assert_eq!(default_code("EF-理化"),"EF");assert_eq!(default_code("D006-理化"),"D006");assert!(months::code_valid(&default_code("中文项目")));
        assert!(ignored("~$EF.xlsx")&&ignored("EF-备份.xlsx")&&ignored("backup"));assert!(!ignored("EF-理化.xlsx"));
    }
    #[test]
    fn flexible_periods_projects_and_next_month_names(){
        for value in ["EF-理化-202607","EF-理化2026.07","EF-理化_2026-7","EF-理化2026年7月"]{assert_eq!(periods(value)[0].0,"2026-07");}
        assert!(periods("20260718").is_empty());assert!(periods("2026.13").is_empty());
        let root=FsPath::new("C:/总目录");
        for (file,name) in [("EF-理化/2026/7月/原表.xlsx","EF-理化"),("EF-理化/2026.07/原表.xlsx","EF-理化"),("分类/EF-理化/2026/7月/EF-理化-202607.xlsx","EF-理化"),("EF-理化/归档/2026/7月/EF-理化-202607.xlsx","EF-理化"),("EF-理化-202607.xlsx","EF-理化")]{let (project,month,_)=identity(root,&root.join(file)).unwrap();assert_eq!(project,name);assert_eq!(month,"2026-07");}
        assert!(identity(root,&root.join("分类/项目/2026/7月/原表.xlsx")).is_err());
        assert!(identity(root,&root.join("EF/2026/7月/EF-202608.xlsx")).is_err());
        assert!(identity(root,&root.join("没有年月.xlsx")).is_err());
        let project=root.join("EF-理化");
        for selected in [&project,&project.join("2026"),&project.join("2026/7月")]{let file=project.join("2026/7月/原表.xlsx");assert_eq!(project_directory(selected,&file).unwrap(),Some(project.clone()));}
        assert_eq!(project_directory(root,&root.join("EF-理化-202607.xlsx")).unwrap(),None);
        for (before,after) in [("EF-202607.xlsx","EF-202701.xlsx"),("EF-2026.07.xlsx","EF-2027.01.xlsx"),("EF-2026-7.xlsx","EF-2027-1.xlsx"),("EF-2026年7月.xlsx","EF-2027年1月.xlsx"),("原表.xlsx","原表.xlsx")]{assert_eq!(next_filename(before,"2027-01").unwrap(),after);}
    }
}
