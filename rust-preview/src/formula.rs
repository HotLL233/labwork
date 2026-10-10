//! Restricted worksheet arithmetic. Constants and cell addresses come from the original workbook.
use bigdecimal::BigDecimal;
use std::{collections::{BTreeMap, BTreeSet}, str::FromStr};

#[derive(Clone)]
enum Token { Number(BigDecimal), Cell(String), Symbol(u8) }
enum Expr { Number(BigDecimal), Cell(String), Neg(Box<Expr>), Binary(u8,Box<Expr>,Box<Expr>) }

fn number(text:&str)->Result<BigDecimal,String>{
    if text.len()>160{return Err("数字超过160字符".into());}
    if let Some(exponent)=text.split(['e','E']).nth(1){if !exponent.parse::<i32>().is_ok_and(|n|(-120..=120).contains(&n)){return Err("数字指数超出±120范围".into());}}
    let value=BigDecimal::from_str(text).map_err(|_|"公式需要有效数字")?;
    if value.as_bigint_and_exponent().1>120||value.abs()>BigDecimal::from_str("1e30").unwrap(){return Err("数字精度或范围超限".into());}Ok(value)
}
fn tokens(expression:&str)->Result<Vec<Token>,String>{
    if expression.len()>8192{return Err("公式超过8192字节".into());}
    let text=expression.strip_prefix('=').unwrap_or(expression).as_bytes();let mut i=0;let mut out=Vec::new();
    while i<text.len(){
        if text[i].is_ascii_whitespace(){i+=1;continue;}
        let start=i;
        match text[i] {
            b'0'..=b'9'|b'.'=>{
                while i<text.len()&&(text[i].is_ascii_digit()||text[i]==b'.'){i+=1;}
                if i<text.len()&&matches!(text[i],b'e'|b'E'){i+=1;if i<text.len()&&matches!(text[i],b'+'|b'-'){i+=1;}while i<text.len()&&text[i].is_ascii_digit(){i+=1;}}
                out.push(Token::Number(number(std::str::from_utf8(&text[start..i]).unwrap())?));
            },
            b'A'..=b'Z'|b'a'..=b'z'|b'$'=>{
                if text[i]==b'$'{i+=1;}
                let col_start=i;let mut col=0u32;
                while i<text.len()&&text[i].is_ascii_alphabetic(){col=col.checked_mul(26).and_then(|n|n.checked_add((text[i].to_ascii_uppercase()-b'A'+1)as u32)).ok_or("公式列引用无效")?;i+=1;}
                if i==col_start||col>16384{return Err("公式列引用超出Excel范围".into());}
                if i<text.len()&&text[i]==b'$'{i+=1;}
                let row_start=i;while i<text.len()&&text[i].is_ascii_digit(){i+=1;}
                if row_start==i{return Err("暂不支持函数、命名区域或跨表公式；支持四则运算和单元格引用".into());}
                let row=std::str::from_utf8(&text[row_start..i]).unwrap().parse::<u32>().map_err(|_|"公式行引用无效")?;
                if row==0||row>1048576{return Err("公式行引用超出Excel范围".into());}
                out.push(Token::Cell(std::str::from_utf8(&text[start..i]).unwrap().to_ascii_uppercase()));
            },
            b'+'|b'-'|b'*'|b'/'|b'('|b')'=>{out.push(Token::Symbol(text[i]));i+=1;},
            _=>return Err("暂不支持此公式语法；支持+、-、*、/、括号和单元格引用".into()),
        }
        if out.len()>1024{return Err("公式运算项超过1024项".into());}
    }
    Ok(out)
}
struct Parser {tokens:Vec<Token>,at:usize}
impl Parser {
    fn symbol(&mut self,symbol:u8)->bool{if matches!(self.tokens.get(self.at),Some(Token::Symbol(s))if *s==symbol){self.at+=1;true}else{false}}
    fn expression(&mut self,min_precedence:u8,depth:u8)->Result<Expr,String>{
        if depth>32{return Err("公式嵌套超过32层".into());}
        let mut left=if self.symbol(b'+'){self.expression(3,depth+1)?}else if self.symbol(b'-'){Expr::Neg(Box::new(self.expression(3,depth+1)?))}else if self.symbol(b'('){let value=self.expression(0,depth+1)?;if !self.symbol(b')'){return Err("公式括号未闭合".into());}value}else{
            let token=self.tokens.get(self.at).cloned().ok_or("公式缺少操作数")?;self.at+=1;
            match token{Token::Number(n)=>Expr::Number(n),Token::Cell(c)=>Expr::Cell(c),_=>return Err("公式操作数无效".into())}
        };
        loop {
            let Some(Token::Symbol(operator @ (b'+'|b'-'|b'*'|b'/')))=self.tokens.get(self.at)else{break};
            let (operator,precedence)=(*operator,if matches!(operator,b'+'|b'-'){1}else{2});
            if precedence<min_precedence{break;}self.at+=1;
            left=Expr::Binary(operator,Box::new(left),Box::new(self.expression(precedence+1,depth+1)?));
        }
        Ok(left)
    }
}
fn parse(expression:&str)->Result<Expr,String>{let mut parser=Parser{tokens:tokens(expression)?,at:0};let tree=parser.expression(0,0)?;if parser.at!=parser.tokens.len(){return Err("公式包含多余内容或缺少运算符".into());}Ok(tree)}

/// Addresses retain their `$` markers so callers can distinguish fixed parameters from row inputs.
pub fn references(expression:&str)->Result<Vec<String>,String>{
    parse(expression)?;
    Ok(tokens(expression)?.into_iter().filter_map(|t|if let Token::Cell(c)=t{Some(c)}else{None}).collect::<BTreeSet<_>>().into_iter().collect())
}
fn rewrite(expression:&str,mut transform:impl FnMut(&str)->Result<String,String>)->Result<String,String>{
    parse(expression)?;
    let text=expression.as_bytes();let mut result=String::new();let mut i=0;
    while i<text.len(){
        let start=i;
        if text[i].is_ascii_digit()||text[i]==b'.' {
            while i<text.len()&&(text[i].is_ascii_digit()||text[i]==b'.'){i+=1;}
            if i<text.len()&&matches!(text[i],b'e'|b'E'){i+=1;if i<text.len()&&matches!(text[i],b'+'|b'-'){i+=1;}while i<text.len()&&text[i].is_ascii_digit(){i+=1;}}
            result.push_str(&expression[start..i]);
        }else if text[i].is_ascii_alphabetic()||text[i]==b'$' {
            if text[i]==b'$'{i+=1;}
            while i<text.len()&&text[i].is_ascii_alphabetic(){i+=1;}
            if i<text.len()&&text[i]==b'$'{i+=1;}
            while i<text.len()&&text[i].is_ascii_digit(){i+=1;}
            result.push_str(&transform(&expression[start..i].to_ascii_uppercase())?);
        }else {result.push(text[i]as char);i+=1;}
    }
    if result.len()>8192{return Err("平移后公式过长".into());}Ok(result)
}
pub fn anchor(expression:&str,addresses:&BTreeSet<String>)->Result<String,String>{
    rewrite(expression,|reference|{
        if !addresses.contains(&reference.replace('$',"")){return Ok(reference.into());}
        let address=reference.replace('$',"");let index=address.find(|c:char|c.is_ascii_digit()).unwrap();
        Ok(format!("${}${}",&address[..index],&address[index..]))
    })
}
pub fn translate(expression:&str,row_delta:i32,col_delta:i32)->Result<String,String>{
    rewrite(expression,|reference|{
        let index=reference.find(|c:char|c.is_ascii_digit()).unwrap();
        let prefix=&reference[..index];let absolute_column=prefix.starts_with('$');let absolute_row=prefix.ends_with('$');
        let mut column=0i64;for c in prefix.bytes().filter(|c|*c!=b'$'){column=column*26+(c-b'A'+1)as i64;}
        let mut row=reference[index..].parse::<i64>().map_err(|_|"公式行引用无效")?;
        if !absolute_column{column+=col_delta as i64;}if !absolute_row{row+=row_delta as i64;}
        if !(1..=16384).contains(&column)||!(1..=1048576).contains(&row){return Err("平移公式后单元格引用超出Excel范围".into());}
        let mut letters=String::new();while column>0{column-=1;letters.insert(0,(b'A'+(column%26)as u8)as char);column/=26;}
        Ok(format!("{}{}{}{}",if absolute_column{"$"}else{""},letters,if absolute_row{"$"}else{""},row))
    })
}
pub fn evaluate(expression:&str,values:&BTreeMap<String,String>)->Result<String,String>{
    fn calculate(tree:&Expr,values:&BTreeMap<String,String>)->Result<BigDecimal,String>{
        let result=match tree {
            Expr::Number(n)=>n.clone(),
            Expr::Cell(reference)=>{let key=reference.replace('$',"");number(values.get(&key).ok_or_else(||format!("缺少公式引用单元格{reference}的数值"))?).map_err(|e|format!("{reference}：{e}"))?},
            Expr::Neg(value)=>-calculate(value,values)?,
            Expr::Binary(operator,left,right)=>{
                let left=calculate(left,values)?;let right=calculate(right,values)?;
                match operator {b'+'=>left+right,b'-'=>left-right,b'*'=>left*right,b'/'=>{if right==BigDecimal::from(0){return Err("公式分母为零，请检查原始录入值".into());}left/right},_=>unreachable!()}
            }
        };
        if result.abs()>BigDecimal::from_str("1e30").unwrap(){return Err("公式中间结果超出计算范围".into());}
        Ok(result.with_prec(60))
    }
    Ok(calculate(&parse(expression)?,values)?.normalized().to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    fn values(pairs:&[(&str,&str)])->BTreeMap<String,String>{pairs.iter().map(|(k,v)|(k.to_string(),v.to_string())).collect()}
    #[test]
    fn original_ef_temperature_and_k001_absolute_parameters(){
        let ef="0.1051/(1+0.0011*(C3-21.9))*E3*118.01/1000/D3*100";
        assert_eq!(references(ef).unwrap(),vec!["C3","D3","E3"]);
        let result=evaluate(ef,&values(&[("C3","23.3"),("D3","0.15"),("E3","10")])).unwrap();
        assert_eq!(BigDecimal::from_str(&result).unwrap().with_scale_round(10,bigdecimal::RoundingMode::HalfUp).to_string(),"82.5585331922");
        let sulfate="$I$1*($I$2+E3-D3)*0.09606/C3*100";
        assert_eq!(references(sulfate).unwrap(),vec!["$I$1","$I$2","C3","D3","E3"]);
        let result=evaluate(sulfate,&values(&[("I1","5.117E-3"),("I2","40.9"),("C3","1.0537"),("D3","2.71"),("E3","0")])).unwrap();
        assert_eq!(BigDecimal::from_str(&result).unwrap().with_scale_round(10,bigdecimal::RoundingMode::HalfUp).to_string(),"1.7815198988");
        assert_eq!(evaluate("= -(2+3)*4/2+1e-3",&BTreeMap::new()).unwrap(),"-9.999");
        assert_eq!(evaluate("10/2/5+2*3",&BTreeMap::new()).unwrap(),"7");
        let first=evaluate("1/3",&BTreeMap::new()).unwrap();
        assert!(evaluate("F37*I37/H37",&values(&[("F37",&first),("I37","10"),("H37","2")])).is_ok());
    }
    #[test]
    fn rejects_ambiguous_unsupported_and_unsafe_formulas(){
        for formula in ["","A0","XFE1","A1048577","1e999999","1/0","1/(2-2)","(1+2","1 2","SUM(A1:A4)","Sheet1!A1","[book]A1","2^3","ROUND(1.25,1)","A1&1",".1.2","1e","1;2"]{assert!(evaluate(formula,&BTreeMap::new()).is_err(),"{formula}");}
        assert!(references("A1+B1").is_ok());
        assert!(evaluate("A1",&values(&[("A1","NaN")])).is_err());
        assert!(evaluate(&format!("{}1{}","(".repeat(40),")".repeat(40)),&BTreeMap::new()).is_err());
        assert!(references(&"A1+".repeat(3000)).is_err());
        assert_eq!(evaluate("A1-$B$2",&values(&[("A1","0.3"),("B2","0.1")])).unwrap(),"0.2");
        assert_eq!(translate("=$A1+B$2+$C$3+1E-3+12.3",2,1).unwrap(),"=$A3+C$2+$C$3+1E-3+12.3");
        assert_eq!(translate("0.1051/(1+0.0011*(C3-21.9))*E3*118.01/1000/D3*100",3,0).unwrap(),"0.1051/(1+0.0011*(C6-21.9))*E6*118.01/1000/D6*100");
        assert!(translate("A1",-1,0).is_err());assert!(translate("A1",0,-1).is_err());assert!(translate("XFD1048576",1,0).is_err());
        assert_eq!(translate("$A$1",-10,-10).unwrap(),"$A$1");
    }
}
