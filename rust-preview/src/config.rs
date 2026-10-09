use serde::Deserialize;
use std::{fs, path::PathBuf};

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Settings { pub port: u16, pub data_dir: PathBuf }

pub fn allowed_host(host: &str, port: u16) -> bool {
    host == format!("127.0.0.1:{port}") || host == format!("localhost:{port}")
}

pub fn load() -> Result<Settings, Box<dyn std::error::Error>> {
    let base=std::env::current_exe()?.parent().ok_or("无法确定程序目录")?.to_path_buf();
    let args=std::env::args().skip(1).collect::<Vec<_>>();
    let mut config=base.join("labwork.json");
    let (mut home,mut port)=(None,None);
    let mut explicit=false;
    let mut i=0;
    while i<args.len() {
        match args[i].as_str() {
            "--tray"|"--headless"=>{},
            "--config"|"--home"|"--port"=>{
                let value=args.get(i+1).ok_or("启动参数缺少值")?;
                match args[i].as_str() {
                    "--config"=>{config=PathBuf::from(value);explicit=true;},
                    "--home"=>home=Some(PathBuf::from(value)),
                    _=>port=Some(value.parse::<u16>().map_err(|_|"端口必须是1024至65535的整数")?),
                }
                i+=1;
            },
            _=>return Err(format!("未知启动参数：{}",args[i]).into()),
        }
        i+=1;
    }
    let mut settings=if config.exists() || explicit {
        let text=fs::read_to_string(&config)?;
        let mut settings:Settings=serde_json::from_str(text.trim_start_matches('\u{feff}'))?;
        if settings.data_dir.is_relative() {settings.data_dir=config.parent().unwrap_or(&base).join(&settings.data_dir);}
        settings
    } else {Settings{port:18806,data_dir:base.join("data")}};
    if let Some(home)=home {settings.data_dir=home;}
    if let Some(port)=port {settings.port=port;}
    if settings.port<1024 {return Err("端口必须是1024至65535的整数".into());}
    if settings.data_dir.as_os_str().is_empty() {return Err("数据目录不能为空".into());}
    Ok(settings)
}

#[cfg(test)]
mod tests {
    #[test]
    fn host_is_exact_and_port_specific() {
        assert!(super::allowed_host("localhost:19846",19846));
        for host in ["localhost:18805","localhost:19846.evil","evil:19846",""] {assert!(!super::allowed_host(host,19846));}
    }
}
