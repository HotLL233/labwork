use crate::{Error, Project, error, random_id, workbook};
use axum::{Json, http::StatusCode};
use serde_json::{Value, json};
use std::{collections::HashSet, fs::{self, OpenOptions}, io::{Cursor, Read, Write}, path::Path};

pub const MAX_BYTES: usize = 20 * 1024 * 1024;

pub(crate) fn validate(bytes: &[u8], project: &Project) -> Result<(), String> {
    if bytes.len() > MAX_BYTES { return Err("工作簿不能超过20 MiB".into()); }
    let mut zip = zip::ZipArchive::new(Cursor::new(bytes)).map_err(|_| "请选择有效的 .xlsx 工作簿；不支持 .xls、.xlsm 或其他格式")?;
    if zip.len() > 3000 { return Err("工作簿部件过多".into()); }
    let mut names = HashSet::new();
    let mut remaining = 100 * 1024 * 1024u64;
    let mut content_types = String::new();
    for index in 0..zip.len() {
        let mut file = zip.by_index(index).map_err(|_| "工作簿ZIP部件无效")?;
        let name = file.name().to_owned();
        if !names.insert(name.clone()) || name.split('/').any(|p| p == "..") || name.starts_with('/') || name.contains('\\') || name.to_ascii_lowercase().contains("vbaproject") {
            return Err("不支持的工作簿结构或宏文件".into());
        }
        if file.size() > remaining { return Err("工作簿展开后超过100 MiB".into()); }
        let mut bytes = Vec::new();
        (&mut file).take(remaining + 1).read_to_end(&mut bytes).map_err(|_| "工作簿解压或校验失败")?;
        if bytes.len() as u64 > remaining { return Err("工作簿展开后超过100 MiB".into()); }
        remaining -= bytes.len() as u64;
        if name.ends_with(".xml") && bytes.windows(9).any(|w| w.eq_ignore_ascii_case(b"<!DOCTYPE")) { return Err("不接受含DTD的XML".into()); }
        if name == "[Content_Types].xml" { content_types = String::from_utf8(bytes).map_err(|_| "工作簿内容类型无效")?; }
    }
    let types = roxmltree::Document::parse(&content_types).map_err(|_| "缺少有效的xlsx内容类型")?;
    if !types.descendants().any(|n| n.attribute("PartName") == Some("/xl/workbook.xml") && n.attribute("ContentType") == Some("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml")) {
        return Err("仅支持普通 .xlsx 工作簿，不支持宏工作簿或模板文件".into());
    }
    let sheets=workbook::sheet_names(bytes)?;
    if sheets.is_empty() {return Err("工作簿中没有可用工作表".into());}
    for sheet in sheets {workbook::raw_view(bytes,&sheet,1)?;}
    for method in &project.methods {validate_method(bytes,method)?;}
    Ok(())
}

pub(crate) fn validate_method(bytes:&[u8],method:&crate::Method)->Result<(),String> {
        let view = workbook::view(bytes, method, &[]).map_err(|e| format!("{}：{e}", method.name))?;
        let rows = view["rows"].as_array().ok_or("缺少工作表记录区")?;
        let header = |column: &str| rows.iter().find(|r| r["number"] == 1).and_then(|r| r["cells"].as_array()).and_then(|cells| cells.iter().find(|c| c["ref"] == format!("{column}1"))).and_then(|c| c["raw"].as_str()).unwrap_or("");
        if !header(&method.columns["batch"]).contains('批') {
            return Err(format!("{}：批号表头不匹配，请选择对应项目的原表", method.name));
        }
        for (key, column) in &method.columns {
            if !view["columns"].as_array().is_some_and(|cols| cols.iter().any(|c|c["label"] == *column)) { return Err(format!("{}：缺少{column}列",method.name)); }
            // Original templates leave the date header and diluted mass headers blank.
            if key == "notes" || key == "date" || (method.kind == "water_diluted" && ["original_mass", "total_mass"].contains(&key.as_str())) { continue; }
            if header(column).trim().is_empty() { return Err(format!("{}：{column}列缺少必要表头，请先核定表格映射", method.name)); }
        }
    Ok(())
}

pub fn bind(home: &Path, project: &Project, bytes: &[u8]) -> Result<Value, Error> {
    if project.code.is_empty() || project.code.len()>50 || !project.code.bytes().all(|c| c.is_ascii_alphanumeric() || c==b'-' || c==b'_') { return Err(error("项目编号无效")); }
    let directory = home.join("workbooks");
    let destination = directory.join(format!("{}.xlsx", project.code));
    let conflict = || (StatusCode::CONFLICT, Json(json!({"error":"此项目已经绑定工作簿，为保护已有记录不能覆盖"})));
    if destination.try_exists().map_err(error)? { return Err(conflict()); }
    validate(bytes, project).map_err(error)?;
    fs::create_dir_all(&directory).map_err(error)?;
    let temporary = directory.join(format!(".import-{}.tmp", random_id()));
    let mut file = OpenOptions::new().write(true).create_new(true).open(&temporary).map_err(error)?;
    let result = (|| {
        file.write_all(bytes).map_err(error)?;
        file.sync_all().map_err(error)?;
        drop(file);
        // A same-directory hard link publishes the complete file without replacing an existing target.
        fs::hard_link(&temporary, &destination).map_err(|e| if e.kind() == std::io::ErrorKind::AlreadyExists { conflict() } else { error(format!("无法安全绑定工作簿：{e}")) })?;
        Ok(json!({"available":true,"name":format!("{}.xlsx",project.code)}))
    })();
    let _ = fs::remove_file(&temporary);
    result
}
