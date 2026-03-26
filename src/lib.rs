use pyo3::prelude::*;

mod parse;

#[pymodule]
fn _xmltodict_rs(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(parse::parse, m)?)?;
    Ok(())
}
