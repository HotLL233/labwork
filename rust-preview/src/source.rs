//! Explicit links to server-local originals. All writes retain a byte-for-byte backup.
use super::*;
use rusqlite::OptionalExtension;
use std::{io::Write, path::Path};

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct Binding { pub path:String, pub hash:String }
pub struct NewSource { pub month:String, pub project:String, pub path:PathBuf, pub data:Vec<u8> }

fn conflict(message:impl ToString)->Error {(StatusCode::CONFLICT,Json(json!({"error":message.to_string()})))}
fn display_path(path:&Path)->String{let text=path.to_string_lossy();text.strip_prefix(r"\\?\").unwrap_or(&text).to_string()}
pub fn initialize(db:&Connection)->Result<(),rusqlite::Error>{
    db.execute_batch("CREATE TABLE IF NOT EXISTS source_links(month TEXT NOT NULL,project TEXT NOT NULL,path TEXT NOT NULL COLLATE NOCASE,hash TEXT NOT NULL,PRIMARY KEY(month,project),UNIQUE(path));")
}
pub fn get(db:&Connection,month:&str,code:&str)->Result<Option<Binding>,Error>{
    db.query_row("SELECT path,hash FROM source_links WHERE month=?1 AND project=?2",params![month,code],|r|Ok(Binding{path:r.get(0)?,hash:r.get(1)?})).optional().map_err(error)
}
fn local_path(path:&str)->Result<PathBuf,Error>{
    let p=PathBuf::from(path);
    if !p.is_absolute()||path.starts_with("\\\\")||path.starts_with("//")||p.extension().is_none_or(|e|!e.to_string_lossy().eq_ignore_ascii_case("xlsx")){return Err(error("请填写服务器本机现存xlsx文件的绝对路径，不支持网络共享或相对路径"));}
    if !p.is_file(){return Err(error("关联原表不存在或不是普通文件"));}
    if fs::metadata(&p).map_err(error)?.len()>20*1024*1024{return Err(error("关联原表超过20MB限制"));}
    let canonical=fs::canonicalize(p).map_err(error)?;
    if canonical.to_string_lossy().starts_with(r"\\?\UNC\"){return Err(error("不支持网络共享原表"));}
    Ok(canonical)
}
fn external(app:&App,path:&Path)->Result<(),Error>{
    if path.starts_with(fs::canonicalize(&app.home).map_err(error)?){return Err(error("不能将平台管理目录中的文件作为原表"));}Ok(())
}
fn available(app:&App,path:&str,month:&str,code:&str)->Result<(),Error>{
    let occupied:bool=app.db.query_row("SELECT EXISTS(SELECT 1 FROM source_links WHERE path=?1 AND NOT(month=?2 AND project=?3))",params![path,month,code],|r|r.get(0)).map_err(error)?;
    if occupied{return Err(conflict("此原表已关联其他项目或月份，请使用独立原表"));}Ok(())
}
pub fn read(app:&App,month:&str,code:&str)->Result<(Vec<u8>,Option<Binding>),Error>{
    let working=app.book(code,month)?;
    let Some(mut binding)=get(&app.db,month,code)? else{return Ok((fs::read(working).map_err(error)?,None));};
    let path=local_path(&binding.path)?;external(app,&path)?;
    let bytes=fs::read(path).map_err(error)?;
    import::validate(&bytes,&Project{code:code.into(),name:code.into(),methods:vec![]}).map_err(error)?;
    binding.hash=hash(&bytes);
    Ok((bytes,Some(binding)))
}
pub fn import_file(app:&App,month:&str,code:&str,path:&str,name:&str)->Result<Value,Error>{
    months::writable(&app.home,month)?;
    if !months::code_valid(code)||name.chars().count()>100{return Err(error("项目编号或名称无效"));}
    let source=local_path(path)?;external(app,&source)?;
    let path=display_path(&source);available(app,&path,"","")?;
    if get(&app.db,month,code)?.is_some(){return Err(conflict("此项目已有原文件关联，不会覆盖"));}
    let bytes=fs::read(&source).map_err(error)?;
    let project=Project{code:code.into(),name:name.into(),methods:vec![]};
    let mut result=import::bind(&months::directory(&app.home,month),&project,&bytes)?;
    let destination=app.book(code,month)?;
    let binding=Binding{path,hash:hash(&bytes)};
    let saved=(||{
        if hash(&fs::read(&source).map_err(error)?)!=binding.hash{return Err(conflict("原文件在导入期间发生变化，请重新导入"));}
        app.db.execute("INSERT INTO source_links(month,project,path,hash) VALUES(?1,?2,?3,?4)",params![month,code,binding.path,binding.hash]).map_err(error)?;
        months::name_project(&app.home,code,name)?;Ok(())
    })();
    if let Err(e)=saved{
        app.db.execute("DELETE FROM source_links WHERE month=?1 AND project=?2 AND path=?3 AND hash=?4",params![month,code,binding.path,binding.hash]).map_err(error)?;
        if hash(&fs::read(&destination).map_err(error)?)==binding.hash{fs::remove_file(&destination).map_err(error)?;}
        return Err(e);
    }
    result["code"]=json!(code);result["source_link"]=json!(binding);Ok(result)
}
pub fn bind(app:&App,month:&str,code:&str,path:Option<String>)->Result<Option<Binding>,Error>{
    months::writable(&app.home,month)?;
    let working=app.book(code,month)?;
    let writing:bool=app.db.query_row("SELECT EXISTS(SELECT 1 FROM previews WHERE state='writing' AND json_extract(request,'$.month')=?1 AND json_extract(request,'$.project')=?2)",params![month,code],|r|r.get(0)).map_err(error)?;
    if writing{return Err(conflict("该项目有待恢复的写入，请先重试完成原写入，再更换关联"));}
    let Some(path)=path else{app.db.execute("DELETE FROM source_links WHERE month=?1 AND project=?2",params![month,code]).map_err(error)?;return Ok(None);};
    let path=local_path(&path)?;external(app,&path)?;
    let path=display_path(&path);
    available(app,&path,month,code)?;
    let bytes=fs::read(&path).map_err(error)?;
    import::validate(&bytes,&Project{code:code.into(),name:code.into(),methods:vec![]}).map_err(error)?;
    if workbook::sheet_names(&bytes).map_err(error)?!=workbook::sheet_names(&fs::read(working).map_err(error)?).map_err(error)? {return Err(error("原表工作表名称或顺序与当前项目不一致"));}
    let binding=Binding{path,hash:hash(&bytes)};
    app.db.execute("INSERT INTO source_links(month,project,path,hash) VALUES(?1,?2,?3,?4) ON CONFLICT(month,project) DO UPDATE SET path=excluded.path,hash=excluded.hash",params![month,code,binding.path,binding.hash]).map_err(error)?;
    Ok(Some(binding))
}
pub fn check(app:&App,month:&str,code:&str,snapshot:Option<&Binding>,after:&str)->Result<(),Error>{
    let current=get(&app.db,month,code)?;
    match (snapshot,current.as_ref()) {
        (None,None)=>Ok(()),
        (Some(before),Some(current)) if before.path==current.path=>{
            let path=local_path(&before.path).map_err(|_|conflict("关联原表不存在或无法读取，请检查原表后重新预览"))?;external(app,&path)?;
            let actual=hash(&fs::read(&path).map_err(|_|conflict("原表被占用或无法读取"))?);
            if actual!=before.hash&&actual!=after{return Err(conflict("原表已被Excel/WPS或其他用户修改，未写入；请重新计算并预览"));}Ok(())
        },
        _=>Err(conflict("原表关联已变化，请重新预览后写入")),
    }
}
pub fn sync(app:&App,month:&str,code:&str,snapshot:Option<&Binding>,afterbytes:&[u8],id:&str)->Result<Value,Error>{
    let after=hash(afterbytes);check(app,month,code,snapshot,&after)?;
    let Some(binding)=snapshot else{return Ok(json!({"state":"unlinked"}));};
    let path=Path::new(&binding.path);let before=fs::read(path).map_err(error)?;
    if hash(&before)!=after {
        let backups=app.home.join("backups");fs::create_dir_all(&backups).map_err(error)?;
        let backup=backups.join(format!("source-{month}-{code}-{}.xlsx",hash(id.as_bytes())));
        match fs::OpenOptions::new().write(true).create_new(true).open(&backup){
            Ok(mut file)=>{file.write_all(&before).and_then(|_|file.sync_all()).map_err(error)?;},
            Err(e) if e.kind()==std::io::ErrorKind::AlreadyExists=>{if fs::read(&backup).map_err(error)?!=before{return Err(conflict("原表备份与当前源文件不一致，已停止写入"));}},
            Err(e)=>return Err(error(e)),
        }
        let temporary=path.with_extension(format!("{}.tmp",random_id()));
        let write=(||{let mut f=fs::OpenOptions::new().write(true).create_new(true).open(&temporary).map_err(error)?;f.write_all(afterbytes).and_then(|_|f.sync_all()).map_err(error)?;
            check(app,month,code,snapshot,&after)?;
            workbook::replace_file(&temporary,path).map_err(error)
        })();
        if write.is_err(){let _=fs::remove_file(&temporary);}write?;
    }
    app.db.execute("UPDATE source_links SET hash=?1 WHERE month=?2 AND project=?3 AND path=?4",params![after,month,code,binding.path]).map_err(error)?;
    Ok(json!({"state":"synced","path":binding.path}))
}

fn next_path(path:&Path,previous:&str,newmonth:&str)->Result<PathBuf,Error>{
    if !months::valid(previous)||!months::valid(newmonth){return Err(error("月份无效"));}
    let month_dir=path.parent().ok_or_else(||error("原表缺少月份目录"))?;
    let year_dir=month_dir.parent().ok_or_else(||error("原表缺少年份目录"))?;
    let project=year_dir.parent().ok_or_else(||error("原表缺少项目目录"))?;
    let (new_y,new_m)=(&newmonth[..4],&newmonth[5..]);
    let old_year=year_dir.file_name().and_then(|v|v.to_str()).ok_or_else(||error("年份目录无效"))?;
    let old_y=old_year.strip_suffix('年').unwrap_or(old_year);
    if old_y.len()!=4||!old_y.bytes().all(|b|b.is_ascii_digit()){return Err(error("原表目录须为 项目/年份/月/文件；年份须为YYYY或YYYY年"));}
    let year=if old_year.ends_with('年'){format!("{new_y}年")}else{new_y.to_string()};
    let current=month_dir.file_name().and_then(|v|v.to_str()).ok_or_else(||error("月份目录无效"))?;
    let component=current.strip_prefix(&format!("{old_y}.")).or_else(||current.strip_prefix(&format!("{old_y}-"))).unwrap_or(current).trim_end_matches('月');
    let old_number=component.parse::<u32>().map_err(|_|error("无法识别原表月份目录"))?;
    if !(1..=12).contains(&old_number){return Err(error("原表月份目录超出1至12"));}
    let old_m=format!("{old_number:02}");let old_number=old_number.to_string();let new_number=new_m.parse::<u32>().map_err(error)?.to_string();
    let actual_month=format!("{old_y}-{old_m}");
    let patterns=[(format!("{old_y}.{old_m}"),format!("{new_y}.{new_m}")),(actual_month.clone(),newmonth.into()),(old_m.clone(),new_m.into()),(old_number.clone(),new_number.clone()),(format!("{old_m}月"),format!("{new_m}月")),(format!("{old_number}月"),format!("{new_number}月"))];
    let month=patterns.iter().find(|(old,_)|old==current).map(|(_,new)|new).ok_or_else(||error("无法识别原表月份目录；支持YYYY.MM、YYYY-MM、MM或M月"))?;
    let mut name=path.file_name().and_then(|v|v.to_str()).ok_or_else(||error("原表文件名无效"))?.to_string();
    for (old,new) in [(format!("{old_y}{old_m}"),format!("{new_y}{new_m}")),(format!("{old_y}.{old_m}"),format!("{new_y}.{new_m}")),(actual_month,newmonth.to_string())]{name=name.replace(&old,&new);}
    Ok(project.join(year).join(month).join(name))
}
pub fn plan_month(app:&App,previous:&str,newmonth:&str,generated:&[(std::ffi::OsString,Vec<u8>)])->Result<Vec<NewSource>,Error>{
    let mut plans=Vec::new();
    for (filename,data) in generated {
        let code=Path::new(filename).file_stem().and_then(|s|s.to_str()).ok_or_else(||error("项目文件名无效"))?;
        let Some(binding)=get(&app.db,previous,code)? else{continue;};
        let source=local_path(&binding.path)?;external(app,&source)?;
        if hash(&fs::read(&source).map_err(error)?)!=binding.hash{return Err(conflict(format!("{code}原表已被外部修改，请先核对关联再创建新月")));}
        let path=next_path(&source,previous,newmonth)?;
        if path.exists(){return Err(conflict(format!("新月原表已存在，不会覆盖：{}",path.display())));}
        let mut existing=path.parent().unwrap();while !existing.exists(){existing=existing.parent().ok_or_else(||error("目标目录无效"))?;}
        external(app,&fs::canonicalize(existing).map_err(error)?)?;
        let key=display_path(&path);
        if plans.iter().any(|p:&NewSource|display_path(&p.path).eq_ignore_ascii_case(&key)){return Err(conflict("多个项目的新月原表路径相同"));}
        let occupied:bool=app.db.query_row("SELECT EXISTS(SELECT 1 FROM source_links WHERE path=?1)",[&key],|r|r.get(0)).map_err(error)?;
        if occupied{return Err(conflict("新月原表路径已关联其他项目或月份"));}
        plans.push(NewSource{month:newmonth.into(),project:code.into(),path,data:data.clone()});
    }Ok(plans)
}
pub fn create_month_sources(app:&App,plans:&[NewSource])->Result<(),Error>{
    let transaction=app.db.unchecked_transaction().map_err(error)?;let mut created=Vec::new();
    let result=(||{
        for plan in plans {
            fs::create_dir_all(plan.path.parent().ok_or_else(||error("新月原表目录无效"))?).map_err(error)?;
            let parent=fs::canonicalize(plan.path.parent().unwrap()).map_err(error)?;external(app,&parent)?;
            let mut file=fs::OpenOptions::new().write(true).create_new(true).open(&plan.path).map_err(error)?;created.push(&plan.path);
            file.write_all(&plan.data).and_then(|_|file.sync_all()).map_err(error)?;
            let canonical=display_path(&fs::canonicalize(&plan.path).map_err(error)?);
            transaction.execute("INSERT INTO source_links(month,project,path,hash) VALUES(?1,?2,?3,?4)",params![plan.month,plan.project,canonical,hash(&plan.data)]).map_err(error)?;
        }Ok(())
    })();
    if result.is_err(){drop(transaction);for path in created{let _=fs::remove_file(path);}return result;}
    if let Err(e)=transaction.commit(){for path in created{let _=fs::remove_file(path);}return Err(error(e));}Ok(())
}
pub fn rollback_month_sources(app:&App,plans:&[NewSource])->Result<(),Error>{
    for plan in plans {
        if plan.path.exists(){if hash(&fs::read(&plan.path).map_err(error)?)!=hash(&plan.data){return Err(conflict("新月原表已发生变化，保留文件待人工核对"));}fs::remove_file(&plan.path).map_err(error)?;}
        app.db.execute("DELETE FROM source_links WHERE month=?1 AND project=?2 AND hash=?3",params![plan.month,plan.project,hash(&plan.data)]).map_err(error)?;
    }Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn monthly_paths_preserve_conventions_and_cross_year(){
        for (old,new) in [("EF/2026/2026.12/EF-202612.xlsx","EF/2027/2027.01/EF-202701.xlsx"),("EF/2026年/12月/报告-2026-12.xlsx","EF/2027年/01月/报告-2027-01.xlsx"),("EF/2026/12/固定.xlsx","EF/2027/01/固定.xlsx")]{assert_eq!(next_path(Path::new(old),"2026-12","2027-01").unwrap(),PathBuf::from(new));}
        assert!(next_path(Path::new("EF/2026/unknown/EF.xlsx"),"2026-12","2027-01").is_err());
        assert_eq!(next_path(Path::new("EF/2026/2026.07/EF-202607.xlsx"),"2026-10","2026-11").unwrap(),PathBuf::from("EF/2026/2026.11/EF-202611.xlsx"));
    }
}
