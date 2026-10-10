use bigdecimal::{BigDecimal as D, RoundingMode};
use serde::{Deserialize, Serialize};
use std::{collections::BTreeMap, str::FromStr};

#[derive(Clone, Serialize, Deserialize)]
pub struct Field { pub key:String, pub label:String, pub unit:String, pub sample:String, #[serde(default)] pub input_type:String }
pub type Values = BTreeMap<String,String>;
fn n(raw:&Values,key:&str)->Result<D,String> {
    let s=raw.get(key).ok_or_else(||format!("缺少 {key}"))?;
    if s.len()>40 {return Err(format!("{key} 数字过长"));}
    if let Some(exponent)=s.split(['e','E']).nth(1){if !exponent.parse::<i32>().is_ok_and(|e|(-12..=10).contains(&e)){return Err(format!("{key} 指数超出范围"));}}
    let v=D::from_str(s).map_err(|_|format!("{key} 必须是数字"))?;
    if v.abs()>D::from(10_000_000_000_i64) || v.as_bigint_and_exponent().1>12 {return Err(format!("{key} 超出精度或范围"));}
    Ok(v)
}
pub fn keys(kind:&str)->&'static [&'static str] {
    match kind {"titration"=>&["mass","volume"],"instrument"=>&["result"],"water_diluted"=>&["result","original_mass","total_mass","water_result"],"fluoride"=>&["mass1","total1","mass2","total2","reading"],"loss"=>&["mass","before","after1","after2"],"melting"=>&["start","end"],_=>&[]}
}
pub fn calculate(kind:&str,raw:&Values,params:&Values)->Result<Values,String> {
    let fields=keys(kind); if fields.is_empty(){return Err("未知方法".into());}
    let v=fields.iter().map(|k|Ok((*k,n(raw,k)?))).collect::<Result<BTreeMap<_,_>,String>>()?;
    for (k,value) in &v {
        if kind!="melting" && value<&D::from(0) {return Err("输入不能为负数".into());}
        if ["mass","mass1","mass2","original_mass"].contains(k) && value<=&D::from(0) {return Err("称量质量必须大于零".into());}
    }
    let value=|key:&str|v[key].clone();
    let result=match kind {
        "titration"=>{
            let c=n(params,"c")?;let mw=n(params,"mw")?;let factor=n(params,"factor")?;let blank=n(params,"blank")?;
            if c<=D::from(0)||mw<=D::from(0)||factor<=D::from(0)||blank<D::from(0){return Err("标定参数不合法".into());}
            let corrected=value("volume")-blank;
            if corrected<D::from(0){return Err("滴定体积不能小于空白体积".into());}
            c*corrected*mw*factor/value("mass")/D::from(10)
        },
        "instrument"=>{if value("result")>D::from(100){return Err("水分不能超过100%".into());}value("result")},
        "water_diluted"=>{
            if value("total_mass")<value("original_mass")||value("result")>D::from(100)||value("water_result")>D::from(100){return Err("总质量须不小于原样质量，水分须在0至100%之间".into());}
            let result=(value("result")*value("total_mass")-(value("total_mass")-value("original_mass"))*value("water_result"))/value("original_mass");
            if result<D::from(0)||result>D::from(100){return Err("换算水分超出0至100%".into());}result
        },
        "fluoride"=>{
            if value("total1")<value("mass1")||value("total2")<value("mass2"){return Err("稀释总质量不能小于取样质量".into());}
            value("reading")*value("total2")/value("mass2")*value("total1")/value("mass1")
        },
        "loss"=>{
            let after=(value("after1")+value("after2"))/D::from(2);
            if value("before")<value("mass")||after>value("before")||value("before")-&after>value("mass"){return Err("恒重前后称量关系不合理".into());}
            (value("before")-after)/value("mass")*D::from(100)
        },
        "melting"=>{
            if value("end")<value("start"){return Err("终熔点不能低于初熔点".into());}
            return Ok(Values::from([("start".into(),value("start").normalized().to_string()),("end".into(),value("end").normalized().to_string()),("display".into(),format!("{}–{}",value("start"),value("end"))),("unit".into(),"℃".into())]));
        },_=>return Err("未知方法".into())
    }.with_precision_round(std::num::NonZeroU64::new(36).unwrap(),RoundingMode::HalfEven);
    let mut output=Values::from([("result".into(),result.normalized().to_string()),("display".into(),result.with_scale_round(5,RoundingMode::HalfEven).to_string()),("unit".into(),if kind=="fluoride"{"ppm"}else{"%"}.into())]);
    if kind=="fluoride"{output.insert("percent".into(),(result/D::from(10000)).normalized().to_string());}
    Ok(output)
}
pub fn formulas(kind:&str,cols:&Values,row:u32,params:&Values)->Values {
    let c=|key:&str|format!("{}{row}",cols[key]);
    match kind {
        "titration"=>Values::from([("result".into(),format!("{}*({}-{})*{}*{}/{}/1000*100",params["c"],c("volume"),params["blank"],params["mw"],params["factor"],c("mass")))]),
        "water_diluted"=>Values::from([("result".into(),format!("({}*{}-({}-{})*{})/{}",c("raw_result"),c("total_mass"),c("total_mass"),c("original_mass"),c("water_result"),c("original_mass")))]),
        "fluoride"=>Values::from([("result".into(),format!("{}*{}/{}*{}/{}",c("reading"),c("total2"),c("mass2"),c("total1"),c("mass1"))),("percent".into(),format!("{}/10000",c("result")))]),
        "loss"=>Values::from([("result".into(),format!("({}-({}+{})/2)/{}*100",c("before"),c("after1"),c("after2"),c("mass")))]),_=>Values::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test] fn decimal_results_and_boundaries(){
        let p=Values::from([("c".into(),"0.5128".into()),("mw".into(),"14.01".into()),("factor".into(),"1".into()),("blank".into(),"0.05".into())]);
        let mut raw=Values::from([("mass".into(),"1".into()),("volume".into(),"10".into())]);
        let out=calculate("titration",&raw,&p).unwrap();assert_eq!(out["result"],"7.14840636");assert_eq!(out["display"],"7.14841");
        for bad in ["0","NaN","-1","0.0000000000001"] {raw.insert("mass".into(),bad.into());assert!(calculate("titration",&raw,&p).is_err());}
        let f=Values::from([("mass1".into(),"2".into()),("total1".into(),"20".into()),("mass2".into(),"5".into()),("total2".into(),"50".into()),("reading".into(),"1.2".into())]);
        assert_eq!(calculate("fluoride",&f,&Values::new()).unwrap()["result"],"120");
    }
}
