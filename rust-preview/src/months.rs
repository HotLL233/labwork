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
pub fn set_rules(home:&FsPath,month:&str,code:&str,rules:Vec<MonthRule>)->Result<(),Error> {
    writable(home,month)?;
    let bytes=fs::read(book(home,month,code)?).map_err(error)?;
    let sheets=workbook::sheet_names(&bytes).map_err(error)?;
    if rules.len()!=sheets.len() || sheets.iter().any(|s|rules.iter().filter(|r|&r.sheet==s).count()!=1) {return Err(error("请为每张工作表确认清空范围；参数表可不选任何列"));}
    workbook::blank_month(&bytes,&rules).map_err(error)?;
    let mut values=saved_rules(home)?;values.insert(code.into(),rules);save_json(&home.join("month-rules-v2.json"),&values)
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn old_month_rules_require_reconfirmation_without_modifying_rollback_file(){
        let home=std::env::temp_dir().join(format!("labwork-month-rules-{}",random_id()));
        fs::create_dir_all(home.join("months/2026-10/workbooks")).unwrap();
        fs::write(home.join("months/2026-10/workbooks/D006.xlsx"),b"not read until rules are confirmed").unwrap();
        let old=br#"{"D006":[{"sheet":"sheet","start_row":4,"columns":["A","B"]}]}"#;
        fs::write(home.join("month-rules.json"),old).unwrap();
        assert!(saved_rules(&home).unwrap().is_empty());
        let failure=create(&home,"2026-11",None).unwrap_err();assert!(failure.1.0["error"].as_str().unwrap().contains("确认"));
        assert!(!home.join("months/2026-11").exists());
        assert_eq!(fs::read(home.join("month-rules.json")).unwrap(),old);
        fs::write(home.join("month-rules-v2.json"),old).unwrap();assert!(saved_rules(&home).unwrap().contains_key("D006"));
        fs::remove_dir_all(home).unwrap();
    }
}
pub fn create(home:&FsPath,month:&str,app:Option<&App>)->Result<(),Error> {
    if !valid(month){return Err(error("月份格式须为YYYY-MM"));}
    let months=list(home)?;
    if months.iter().any(|m|m==month){return Ok(());}
    let previous=months.last().ok_or_else(||error("缺少上个月份"))?;
    if month<=previous.as_str(){return Err(error("只能在最新月份之后新建，已有月份可切换查看"));}
    let rules=saved_rules(home)?;
    let mut generated=Vec::new();
    for entry in fs::read_dir(directory(home,previous).join("workbooks")).map_err(error)? {
        let path=entry.map_err(error)?.path();if path.extension().is_none_or(|e|e!="xlsx"){continue;}
        let code=path.file_stem().and_then(|s|s.to_str()).ok_or_else(||error("项目名无效"))?;
        let bytes=fs::read(&path).map_err(error)?;
        let project_rules=rules.get(code).ok_or_else(||error(format!("请先在项目 {code} 确认月模板清空范围")))?;
        let sheets=workbook::sheet_names(&bytes).map_err(error)?;
        if sheets.len()!=project_rules.len()||sheets.iter().any(|s|!project_rules.iter().any(|r|&r.sheet==s)){return Err(error(format!("项目 {code} 表结构已改变，请重新确认月模板规则")));}
        generated.push((path.file_name().unwrap().to_owned(),workbook::blank_month(&bytes,project_rules).map_err(error)?));
    }
    let plans=if let Some(app)=app{source::plan_month(app,previous,month,&generated)?}else{Vec::new()};
    let stage=home.join("months").join(format!(".new-{}",random_id()));
    fs::create_dir_all(stage.join("workbooks")).map_err(error)?;
    for (name,bytes) in generated {fs::write(stage.join("workbooks").join(name),bytes).map_err(error)?;}
    if let Some(app)=app {if let Err(e)=source::create_month_sources(app,&plans){return Err(e);}}
    if let Err(e)=fs::rename(&stage,directory(home,month)){
        if let Some(app)=app{source::rollback_month_sources(app,&plans)?;}
        return Err(error(e));
    }Ok(())
}
