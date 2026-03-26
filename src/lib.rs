use pyo3::prelude::*;

mod parse;
mod unparse;

#[pymodule]
fn _xmltodict_rs(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(parse::parse, m)?)?;
    m.add_function(wrap_pyfunction!(unparse::unparse, m)?)?;
    Ok(())
}
