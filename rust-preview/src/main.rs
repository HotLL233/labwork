#![cfg_attr(windows, windows_subsystem = "windows")]
mod config;
mod formula;
mod months;
mod source;
mod auth;
mod import;
mod calc;
mod workbook;
#[cfg(windows)]
mod tray;

use axum::{Json, Router, extract::{DefaultBodyLimit, Path, Query, State}, http::{HeaderMap, StatusCode, header}, middleware::{self, Next}, response::{Html, IntoResponse, Response}, routing::{get, post}};
use rusqlite::{Connection, params};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{collections::BTreeMap, fs, path::PathBuf, sync::{Arc, Mutex}, time::{SystemTime, UNIX_EPOCH}};

type Error = (StatusCode, Json<Value>);
fn error(message: impl ToString) -> Error { (StatusCode::BAD_REQUEST, Json(json!({"error":message.to_string()}))) }
fn random_id() -> String { let mut b=[0;16]; getrandom::fill(&mut b).expect("Windows random source"); b.iter().map(|b|format!("{b:02x}")).collect() }
fn hash(bytes: &[u8]) -> String { format!("{:x}", Sha256::digest(bytes)) }

#[derive(Clone, Serialize, Deserialize)]
struct Method { key: String, name: String, kind: String, params: BTreeMap<String,String>, fields: Vec<calc::Field>, columns: BTreeMap<String,String>, sheet: String }
#[derive(Clone, Serialize, Deserialize)]
struct Project { code: String, name: String, methods: Vec<Method> }
struct App { home: PathBuf, db: Connection, projects: Vec<Project>, token: String, port: u16, lan_ip: String, access: auth::Access }
type Shared = Arc<Mutex<App>>;

async fn local_host(State(state):State<Shared>,request:axum::extract::Request,next:Next)->Response {
    let allowed=state.lock().unwrap().allowed_host(request.headers().get(header::HOST).and_then(|h|h.to_str().ok()).unwrap_or(""));
    if !allowed {
        return (StatusCode::FORBIDDEN,"访问地址与服务器配置不一致").into_response();
    }
    let mut response=next.run(request).await;
    response.headers_mut().insert("x-content-type-options","nosniff".parse().unwrap());
    response.headers_mut().insert("x-frame-options","DENY".parse().unwrap());
    response.headers_mut().insert(header::CACHE_CONTROL,"no-store".parse().unwrap());
    response.headers_mut().insert("content-security-policy","default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'; object-src 'none'; base-uri 'none'".parse().unwrap());
    response
}

#[derive(Serialize, Deserialize, Clone)]
struct Request { #[serde(default)] month: String, project: String, method: String, #[serde(default)] date: String, #[serde(default)] batch: String, #[serde(default)] notes: String, #[serde(default)] measurements: Vec<BTreeMap<String,String>>, #[serde(default)] entries: Vec<Entry> }
#[derive(Serialize, Deserialize, Clone)]
struct Entry { date: String, batch: String, #[serde(default)] notes: String, measurement: BTreeMap<String,String> }
impl Request {
    fn normalize(&mut self) -> Result<(),String> {
        if !self.entries.is_empty() && !self.measurements.is_empty() {return Err("不能同时提交旧版测定和批量行".into());}
        if self.entries.is_empty() {self.entries=self.measurements.iter().map(|m|Entry{date:self.date.clone(),batch:self.batch.clone(),notes:self.notes.clone(),measurement:m.clone()}).collect();}
        if self.entries.is_empty()||self.entries.len()>200 {return Err("每次支持1至200行测定".into());}
        for (i,row) in self.entries.iter_mut().enumerate() {
            row.batch=row.batch.trim().to_owned();
            if row.batch.is_empty()||row.batch.chars().count()>100||row.notes.chars().count()>2000 {return Err(format!("第{}行：批号须为1至100字，备注最多2000字",i+1));}
            let date=chrono::NaiveDate::parse_from_str(&row.date,"%Y-%m-%d").map_err(|_|format!("第{}行：检测日期无效",i+1))?;
            if !(1900..=2100).contains(&chrono::Datelike::year(&date)) {return Err("检测日期须为1900至2100年".into());}
            row.date=date.to_string();
        }
        // Keep each day's batches together so blank labels always have one unambiguous owner.
        let mut groups=Vec::<(String,String)>::new();
        for row in &self.entries {let key=(row.date.clone(),row.batch.clone());if !groups.contains(&key){groups.push(key);}}
        self.entries.sort_by_key(|r|(r.date.clone(),groups.iter().position(|g|g==&(r.date.clone(),r.batch.clone())).unwrap()));
        self.measurements.clear();
        Ok(())
    }
}

impl App {
    fn allowed_host(&self,host:&str)->bool {
        config::allowed_host(host,self.port) || (!self.lan_ip.is_empty() && host==format!("{}:{}",self.lan_ip,self.port))
    }
    fn allowed_origin(&self,origin:&str)->bool {origin.strip_prefix("http://").is_some_and(|host|self.allowed_host(host))}

    fn book(&self, project:&str,month:&str)->Result<PathBuf,Error> {months::book(&self.home,month,project)}
    fn mapped_methods(&self,code:&str,bytes:&[u8])->Vec<Method> {
        let mut legacy:Vec<Method>=self.projects.iter().find(|p|p.code.eq_ignore_ascii_case(code)).map(|p|p.methods.iter().filter(|m|import::validate_method(bytes,m).is_ok()).cloned().collect()).unwrap_or_default();
        let discovered=workbook::generic_methods(bytes).unwrap_or_default();
        let mut result=discovered.iter().filter(|m|m.kind=="workbook_formula").cloned().collect::<Vec<_>>();
        result.append(&mut legacy);
        result.extend(discovered.into_iter().filter(|m|m.kind=="manual"));
        result
    }


}

async fn index(State(state): State<Shared>) -> Html<String> {
    let app=state.lock().unwrap();
    Html(include_str!("../web/index.html").replace("__TOKEN__",&app.token).replace("__VERSION__",env!("CARGO_PKG_VERSION")).replace("__PORT__",&app.port.to_string()))
}
async fn css() -> impl IntoResponse { ([(header::CONTENT_TYPE,"text/css; charset=utf-8")],include_str!("../web/app.css")) }
async fn js() -> impl IntoResponse { ([(header::CONTENT_TYPE,"application/javascript; charset=utf-8")],include_str!("../web/app.js")) }
async fn grid_js() -> impl IntoResponse { ([(header::CONTENT_TYPE,"application/javascript; charset=utf-8")],include_str!("../web/grid.js")) }
async fn catalog(State(state): State<Shared>,headers:HeaderMap) -> Result<Json<Value>,Error> {
    let app=state.lock().unwrap();
    let month=months::select(&app.home,&headers)?;
    let all_months=months::list(&app.home)?;
    let names=months::names(&app.home)?;
    let rules=months::saved_rules(&app.home)?;
    let mut projects=Vec::new();let mut monthly_rules=BTreeMap::new();
    for entry in fs::read_dir(months::directory(&app.home,&month).join("workbooks")).map_err(error)? {
        let path=entry.map_err(error)?.path();if path.extension().is_none_or(|e|e!="xlsx"){continue;}
        let code=path.file_stem().and_then(|s|s.to_str()).ok_or_else(||error("项目名无效"))?;
        let (bytes,source_error)=match source::read(&app,&month,code){
            Ok((bytes,_))=>(bytes,None),
            Err((_,body))=>(fs::read(&path).map_err(error)?,Some(body.0["error"].as_str().unwrap_or("关联原文件无法读取，请重新关联").to_owned())),
        };
        let methods=app.mapped_methods(code,&bytes);
        let sheets=workbook::sheet_names(&bytes).map_err(error)?;
        let confirmed=rules.get(code).is_some_and(|r|r.len()==sheets.len()&&sheets.iter().all(|s|r.iter().any(|r|&r.sheet==s)));
        monthly_rules.insert(code.to_owned(),if confirmed{rules[code].clone()}else{workbook::discover(&bytes).map_err(error)?});
        projects.push(json!({"code":code,"name":names.get(code).cloned().unwrap_or_else(||code.into()),"methods":methods,"workbook_path":path.display().to_string(),"source_link":source::get(&app.db,&month,code)?,"source_error":source_error,"available":true,"rules_confirmed":confirmed,"sheets":sheets.iter().enumerate().map(|(i,name)|json!({"name":name,"key":format!("s{i}"),"method_key":methods.iter().find(|m|m.sheet.trim()==name.trim()).map(|m|&m.key),"reason":if methods.iter().any(|m|m.sheet.trim()==name.trim()){None}else{workbook::generic_error(&bytes,name)}})).collect::<Vec<_>>()}));
    }
    projects.sort_by(|a,b|a["code"].as_str().cmp(&b["code"].as_str()));
    Ok(Json(json!({"version":env!("CARGO_PKG_VERSION"),"port":app.port,"lan_url":if app.lan_ip.is_empty(){None}else{Some(format!("http://{}:{}",app.lan_ip,app.port))},"data_directory":app.home.display().to_string(),"projects":projects,"active_month":month,"current_month":months::current(),"months":all_months,"writable":months::writable(&app.home,&month).is_ok(),"month_ready":true,"monthly_rules":monthly_rules})))
}
#[derive(Deserialize,Default)]
struct ImportQuery {#[serde(default)] name:String}
async fn bind_workbook(State(state):State<Shared>,Path(code):Path<String>,Query(query):Query<ImportQuery>,headers:HeaderMap,body:axum::body::Bytes)->Result<Json<Value>,Error> {
    let app=state.lock().unwrap();authorize(&app,&headers)?;
    let month=months::select(&app.home,&headers)?;months::writable(&app.home,&month)?;
    if !months::code_valid(&code)||query.name.chars().count()>100{return Err(error("项目编号须为1至50个字母数字或下划线短横线，名称最多100字"));}
    let project=Project{code:code.clone(),name:query.name.clone(),methods:vec![]};
    let result=import::bind(&months::directory(&app.home,&month),&project,&body)?;
    months::name_project(&app.home,&code,&query.name)?;Ok(Json(result))
}
#[derive(Deserialize)]
struct MonthRequest {month:String,#[serde(default)] fingerprint:Option<String>}
#[derive(Deserialize)]
struct MonthPreviewRequest {month:String,#[serde(default)] project:Option<String>,#[serde(default)] sheet:Option<String>,#[serde(default="first_row")] start:u32}
#[derive(Deserialize)]
struct SourceRequest {path:Option<String>}
#[derive(Deserialize)]
struct SourceImportRequest {path:String, #[serde(default)] name:String}
async fn import_source(State(state):State<Shared>,Path(code):Path<String>,headers:HeaderMap,Json(request):Json<SourceImportRequest>)->Result<Json<Value>,Error>{
    let app=state.lock().unwrap();authorize(&app,&headers)?;
    let month=months::select(&app.home,&headers)?;
    Ok(Json(source::import_file(&app,&month,&code,&request.path,&request.name)?))
}
async fn bind_source(State(state):State<Shared>,Path(code):Path<String>,headers:HeaderMap,Json(request):Json<SourceRequest>)->Result<Json<Value>,Error> {
    let app=state.lock().unwrap();authorize(&app,&headers)?;
    let month=months::select(&app.home,&headers)?;months::writable(&app.home,&month)?;
    pending_write(&app,&month,Some(&code))?;
    let binding=source::bind(&app,&month,&code,request.path)?;
    Ok(Json(json!({"source_link":binding})))
}
async fn download_book(State(state):State<Shared>,Path(code):Path<String>,Query(query):Query<MonthRequest>)->Result<Response,Error> {
    let app=state.lock().unwrap();
    let (bytes,_)=source::read(&app,&query.month,&code)?;
    Ok(([(header::CONTENT_TYPE,"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet".to_owned()),(header::CONTENT_DISPOSITION,format!("attachment; filename=\"{code}-{}.xlsx\"",query.month))],bytes).into_response())
}
async fn choose_month(State(state):State<Shared>,headers:HeaderMap,Json(request):Json<MonthRequest>)->Result<Json<Value>,Error> {
    let app=state.lock().unwrap();authorize(&app,&headers)?;let previous=months::list(&app.home)?.last().cloned().ok_or_else(||error("缺少月份"))?;pending_write(&app,&previous,None)?;months::create(&app.home,&request.month,&app,request.fingerprint.as_deref())?;Ok(Json(json!({"month":request.month})))
}
async fn preview_month(State(state):State<Shared>,headers:HeaderMap,Json(request):Json<MonthPreviewRequest>)->Result<Json<Value>,Error> {
    let app=state.lock().unwrap();authorize(&app,&headers)?;
    let previous=months::list(&app.home)?.last().cloned().ok_or_else(||error("缺少月份"))?;pending_write(&app,&previous,None)?;
    let plan=months::plan(&app.home,&request.month,Some(&app))?;
    let selected=if let Some(code)=request.project{plan.projects.iter().find(|p|p.code==code).ok_or_else(||error("预览项目不存在"))?}else{&plan.projects[0]};
    let sheet=request.sheet.unwrap_or_else(||selected.sheets[0].clone());
    if !selected.sheets.contains(&sheet){return Err(error("预览工作表不存在"));}
    Ok(Json(json!({"month":request.month,"previous":plan.previous,"fingerprint":plan.fingerprint,"projects":plan.projects.iter().map(|p|json!({"code":p.code,"name":p.name,"sheets":p.sheets,"source_path":p.source_path})).collect::<Vec<_>>(),"project":selected.code,"sheet":sheet,"view":workbook::raw_view(&selected.bytes,&sheet,request.start).map_err(error)?})))
}
async fn remove_workbook(State(state):State<Shared>,Path(code):Path<String>,headers:HeaderMap,Json(_):Json<Value>)->Result<Json<Value>,Error> {
    let app=state.lock().unwrap();authorize(&app,&headers)?;
    let month=months::select(&app.home,&headers)?;months::writable(&app.home,&month)?;pending_write(&app,&month,Some(&code))?;
    Ok(Json(months::remove_project(&app,&month,&code)?))
}
#[derive(Deserialize)]
struct RuleRequest {rules:Vec<workbook::MonthRule>}
async fn month_rules(State(state):State<Shared>,Path(code):Path<String>,headers:HeaderMap,Json(request):Json<RuleRequest>)->Result<Json<Value>,Error> {
    let app=state.lock().unwrap();authorize(&app,&headers)?;let month=months::select(&app.home,&headers)?;
    pending_write(&app,&month,Some(&code))?;
    months::set_rules(&app.home,&month,&code,request.rules,Some(&app))?;Ok(Json(json!({"ok":true})))
}
#[derive(Deserialize)]
struct PageQuery {#[serde(default="first_row")] start:u32}
fn first_row()->u32{1}
async fn raw_sheet(State(state):State<Shared>,Path((code,key)):Path<(String,String)>,Query(query):Query<PageQuery>,headers:HeaderMap)->Result<Json<Value>,Error> {
    let app=state.lock().unwrap();let month=months::select(&app.home,&headers)?;
    let (bytes,_)=source::read(&app,&month,&code)?;
    let names=workbook::sheet_names(&bytes).map_err(error)?;
    let index=key.strip_prefix('s').and_then(|s|s.parse::<usize>().ok()).ok_or_else(||error("工作表编号无效"))?;
    let name=names.get(index).ok_or_else(||error("工作表不存在"))?;
    Ok(Json(json!({"view":workbook::raw_view(&bytes,name,query.start).map_err(error)?,"fingerprint":hash(&bytes)})))
}
async fn sheet(State(state): State<Shared>,Path((project,method)):Path<(String,String)>,headers:HeaderMap) -> Result<Json<Value>,Error> {
    let app=state.lock().unwrap();let month=months::select(&app.home,&headers)?;
    let (bytes,_)=source::read(&app,&month,&project)?;
    let methods=app.mapped_methods(&project,&bytes);
    let method=methods.iter().find(|m|m.key==method).ok_or_else(||error("此表尚无校核的计算方法，请使用原表浏览"))?;
    Ok(Json(json!({"view":workbook::view(&bytes,method,&[]).map_err(error)?,"next_row":workbook::next_row(&bytes,method).map_err(error)?,"fingerprint":hash(&bytes)})))
}
fn authorize(app: &App,headers:&HeaderMap) -> Result<(),Error> {
    if headers.get("x-labwork-token").and_then(|v|v.to_str().ok()) != Some(&app.token) {return Err((StatusCode::FORBIDDEN,Json(json!({"error":"页面已过期，请刷新后重试"}))));}
    if let Some(origin)=headers.get(header::ORIGIN).and_then(|v|v.to_str().ok()) {
        if !app.allowed_origin(origin) {return Err((StatusCode::FORBIDDEN,Json(json!({"error":"拒绝跨站提交"}))));}
    }
    Ok(())
}
fn pending_write(app:&App,month:&str,project:Option<&str>)->Result<(),Error>{
    let initial=months::list(&app.home)?.first().cloned().unwrap_or_default();
    let pending:bool=app.db.query_row("SELECT EXISTS(SELECT 1 FROM previews WHERE state='writing' AND COALESCE(NULLIF(json_extract(request,'$.month'),''),?3)=?1 AND (?2 IS NULL OR json_extract(request,'$.project')=?2))",params![month,project,initial],|r|r.get(0)).map_err(error)?;
    if pending{return Err((StatusCode::CONFLICT,Json(json!({"error":"存在尚未完成的写入，请在写入历史中重试完成后继续"}))));}Ok(())
}
async fn preview(State(state):State<Shared>,headers:HeaderMap,Json(mut request):Json<Request>) -> Result<Json<Value>,Error> {
    let app=state.lock().unwrap(); authorize(&app,&headers)?;
    request.month=months::select(&app.home,&headers)?;months::writable(&app.home,&request.month)?;pending_write(&app,&request.month,Some(&request.project))?;
    let (bytes,binding)=source::read(&app,&request.month,&request.project)?;
    let workspace_before_hash=hash(&fs::read(app.book(&request.project,&request.month)?).map_err(error)?);
    let method=app.mapped_methods(&request.project,&bytes).into_iter().find(|m|m.key==request.method).ok_or_else(||error("此表尚无校核的计算方法"))?;
    request.normalize().map_err(error)?;
    if request.entries.iter().any(|r|!r.date.starts_with(&request.month)){return Err(error("检测日期必须属于当前选择月份"));}
    let outputs=request.entries.iter().enumerate().map(|(i,row)|(if matches!(method.kind.as_str(),"workbook_formula"|"manual"){workbook::generic_calculate(&method,&row.measurement)}else{calc::calculate(&method.kind,&row.measurement,&method.params)}).map_err(|e|format!("第{}行（{}）：{e}",i+1,row.batch))).collect::<Result<Vec<_>,_>>().map_err(error)?;
    let (candidate,rows)=workbook::append(&bytes,&method,&request,&outputs).map_err(error)?;
    let view=workbook::view(&candidate,&method,&rows).map_err(error)?;
    let id=random_id();
    let created=SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs() as i64;
    let mut snapshot=json!(&method);snapshot["source_link"]=json!(binding);snapshot["workspace_before_hash"]=json!(workspace_before_hash);snapshot["preserves_layout"]=json!(true);
    app.db.execute("INSERT INTO previews(id,created,request,outputs,before_hash,after_hash,rows,state,method_snapshot) VALUES(?1,?2,?3,?4,?5,?6,?7,'preview',?8)",params![id,created,serde_json::to_string(&request).unwrap(),serde_json::to_string(&outputs).unwrap(),hash(&bytes),hash(&candidate),serde_json::to_string(&rows).unwrap(),serde_json::to_string(&snapshot).unwrap()]).map_err(error)?;
    fs::write(app.home.join("previews").join(format!("{id}.xlsx")),candidate).map_err(error)?;
    Ok(Json(json!({"id":id,"entries":request.entries,"outputs":outputs,"rows":rows,"view":view,"state":"preview","download":format!("/api/download/{id}")})))
}
async fn commit(State(state):State<Shared>,headers:HeaderMap,Path(id):Path<String>) -> Result<Json<Value>,Error> {
    let app=state.lock().unwrap(); authorize(&app,&headers)?;
    commit_preview(&app,&headers,&id)
}
fn commit_preview(app:&App,headers:&HeaderMap,id:&str)->Result<Json<Value>,Error>{
    let (raw,before,after,status,saved_rows,snapshot):(String,String,String,String,String,String)=app.db.query_row("SELECT request,before_hash,after_hash,state,rows,method_snapshot FROM previews WHERE id=?1",[&id],|r|Ok((r.get(0)?,r.get(1)?,r.get(2)?,r.get(3)?,r.get(4)?,r.get(5)?))).map_err(|_|error("预览不存在"))?;
    if status=="removed"{return Err((StatusCode::CONFLICT,Json(json!({"error":"此预览的项目已移除，不能再次写入；请重新导入并计算预览"}))));}
    let mut request:Request=serde_json::from_str(&raw).map_err(error)?;
    if request.month.is_empty(){request.month=months::list(&app.home)?.first().cloned().ok_or_else(||error("缺少原始月份"))?;}
    months::writable(&app.home,&request.month)?;
    if months::select(&app.home,&headers)?!=request.month{return Err(error("预览属于其他月份，请切回对应月份"));}
    let book=app.book(&request.project,&request.month)?;
    let current=hash(&fs::read(&book).map_err(error)?);
    let saved_rows:Vec<u32>=serde_json::from_str(&saved_rows).map_err(error)?;
    let snapshot:Value=serde_json::from_str(&snapshot).map_err(error)?;
    let sheet=snapshot["sheet"].as_str().unwrap_or("");
    let workspace_before=snapshot["workspace_before_hash"].as_str().unwrap_or(&before);
    let binding:Option<source::Binding>=serde_json::from_value(snapshot.get("source_link").cloned().unwrap_or(Value::Null)).map_err(error)?;
    let result=|message:&str|Json(json!({"state":"done","message":message,"workbook_path":book.display().to_string(),"sheet":sheet,"rows":saved_rows,"source_sync":if let Some(ref b)=binding{json!({"state":"synced","path":b.path,"message":"已同步关联原文件"})}else{json!({"state":"unlinked","message":"未关联原文件，仅保存月份副本"})},"download":format!("/api/workbook/{}/download?month={}",request.project,request.month)}));
    if status=="done" {return Ok(result("已写入当前月份工作簿，无重复追加"));}
    if binding.is_none(){return Err((StatusCode::CONFLICT,Json(json!({"error":"请先在工作簿管理中关联服务器原文件，再重新计算并预览；本次未写入任何文件"}))));}
    let old_preview=snapshot.get("workspace_before_hash").is_none()||snapshot["preserves_layout"]!=true;
    if status=="preview"&&old_preview{return Err((StatusCode::CONFLICT,Json(json!({"error":"这是旧版生成的预览，请重新计算并预览后写入；本次未覆盖任何文件"}))));}
    source::check(&app,&request.month,&request.project,binding.as_ref(),&after)?;
    if status=="writing"&&old_preview&&(current!=after||hash(&fs::read(&binding.as_ref().unwrap().path).map_err(error)?)!=after){
        return Err((StatusCode::CONFLICT,Json(json!({"error":"旧版写入尚未完成原文件同步，需要人工核对原文件、月份副本和写前备份；所有文件已保留，本次未覆盖原文件"}))));
    }
    if status=="writing" && current==after {
        source::sync(&app,&request.month,&request.project,binding.as_ref(),&fs::read(&book).map_err(error)?,&id)?;
        app.db.execute("UPDATE previews SET state='done' WHERE id=?1",[&id]).map_err(error)?;
        return Ok(result("已恢复中断后的写入状态"));
    }
    if current!=workspace_before {return Err((StatusCode::CONFLICT,Json(json!({"error":"月份工作簿已经变化，请重新计算并预览；未覆盖文件"}))));}
    let candidate=app.home.join("previews").join(format!("{id}.xlsx"));
    let bytes=fs::read(&candidate).map_err(error)?;
    if hash(&bytes)!=after {return Err(error("预览副本校验失败，未写入"));}
    workbook::validate_write(&bytes).map_err(error)?;
    let backup=app.home.join("backups").join(format!("{}-{id}.xlsx",request.project));
    fs::copy(&book,&backup).map_err(error)?;
    if hash(&fs::read(&backup).map_err(error)?)!=workspace_before {return Err(error("写前备份校验失败"));}
    let temp=book.with_extension(format!("{id}.tmp"));
    fs::write(&temp,bytes).map_err(error)?;
    if hash(&fs::read(&book).map_err(error)?)!=workspace_before {return Err(error("文件在回写期间发生变化，请重新预览"));}
    app.db.execute("UPDATE previews SET state='writing' WHERE id=?1",[&id]).map_err(error)?;
    // Windows rename cannot replace an existing file; use the native replacement below.
    workbook::replace_file(&temp,&book).map_err(|e|error(format!("文件被占用或无写入权限：{e}；请关闭Excel/WPS后重试")))?;
    source::sync(&app,&request.month,&request.project,binding.as_ref(),&fs::read(&book).map_err(error)?,&id).map_err(|(status,body)|(status,Json(json!({"error":format!("月份副本已保存，但原文件尚未同步：{}。请排除占用或冲突后再次点击确认写入，不会重复追加",body.0["error"].as_str().unwrap_or("同步失败"))}))))?;
    app.db.execute("UPDATE previews SET state='done' WHERE id=?1",[&id]).map_err(error)?;
    Ok(result("已将结果写入关联原文件，并保存月份工作簿"))
}
#[derive(Deserialize)]
struct FormatRequest { fingerprint:String, #[serde(flatten)] format:workbook::FormatRequest }
async fn format_workbook(State(state):State<Shared>,Path(code):Path<String>,headers:HeaderMap,Json(request):Json<FormatRequest>)->Result<Json<Value>,Error> {
    let app=state.lock().unwrap();authorize(&app,&headers)?;
    let month=months::select(&app.home,&headers)?;months::writable(&app.home,&month)?;
    pending_write(&app,&month,Some(&code))?;
    let path=app.book(&code,&month)?;let (before,binding)=source::read(&app,&month,&code)?;
    if binding.is_none(){return Err((StatusCode::CONFLICT,Json(json!({"error":"请先关联服务器原文件，再设置并保存格式"}))));}
    let workspace_before_hash=hash(&fs::read(&path).map_err(error)?);
    if request.fingerprint!=hash(&before){return Err((StatusCode::CONFLICT,Json(json!({"error":"工作簿已经变化，请刷新后重新设置格式"}))));}
    let bytes=workbook::format_cells(&before,&request.format).map_err(error)?;
    let view=workbook::raw_view(&bytes,&request.format.sheet,1).map_err(error)?;
    let id=random_id();let created=SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs() as i64;
    let saved_request=json!({"month":month,"project":code,"method":"__format__","operation":"format","sheet":request.format.sheet,"range":request.format.range,"entries":[]});
    let snapshot=json!({"sheet":request.format.sheet,"source_link":binding,"workspace_before_hash":workspace_before_hash,"preserves_layout":true});
    fs::write(app.home.join("previews").join(format!("{id}.xlsx")),&bytes).map_err(error)?;
    app.db.execute("INSERT INTO previews(id,created,request,outputs,before_hash,after_hash,rows,state,method_snapshot) VALUES(?1,?2,?3,'[]',?4,?5,'[]','preview',?6)",params![id,created,saved_request.to_string(),hash(&before),hash(&bytes),snapshot.to_string()]).map_err(error)?;
    let mut result=commit_preview(&app,&headers,&id)?.0;
    result["view"]=view;result["fingerprint"]=json!(hash(&bytes));result["message"]=json!("格式已保存到关联原文件和当前月份工作簿");Ok(Json(result))
}
async fn download(State(state):State<Shared>,Path(id):Path<String>) -> Result<Response,Error> {
    let app=state.lock().unwrap();
    if id.len()!=32 || !id.bytes().all(|c|c.is_ascii_hexdigit()) {return Err(error("预览编号无效"));}
    let bytes=fs::read(app.home.join("previews").join(format!("{id}.xlsx"))).map_err(error)?;
    Ok(([(header::CONTENT_TYPE,"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),(header::CONTENT_DISPOSITION,"attachment; filename=labwork-preview.xlsx")],bytes).into_response())
}
async fn history(State(state):State<Shared>,headers:HeaderMap) -> Result<Json<Value>,Error> {
    let app=state.lock().unwrap();
    let month=months::select(&app.home,&headers)?;
    let initial=months::list(&app.home)?.first().cloned().unwrap_or_default();
    let mut query=app.db.prepare("SELECT id,created,request,outputs,rows,state FROM previews WHERE COALESCE(NULLIF(json_extract(request,'$.month'),''),?1)=?2 ORDER BY created DESC,rowid DESC LIMIT 40").map_err(error)?;
    let rows=query.query_map(params![initial,month],|r|Ok(json!({"id":r.get::<_,String>(0)?,"created":r.get::<_,i64>(1)?,"request":serde_json::from_str::<Value>(&r.get::<_,String>(2)?).unwrap_or(Value::Null),"outputs":serde_json::from_str::<Value>(&r.get::<_,String>(3)?).unwrap_or(Value::Null),"rows":serde_json::from_str::<Value>(&r.get::<_,String>(4)?).unwrap_or(Value::Null),"state":r.get::<_,String>(5)?}))).map_err(error)?.collect::<Result<Vec<_>,_>>().map_err(error)?;
    Ok(Json(json!(rows)))
}

#[tokio::main]
async fn main() {
    let tray_mode=cfg!(windows) && !std::env::args().any(|a|a=="--headless");
    if let Err(err)=run(tray_mode).await {
        eprintln!("{err}");
        #[cfg(windows)]
        if tray_mode {tray::show_error(&err.to_string());}
        std::process::exit(1);
    }
}

async fn run(tray_mode:bool) -> Result<(),Box<dyn std::error::Error>> {
    let settings=config::load()?;
    let port=settings.port;
    let home=settings.data_dir;
    let lan_ip=settings.lan_ip;
    let mut access=auth::Access::new(if lan_ip.is_empty(){None}else{Some(&settings.access_password)});
    let listener=tokio::net::TcpListener::bind((if lan_ip.is_empty(){"127.0.0.1"}else{"0.0.0.0"},port)).await.map_err(|e|format!("labwork 启动失败：端口 {port} 无法使用（{e}）。请检查是否已经运行，或修改 labwork.json 中的端口。"))?;
    let marker=home.join(".labwork-rust-preview");
    if home.exists() && !marker.exists() && fs::read_dir(&home)?.next().is_some() {return Err("拒绝使用已有且未标记的目录；请使用独立预览目录".into());}
    fs::create_dir_all(&home)?;
    #[cfg(windows)]
    let _data_lock={
        use std::os::windows::fs::OpenOptionsExt;
        fs::OpenOptions::new().write(true).create(true).truncate(false).share_mode(0).open(home.join(".running.lock"))
            .map_err(|e|format!("数据目录正在被其他实例使用或无法写入，请先退出旧版平台：{e}"))?
    };
    fs::write(&marker,"0.0.2-alpha.1; local preview only")?;
    for folder in ["workbooks","previews","backups"] {fs::create_dir_all(home.join(folder))?;}
    months::initialize(&home)?;
    let db=Connection::open(home.join("preview.sqlite3"))?;
    db.busy_timeout(std::time::Duration::from_secs(5))?;
    db.execute_batch("PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; CREATE TABLE IF NOT EXISTS previews(id TEXT PRIMARY KEY,created INTEGER NOT NULL,request TEXT NOT NULL,outputs TEXT NOT NULL,before_hash TEXT NOT NULL,after_hash TEXT NOT NULL,rows TEXT NOT NULL,state TEXT NOT NULL,method_snapshot TEXT NOT NULL);")?;
    access.restore(&db)?;source::initialize(&db)?;
    let (shutdown_tx,shutdown_rx)=tokio::sync::oneshot::channel::<()>();
    #[cfg(windows)]
    let _tray=if tray_mode {Some(tray::start(home.clone(),port,shutdown_tx)?)}else{drop(shutdown_tx);None};
    #[cfg(not(windows))]
    if tray_mode {return Err("托盘模式仅支持Windows".into());}
    let state=Arc::new(Mutex::new(App{home,db,projects:serde_json::from_str(include_str!("../catalog.json"))?,token:random_id(),port,lan_ip,access}));
    let router=Router::new().route("/",get(index)).route("/app.css",get(css)).route("/app.js",get(js)).route("/grid.js",get(grid_js))
        .route("/login",get(auth::page)).route("/login.js",get(auth::script)).route("/api/login",post(auth::login)).route("/api/logout",post(auth::logout))
        .route("/api/workbook/{project}/download",get(download_book))
        .route("/api/workbook/{project}/format",post(format_workbook))
        .route("/api/workbook/{project}/source",post(bind_source))
        .route("/api/workbook/{project}/source-import",post(import_source))
        .route("/api/workbook/{project}/remove",post(remove_workbook))
        .route("/api/month",post(choose_month)).route("/api/month/preview",post(preview_month)).route("/api/month-rules/{project}",post(month_rules)).route("/api/book-sheet/{project}/{sheet}",get(raw_sheet))
        .route("/api/workbook/{project}",post(bind_workbook).layer(DefaultBodyLimit::max(import::MAX_BYTES)))
        .route("/api/catalog",get(catalog)).route("/api/sheet/{project}/{method}",get(sheet)).route("/api/preview",post(preview))
        .route("/api/commit/{id}",post(commit)).route("/api/download/{id}",get(download)).route("/api/history",get(history))
        .layer(DefaultBodyLimit::max(1024*1024)).layer(middleware::from_fn_with_state(state.clone(),auth::guard)).layer(middleware::from_fn_with_state(state.clone(),local_host)).with_state(state);
    println!("labwork-v{}: http://127.0.0.1:{port} ; Ctrl+C to stop",env!("CARGO_PKG_VERSION"));
    axum::serve(listener,router).with_graceful_shutdown(async move {
        if tray_mode {let _=shutdown_rx.await;}
        else {let _=tokio::signal::ctrl_c().await;}
    }).await?;
    Ok(())
}
