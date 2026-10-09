use super::*;
use axum::response::Redirect;

pub struct Access {
    password: Option<String>,
    sessions: BTreeMap<String,u64>,
    failed: u32,
    retry_at: u64,
}
fn now() -> u64 {SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_secs()}
impl Access {
    pub fn new(password: Option<&str>) -> Self {
        Self{password:password.map(|p|hash(p.as_bytes())),sessions:BTreeMap::new(),failed:0,retry_at:0}
    }
    fn permitted(&self, headers: &HeaderMap) -> bool {
        if self.password.is_none() {return true;}
        headers.get(header::COOKIE).and_then(|h|h.to_str().ok()).unwrap_or("")
            .split(';').filter_map(|c|c.trim().strip_prefix("labwork_session="))
            .any(|id|self.sessions.get(id).is_some_and(|expires|*expires>now()))
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
pub struct Login {password:String}
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
    access.sessions.insert(id.clone(),current+12*3600);
    ([(header::SET_COOKIE,format!("labwork_session={id}; Path=/; HttpOnly; SameSite=Strict; Max-Age=43200"))],Json(json!({"ok":true}))).into_response()
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
        access.sessions.insert("token".into(),now()+60);
        assert!(access.permitted(&headers));
        access.sessions.insert("token".into(),now()-1);
        assert!(!access.permitted(&headers));
    }
}
