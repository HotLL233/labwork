use super::*;
use axum::response::Redirect;

pub struct Access {
    password: Option<String>,
    sessions: BTreeMap<String,i64>,
    failed: u32,
    retry_at: i64,
}
fn now() -> i64 {SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_secs() as i64}
impl Access {
    pub fn new(password: Option<&str>) -> Self {
        Self{password:password.map(|p|hash(p.as_bytes())),sessions:BTreeMap::new(),failed:0,retry_at:0}
    }
    pub fn restore(&mut self,db:&Connection)->rusqlite::Result<()> {
        db.execute_batch("CREATE TABLE IF NOT EXISTS login_sessions(token_hash TEXT PRIMARY KEY,expires INTEGER NOT NULL,password_hash TEXT NOT NULL);")?;
        let password=self.password.as_deref().unwrap_or("");
        db.execute("DELETE FROM login_sessions WHERE expires<=?1 OR password_hash<>?2",params![now(),password])?;
        let mut query=db.prepare("SELECT token_hash,expires FROM login_sessions WHERE password_hash=?1")?;
        self.sessions=query.query_map([password],|row|Ok((row.get::<_,String>(0)?,row.get::<_,i64>(1)?)))?.collect::<Result<_,_>>()?;
        Ok(())
    }
    fn permitted(&self, headers: &HeaderMap) -> bool {
        if self.password.is_none() {return true;}
        headers.get(header::COOKIE).and_then(|h|h.to_str().ok()).unwrap_or("")
            .split(';').filter_map(|c|c.trim().strip_prefix("labwork_session="))
            .any(|id|self.sessions.get(&hash(id.as_bytes())).is_some_and(|expires|*expires>now()))
    }
}

pub async fn guard(State(state):State<Shared>,request:axum::extract::Request,next:Next)->Response {
    let public=matches!(request.uri().path(),"/login"|"/login.js"|"/app.css"|"/api/login");
    if !public && !state.lock().unwrap().access.permitted(request.headers()) {
        if request.uri().path().starts_with("/api/") {
            return (StatusCode::UNAUTHORIZED,Json(json!({"error":"请先输入访问口令"}))).into_response();
        }
        return Redirect::to("/login").into_response();
    }
    next.run(request).await
}

#[derive(Deserialize)]
pub struct Login {password:String,#[serde(default)] remember:bool}
pub async fn login(State(state):State<Shared>,headers:HeaderMap,Json(request):Json<Login>)->Response {
    let mut app=state.lock().unwrap();
    if !headers.get(header::ORIGIN).and_then(|v|v.to_str().ok()).is_some_and(|o|app.allowed_origin(o)) {
        return (StatusCode::FORBIDDEN,Json(json!({"error":"请从平台登录页面提交"}))).into_response();
    }
    let access=&mut app.access;
    let current=now();
    if current<access.retry_at {
        return (StatusCode::TOO_MANY_REQUESTS,Json(json!({"error":"口令错误次数过多，请30秒后重试"}))).into_response();
    }
    if access.password.as_ref().is_some_and(|expected|*expected!=hash(request.password.as_bytes())) {
        access.failed+=1;
        if access.failed>=5 {access.retry_at=current+30;access.failed=0;}
        return (StatusCode::UNAUTHORIZED,Json(json!({"error":"访问口令不正确"}))).into_response();
    }
    access.failed=0;
    access.sessions.retain(|_,expires|*expires>current);
    if access.sessions.len()>=256 {return (StatusCode::TOO_MANY_REQUESTS,Json(json!({"error":"在线会话过多，请稍后重试"}))).into_response();}
    let id=random_id();
    let duration=if request.remember {30*24*3600}else{12*3600};
    let expires=current+duration;
    let token_hash=hash(id.as_bytes());
    let password_hash=access.password.clone().unwrap_or_default();
    if request.remember {
        if let Err(err)=app.db.execute("INSERT INTO login_sessions(token_hash,expires,password_hash) VALUES(?1,?2,?3)",params![token_hash,expires,password_hash]) {
            return error(format!("无法保存登录状态：{err}")).into_response();
        }
    }
    app.access.sessions.insert(token_hash,expires);
    let persistence=if request.remember{format!("; Max-Age={duration}")}else{String::new()};
    ([(header::SET_COOKIE,format!("labwork_session={id}; Path=/; HttpOnly; SameSite=Strict{persistence}"))],Json(json!({"ok":true}))).into_response()
}

pub async fn logout(State(state):State<Shared>,headers:HeaderMap)->Result<Response,Error> {
    let mut app=state.lock().unwrap();authorize(&app,&headers)?;
    for id in headers.get(header::COOKIE).and_then(|v|v.to_str().ok()).unwrap_or("").split(';').filter_map(|c|c.trim().strip_prefix("labwork_session=")) {
        let key=hash(id.as_bytes());app.db.execute("DELETE FROM login_sessions WHERE token_hash=?1",[&key]).map_err(error)?;app.access.sessions.remove(&key);
    }
    Ok(([(header::SET_COOKIE,"labwork_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0")],Json(json!({"ok":true}))).into_response())
}

pub async fn page()->Html<&'static str> {Html(include_str!("../web/login.html"))}
pub async fn script()->impl IntoResponse {([(header::CONTENT_TYPE,"application/javascript; charset=utf-8")],include_str!("../web/login.js"))}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn session_is_required_and_expires() {
        let mut access=Access::new(Some("test"));
        let mut headers=HeaderMap::new();
        assert!(!access.permitted(&headers));
        headers.insert(header::COOKIE,"labwork_session=token".parse().unwrap());
        access.sessions.insert(hash(b"token"),now()+60);
        assert!(access.permitted(&headers));
        access.sessions.insert(hash(b"token"),now()-1);
        assert!(!access.permitted(&headers));
    }
    #[test]
    fn remembered_sessions_restore_and_password_change_revokes() {
        let db=Connection::open_in_memory().unwrap();
        let mut access=Access::new(Some("old"));access.restore(&db).unwrap();
        db.execute("INSERT INTO login_sessions VALUES(?1,?2,?3)",params![hash(b"opaque-token"),now()+60,hash(b"old")]).unwrap();
        access.restore(&db).unwrap();assert_eq!(access.sessions.len(),1);
        let mut changed=Access::new(Some("new"));changed.restore(&db).unwrap();assert!(changed.sessions.is_empty());
        assert_eq!(db.query_row("SELECT COUNT(*) FROM login_sessions",[],|r|r.get::<_,i64>(0)).unwrap(),0);
    }
}
