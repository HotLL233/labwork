use super::*;
use std::path::Path as FsPath;
use workbook::MonthRule;

pub fn current()->String {
    let seconds=SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_secs() as i64;
    chrono::DateTime::from_timestamp(seconds+8*3600,0).unwrap().format("%Y-%m").to_string()
}
pub fn valid(month:&str)->bool {
    month.len()==7 && chrono::NaiveDate::parse_from_str(&format!("{month}-01"),"%Y-%m-%d").is_ok() && ("1900-01"..="2100-12").contains(&month)
}
pub fn code_valid(code:&str)->bool {!code.is_empty()&&code.len()<=50&&code.bytes().all(|c|c.is_ascii_alphanumeric()||c==b'-'||c==b'_')}
pub fn list(home:&FsPath)->Result<Vec<String>,Error> {
    let mut out=fs::read_dir(home.join("months")).map_err(error)?.filter_map(Result::ok).filter(|e|e.path().is_dir()).filter_map(|e|e.file_name().into_string().ok()).filter(|m|valid(m)).collect::<Vec<_>>();
    out.sort();Ok(out)
}
pub fn select(home:&FsPath,headers:&HeaderMap)->Result<String,Error> {
    let available=list(home)?;
    let month=match headers.get("x-labwork-month") {Some(value)=>value.to_str().map_err(error)?.to_owned(),None=>available.last().cloned().ok_or_else(||error("月份目录为空"))?};
    if !available.contains(&month) {return Err(error("月份不存在，请刷新目录"));}Ok(month)
}
pub fn writable(home:&FsPath,month:&str)->Result<(),Error> {
    if list(home)?.last().is_none_or(|m|m!=month) {return Err((StatusCode::CONFLICT,Json(json!({"error":"历史月份只读，请切换到最新月份"}))));}Ok(())
}
pub fn directory(home:&FsPath,month:&str)->PathBuf {home.join("months").join(month)}
pub fn book(home:&FsPath,month:&str,code:&str)->Result<PathBuf,Error> {
    if !valid(month)||!code_valid(code){return Err(error("项目或月份无效"));}
    let path=directory(home,month).join("workbooks").join(format!("{code}.xlsx"));
    if !path.is_file(){return Err(error("此月份尚未导入该项目"));}Ok(path)
}
pub fn initialize(home:&FsPath)->Result<(),Box<dyn std::error::Error>> {
    fs::create_dir_all(home.join("months"))?;
    if !list(home).map_err(|e|e.1.0.to_string())?.is_empty(){return Ok(());}
    let stage=home.join("months").join(format!(".init-{}",random_id()));
    fs::create_dir_all(stage.join("workbooks"))?;
    for entry in fs::read_dir(home.join("workbooks"))? {
        let path=entry?.path();
        if path.extension().is_some_and(|e|e=="xlsx") {
            let before=fs::read(&path)?;fs::write(stage.join("workbooks").join(path.file_name().unwrap()),&before)?;
            if hash(&before)!=hash(&fs::read(&path)?){return Err("旧工作簿在升级期间发生变化，请先停止旧版".into());}
        }
    }
    fs::rename(stage,directory(home,&current()))?;Ok(())
}
fn save_json(path:&FsPath,value:&impl Serialize)->Result<(),Error> {
    let temp=path.with_extension(format!("{}.tmp",random_id()));
    fs::write(&temp,serde_json::to_vec(value).map_err(error)?).map_err(error)?;
    if path.exists(){workbook::replace_file(&temp,path).map_err(error)?;}else{fs::rename(&temp,path).map_err(error)?;}Ok(())
}
pub fn names(home:&FsPath)->Result<BTreeMap<String,String>,Error> {
    let p=home.join("project-names.json");if !p.exists(){return Ok(BTreeMap::new());}serde_json::from_slice(&fs::read(p).map_err(error)?).map_err(error)
}
pub fn name_project(home:&FsPath,code:&str,name:&str)->Result<(),Error> {
    if name.chars().count()>100 {return Err(error("项目名称最多100字"));}
    let mut values=names(home)?;values.insert(code.into(),if name.trim().is_empty(){code.into()}else{name.trim().into()});save_json(&home.join("project-names.json"),&values)
}
pub fn saved_rules(home:&FsPath)->Result<BTreeMap<String,Vec<MonthRule>>,Error> {
    // Grid 7 discovers additional formula input columns; old confirmations must be reviewed again.
    let p=home.join("month-rules-v2.json");if !p.exists(){return Ok(BTreeMap::new());}serde_json::from_slice(&fs::read(p).map_err(error)?).map_err(error)
}
pub fn set_rules(home:&FsPath,month:&str,code:&str,rules:Vec<MonthRule>,app:Option<&App>)->Result<(),Error> {
    writable(home,month)?;
    let bytes=if let Some(app)=app{source::read(app,month,code)?.0}else{fs::read(book(home,month,code)?).map_err(error)?};
    let sheets=workbook::sheet_names(&bytes).map_err(error)?;
    if rules.len()!=sheets.len() || sheets.iter().any(|s|rules.iter().filter(|r|&r.sheet==s).count()!=1) {return Err(error("请为每张工作表确认清空范围；参数表可不选任何列"));}
    workbook::blank_month(&bytes,&rules).map_err(error)?;
    let mut values=saved_rules(home)?;values.insert(code.into(),rules);save_json(&home.join("month-rules-v2.json"),&values)
}
pub fn remove_project(app:&App,month:&str,code:&str)->Result<Value,Error> {
    writable(&app.home,month)?;
    let working=book(&app.home,month,code)?;
    let mut rules=saved_rules(&app.home)?;let names=names(&app.home)?;
    let rules_path=app.home.join("month-rules-v2.json");
    let old_rules=if rules_path.exists(){Some(fs::read(&rules_path).map_err(error)?)}else{None};
    let archive=app.home.join("backups").join(format!("removed-{month}-{code}-{}",random_id()));
    fs::create_dir_all(&archive).map_err(error)?;
    save_json(&archive.join("project.json"),&json!({"month":month,"code":code,"name":names.get(code),"source_link":source::get(&app.db,month,code)?,"rules":rules.get(code),"workbook_path":working.display().to_string()}))?;
    let archived=archive.join(format!("{code}.xlsx"));
    let initial=list(&app.home)?.first().cloned().unwrap_or_default();
    let transaction=app.db.unchecked_transaction().map_err(error)?;
    let result=(||{
        transaction.execute("DELETE FROM source_links WHERE month=?1 AND project=?2",params![month,code]).map_err(error)?;
        transaction.execute("UPDATE previews SET state='removed' WHERE state!='done' AND COALESCE(NULLIF(json_extract(request,'$.month'),''),?3)=?1 AND json_extract(request,'$.project')=?2",params![month,code,initial]).map_err(error)?;
        fs::rename(&working,&archived).map_err(error)?;
        rules.remove(code);save_json(&rules_path,&rules)?;
        transaction.commit().map_err(error)
    })();
    if let Err(e)=result {
        let rollback=(||{
            if archived.exists(){fs::rename(&archived,&working).map_err(error)?;}
            if let Some(bytes)=old_rules {if fs::read(&rules_path).ok().as_deref()!=Some(bytes.as_slice()){let temp=rules_path.with_extension(format!("{}.tmp",random_id()));fs::write(&temp,bytes).map_err(error)?;if rules_path.exists(){workbook::replace_file(&temp,&rules_path).map_err(error)?;}else{fs::rename(temp,&rules_path).map_err(error)?;}}}
            else if rules_path.exists(){fs::remove_file(&rules_path).map_err(error)?;}Ok::<_,Error>(())
        })();
        if rollback.is_err(){return Err(error(format!("移除失败，回滚文件时受阻；工作簿与关联信息备份保留在 {}，请恢复后重试",archive.display())));}return Err(e);
    }
    Ok(json!({"ok":true,"code":code,"month":month,"archive_path":archive.display().to_string(),"message":"已移除当前月份项目；原文件、历史月份及写入历史保留"}))
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn old_month_rules_are_ignored_without_modifying_rollback_file(){
        let home=std::env::temp_dir().join(format!("labwork-month-rules-{}",random_id()));
        fs::create_dir_all(home.join("months/2026-10/workbooks")).unwrap();
        fs::write(home.join("months/2026-10/workbooks/D006.xlsx"),b"not read until rules are confirmed").unwrap();
        let old=br#"{"D006":[{"sheet":"sheet","start_row":4,"columns":["A","B"]}]}"#;
        fs::write(home.join("month-rules.json"),old).unwrap();
        assert!(saved_rules(&home).unwrap().is_empty());
        let failure=plan(&home,"2026-11",None,&[]).err().unwrap();assert!(!failure.1.0["error"].as_str().unwrap().contains("确认"));
        assert!(!home.join("months/2026-11").exists());
        assert_eq!(fs::read(home.join("month-rules.json")).unwrap(),old);
        fs::write(home.join("month-rules-v2.json"),old).unwrap();assert!(saved_rules(&home).unwrap().contains_key("D006"));
        fs::remove_dir_all(home).unwrap();
    }
}
pub struct PlannedProject {pub code:String,pub name:String,pub sheets:Vec<String>,pub sheet_states:Vec<Value>,pub source_path:Option<String>,pub original:Vec<u8>,pub bytes:Vec<u8>}
pub struct Plan {pub previous:String,pub fingerprint:String,pub projects:Vec<PlannedProject>,pub sources:Vec<source::NewSource>,pub issues:Vec<String>}
pub fn plan(home:&FsPath,month:&str,app:Option<&App>,adjustments:&[MonthAdjustment])->Result<Plan,Error> {
    workbook::validate_month_adjustments(adjustments).map_err(error)?;
    if !valid(month){return Err(error("月份格式须为YYYY-MM"));}
    let months=list(home)?;
    if months.iter().any(|m|m==month){return Err(error("月份已存在，请直接切换查看"));}
    let previous=months.last().ok_or_else(||error("缺少上个月份"))?;
    if month<=previous.as_str(){return Err(error("只能在最新月份之后新建，已有月份可切换查看"));}
    let rules=saved_rules(home)?;let names=names(home)?;
    let mut projects=Vec::new();let mut inputs=BTreeMap::new();let mut bindings=BTreeMap::new();
    for entry in fs::read_dir(directory(home,previous).join("workbooks")).map_err(error)? {
        let path=entry.map_err(error)?.path();if path.extension().is_none_or(|e|e!="xlsx"){continue;}
        let code=path.file_stem().and_then(|s|s.to_str()).ok_or_else(||error("项目名无效"))?;
        let (bytes,binding)=if let Some(app)=app{source::read(app,previous,code)?}else{(fs::read(&path).map_err(error)?,None)};
        let sheets=workbook::sheet_names(&bytes).map_err(error)?;
        let saved=rules.get(code).filter(|r|r.len()==sheets.len()&&sheets.iter().all(|s|r.iter().filter(|r|&r.sheet==s).count()==1));
        let project_rules=if let Some(saved)=saved{saved.clone()}else{workbook::discover(&bytes).map_err(error)?};
        let selected=adjustments.iter().filter(|a|a.project==code).cloned().collect::<Vec<_>>();
        if selected.iter().any(|a|!sheets.contains(&a.sheet)){return Err(error(format!("项目 {code} 的调整工作表不存在")));}
        let states=project_rules.iter().map(|r|{let custom=selected.iter().any(|a|a.sheet==r.sheet);let state=if custom{"custom"}else if r.clear_columns.is_empty(){"keep"}else if saved.is_some(){"saved"}else{"auto"};json!({"sheet":r.sheet,"state":state,"message":match state{"custom"=>"按所选区域调整","keep"=>"保留原表内容；可选择数据区清空","saved"=>"沿用已保存清空规则",_=>"自动清空已识别数据区"}})}).collect::<Vec<_>>();
        inputs.insert(code.to_owned(),json!({"source":hash(&bytes),"mirror":hash(&fs::read(&path).map_err(error)?),"binding":binding,"rules":project_rules}));
        if let Some(binding)=binding{bindings.insert(code.to_owned(),binding);}
        let blank=workbook::blank_month_adjusted(&bytes,&project_rules,&selected).map_err(error)?;
        projects.push(PlannedProject{code:code.into(),name:names.get(code).cloned().unwrap_or_else(||code.into()),sheets,sheet_states:states,source_path:None,original:bytes,bytes:blank});
    }
    if projects.is_empty(){return Err(error("当前月份没有项目，请先导入工作簿"));}
    projects.sort_by(|a,b|a.code.cmp(&b.code));
    if adjustments.iter().any(|a|!projects.iter().any(|p|p.code==a.project)){return Err(error("模板调整的项目不存在"));}
    let generated=projects.iter().map(|p|(std::ffi::OsString::from(format!("{}.xlsx",p.code)),p.bytes.clone())).collect::<Vec<_>>();
    let(sources,issues)=if let Some(app)=app{source::plan_month(app,previous,month,&generated,&bindings)?}else{(Vec::new(),Vec::new())};
    for project in &mut projects {project.source_path=sources.iter().find(|p|p.project==project.code).map(|p|source::display_path(&p.path));}
    // ZIP timestamps vary per generation; validate the exact inputs and destinations instead.
    let targets=projects.iter().map(|p|json!({"code":p.code,"name":p.name,"source_path":p.source_path})).collect::<Vec<_>>();
    let fingerprint=hash(&serde_json::to_vec(&json!({"previous":previous,"month":month,"inputs":inputs,"projects":targets,"adjustments":adjustments,"issues":issues})).map_err(error)?);
    Ok(Plan{previous:previous.clone(),fingerprint,projects,sources,issues})
}
pub fn create(home:&FsPath,month:&str,app:&App,fingerprint:Option<&str>,adjustments:&[MonthAdjustment])->Result<(),Error> {
    if !valid(month){return Err(error("月份格式须为YYYY-MM"));}
    if list(home)?.iter().any(|m|m==month){return Ok(());}
    let plan=plan(home,month,Some(app),adjustments)?;
    if !plan.issues.is_empty(){return Err((StatusCode::CONFLICT,Json(json!({"error":plan.issues.join("；")}))));}
    if fingerprint!=Some(plan.fingerprint.as_str()){return Err((StatusCode::CONFLICT,Json(json!({"error":"请先预览新月份模板；原文件、项目或清空规则变化后须重新预览确认"}))));}
    let stage=home.join("months").join(format!(".new-{}",random_id()));
    let result=(||{
        fs::create_dir_all(stage.join("workbooks")).map_err(error)?;
        for project in &plan.projects {fs::write(stage.join("workbooks").join(format!("{}.xlsx",project.code)),&project.bytes).map_err(error)?;}
        source::create_month_sources(app,&plan.sources)?;
        if let Err(e)=fs::rename(&stage,directory(home,month)){source::rollback_month_sources(app,&plan.sources)?;return Err(error(e));}Ok(())
    })();
    if result.is_err(){let _=fs::remove_dir_all(&stage);}result
}
