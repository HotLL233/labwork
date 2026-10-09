#![cfg_attr(windows, windows_subsystem = "windows")]
mod config;
mod calc;
mod workbook;
#[cfg(windows)]
mod tray;

use axum::{Json, Router, extract::{DefaultBodyLimit, Path, State}, http::{HeaderMap, StatusCode, header}, middleware::{self, Next}, response::{Html, IntoResponse, Response}, routing::{get, post}};
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
struct App { home: PathBuf, db: Connection, projects: Vec<Project>, token: String, port: u16 }
type Shared = Arc<Mutex<App>>;

async fn local_host(State(state):State<Shared>,request:axum::extract::Request,next:Next)->Response {
    let port=state.lock().unwrap().port;
    if !config::allowed_host(request.headers().get(header::HOST).and_then(|h|h.to_str().ok()).unwrap_or(""),port) {
        return (StatusCode::FORBIDDEN,"This preview accepts localhost only").into_response();
    }
    let mut response=next.run(request).await;
    response.headers_mut().insert("x-content-type-options","nosniff".parse().unwrap());
    response.headers_mut().insert("x-frame-options","DENY".parse().unwrap());
    response.headers_mut().insert(header::CACHE_CONTROL,"no-store".parse().unwrap());
    response.headers_mut().insert("content-security-policy","default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'; object-src 'none'; base-uri 'none'".parse().unwrap());
    response
}

#[derive(Serialize, Deserialize, Clone)]
struct Request { project: String, method: String, #[serde(default)] date: String, #[serde(default)] batch: String, #[serde(default)] notes: String, #[serde(default)] measurements: Vec<BTreeMap<String,String>>, #[serde(default)] entries: Vec<Entry> }
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
    fn book(&self, project: &str) -> Result<PathBuf, Error> {
        if !self.projects.iter().any(|p|p.code==project) {return Err(error("项目不存在"));}
        let path=self.home.join("workbooks").join(format!("{project}.xlsx"));
        if !path.exists() {return Err(error("尚未导入此项目的工作簿副本"));}
        Ok(path)
    }
    fn method(&self, request: &Request) -> Result<Method,Error> {
        self.projects.iter().find(|p|p.code==request.project).and_then(|p|p.methods.iter().find(|m|m.key==request.method)).cloned().ok_or_else(||error("此项目的方法尚未迁移"))
    }
}

async fn index(State(state): State<Shared>) -> Html<String> {
    let app=state.lock().unwrap();
    Html(include_str!("../web/index.html").replace("__TOKEN__",&app.token).replace("__VERSION__",env!("CARGO_PKG_VERSION")).replace("__PORT__",&app.port.to_string()))
}
async fn css() -> impl IntoResponse { ([(header::CONTENT_TYPE,"text/css; charset=utf-8")],include_str!("../web/app.css")) }
async fn js() -> impl IntoResponse { ([(header::CONTENT_TYPE,"application/javascript; charset=utf-8")],include_str!("../web/app.js")) }
async fn grid_js() -> impl IntoResponse { ([(header::CONTENT_TYPE,"application/javascript; charset=utf-8")],include_str!("../web/grid.js")) }
async fn catalog(State(state): State<Shared>) -> Json<Value> {
    let app=state.lock().unwrap();
    Json(json!({"version":env!("CARGO_PKG_VERSION"),"port":app.port,"data_directory":app.home.canonicalize().unwrap_or_else(|_|app.home.clone()).display().to_string(),"projects":app.projects.iter().map(|p|json!({"code":p.code,"name":p.name,"methods":p.methods,"available":app.home.join("workbooks").join(format!("{}.xlsx",p.code)).exists()})).collect::<Vec<_>>()}))
}
async fn sheet(State(state): State<Shared>,Path((project,method)):Path<(String,String)>) -> Result<Json<Value>,Error> {
    let app=state.lock().unwrap();
    let book=app.book(&project)?;
    let method=app.projects.iter().find(|p|p.code==project).and_then(|p|p.methods.iter().find(|m|m.key==method)).ok_or_else(||error("方法尚未支持"))?;
    let bytes=fs::read(book).map_err(error)?;
    let view=workbook::view(&bytes,method,&[]).map_err(error)?;
    Ok(Json(json!({"view":view,"next_row":workbook::next_row(&bytes,method).map_err(error)?,"fingerprint":hash(&bytes)})))
}
fn authorize(app: &App,headers:&HeaderMap) -> Result<(),Error> {
    if headers.get("x-labwork-token").and_then(|v|v.to_str().ok()) != Some(&app.token) {return Err((StatusCode::FORBIDDEN,Json(json!({"error":"页面已过期，请刷新后重试"}))));}
    if let Some(origin)=headers.get(header::ORIGIN).and_then(|v|v.to_str().ok()) {
        if !origin.strip_prefix("http://").is_some_and(|host|config::allowed_host(host,app.port)) {return Err((StatusCode::FORBIDDEN,Json(json!({"error":"拒绝跨站提交"}))));}
    }
    Ok(())
}
async fn preview(State(state):State<Shared>,headers:HeaderMap,Json(mut request):Json<Request>) -> Result<Json<Value>,Error> {
    let app=state.lock().unwrap(); authorize(&app,&headers)?;
    let method=app.method(&request)?;
    request.normalize().map_err(error)?;
    let outputs=request.entries.iter().enumerate().map(|(i,row)|calc::calculate(&method.kind,&row.measurement,&method.params).map_err(|e|format!("第{}行（{}）：{e}",i+1,row.batch))).collect::<Result<Vec<_>,_>>().map_err(error)?;
    let bytes=fs::read(app.book(&request.project)?).map_err(error)?;
    let (candidate,rows)=workbook::append(&bytes,&method,&request,&outputs).map_err(error)?;
    let view=workbook::view(&candidate,&method,&rows).map_err(error)?;
    let id=random_id();
    let created=SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs() as i64;
    app.db.execute("INSERT INTO previews(id,created,request,outputs,before_hash,after_hash,rows,state,method_snapshot) VALUES(?1,?2,?3,?4,?5,?6,?7,'preview',?8)",params![id,created,serde_json::to_string(&request).unwrap(),serde_json::to_string(&outputs).unwrap(),hash(&bytes),hash(&candidate),serde_json::to_string(&rows).unwrap(),serde_json::to_string(&method).unwrap()]).map_err(error)?;
    fs::write(app.home.join("previews").join(format!("{id}.xlsx")),candidate).map_err(error)?;
    Ok(Json(json!({"id":id,"entries":request.entries,"outputs":outputs,"rows":rows,"view":view,"state":"preview","download":format!("/api/download/{id}")})))
}
async fn commit(State(state):State<Shared>,headers:HeaderMap,Path(id):Path<String>) -> Result<Json<Value>,Error> {
    let app=state.lock().unwrap(); authorize(&app,&headers)?;
    let (raw,before,after,status):(String,String,String,String)=app.db.query_row("SELECT request,before_hash,after_hash,state FROM previews WHERE id=?1",[&id],|r|Ok((r.get(0)?,r.get(1)?,r.get(2)?,r.get(3)?))).map_err(|_|error("预览不存在"))?;
    let request:Request=serde_json::from_str(&raw).map_err(error)?;
    let book=app.book(&request.project)?;
    let current=hash(&fs::read(&book).map_err(error)?);
    if status=="done" {return Ok(Json(json!({"state":"done","message":"已写入工作副本，无重复追加"})));}
    if status=="writing" && current==after {
        app.db.execute("UPDATE previews SET state='done' WHERE id=?1",[&id]).map_err(error)?;
        return Ok(Json(json!({"state":"done","message":"已恢复中断后的写入状态"})));
    }
    if current!=before {return Err((StatusCode::CONFLICT,Json(json!({"error":"工作簿已经变化，请重新计算并预览；未覆盖文件"}))));}
    let candidate=app.home.join("previews").join(format!("{id}.xlsx"));
    let bytes=fs::read(&candidate).map_err(error)?;
    if hash(&bytes)!=after {return Err(error("预览副本校验失败，未写入"));}
    let backup=app.home.join("backups").join(format!("{}-{id}.xlsx",request.project));
    fs::copy(&book,&backup).map_err(error)?;
    if hash(&fs::read(&backup).map_err(error)?)!=before {return Err(error("写前备份校验失败"));}
    let temp=book.with_extension(format!("{id}.tmp"));
    fs::write(&temp,bytes).map_err(error)?;
    if hash(&fs::read(&book).map_err(error)?)!=before {return Err(error("文件在回写期间发生变化，请重新预览"));}
    app.db.execute("UPDATE previews SET state='writing' WHERE id=?1",[&id]).map_err(error)?;
    // Windows rename cannot replace an existing file; use the native replacement below.
    workbook::replace_file(&temp,&book).map_err(|e|error(format!("文件被占用或无写入权限：{e}；请关闭Excel/WPS后重试")))?;
    app.db.execute("UPDATE previews SET state='done' WHERE id=?1",[&id]).map_err(error)?;
    Ok(Json(json!({"state":"done","message":"已写入工作副本；原表未改动"})))
}
async fn download(State(state):State<Shared>,Path(id):Path<String>) -> Result<Response,Error> {
    let app=state.lock().unwrap();
    if id.len()!=32 || !id.bytes().all(|c|c.is_ascii_hexdigit()) {return Err(error("预览编号无效"));}
    let bytes=fs::read(app.home.join("previews").join(format!("{id}.xlsx"))).map_err(error)?;
    Ok(([(header::CONTENT_TYPE,"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),(header::CONTENT_DISPOSITION,"attachment; filename=Labwork-preview.xlsx")],bytes).into_response())
}
async fn history(State(state):State<Shared>) -> Result<Json<Value>,Error> {
    let app=state.lock().unwrap();
    let mut query=app.db.prepare("SELECT id,created,request,outputs,rows,state FROM previews ORDER BY created DESC,rowid DESC LIMIT 40").map_err(error)?;
    let rows=query.query_map([],|r|Ok(json!({"id":r.get::<_,String>(0)?,"created":r.get::<_,i64>(1)?,"request":serde_json::from_str::<Value>(&r.get::<_,String>(2)?).unwrap_or(Value::Null),"outputs":serde_json::from_str::<Value>(&r.get::<_,String>(3)?).unwrap_or(Value::Null),"rows":serde_json::from_str::<Value>(&r.get::<_,String>(4)?).unwrap_or(Value::Null),"state":r.get::<_,String>(5)?}))).map_err(error)?.collect::<Result<Vec<_>,_>>().map_err(error)?;
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
    let listener=tokio::net::TcpListener::bind(("127.0.0.1",port)).await.map_err(|e|format!("理化检测平台启动失败：端口 {port} 无法使用（{e}）。请检查是否已经运行，或修改 labwork.json 中的端口。"))?;
    let marker=home.join(".labwork-rust-preview");
    if home.exists() && !marker.exists() && fs::read_dir(&home)?.next().is_some() {return Err("拒绝使用已有且未标记的目录；请使用独立预览目录".into());}
    fs::create_dir_all(&home)?; fs::write(&marker,"0.0.2-alpha.1; local preview only")?;
    for folder in ["workbooks","previews","backups"] {fs::create_dir_all(home.join(folder))?;}
    let db=Connection::open(home.join("preview.sqlite3"))?;
    db.busy_timeout(std::time::Duration::from_secs(5))?;
    db.execute_batch("PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; CREATE TABLE IF NOT EXISTS previews(id TEXT PRIMARY KEY,created INTEGER NOT NULL,request TEXT NOT NULL,outputs TEXT NOT NULL,before_hash TEXT NOT NULL,after_hash TEXT NOT NULL,rows TEXT NOT NULL,state TEXT NOT NULL,method_snapshot TEXT NOT NULL);")?;
    let (shutdown_tx,shutdown_rx)=tokio::sync::oneshot::channel::<()>();
    #[cfg(windows)]
    let _tray=if tray_mode {Some(tray::start(home.clone(),port,shutdown_tx)?)}else{drop(shutdown_tx);None};
    #[cfg(not(windows))]
    if tray_mode {return Err("托盘模式仅支持Windows".into());}
    let state=Arc::new(Mutex::new(App{home,db,projects:serde_json::from_str(include_str!("../catalog.json"))?,token:random_id(),port}));
    let router=Router::new().route("/",get(index)).route("/app.css",get(css)).route("/app.js",get(js)).route("/grid.js",get(grid_js))
        .route("/api/catalog",get(catalog)).route("/api/sheet/{project}/{method}",get(sheet)).route("/api/preview",post(preview))
        .route("/api/commit/{id}",post(commit)).route("/api/download/{id}",get(download)).route("/api/history",get(history))
        .layer(DefaultBodyLimit::max(1024*1024)).layer(middleware::from_fn_with_state(state.clone(),local_host)).with_state(state);
    // This validation build intentionally cannot bind to a LAN interface before account migration.
    println!("Rust + SQLite validation: http://127.0.0.1:{port} ; Ctrl+C to stop");
    axum::serve(listener,router).with_graceful_shutdown(async move {
        if tray_mode {let _=shutdown_rx.await;}
        else {let _=tokio::signal::ctrl_c().await;}
    }).await?;
    Ok(())
}
